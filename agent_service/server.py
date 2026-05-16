"""WebSocket server that exposes the orchestrator + worker manager to the
Swift app. Listens on 127.0.0.1 only.

Protocol:

  App → server
    {"type": "turn.submit", "turnId": "...", "text": "..."}
    {"type": "confirmation.response", "confirmationId": "...", "decision": "confirm"|"cancel"}
    {"type": "worker.cancel", "taskId": "..."}
    {"type": "worker.cancel_all"}
    {"type": "workers.list"}
    {"type": "stop"}
    {"type": "ping"}

  Server → app
    {"type": "agent.thinking", "turnId": "...", "active": true|false}
    {"type": "agent.say_start", "turnId": "...", "utteranceId": "...", "interruptible": true}
    {"type": "agent.say_chunk", "utteranceId": "...", "text": "..."}
    {"type": "agent.say_end", "utteranceId": "...", "interrupted"?: bool}
    {"type": "worker.started", ...}
    {"type": "worker.progress", ...}
    {"type": "worker.needs_confirmation", ...}
    {"type": "worker.result", ...}
    {"type": "workers.snapshot", "workers": [...]}
    {"type": "error", "code": "...", "message": "..."}
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Optional

import websockets
from websockets.server import WebSocketServerProtocol

from .config import AgentConfig, load_config
from .hermes_bridge import make_bridge
from .orchestrator import Orchestrator
from .worker_manager import WorkerManager

log = logging.getLogger(__name__)


class AgentServer:
    def __init__(self, host: str = "127.0.0.1", port: int = 7422, cfg: Optional[AgentConfig] = None) -> None:
        self.host = host
        self.port = port
        self.cfg = cfg or load_config()

    async def serve_forever(self) -> None:
        log.info("agent_service listening on ws://%s:%d", self.host, self.port)
        async with websockets.serve(self._on_connect, self.host, self.port, max_size=2**24):
            await asyncio.Future()

    async def _on_connect(self, ws: WebSocketServerProtocol) -> None:
        log.info("client connected: %s", ws.remote_address)
        session = AgentSession(self.cfg, ws)
        try:
            await session.run()
        except websockets.ConnectionClosed:
            log.info("client connection closed: %s", ws.remote_address)
        except Exception:  # noqa: BLE001
            log.exception("session crashed")
        finally:
            await session.shutdown()
            log.info("client disconnected: %s", ws.remote_address)


class AgentSession:
    def __init__(self, cfg: AgentConfig, ws: WebSocketServerProtocol) -> None:
        self.cfg = cfg
        self.ws = ws
        self._send_lock = asyncio.Lock()
        self._bridge = make_bridge(mode=cfg.hermes_mode, hermes_bin=cfg.hermes_bin)
        self._workers = WorkerManager(self._bridge, self._send, max_concurrent=cfg.max_concurrent_workers)
        self._orchestrator = Orchestrator(cfg, self._workers, self._send)

    async def run(self) -> None:
        async for raw in self.ws:
            if isinstance(raw, bytes):
                continue
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await self._send({"type": "error", "code": "bad_json", "message": "non-JSON message"})
                continue
            await self._dispatch(msg)

    async def shutdown(self) -> None:
        await self._workers.cancel_all(reason="session_closed")
        await self._orchestrator.interrupt()

    async def _dispatch(self, msg: dict) -> None:
        kind = msg.get("type", "")
        try:
            if kind == "turn.submit":
                turn_id = msg.get("turnId") or "turn_unknown"
                text = (msg.get("text") or "").strip()
                if text:
                    await self._orchestrator.submit_turn(turn_id, text)
            elif kind == "confirmation.response":
                await self._workers.confirm(
                    msg.get("confirmationId") or "",
                    msg.get("decision") or "cancel",
                )
            elif kind == "worker.cancel":
                await self._workers.cancel(msg.get("taskId") or "", reason=msg.get("reason", "user_requested"))
            elif kind == "worker.cancel_all":
                await self._workers.cancel_all(reason=msg.get("reason", "user_requested"))
            elif kind == "workers.list":
                await self._send({"type": "workers.snapshot", "workers": self._workers.public_snapshot()})
            elif kind == "stop":
                await self._orchestrator.interrupt()
            elif kind == "ping":
                await self._send({"type": "pong"})
            else:
                await self._send({"type": "error", "code": "unknown_type", "message": f"unknown type {kind}"})
        except Exception as e:  # noqa: BLE001
            log.exception("dispatch failed for %s", kind)
            await self._send({"type": "error", "code": "internal", "message": str(e)})

    async def _send(self, payload: dict) -> None:
        async with self._send_lock:
            try:
                await self.ws.send(json.dumps(payload))
            except websockets.ConnectionClosed:
                pass
