"""Foreground orchestrator agent.

Drives the user-facing conversation via streaming Claude API calls, classifies
each turn (per docs/spec.md section 10.3), and dispatches background work
through the Worker Manager. Speech output is streamed to the Swift app as
``agent.say_*`` events; the app forwards them to voice_service for TTS.

Tool use:
  * dispatch_worker — start a background task
  * cancel_worker   — stop a worker by id

Tool calls are processed after the streamed message completes. The natural
language portion of the response is what gets spoken; tool-use blocks are
silent side effects.
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import os
import uuid
from pathlib import Path
from typing import Awaitable, Callable, Optional

import anthropic

from .config import AgentConfig
from .safety_policy import SafetyDecision, evaluate
from .worker_manager import WorkerManager

log = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).parent / "prompts"


def _load_prompt(name: str) -> str:
    path = PROMPTS_DIR / name
    return path.read_text(encoding="utf-8") if path.exists() else ""


TOOLS: list[dict] = [
    {
        "name": "dispatch_worker",
        "description": (
            "Start a background worker that operates the user's Mac through "
            "Hermes computer_use. Use this for any task that requires opening "
            "or interacting with macOS apps. Returns immediately; progress is "
            "delivered through the app's task drawer."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Short user-facing title for the task drawer."},
                "instruction": {"type": "string", "description": "Detailed instruction for the worker agent."},
                "allowed_apps": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "macOS app names the worker may operate. Use the smallest necessary set.",
                },
                "risk_level": {
                    "type": "string",
                    "enum": ["low", "medium", "high"],
                    "description": "Risk classification. Medium and high force user confirmation.",
                },
                "read_only": {
                    "type": "boolean",
                    "description": "True for tasks that only read screen state (no clicks, types, or saves).",
                },
            },
            "required": ["title", "instruction", "allowed_apps", "risk_level"],
        },
    },
    {
        "name": "cancel_worker",
        "description": "Cancel a running worker by task_id. Use 'all' to cancel every active worker.",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "reason": {"type": "string", "default": "user_requested"},
            },
            "required": ["task_id"],
        },
    },
]


@dataclasses.dataclass
class ConversationMessage:
    role: str  # "user" | "assistant"
    content: list[dict]  # Anthropic content blocks


EventEmitter = Callable[[dict], Awaitable[None]]


class Orchestrator:
    def __init__(
        self,
        cfg: AgentConfig,
        worker_manager: WorkerManager,
        emit: EventEmitter,
        client: Optional[anthropic.AsyncAnthropic] = None,
    ) -> None:
        self.cfg = cfg
        self.workers = worker_manager
        self._emit = emit
        self._system_prompt = _load_prompt("foreground_agent.md")
        self._history: list[ConversationMessage] = []
        self._client = client or self._build_client()
        self._current_turn: Optional[asyncio.Task[None]] = None

    def _build_client(self) -> anthropic.AsyncAnthropic:
        api_key = self.cfg.anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            log.warning("ANTHROPIC_API_KEY not set; orchestrator will fail on first turn")
        return anthropic.AsyncAnthropic(api_key=api_key or "")

    async def submit_turn(self, turn_id: str, user_text: str) -> None:
        """Process a user turn end-to-end. Returns when the spoken reply is
        done streaming and any tool-driven side effects have been launched."""
        if self._current_turn and not self._current_turn.done():
            log.info("interrupting previous turn for %s", turn_id)
            self._current_turn.cancel()
            try:
                await self._current_turn
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        self._current_turn = asyncio.create_task(self._run_turn(turn_id, user_text))
        try:
            await self._current_turn
        except asyncio.CancelledError:
            pass

    async def interrupt(self) -> None:
        if self._current_turn and not self._current_turn.done():
            self._current_turn.cancel()
            try:
                await self._current_turn
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

    async def _run_turn(self, turn_id: str, user_text: str) -> None:
        utterance_id = f"utt_{uuid.uuid4().hex[:12]}"
        user_text = self._normalize_user_text(user_text)

        # Inject a fresh snapshot of workers each turn so the agent never
        # invents worker state from memory.
        worker_snapshot = self.workers.public_snapshot()
        snapshot_block = self._format_worker_snapshot(worker_snapshot)
        user_blocks = [{"type": "text", "text": user_text}]
        if snapshot_block:
            user_blocks.insert(0, {"type": "text", "text": snapshot_block})

        self._history.append(ConversationMessage(role="user", content=user_blocks))
        self._trim_history()

        await self._emit({"type": "agent.thinking", "turnId": turn_id, "active": True})

        say_started = False
        assistant_blocks: list[dict] = []
        tool_calls: list[dict] = []

        try:
            async with self._client.messages.stream(
                model=self.cfg.foreground_model,
                system=self._system_prompt,
                max_tokens=512,
                tools=TOOLS,
                messages=[{"role": m.role, "content": m.content} for m in self._history],
            ) as stream:
                async for event in stream:
                    if event.type == "content_block_start":
                        block = event.content_block
                        if block.type == "tool_use":
                            tool_calls.append(
                                {"id": block.id, "name": block.name, "input": {}, "input_buf": ""}
                            )
                    elif event.type == "content_block_delta":
                        delta = event.delta
                        if delta.type == "text_delta":
                            if not say_started:
                                say_started = True
                                await self._emit(
                                    {
                                        "type": "agent.say_start",
                                        "turnId": turn_id,
                                        "utteranceId": utterance_id,
                                        "interruptible": True,
                                    }
                                )
                            await self._emit(
                                {
                                    "type": "agent.say_chunk",
                                    "utteranceId": utterance_id,
                                    "text": delta.text,
                                }
                            )
                        elif delta.type == "input_json_delta" and tool_calls:
                            tool_calls[-1]["input_buf"] += delta.partial_json
                    elif event.type == "content_block_stop":
                        if tool_calls and "input_buf" in tool_calls[-1]:
                            buf = tool_calls[-1].pop("input_buf")
                            try:
                                tool_calls[-1]["input"] = json.loads(buf) if buf else {}
                            except json.JSONDecodeError:
                                log.warning("could not parse tool input json: %r", buf)
                                tool_calls[-1]["input"] = {}

                final = await stream.get_final_message()
                assistant_blocks = [self._serialize_block(b) for b in final.content]
        except asyncio.CancelledError:
            await self._emit({"type": "agent.thinking", "turnId": turn_id, "active": False})
            if say_started:
                await self._emit({"type": "agent.say_end", "utteranceId": utterance_id, "interrupted": True})
            raise
        except Exception as e:  # noqa: BLE001
            log.exception("foreground LLM call failed")
            await self._emit({"type": "error", "code": "foreground_llm", "message": str(e)})
            await self._emit({"type": "agent.thinking", "turnId": turn_id, "active": False})
            return

        if say_started:
            await self._emit({"type": "agent.say_end", "utteranceId": utterance_id})
        await self._emit({"type": "agent.thinking", "turnId": turn_id, "active": False})

        history_blocks = [b for b in assistant_blocks if b.get("type") == "text"]
        if history_blocks:
            self._history.append(ConversationMessage(role="assistant", content=history_blocks))

        for call in tool_calls:
            await self._execute_tool(call)

    def _format_worker_snapshot(self, workers: list[dict]) -> str:
        if not workers:
            return "[runtime state] No background workers are currently running."
        lines = ["[runtime state] Currently active workers:"]
        for w in workers:
            lines.append(
                f"  - {w['taskId']} \"{w['title']}\" state={w['state']} "
                f"risk={w['riskLevel']} apps={w['targetApps']} msg={w['lastMessage']!r}"
            )
        return "\n".join(lines)

    @staticmethod
    def _normalize_user_text(text: str) -> str:
        stripped = text.strip()
        low = " ".join(stripped.lower().split())
        textedit_mishears = {
            "open taxi",
            "open taxi.",
            "open text held",
            "open text held.",
            "open texthead",
            "open texthead.",
            "open text at it",
            "open text at it.",
            "oh text held",
            "oh text held.",
        }
        if low in textedit_mishears:
            return "Open TextEdit."
        return stripped

    @staticmethod
    def _serialize_block(block) -> dict:
        # anthropic returns pydantic-like content blocks; serialize to dict for replay.
        if block.type == "text":
            return {"type": "text", "text": block.text}
        if block.type == "tool_use":
            return {"type": "tool_use", "id": block.id, "name": block.name, "input": block.input}
        return {"type": block.type}

    async def _execute_tool(self, call: dict) -> None:
        name = call["name"]
        inp = call.get("input", {}) or {}
        if name == "dispatch_worker":
            await self._tool_dispatch_worker(inp)
        elif name == "cancel_worker":
            await self._tool_cancel_worker(inp)
        else:
            log.warning("unknown tool %s", name)

    async def _tool_dispatch_worker(self, inp: dict) -> None:
        title = (inp.get("title") or "").strip() or "Background task"
        instruction = (inp.get("instruction") or "").strip()
        allowed = tuple(a for a in inp.get("allowed_apps", []) if isinstance(a, str))
        risk = inp.get("risk_level", "low")
        read_only = bool(inp.get("read_only", False))
        decision: SafetyDecision = evaluate(instruction, allowed)
        if decision.is_blocked:
            await self._emit(
                {
                    "type": "error",
                    "code": "blocked_by_safety",
                    "message": "; ".join(decision.reasons) or "blocked by safety policy",
                }
            )
            return
        # Bump risk if safety found something more severe than the model claimed.
        if decision.risk == "high" and risk != "high":
            risk = "high"
        elif decision.risk == "medium" and risk == "low":
            risk = "medium"
        await self.workers.start_worker(
            title=title,
            instruction=instruction,
            allowed_apps=allowed,
            risk_level=risk,
            safety_decision=decision,
            read_only=read_only,
        )

    async def _tool_cancel_worker(self, inp: dict) -> None:
        task_id = (inp.get("task_id") or "").strip()
        if task_id == "all":
            await self.workers.cancel_all(reason=inp.get("reason", "user_requested"))
        elif task_id:
            await self.workers.cancel(task_id, reason=inp.get("reason", "user_requested"))

    def _trim_history(self) -> None:
        limit = self.cfg.history_max_turns * 2
        if len(self._history) > limit:
            self._history = self._history[-limit:]
