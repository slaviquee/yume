"""Tests for orchestrator tool routing and safety integration.

We don't call the real Claude API in tests — we stub the tool execution path
directly. This verifies the wiring between tool calls, safety, and the worker
manager.
"""
from __future__ import annotations

import asyncio

import pytest

from agent_service.config import AgentConfig
from agent_service.hermes_bridge import HermesBridge, HermesEvent, HermesTaskSpec
from agent_service.orchestrator import Orchestrator
from agent_service.worker_manager import WorkerManager

pytestmark = pytest.mark.asyncio


class StubBridge(HermesBridge):
    async def run(self, spec):
        yield HermesEvent(kind="result", sequence=1, timestamp=0, status="completed", summary="ok")
    async def cancel(self, task_id): pass
    async def deliver_confirmation(self, task_id, decision): pass


def _make(emit):
    cfg = AgentConfig(anthropic_api_key="fake")
    wm = WorkerManager(StubBridge(), emit, max_concurrent=2)
    o = Orchestrator(cfg, wm, emit, client=object())  # client unused for tool tests
    return o, wm


async def test_dispatch_worker_tool_starts_worker():
    events: list[dict] = []
    async def emit(p): events.append(p)
    o, wm = _make(emit)
    await o._tool_dispatch_worker({
        "title": "Draft checklist",
        "instruction": "Open TextEdit and write a list",
        "allowed_apps": ["TextEdit"],
        "risk_level": "low",
    })
    # Worker.started should have been emitted.
    assert any(e.get("type") == "worker.started" for e in events)


async def test_dispatch_worker_blocked_by_safety():
    events: list[dict] = []
    async def emit(p): events.append(p)
    o, wm = _make(emit)
    await o._tool_dispatch_worker({
        "title": "Login flow",
        "instruction": "Type my password into 1Password and submit",
        "allowed_apps": ["1Password"],
        "risk_level": "low",
    })
    assert any(e.get("code") == "blocked_by_safety" for e in events)


async def test_dispatch_worker_bumps_risk_on_send_email():
    events: list[dict] = []
    async def emit(p): events.append(p)
    o, wm = _make(emit)
    await o._tool_dispatch_worker({
        "title": "Email Alice",
        "instruction": "Send an email to Alice about the deadline",
        "allowed_apps": ["Mail"],
        "risk_level": "low",  # model under-claimed risk
    })
    started = [e for e in events if e.get("type") == "worker.started"][0]
    assert started["riskLevel"] in ("medium", "high")


async def test_cancel_all():
    events: list[dict] = []
    async def emit(p): events.append(p)
    o, wm = _make(emit)
    await o._tool_dispatch_worker({
        "title": "t1", "instruction": "draft", "allowed_apps": ["TextEdit"], "risk_level": "low",
    })
    await asyncio.sleep(0.05)
    await o._tool_cancel_worker({"task_id": "all"})
    # Both result events should fire.
    for _ in range(20):
        await asyncio.sleep(0.05)
        if any(e.get("type") == "worker.result" for e in events):
            break
    results = [e for e in events if e.get("type") == "worker.result"]
    assert results
