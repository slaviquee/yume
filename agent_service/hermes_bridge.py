"""Hermes Agent bridge.

The bridge owns Hermes process lifecycle, stdin/stdout handling, soft
cancellation, and event normalization. UI code never touches Hermes output
directly — it consumes the normalized `HermesEvent` stream below.

Two implementations:

* `RealHermesBridge` — spawns the `hermes` CLI in `-t computer_use` mode with
  the worker instruction. Output is parsed as newline-delimited JSON.
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
        self._processes: dict[str, asyncio.subprocess.Process] = {}
        self._confirm_queues: dict[str, asyncio.Queue[str]] = {}

    async def run(self, spec: HermesTaskSpec) -> AsyncIterator[HermesEvent]:
        argv = [
            self.hermes_bin,
            "-t",
            "computer_use",
            "run",
            "--json",
            "--instruction",
            spec.instruction,
            "--capture-mode",
            spec.capture_mode,
        ]
        for app in spec.allowed_apps:
            argv.extend(["--app", app])

        log.info("spawning hermes: task=%s apps=%s", spec.task_id, spec.allowed_apps)
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._processes[spec.task_id] = proc
        self._confirm_queues[spec.task_id] = asyncio.Queue()

        seq = 0
        try:
            assert proc.stdout is not None
            while True:
                try:
                    line = await asyncio.wait_for(proc.stdout.readline(), timeout=spec.timeout_sec)
                except asyncio.TimeoutError:
                    yield HermesEvent(kind="error", sequence=seq, timestamp=time.time(), message="timeout")
                    break
                if not line:
                    rc = await proc.wait()
                    if rc != 0:
                        stderr = (await proc.stderr.read()).decode("utf-8", errors="replace") if proc.stderr else ""
                        yield HermesEvent(
                            kind="error",
                            sequence=seq,
                            timestamp=time.time(),
                            message=f"hermes exited rc={rc}: {stderr.strip()[:300]}",
                        )
                    break
                try:
                    msg = json.loads(line.decode("utf-8", errors="replace"))
                except json.JSONDecodeError:
                    log.debug("hermes non-json line: %r", line[:200])
                    continue
                seq += 1
                ev = self._normalize(msg, seq)
                yield ev
                if ev.kind in ("result", "error"):
                    break
                if ev.kind == "needs_confirmation":
                    decision = await self._confirm_queues[spec.task_id].get()
                    if proc.stdin is not None and not proc.stdin.is_closing():
                        proc.stdin.write(
                            (json.dumps({"type": "confirmation", "decision": decision}) + "\n").encode()
                        )
                        await proc.stdin.drain()
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
