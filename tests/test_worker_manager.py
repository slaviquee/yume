"""Worker manager + lifecycle tests. See docs/spec.md section 11.4."""
from __future__ import annotations

import asyncio
from typing import AsyncIterator

import pytest

from agent_service.hermes_bridge import HermesBridge, HermesEvent, HermesTaskSpec
from agent_service.safety_policy import SafetyDecision
from agent_service.worker_manager import WorkerManager

pytestmark = pytest.mark.asyncio


class ScriptedBridge(HermesBridge):
    """Test bridge that emits a scripted event list per task and respects cancel."""

    def __init__(self, script: list[HermesEvent], confirm_responses: dict[str, str] | None = None):
        self._script = script
        self._confirm_responses = confirm_responses or {}
        self._cancelled: set[str] = set()
        self._confirm_queues: dict[str, asyncio.Queue[str]] = {}

    async def run(self, spec: HermesTaskSpec) -> AsyncIterator[HermesEvent]:
        self._confirm_queues[spec.task_id] = asyncio.Queue()
        for ev in self._script:
            if spec.task_id in self._cancelled:
                return
            yield ev
            if ev.kind == "needs_confirmation":
                response = await self._confirm_queues[spec.task_id].get()
                if response != "confirm":
                    return
        self._confirm_queues.pop(spec.task_id, None)

    async def cancel(self, task_id: str) -> None:
        self._cancelled.add(task_id)
        q = self._confirm_queues.get(task_id)
        if q is not None:
            try:
                q.put_nowait("cancel")
            except asyncio.QueueFull:
                pass

    async def deliver_confirmation(self, task_id: str, decision: str) -> None:
        q = self._confirm_queues.get(task_id)
        if q is not None:
            await q.put(decision)


def _decision(risk="low", require_confirm=False, blocked=False) -> SafetyDecision:
    return SafetyDecision(
        risk="blocked" if blocked else risk,
        require_confirmation=require_confirm,
        reasons=(),
    )


async def _collect_events(events_list: list[dict], coro):
    """Helper: capture emitted events."""
    async def emit(payload):
        events_list.append(payload)
    return emit


async def test_completed_lifecycle():
    bridge = ScriptedBridge([
        HermesEvent(kind="progress", sequence=1, timestamp=0, message="opening"),
        HermesEvent(kind="progress", sequence=2, timestamp=0, message="typing"),
        HermesEvent(kind="result", sequence=3, timestamp=0, status="completed", summary="done"),
    ])
    events: list[dict] = []
    async def emit(p): events.append(p)
    wm = WorkerManager(bridge, emit, max_concurrent=2)
    task_id = await wm.start_worker(
        title="t", instruction="open TextEdit",
        allowed_apps=("TextEdit",), risk_level="low",
        safety_decision=_decision(),
    )
    assert task_id is not None
    # Drain by waiting for the result event.
    for _ in range(50):
        await asyncio.sleep(0.05)
        if any(e.get("type") == "worker.result" for e in events):
            break
    result = next(e for e in events if e.get("type") == "worker.result")
    assert result["status"] == "completed"
    assert result["summary"] == "done"
    # Worker is in terminal state and not in active snapshot.
    assert wm.public_snapshot() == []


async def test_cancellation_is_monotonic():
    """Once cancelled, the worker reaches terminal=cancelled, not the
    completion that the bridge would have otherwise emitted."""

    class SlowBridge(HermesBridge):
        async def run(self, spec):
            yield HermesEvent(kind="progress", sequence=1, timestamp=0, message="working")
            # Wait long enough for the test to cancel us first.
            await asyncio.sleep(5.0)
            yield HermesEvent(kind="result", sequence=2, timestamp=0, status="completed", summary="should-not-emit")
        async def cancel(self, task_id): pass
        async def deliver_confirmation(self, task_id, decision): pass

    events: list[dict] = []
    async def emit(p): events.append(p)
    wm = WorkerManager(SlowBridge(), emit, max_concurrent=2)
    task_id = await wm.start_worker(
        title="t", instruction="x", allowed_apps=("TextEdit",),
        risk_level="low", safety_decision=_decision(),
    )
    await asyncio.sleep(0.1)
    cancelled = await wm.cancel(task_id, reason="user_cancelled")
    assert cancelled is True
    result = next(e for e in events if e.get("type") == "worker.result")
    assert result["status"] == "cancelled"
    # The completion event from the bridge must not have fired.
    completed = [e for e in events if e.get("type") == "worker.result" and e.get("status") == "completed"]
    assert completed == []


async def test_blocked_safety_prevents_dispatch():
    bridge = ScriptedBridge([HermesEvent(kind="result", sequence=1, timestamp=0, status="completed")])
    events: list[dict] = []
    async def emit(p): events.append(p)
    wm = WorkerManager(bridge, emit, max_concurrent=2)
    task_id = await wm.start_worker(
        title="t", instruction="type my password",
        allowed_apps=("Safari",), risk_level="low",
        safety_decision=_decision(blocked=True),
    )
    assert task_id is None
    assert any(e.get("code") == "blocked_by_safety" for e in events)


async def test_worker_limit_enforced():
    # Slow scripted bridge so the first worker is still active.
    slow_events = [
        HermesEvent(kind="progress", sequence=1, timestamp=0, message="working"),
    ]

    class SlowBridge(HermesBridge):
        async def run(self, spec):
            yield slow_events[0]
            await asyncio.sleep(2.0)
        async def cancel(self, task_id): pass
        async def deliver_confirmation(self, task_id, decision): pass

    events: list[dict] = []
    async def emit(p): events.append(p)
    wm = WorkerManager(SlowBridge(), emit, max_concurrent=1)
    t1 = await wm.start_worker("a", "do x", ("TextEdit",), "low", _decision())
    assert t1 is not None
    await asyncio.sleep(0.05)
    t2 = await wm.start_worker("b", "do y", ("TextEdit",), "low", _decision())
    assert t2 is None
    assert any(e.get("code") == "worker_limit" for e in events)
    await wm.cancel(t1, reason="cleanup")


async def test_app_writer_lock():
    """Two writers cannot operate the same app concurrently."""
    class IdleBridge(HermesBridge):
        async def run(self, spec):
            yield HermesEvent(kind="progress", sequence=1, timestamp=0, message="x")
            await asyncio.sleep(2.0)
        async def cancel(self, task_id): pass
        async def deliver_confirmation(self, task_id, decision): pass

    events: list[dict] = []
    async def emit(p): events.append(p)
    wm = WorkerManager(IdleBridge(), emit, max_concurrent=4)
    t1 = await wm.start_worker("a", "type x", ("Safari",), "low", _decision())
    await asyncio.sleep(0.05)
    t2 = await wm.start_worker("b", "type y", ("Safari",), "low", _decision())
    assert t2 is None
    assert any(e.get("code") == "app_locked" for e in events)
    # Read-only worker can still run on the locked app.
    t3 = await wm.start_worker("c", "summarize", ("Safari",), "low", _decision(), read_only=True)
    assert t3 is not None
    await wm.cancel(t1, reason="cleanup")
    await wm.cancel(t3, reason="cleanup")


async def test_confirmation_blocks_until_response():
    bridge = ScriptedBridge([
        HermesEvent(kind="progress", sequence=1, timestamp=0, message="step1"),
        HermesEvent(kind="needs_confirmation", sequence=2, timestamp=0, message="Save?",
                    risk_level="medium", choices=("confirm", "cancel")),
        HermesEvent(kind="result", sequence=3, timestamp=0, status="completed", summary="saved"),
    ])
    events: list[dict] = []
    async def emit(p): events.append(p)
    wm = WorkerManager(bridge, emit, max_concurrent=2)
    task_id = await wm.start_worker("a", "save", ("TextEdit",), "medium", _decision(risk="medium", require_confirm=True))
    # Wait for the confirmation event.
    for _ in range(40):
        await asyncio.sleep(0.05)
        if any(e.get("type") == "worker.needs_confirmation" for e in events):
            break
    confirm_event = next(e for e in events if e.get("type") == "worker.needs_confirmation")
    confirmation_id = confirm_event["confirmationId"]
    # Worker must be in waiting state, not running.
    progress_events = [e for e in events if e.get("type") == "worker.progress"]
    assert any(p["state"] == "waiting_for_user_confirmation" for p in progress_events)
    # Approve and let it finish.
    await wm.confirm(confirmation_id, "confirm")
    for _ in range(40):
        await asyncio.sleep(0.05)
        if any(e.get("type") == "worker.result" for e in events):
            break
    result = next(e for e in events if e.get("type") == "worker.result")
    assert result["status"] == "completed"
