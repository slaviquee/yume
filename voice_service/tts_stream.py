"""Gradium TTS streaming.

Implements docs/spec.md section 8.2 rules:
  * Chunk incremental text on whitespace only — never inside a word.
  * Never put punctuation in its own chunk; punctuation stays attached to the
    preceding word.
  * Emit `<flush>` when the caller wants the model to flush its remaining audio.
  * Decode PCM frames (48 kHz, 16-bit signed mono) and forward to the consumer.
"""
from __future__ import annotations

import asyncio
import base64
import dataclasses
import json
import logging
import uuid
from typing import Awaitable, Callable, Optional

from websockets.client import WebSocketClientProtocol

from .config import GradiumConfig
from .gradium_client import open_gradium_ws, safe_close

log = logging.getLogger(__name__)


@dataclasses.dataclass
class TtsEvent:
    type: str  # "audio" | "done" | "error"
    utterance_id: str
    pcm: bytes = b""
    message: Optional[str] = None


class TtsStream:
    """A single TTS utterance.

    Use `append(text)` to feed incremental text (e.g. token-by-token from the
    LLM). Whitespace-only splitting is enforced; we buffer trailing
    non-whitespace until the next whitespace arrives. Call `flush_text()` to
    push remaining buffered text + a `<flush>` marker. Call `close()` to
    finalize.
    """

    def __init__(
        self,
        cfg: GradiumConfig,
        utterance_id: Optional[str] = None,
        voice_id: Optional[str] = None,
        emit: Optional[Callable[[TtsEvent], Awaitable[None]]] = None,
    ) -> None:
        self.cfg = cfg
        self.utterance_id = utterance_id or f"utt_{uuid.uuid4().hex[:12]}"
        self.voice_id = voice_id or cfg.tts_voice_id
        self._emit = emit or (lambda _e: asyncio.sleep(0))
        self._ws: Optional[WebSocketClientProtocol] = None
        self._reader_task: Optional[asyncio.Task[None]] = None
        self._buffer: str = ""
        self._closed: bool = False
        self._cancelled: bool = False

    async def open(self) -> None:
        self._ws = await open_gradium_ws(self.cfg.tts_url, self.cfg.api_key)
        await self._ws.send(
            json.dumps(
                {
                    "type": "setup",
                    "model_name": self.cfg.tts_model,
                    "voice_id": self.voice_id,
                    "output_format": "pcm",
                }
            )
        )
        await self._wait_until_ready()
        self._reader_task = asyncio.create_task(self._reader())

    async def append(self, text: str) -> None:
        """Feed incremental text. Splits on whitespace boundaries only."""
        if self._closed or not text:
            return
        self._buffer += text
        # Send everything up to the last whitespace; keep the trailing partial
        # word in the buffer.
        last_ws = max(self._buffer.rfind(" "), self._buffer.rfind("\n"), self._buffer.rfind("\t"))
        if last_ws >= 0:
            send = self._buffer[: last_ws + 1]
            self._buffer = self._buffer[last_ws + 1 :]
            if send.strip():
                await self._send_text(send)

    async def flush_text(self) -> None:
        """Send any buffered tail text and emit a <flush> marker."""
        if self._closed:
            return
        if self._buffer.strip():
            await self._send_text(self._buffer)
            self._buffer = ""
        if self._ws is not None:
            await self._ws.send(json.dumps({"type": "text", "text": "<flush>"}))
            await self._ws.send(json.dumps({"type": "end_of_stream"}))

    async def cancel(self) -> None:
        """Stop pending audio output. Used for barge-in."""
        self._cancelled = True
        if self._ws is not None:
            try:
                await self._ws.send(json.dumps({"type": "cancel"}))
            except Exception:  # noqa: BLE001
                pass
        await self.close()

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

    async def _send_text(self, text: str) -> None:
        if self._ws is None:
            return
        await self._ws.send(json.dumps({"type": "text", "text": text}))

    async def _reader(self) -> None:
        assert self._ws is not None
        try:
            async for raw in self._ws:
                if self._cancelled:
                    break
                if isinstance(raw, bytes):
                    await self._emit(TtsEvent(type="audio", utterance_id=self.utterance_id, pcm=raw))
                    continue
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                kind = msg.get("type") or msg.get("event")
                if kind == "audio":
                    pcm = msg.get("audio", "")
                    if isinstance(pcm, str):
                        await self._emit(
                            TtsEvent(
                                type="audio",
                                utterance_id=self.utterance_id,
                                pcm=base64.b64decode(pcm),
                            )
                        )
                elif kind in ("end_of_stream", "done"):
                    await self._emit(
                        TtsEvent(type="done", utterance_id=self.utterance_id)
                    )
                elif kind == "error":
                    await self._emit(
                        TtsEvent(
                            type="error",
                            utterance_id=self.utterance_id,
                            message=msg.get("message", "tts error"),
                        )
                    )
        except Exception as e:  # noqa: BLE001
            await self._emit(
                TtsEvent(type="error", utterance_id=self.utterance_id, message=str(e))
            )

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
                raise RuntimeError(msg.get("message", "tts setup failed"))


def split_for_tts(text: str) -> list[str]:
    """Splits text on whitespace runs. Returned chunks include their trailing
    whitespace so concatenating reproduces the input. Used by tests and by
    callers that want offline chunking.

    Rules from docs/spec.md section 8.2:
      * Split only on whitespace.
      * Never split inside a word.
      * Punctuation stays attached to its preceding word.
    """
    chunks: list[str] = []
    if not text:
        return chunks
    current = []
    in_ws = text[0].isspace()
    for ch in text:
        if ch.isspace() == in_ws:
            current.append(ch)
        else:
            chunks.append("".join(current))
            current = [ch]
            in_ws = ch.isspace()
    if current:
        chunks.append("".join(current))
    # Merge each word with its trailing whitespace so we always send word + space.
    merged: list[str] = []
    i = 0
    while i < len(chunks):
        if i + 1 < len(chunks) and not chunks[i].isspace() and chunks[i + 1].isspace():
            merged.append(chunks[i] + chunks[i + 1])
            i += 2
        else:
            merged.append(chunks[i])
            i += 1
    return merged
