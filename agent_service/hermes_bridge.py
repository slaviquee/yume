"""Hermes Agent bridge.

The bridge owns Hermes process lifecycle, stdin/stdout handling, soft
cancellation, and event normalization. UI code never touches Hermes output
directly — it consumes the normalized `HermesEvent` stream below.

Two implementations:

* `RealHermesBridge` — spawns the Hermes CLI in non-interactive chat mode with
  the `computer_use` toolset enabled. The current Hermes CLI is optimized for
  terminal UX, so this bridge treats stdout/stderr as process output and emits
  normalized yume worker events around it.
  Note: we use ``asyncio.create_subprocess_exec`` which passes argv as a list
  — no shell interpretation, no command injection surface.
* `MockHermesBridge` — a deterministic timer-based simulator used when Hermes
  is not installed or ``YUME_HERMES_MODE=mock``. It emits realistic progress
  and result events so the rest of the system can be exercised end-to-end.

The ``auto`` mode prefers real Hermes when the binary is on PATH; otherwise
it falls back to the mock so the demo remains exercisable.
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import os
import re
import shutil
import time
from typing import AsyncIterator, Literal, Optional

log = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True)
class HermesTaskSpec:
    task_id: str
    title: str
    instruction: str
    allowed_apps: tuple[str, ...]
    risk_level: str = "low"
    require_user_confirmation: bool = False
    capture_mode: Literal["som", "ax"] = "som"
    timeout_sec: int = 300


@dataclasses.dataclass
class HermesEvent:
    kind: Literal["progress", "needs_confirmation", "result", "error"]
    sequence: int
    timestamp: float
    message: Optional[str] = None
    last_action: Optional[str] = None
    status: Optional[str] = None
    summary: Optional[str] = None
    risk_level: Optional[str] = None
    choices: Optional[tuple[str, ...]] = None
    artifacts: tuple[dict, ...] = ()


class HermesBridge:
    async def run(self, spec: HermesTaskSpec) -> AsyncIterator[HermesEvent]:  # pragma: no cover
        raise NotImplementedError
        yield  # type: ignore[unreachable]

    async def cancel(self, task_id: str) -> None:  # pragma: no cover
        raise NotImplementedError

    async def deliver_confirmation(self, task_id: str, decision: str) -> None:  # pragma: no cover
        raise NotImplementedError


def make_bridge(mode: str = "auto", hermes_bin: str = "hermes") -> HermesBridge:
    if mode == "mock":
        return MockHermesBridge()
    if mode == "real":
        return RealHermesBridge(hermes_bin)
    if shutil.which(hermes_bin):
        return RealHermesBridge(hermes_bin)
    log.warning("hermes binary %s not found; using mock bridge", hermes_bin)
    return MockHermesBridge()


class RealHermesBridge(HermesBridge):
    """Spawns the Hermes CLI per task using argv-list invocation (no shell)."""

    def __init__(self, hermes_bin: str = "hermes") -> None:
        self.hermes_bin = hermes_bin
        self.provider = os.environ.get("HERMES_INFERENCE_PROVIDER", "anthropic")
        self.model = (
            os.environ.get("HERMES_INFERENCE_MODEL")
            or os.environ.get("YUME_WORKER_MODEL")
            or os.environ.get("YUME_FOREGROUND_MODEL")
            or "claude-sonnet-4-6"
        )
        self._processes: dict[str, asyncio.subprocess.Process] = {}
        self._confirm_queues: dict[str, asyncio.Queue[str]] = {}

    async def run(self, spec: HermesTaskSpec) -> AsyncIterator[HermesEvent]:
        self._confirm_queues[spec.task_id] = asyncio.Queue()
        seq = 0

        if spec.require_user_confirmation:
            seq += 1
            yield HermesEvent(
                kind="needs_confirmation",
                sequence=seq,
                timestamp=time.time(),
                message=f"Start background worker \"{spec.title}\"?",
                risk_level=spec.risk_level,
                choices=("confirm", "cancel"),
            )
            decision = await self._confirm_queues[spec.task_id].get()
            if decision != "confirm":
                seq += 1
                yield HermesEvent(
                    kind="result",
                    sequence=seq,
                    timestamp=time.time(),
                    status="cancelled",
                    summary="user declined confirmation",
                )
                self._confirm_queues.pop(spec.task_id, None)
                return

        seq += 1
        yield HermesEvent(
            kind="progress",
            sequence=seq,
            timestamp=time.time(),
            message="starting Hermes computer-use worker",
            last_action="hermes.oneshot",
        )

        preflight_error = await self._preflight_computer_use()
        if preflight_error:
            log.error("hermes computer-use preflight failed: task=%s error=%s", spec.task_id, preflight_error)
            yield HermesEvent(
                kind="error",
                sequence=seq + 1,
                timestamp=time.time(),
                message=preflight_error,
            )
            return

        prompt = self._build_prompt(spec)
        argv = [
            self.hermes_bin,
            "--oneshot",
            prompt,
            "--provider",
            self.provider,
            "--model",
            self.model,
            "--toolsets",
            "computer_use",
            "--ignore-rules",
        ]

        log.info("spawning hermes: task=%s apps=%s", spec.task_id, spec.allowed_apps)
        env = os.environ.copy()
        env.setdefault("HERMES_INFERENCE_PROVIDER", self.provider)
        env.setdefault("HERMES_INFERENCE_MODEL", self.model)
        cua_driver = shutil.which("cua-driver")
        if cua_driver:
            env.setdefault("HERMES_CUA_DRIVER_CMD", cua_driver)
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        self._processes[spec.task_id] = proc

        try:
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=spec.timeout_sec)
            except asyncio.TimeoutError:
                log.error("hermes timed out: task=%s timeout_sec=%s", spec.task_id, spec.timeout_sec)
                yield HermesEvent(kind="error", sequence=seq + 1, timestamp=time.time(), message="timeout")
                return

            out_text = stdout.decode("utf-8", errors="replace")
            err_text = stderr.decode("utf-8", errors="replace")
            log.info(
                "hermes completed: task=%s rc=%s stdout=%r stderr=%r",
                spec.task_id,
                proc.returncode,
                self._clip_for_log(out_text),
                self._clip_for_log(err_text),
            )
            if proc.returncode != 0:
                message = self._clean_output(err_text or out_text) or f"hermes exited rc={proc.returncode}"
                yield HermesEvent(
                    kind="error",
                    sequence=seq + 1,
                    timestamp=time.time(),
                    message=message[:800],
                )
                return

            summary = self._clean_output(out_text) or "Hermes worker completed."
            yield HermesEvent(
                kind="result",
                sequence=seq + 1,
                timestamp=time.time(),
                status="completed",
                summary=summary[:1200],
            )
        finally:
            self._processes.pop(spec.task_id, None)
            self._confirm_queues.pop(spec.task_id, None)
            if proc.returncode is None:
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=3.0)
                except asyncio.TimeoutError:
                    proc.kill()
                    await proc.wait()

    async def cancel(self, task_id: str) -> None:
        proc = self._processes.get(task_id)
        if proc is None or proc.returncode is not None:
            return
        try:
            if proc.stdin is not None and not proc.stdin.is_closing():
                proc.stdin.write((json.dumps({"type": "cancel"}) + "\n").encode())
                await proc.stdin.drain()
        except Exception:  # noqa: BLE001
            pass
        await asyncio.sleep(0.5)
        if proc.returncode is None:
            proc.terminate()

    async def deliver_confirmation(self, task_id: str, decision: str) -> None:
        queue = self._confirm_queues.get(task_id)
        if queue is None:
            return
        await queue.put(decision)

    def _build_prompt(self, spec: HermesTaskSpec) -> str:
        apps = ", ".join(spec.allowed_apps) if spec.allowed_apps else "the minimum required apps"
        return "\n".join(
            [
                "You are a yume background Mac worker.",
                "Use the computer_use toolset when macOS UI interaction is required.",
                "For requests to open or launch an app, call computer_use(action='list_apps') to find the bundle_id, then computer_use(action='launch_app', bundle_id='...').",
                "After launching, stop unless the user asked you to do more inside the app.",
                "For writing into a blank document editor such as TextEdit, launch the app, send computer_use(action='key', keys='cmd+n') if a new document is needed, then use computer_use(action='type', text='...'). Do not wait for a screenshot before creating the first document window.",
                "For Calculator arithmetic, launch Calculator, call computer_use(action='capture', mode='som', app='Calculator'), clear the current value with computer_use(action='key', keys='escape'), then click the Calculator buttons by their numbered element indices from the capture (digits, Add, Equals, etc.). Do not use computer_use(action='type', text='2+2=') in Calculator because its display is not a text field and AX typing can report success without entering the expression. Verify the displayed result with a follow-up capture.",
                f"Task title: {spec.title}",
                f"Allowed apps: {apps}. Do not operate apps outside this list unless the task is impossible otherwise.",
                f"Capture mode preference: {spec.capture_mode}.",
                "Do not type or request passwords, API keys, 2FA codes, payment details, or other secrets.",
                "Do not click permission dialogs, payment UI, destructive delete controls, or account/security settings.",
                "Keep the task bounded. Return a concise final summary of what you did and anything the user must review.",
                "",
                "User task:",
                spec.instruction,
            ]
        )

    async def _preflight_computer_use(self) -> Optional[str]:
        """Return a user-facing error if CuaDriver is clearly unavailable.

        Hermes can otherwise spend minutes in a worker while the underlying
        computer-use backend is blind. We keep this check narrow: CuaDriver must
        be installed and able to see at least one macOS window from its own
        list_windows tool.
        """
        if os.environ.get("YUME_SKIP_HERMES_PREFLIGHT") == "1":
            return None

        cua_driver = shutil.which("cua-driver")
        if not cua_driver:
            return "CuaDriver is not installed. Run `hermes computer-use install --upgrade`, then grant Accessibility and Screen Recording permissions."

        daemon_error = await self._ensure_cuadriver_daemon(cua_driver)
        if daemon_error is not None:
            return daemon_error
        permission_error = await self._check_cuadriver_permissions(cua_driver)
        if permission_error is not None:
            return permission_error

        try:
            proc = await asyncio.create_subprocess_exec(
                cua_driver,
                "call",
                "list_windows",
                "{}",
                "--raw",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=4.0)
        except asyncio.TimeoutError:
            return "CuaDriver health check timed out. Restart CuaDriver and grant Accessibility and Screen Recording permissions to CuaDriver.app."
        except Exception as e:  # noqa: BLE001
            return f"CuaDriver health check failed: {e}"

        out_text = stdout.decode("utf-8", errors="replace")
        err_text = stderr.decode("utf-8", errors="replace")
        if proc.returncode != 0:
            detail = self._clean_output(err_text or out_text)
            suffix = f": {detail[:240]}" if detail else ""
            return f"CuaDriver is not ready{suffix}"

        try:
            payload = json.loads(out_text)
        except json.JSONDecodeError:
            log.warning("cua-driver list_windows returned non-json output: %r", self._clip_for_log(out_text))
            return "CuaDriver returned an unreadable health-check response. Restart CuaDriver.app and try again."

        structured = payload.get("structuredContent") if isinstance(payload, dict) else {}
        windows = structured.get("windows") if isinstance(structured, dict) else None
        if isinstance(windows, list) and windows:
            return None
        return (
            "CuaDriver cannot see any macOS windows, so Hermes cannot operate Calculator yet. "
            "Open CuaDriver.app once and grant both Accessibility and Screen Recording permissions, then restart yume services."
        )

    async def _ensure_cuadriver_daemon(self, cua_driver: str) -> Optional[str]:
        """Ensure `cua-driver call` proxies through CuaDriver.app's TCC grants."""
        try:
            status_proc = await asyncio.create_subprocess_exec(
                cua_driver,
                "status",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(status_proc.communicate(), timeout=2.0)
            if status_proc.returncode == 0:
                return None
        except Exception:  # noqa: BLE001
            pass

        try:
            serve_proc = await asyncio.create_subprocess_exec(
                cua_driver,
                "serve",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(serve_proc.communicate(), timeout=6.0)
        except asyncio.TimeoutError:
            return (
                "CuaDriver daemon launch timed out. Run `cua-driver serve` once, then retry yume."
            )
        except Exception as e:  # noqa: BLE001
            return f"CuaDriver daemon launch failed: {e}"

        if serve_proc.returncode != 0:
            detail = self._clean_output(
                stdout.decode("utf-8", errors="replace") + "\n" + stderr.decode("utf-8", errors="replace")
            )
            suffix = f": {detail[:240]}" if detail else ""
            return f"CuaDriver daemon is not ready{suffix}"
        return None

    async def _check_cuadriver_permissions(self, cua_driver: str) -> Optional[str]:
        """Use the daemon's authoritative TCC probe."""
        try:
            proc = await asyncio.create_subprocess_exec(
                cua_driver,
                "call",
                "check_permissions",
                json.dumps({"prompt": False}),
                "--raw",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=4.0)
        except asyncio.TimeoutError:
            return "CuaDriver permission health check timed out. Run `cua-driver serve`, then restart yume services."
        except Exception as e:  # noqa: BLE001
            return f"CuaDriver permission health check failed: {e}"

        out_text = stdout.decode("utf-8", errors="replace")
        err_text = stderr.decode("utf-8", errors="replace")
        detail = self._clean_output(err_text or out_text)
        lowered = detail.lower()
        if proc.returncode != 0:
            suffix = f": {detail[:240]}" if detail else ""
            return f"CuaDriver permission health check failed{suffix}"
        if "accessibility: not granted" in lowered or "accessibility permission not granted" in lowered:
            return (
                "macOS denied Accessibility access for CuaDriver.app. "
                "In System Settings -> Privacy & Security -> Accessibility, toggle CuaDriver.app off and on (or remove/re-add it), "
                "then restart CuaDriver with `open -n -g -a CuaDriver --args serve` and restart yume services."
            )
        if "screen recording: not granted" in lowered or "screen recording permission not granted" in lowered:
            return (
                "macOS denied Screen Recording for CuaDriver.app. "
                "Grant CuaDriver.app Screen Recording in System Settings, then restart CuaDriver and yume services."
            )
        return None

    @staticmethod
    def _clean_output(text: str) -> str:
        lines: list[str] = []
        for raw in text.splitlines():
            line = raw.strip()
            if not line:
                continue
            low = line.lower()
            if low.startswith("session id:") or low.startswith("session_id:"):
                continue
            if low.startswith("cost:") or low.startswith("tokens:"):
                continue
            lines.append(line)
        return "\n".join(lines).strip()

    @staticmethod
    def _clip_for_log(text: str, limit: int = 2000) -> str:
        redacted = re.sub(r"sk-ant-[A-Za-z0-9_\-]+", "sk-ant-[redacted]", text)
        redacted = re.sub(r"gsk_[A-Za-z0-9_\-]+", "gsk_[redacted]", redacted)
        redacted = re.sub(r"(?i)(api[_-]?key=)[^&\s]+", r"\1[redacted]", redacted)
        cleaned = redacted.replace("\x00", "")
        if len(cleaned) > limit:
            return cleaned[:limit] + "...[truncated]"
        return cleaned

    def _normalize(self, msg: dict, sequence: int) -> HermesEvent:
        kind = msg.get("type", "progress")
        ts = time.time()
        if kind == "needs_confirmation":
            return HermesEvent(
                kind="needs_confirmation",
                sequence=sequence,
                timestamp=ts,
                message=msg.get("prompt") or msg.get("message"),
                risk_level=msg.get("riskLevel") or msg.get("risk_level"),
                choices=tuple(msg.get("choices") or ()),
            )
        if kind == "result":
            return HermesEvent(
                kind="result",
                sequence=sequence,
                timestamp=ts,
                status=msg.get("status", "completed"),
                summary=msg.get("summary"),
                artifacts=tuple(msg.get("artifacts") or ()),
            )
        if kind == "error":
            return HermesEvent(
                kind="error",
                sequence=sequence,
                timestamp=ts,
                message=msg.get("message") or "hermes error",
            )
        return HermesEvent(
            kind="progress",
            sequence=sequence,
            timestamp=ts,
            message=msg.get("message"),
            last_action=msg.get("lastAction") or msg.get("last_action"),
        )


class MockHermesBridge(HermesBridge):
    """Timer-based simulator. Used when Hermes is not installed.

    Behavior:
      * Emits a sequence of progress events tied to the apps in the spec.
      * Optionally emits a ``needs_confirmation`` for any task whose
        instruction contains words like "save", "send", or "delete".
      * Settles into a ``result`` after a few seconds.
    """

    def __init__(self) -> None:
        self._cancelled: set[str] = set()
        self._confirm_queues: dict[str, asyncio.Queue[str]] = {}

    async def run(self, spec: HermesTaskSpec) -> AsyncIterator[HermesEvent]:
        self._confirm_queues[spec.task_id] = asyncio.Queue()
        seq = 0
        try:
            seq += 1
            yield HermesEvent(
                kind="progress",
                sequence=seq,
                timestamp=time.time(),
                message=f"opening {spec.allowed_apps[0] if spec.allowed_apps else 'the target app'}",
                last_action="computer_use.capture",
            )
            await self._sleep(spec.task_id, 1.2)
            if spec.task_id in self._cancelled:
                return

            seq += 1
            yield HermesEvent(
                kind="progress",
                sequence=seq,
                timestamp=time.time(),
                message="working on the request",
                last_action="computer_use.type",
            )
            await self._sleep(spec.task_id, 1.4)
            if spec.task_id in self._cancelled:
                return

            needs_confirm = any(w in spec.instruction.lower() for w in ("save", "send", "delete", "submit"))
            if needs_confirm:
                seq += 1
                yield HermesEvent(
                    kind="needs_confirmation",
                    sequence=seq,
                    timestamp=time.time(),
                    message="About to perform a state-changing action. Proceed?",
                    risk_level=spec.risk_level,
                    choices=("confirm", "cancel"),
                )
                try:
                    decision = await asyncio.wait_for(self._confirm_queues[spec.task_id].get(), timeout=60.0)
                except asyncio.TimeoutError:
                    decision = "cancel"
                if decision != "confirm":
                    seq += 1
                    yield HermesEvent(
                        kind="result",
                        sequence=seq,
                        timestamp=time.time(),
                        status="cancelled",
                        summary="user declined confirmation",
                    )
                    return

            await self._sleep(spec.task_id, 1.2)
            if spec.task_id in self._cancelled:
                return

            seq += 1
            yield HermesEvent(
                kind="result",
                sequence=seq,
                timestamp=time.time(),
                status="completed",
                summary=f"Finished: {spec.title}",
            )
        finally:
            self._confirm_queues.pop(spec.task_id, None)
            self._cancelled.discard(spec.task_id)

    async def cancel(self, task_id: str) -> None:
        self._cancelled.add(task_id)
        queue = self._confirm_queues.get(task_id)
        if queue is not None:
            try:
                queue.put_nowait("cancel")
            except asyncio.QueueFull:
                pass

    async def deliver_confirmation(self, task_id: str, decision: str) -> None:
        queue = self._confirm_queues.get(task_id)
        if queue is None:
            return
        await queue.put(decision)

    async def _sleep(self, task_id: str, seconds: float) -> None:
        end = asyncio.get_running_loop().time() + seconds
        while True:
            remaining = end - asyncio.get_running_loop().time()
            if remaining <= 0 or task_id in self._cancelled:
                return
            await asyncio.sleep(min(0.1, remaining))
