"""Worker Manager — owns worker lifecycle, concurrency, per-app locks, and
cancellation. See docs/spec.md sections 7.1, 10.2, and 11.2.

Invariants enforced here:
  * Every worker ends in exactly one terminal state.
  * `cancellation_requested` is monotonic.
  * A worker in `waiting_for_user_confirmation` does not execute tool actions
    until a matching confirmation response arrives.
  * Two workers cannot mutate the same target app at the same time unless both
    are read-only.
  * Progress events are ordered by monotonic `sequence` before display.
"""
from __future__ import annotations

import asyncio
import dataclasses
import logging
import time
import uuid
from typing import Awaitable, Callable, Literal, Optional

from .hermes_bridge import HermesBridge, HermesEvent, HermesTaskSpec
from .safety_policy import SafetyDecision

log = logging.getLogger(__name__)

WorkerState = Literal[
    "queued",
    "starting",
    "running",
    "waiting_for_user_confirmation",
    "paused",
    "completed",
    "failed",
    "cancelled",
]

TERMINAL_STATES: frozenset[WorkerState] = frozenset({"completed", "failed", "cancelled"})


@dataclasses.dataclass
class PendingConfirmation:
    confirmation_id: str
    prompt: str
    risk_level: str
    choices: tuple[str, ...]
    created_at: float = dataclasses.field(default_factory=time.monotonic)


@dataclasses.dataclass
class WorkerSummary:
    task_id: str
    title: str
    state: WorkerState
    risk_level: str
    last_message: str = ""
    last_action: str = ""
    started_at: float = dataclasses.field(default_factory=time.monotonic)
    updated_at: float = dataclasses.field(default_factory=time.monotonic)
    sequence: int = 0
    needs_user: bool = False
    pending_confirmation: Optional[PendingConfirmation] = None
    target_apps: tuple[str, ...] = ()
    read_only: bool = False
    summary: str = ""

    def to_public_dict(self) -> dict:
        return {
            "taskId": self.task_id,
            "title": self.title,
            "state": self.state,
            "riskLevel": self.risk_level,
            "lastMessage": self.last_message,
            "lastAction": self.last_action,
            "needsUser": self.needs_user,
            "targetApps": list(self.target_apps),
            "summary": self.summary,
            "elapsedSec": round(time.monotonic() - self.started_at, 1),
        }


EventHandler = Callable[[dict], Awaitable[None]]


class WorkerManager:
    """In-memory worker registry. One per agent_service session."""

    def __init__(
        self,
        bridge: HermesBridge,
        emit: EventHandler,
        max_concurrent: int = 2,
    ) -> None:
        self._bridge = bridge
        self._emit = emit
        self._max_concurrent = max_concurrent
        self._workers: dict[str, WorkerSummary] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._pending_confirmations: dict[str, asyncio.Future[str]] = {}
        # Per-app locks: writer-exclusive. Read-only workers do not take a lock.
        self._app_writers: dict[str, str] = {}  # app -> task_id
        # Monotonic cancellation marker. Once a task_id is here, the only
        # valid terminal state is "cancelled" — see docs/spec.md §11.4.
        self._cancellation_requested: set[str] = set()

    # ── Public API ────────────────────────────────────────────────────────

    def summaries(self) -> list[WorkerSummary]:
        return list(self._workers.values())

    def public_snapshot(self) -> list[dict]:
        return [w.to_public_dict() for w in self._workers.values() if w.state not in TERMINAL_STATES]

    def active_count(self) -> int:
        return sum(1 for w in self._workers.values() if w.state not in TERMINAL_STATES)

    async def start_worker(
        self,
        title: str,
        instruction: str,
        allowed_apps: tuple[str, ...],
        risk_level: str,
        safety_decision: SafetyDecision,
        read_only: bool = False,
        timeout_sec: int = 300,
    ) -> Optional[str]:
        """Queue and dispatch a worker. Returns the task_id, or None if rejected."""
        if safety_decision.is_blocked:
            await self._emit(
                {
                    "type": "error",
                    "code": "blocked_by_safety",
                    "message": "; ".join(safety_decision.reasons),
                }
            )
            return None
        if self.active_count() >= self._max_concurrent:
            await self._emit(
                {
                    "type": "error",
                    "code": "worker_limit",
                    "message": f"already running {self._max_concurrent} workers; cancel one first",
                }
            )
            return None
        # Per-app exclusion: a writer cannot run if any writer for the same app is active.
        if not read_only:
            for app in allowed_apps:
                holder = self._app_writers.get(app)
                if holder and holder in self._workers and self._workers[holder].state not in TERMINAL_STATES:
                    await self._emit(
                        {
                            "type": "error",
                            "code": "app_locked",
                            "message": f"another worker is already operating {app}",
                        }
                    )
                    return None

        task_id = f"task_{uuid.uuid4().hex[:12]}"
        summary = WorkerSummary(
            task_id=task_id,
            title=title,
            state="queued",
            risk_level=risk_level,
            target_apps=tuple(allowed_apps),
            read_only=read_only,
        )
        self._workers[task_id] = summary
        if not read_only:
            for app in allowed_apps:
                self._app_writers[app] = task_id

        await self._emit(
            {
                "type": "worker.started",
                "taskId": task_id,
                "title": title,
                "riskLevel": risk_level,
                "targetApps": list(allowed_apps),
            }
        )

        spec = HermesTaskSpec(
            task_id=task_id,
            title=title,
            instruction=instruction,
            allowed_apps=allowed_apps,
            risk_level=risk_level,
            require_user_confirmation=safety_decision.require_confirmation,
            timeout_sec=timeout_sec,
        )
        self._tasks[task_id] = asyncio.create_task(self._run_worker(spec))
        return task_id

    async def cancel(self, task_id: str, reason: str = "user_cancelled") -> bool:
        worker = self._workers.get(task_id)
        if worker is None or worker.state in TERMINAL_STATES:
            return False
        log.info("cancelling worker %s reason=%s", task_id, reason)
        # Step 1: mark cancellation_requested immediately so any subsequent
        # natural-terminal event is coerced to "cancelled" (monotonicity).
        self._cancellation_requested.add(task_id)
        # Step 2: soft interrupt through the bridge.
        await self._bridge.cancel(task_id)
        # Step 3: brief grace period for the bridge to wind down cleanly.
        task = self._tasks.get(task_id)
        if task and not task.done():
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=2.0)
            except asyncio.TimeoutError:
                # Step 4: hard-terminate the asyncio task.
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
        # Step 5: emit terminal state if the bridge did not already.
        if worker.state not in TERMINAL_STATES:
            await self._set_terminal(worker, "cancelled", summary=f"cancelled ({reason})")
        return True

    async def cancel_all(self, reason: str = "user_cancelled") -> int:
        cancelled = 0
        for tid in list(self._workers.keys()):
            if await self.cancel(tid, reason=reason):
                cancelled += 1
        return cancelled

    async def confirm(self, confirmation_id: str, decision: str) -> bool:
        fut = self._pending_confirmations.pop(confirmation_id, None)
        if fut is None or fut.done():
            return False
        fut.set_result(decision)
        return True

    # ── Internal: per-worker lifecycle ────────────────────────────────────

    async def _run_worker(self, spec: HermesTaskSpec) -> None:
        worker = self._workers[spec.task_id]
        try:
            await self._update_state(worker, "starting", message="initializing worker")
            async for ev in self._bridge.run(spec):
                await self._handle_event(worker, ev)
                if worker.state in TERMINAL_STATES:
                    break
        except asyncio.CancelledError:
            if worker.state not in TERMINAL_STATES:
                await self._set_terminal(worker, "cancelled", summary="cancelled")
            raise
        except Exception as e:  # noqa: BLE001
            log.exception("worker %s crashed", spec.task_id)
            await self._set_terminal(worker, "failed", summary=f"error: {e}")
        finally:
            # Release per-app locks held by this worker.
            for app, holder in list(self._app_writers.items()):
                if holder == spec.task_id:
                    del self._app_writers[app]

    async def _handle_event(self, worker: WorkerSummary, ev: HermesEvent) -> None:
        worker.sequence = max(worker.sequence + 1, ev.sequence)
        worker.updated_at = time.monotonic()
        if ev.kind == "progress":
            await self._update_state(
                worker,
                "running",
                message=ev.message or worker.last_message,
                last_action=ev.last_action or worker.last_action,
            )
        elif ev.kind == "needs_confirmation":
            await self._handle_confirmation(worker, ev)
        elif ev.kind == "result":
            status: WorkerState = "completed" if ev.status == "completed" else "failed"
            await self._set_terminal(worker, status, summary=ev.summary or "")
        elif ev.kind == "error":
            await self._set_terminal(worker, "failed", summary=ev.message or "worker error")

    async def _handle_confirmation(self, worker: WorkerSummary, ev: HermesEvent) -> None:
        confirmation_id = f"confirm_{uuid.uuid4().hex[:10]}"
        pending = PendingConfirmation(
            confirmation_id=confirmation_id,
            prompt=ev.message or "Proceed?",
            risk_level=ev.risk_level or worker.risk_level,
            choices=tuple(ev.choices or ("confirm", "cancel")),
        )
        worker.pending_confirmation = pending
        worker.needs_user = True
        await self._update_state(
            worker,
            "waiting_for_user_confirmation",
            message=pending.prompt,
        )
        await self._emit(
            {
                "type": "worker.needs_confirmation",
                "taskId": worker.task_id,
                "confirmationId": confirmation_id,
                "prompt": pending.prompt,
                "riskLevel": pending.risk_level,
                "choices": list(pending.choices),
            }
        )
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[str] = loop.create_future()
        self._pending_confirmations[confirmation_id] = fut
        try:
            decision = await asyncio.wait_for(fut, timeout=120.0)
        except asyncio.TimeoutError:
            decision = "cancel"
        worker.pending_confirmation = None
        worker.needs_user = False
        await self._bridge.deliver_confirmation(worker.task_id, decision)
        if decision != "confirm":
            await self.cancel(worker.task_id, reason="user_rejected_confirmation")

    async def _update_state(
        self,
        worker: WorkerSummary,
        state: WorkerState,
        *,
        message: str = "",
        last_action: str = "",
    ) -> None:
        worker.state = state
        if message:
            worker.last_message = message
        if last_action:
            worker.last_action = last_action
        await self._emit(
            {
                "type": "worker.progress",
                "taskId": worker.task_id,
                "state": state,
                "message": worker.last_message,
                "lastAction": worker.last_action,
                "needsUser": worker.needs_user,
                "sequence": worker.sequence,
            }
        )

    async def _set_terminal(self, worker: WorkerSummary, state: WorkerState, *, summary: str) -> None:
        if worker.state in TERMINAL_STATES:
            return
        # Monotonic cancellation: if a cancel was requested, we never report
        # a non-cancelled terminal state regardless of what the bridge emitted.
        if worker.task_id in self._cancellation_requested and state != "cancelled":
            state = "cancelled"
            summary = summary or "cancelled"
        worker.state = state
        worker.summary = summary
        worker.updated_at = time.monotonic()
        await self._emit(
            {
                "type": "worker.result",
                "taskId": worker.task_id,
                "status": state,
                "summary": summary,
                "artifacts": [],
            }
        )
