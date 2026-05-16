"""Gradium STT realtime stream.

Implements the segment-assembly + flush semantics described in docs/spec.md
section 8.1. Provider `text` events are treated as segment text; final user
turns are assembled from those segments and finalized on `flushed` (for PTT)
or on VAD step events (for continuous mode).
"""
from __future__ import annotations

import asyncio
import base64
import dataclasses
import json
import logging
import time
import uuid
from typing import AsyncIterator, Awaitable, Callable, Optional

from websockets.client import WebSocketClientProtocol

from .config import GradiumConfig
from .gradium_client import open_gradium_ws, safe_close

log = logging.getLogger(__name__)


@dataclasses.dataclass
class SttEvent:
    """Normalized event published by the STT stream to the rest of the app."""

    type: str  # "partial" | "final" | "turn_end" | "error"
    turn_id: str
    text: str = ""
    finalized_by: Optional[str] = None  # "flushed" | "vad" | "eos"
    started_at: Optional[str] = None
    ended_at: Optional[str] = None
    message: Optional[str] = None


class SttStream:
    """A single STT session for one push-to-talk press or one continuous run.

    The stream multiplexes provider segment events into app-level events. Final
    user turns are emitted on:
      - `flushed` after `send_flush()` (push-to-talk release)
      - VAD `step` indicating turn end (continuous mode)
      - End of stream
    """

    def __init__(
        self,
        cfg: GradiumConfig,
        mode: str,
        turn_id: Optional[str] = None,
        emit: Optional[Callable[[SttEvent], Awaitable[None]]] = None,
    ) -> None:
        if mode not in ("push_to_talk", "continuous"):
            raise ValueError(f"unknown mode {mode!r}")
        self.cfg = cfg
        self.mode = mode
        self.turn_id = turn_id or f"turn_{uuid.uuid4().hex[:12]}"
        self._emit = emit or (lambda _e: asyncio.sleep(0))
        self._ws: Optional[WebSocketClientProtocol] = None
        self._reader_task: Optional[asyncio.Task[None]] = None
        self._segments: list[str] = []
        self._segment_open: bool = False
        self._started_at: Optional[str] = None
        self._closed: bool = False
        self._flush_id: int = 0

    @property
    def is_open(self) -> bool:
        return self._ws is not None and not self._closed

    async def open(self) -> None:
        self._ws = await open_gradium_ws(self.cfg.stt_url, self.cfg.api_key)
        self._started_at = _now()
        await self._ws.send(
            json.dumps(
                {
                    "type": "setup",
                    "model_name": self.cfg.stt_model,
                    "input_format": "pcm",
                }
            )
        )
        await self._wait_until_ready()
        self._reader_task = asyncio.create_task(self._reader())

    async def send_audio(self, pcm: bytes) -> None:
        if not self.is_open:
            raise RuntimeError("stt stream is not open")
        await self._ws.send(  # type: ignore[union-attr]
            json.dumps({"type": "audio", "audio": base64.b64encode(pcm).decode("ascii")})
        )

    async def send_flush(self) -> None:
        """End-of-input on push-to-talk. Waits for matching `flushed` before
        emitting the final transcript."""
        if not self.is_open:
            return
        self._flush_id += 1
        await self._ws.send(json.dumps({"type": "flush", "flush_id": self._flush_id}))  # type: ignore[union-attr]

    async def send_eos(self) -> None:
        if not self.is_open:
            return
        await self._ws.send(json.dumps({"type": "end_of_stream"}))  # type: ignore[union-attr]

    async def close(self) -> None:
        self._closed = True
        if self._reader_task:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        await safe_close(self._ws)
        self._ws = None

    async def _reader(self) -> None:
        assert self._ws is not None
        try:
            async for raw in self._ws:
                if isinstance(raw, bytes):
                    continue
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    log.warning("non-JSON STT frame ignored")
                    continue
                await self._handle_message(msg)
        except Exception as e:  # noqa: BLE001
            await self._emit(
                SttEvent(type="error", turn_id=self.turn_id, message=str(e))
            )

    async def _handle_message(self, msg: dict) -> None:
        kind = msg.get("type") or msg.get("event")
        if kind == "text":
            # Partial segment text. Append and emit a partial.
            text = msg.get("text", "")
            if text:
                if self._segment_open:
                    # Replace last partial segment with the newer version.
                    self._segments[-1] = text
                else:
                    self._segments.append(text)
                    self._segment_open = True
                await self._emit(
                    SttEvent(
                        type="partial",
                        turn_id=self.turn_id,
                        text=self._join(),
                        started_at=self._started_at,
                    )
                )
        elif kind == "end_text":
            # Segment finalized. Lock it in; next text begins a new segment.
            self._segment_open = False
        elif kind == "step":
            # VAD step event. In continuous mode, a low inactivity probability
            # signals turn end. Spec default: prob > 0.5 over a 2s horizon.
            prob = _vad_inactivity_probability(msg)
            if self.mode == "continuous" and prob > 0.5 and self._segments:
                await self._emit_final("vad")
        elif kind == "flushed":
            if self.mode == "push_to_talk":
                await self._emit_final("flushed")
        elif kind == "end_of_stream":
            if self._segments:
                await self._emit_final("eos")
        elif kind == "error":
            await self._emit(
                SttEvent(
                    type="error",
                    turn_id=self.turn_id,
                    message=msg.get("message", "stt error"),
                )
            )
        else:
            log.debug("unknown STT message kind: %s", kind)

    async def _emit_final(self, finalized_by: str) -> None:
        text = self._join().strip()
        if not text:
            return
        await self._emit(
            SttEvent(
                type="final",
                turn_id=self.turn_id,
                text=text,
                finalized_by=finalized_by,
                started_at=self._started_at,
                ended_at=_now(),
            )
        )
        self._segments.clear()
        self._segment_open = False
        # For continuous mode we keep the stream open and start a new turn.
        if self.mode == "continuous":
            self.turn_id = f"turn_{uuid.uuid4().hex[:12]}"
            self._started_at = _now()

    def _join(self) -> str:
        # Provider segments are joined with single spaces; whitespace at the
        # boundaries is normalized.
        return " ".join(s.strip() for s in self._segments if s.strip())

    async def _wait_until_ready(self) -> None:
        assert self._ws is not None
        while True:
            raw = await asyncio.wait_for(self._ws.recv(), timeout=10)
            if isinstance(raw, bytes):
                continue
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            kind = msg.get("type") or msg.get("event")
            if kind == "ready":
                return
            if kind == "error":
                raise RuntimeError(msg.get("message", "stt setup failed"))


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _vad_inactivity_probability(msg: dict) -> float:
    vad = msg.get("vad")
    if isinstance(vad, list) and vad:
        for item in vad:
            if isinstance(item, dict) and item.get("horizon_s") == 2.0:
                return float(item.get("inactivity_prob") or 0.0)
        last = vad[-1]
        if isinstance(last, dict):
            return float(last.get("inactivity_prob") or 0.0)
    return float(msg.get("inactivity_probability") or msg.get("p") or 0.0)


async def iter_events(stream: SttStream) -> AsyncIterator[SttEvent]:
    """Helper for tests: drains events emitted into a queue."""
    queue: asyncio.Queue[SttEvent] = asyncio.Queue()

    async def emit(e: SttEvent) -> None:
        await queue.put(e)

    stream._emit = emit  # type: ignore[assignment]
    while True:
        yield await queue.get()
