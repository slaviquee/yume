"""Localhost WebSocket server bridging the Swift app to Gradium STT/TTS.

The server listens on 127.0.0.1:YUME_VOICE_PORT only. There is exactly one
expected client at a time (the Mac app), but additional connections are
allowed for debug tools.

Protocol: JSON messages defined in voice_service.protocol. Audio is sent
base64-encoded as PCM s16le.
"""
from __future__ import annotations

import asyncio
import array
import base64
import json
import logging
import math
import sys
from typing import Any, Optional

import websockets
from websockets.server import WebSocketServerProtocol

from .config import GradiumConfig, load_config
from .stt_stream import SttEvent, SttStream
from .tts_stream import TtsEvent, TtsStream

log = logging.getLogger(__name__)


class VoiceServer:
    def __init__(self, host: str = "127.0.0.1", port: int = 7421, cfg: Optional[GradiumConfig] = None) -> None:
        self.host = host
        self.port = port
        self.cfg = cfg or load_config()

    async def serve_forever(self) -> None:
        log.info("voice_service listening on ws://%s:%d", self.host, self.port)
        async with websockets.serve(self._on_connect, self.host, self.port, max_size=2**24):
            await asyncio.Future()

    async def _on_connect(self, ws: WebSocketServerProtocol) -> None:
        log.info("client connected: %s", ws.remote_address)
        session = VoiceSession(self.cfg, ws)
        try:
            await session.run()
        except Exception:  # noqa: BLE001
            log.exception("session error")
        finally:
            await session.shutdown()
            log.info("client disconnected: %s", ws.remote_address)


class VoiceSession:
    """One Swift-app connection. Manages one active STT stream and one or more
    TTS utterances (typically only the most recent — older ones are cancelled
    on barge-in).
    """

    def __init__(self, cfg: GradiumConfig, ws: WebSocketServerProtocol) -> None:
        self.cfg = cfg
        self.ws = ws
        self._send_lock = asyncio.Lock()
        self._stt: Optional[SttStream] = None
        self._tts: dict[str, TtsStream] = {}
        self._stt_audio_frames = 0
        self._stt_audio_bytes = 0

    async def run(self) -> None:
        async for raw in self.ws:
            if isinstance(raw, bytes):
                # Binary frames not used in the app-facing protocol.
                continue
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await self._send({"type": "error", "code": "bad_json", "message": "non-JSON message"})
                continue
            await self._dispatch(msg)

    async def shutdown(self) -> None:
        if self._stt is not None:
            await self._stt.close()
            self._stt = None
        for utt_id, stream in list(self._tts.items()):
            await stream.close()
        self._tts.clear()

    async def _dispatch(self, msg: dict[str, Any]) -> None:
        kind = msg.get("type", "")
        try:
            if kind == "stt.start":
                await self._stt_start(msg)
            elif kind == "stt.audio":
                await self._stt_audio(msg)
            elif kind == "stt.flush":
                await self._stt_flush(msg)
            elif kind == "stt.stop":
                await self._stt_stop(msg)
            elif kind == "tts.speak":
                await self._tts_speak(msg)
            elif kind == "tts.append":
                await self._tts_append(msg)
            elif kind == "tts.stop":
                await self._tts_stop(msg)
            elif kind == "ping":
                await self._send({"type": "pong"})
            else:
                await self._send({"type": "error", "code": "unknown_type", "message": f"unknown type {kind}"})
        except Exception as e:  # noqa: BLE001
            log.exception("dispatch failed for %s", kind)
            await self._send({"type": "error", "code": "internal", "message": str(e)})

    async def _stt_start(self, msg: dict) -> None:
        if self._stt is not None:
            await self._stt.close()
        self._stt_audio_frames = 0
        self._stt_audio_bytes = 0
        turn_id = msg.get("turnId") or "turn_unknown"
        mode = msg.get("mode") or "push_to_talk"
        log.info("stt start turn=%s mode=%s", turn_id, mode)
        self._stt = SttStream(self.cfg, mode=mode, turn_id=turn_id, emit=self._on_stt_event)
        await self._stt.open()

    async def _stt_audio(self, msg: dict) -> None:
        if self._stt is None or not self._stt.is_open:
            return
        pcm = base64.b64decode(msg.get("pcm_b64", ""))
        self._log_audio_stats(pcm)
        await self._stt.send_audio(pcm)

    async def _stt_flush(self, _msg: dict) -> None:
        if self._stt is None:
            return
        log.info("stt flush frames=%d bytes=%d", self._stt_audio_frames, self._stt_audio_bytes)
        await self._stt.send_flush()

    async def _stt_stop(self, _msg: dict) -> None:
        if self._stt is None:
            return
        log.info("stt stop frames=%d bytes=%d", self._stt_audio_frames, self._stt_audio_bytes)
        await self._stt.send_eos()
        await self._stt.close()
        self._stt = None

    async def _tts_speak(self, msg: dict) -> None:
        utt_id = msg.get("utteranceId") or "utt_unknown"
        if utt_id in self._tts:
            await self._tts[utt_id].close()
        stream = TtsStream(self.cfg, utterance_id=utt_id, voice_id=msg.get("voiceId"), emit=self._on_tts_event)
        self._tts[utt_id] = stream
        await stream.open()
        text = msg.get("text") or ""
        if text:
            await stream.append(text)

    async def _tts_append(self, msg: dict) -> None:
        utt_id = msg.get("utteranceId") or ""
        stream = self._tts.get(utt_id)
        if stream is None:
            await self._send({"type": "error", "code": "no_utterance", "message": utt_id, "utteranceId": utt_id})
            return
        text = msg.get("text") or ""
        if text:
            await stream.append(text)
        if msg.get("flush"):
            await stream.flush_text()

    async def _tts_stop(self, msg: dict) -> None:
        utt_id = msg.get("utteranceId") or ""
        stream = self._tts.pop(utt_id, None)
        if stream is None:
            return
        await stream.cancel()

    async def _on_stt_event(self, ev: SttEvent) -> None:
        if ev.type == "error":
            log.warning("stt error turn=%s message=%s", ev.turn_id, ev.message or "")
            await self._send({"type": "error", "code": "stt", "message": ev.message or "", "turnId": ev.turn_id})
            return
        if ev.type in ("partial", "final"):
            level = logging.INFO if ev.type == "final" else logging.DEBUG
            log.log(level, "stt %s turn=%s text=%r", ev.type, ev.turn_id, ev.text)
            await self._send(
                {
                    "type": "stt.transcript",
                    "turnId": ev.turn_id,
                    "text": ev.text,
                    "isFinal": ev.type == "final",
                    "finalizedBy": ev.finalized_by,
                    "startedAt": ev.started_at,
                    "endedAt": ev.ended_at,
                }
            )

    async def _on_tts_event(self, ev: TtsEvent) -> None:
        if ev.type == "audio":
            await self._send(
                {
                    "type": "tts.audio",
                    "utteranceId": ev.utterance_id,
                    "pcm_b64": base64.b64encode(ev.pcm).decode("ascii"),
                }
            )
        elif ev.type == "done":
            await self._send({"type": "tts.done", "utteranceId": ev.utterance_id})
        elif ev.type == "error":
            await self._send({"type": "error", "code": "tts", "message": ev.message or "", "utteranceId": ev.utterance_id})

    async def _send(self, payload: dict) -> None:
        async with self._send_lock:
            await self.ws.send(json.dumps(payload))

    def _log_audio_stats(self, pcm: bytes) -> None:
        self._stt_audio_frames += 1
        self._stt_audio_bytes += len(pcm)
        if not pcm or len(pcm) % 2 != 0:
            log.warning("stt audio bad frame bytes=%d", len(pcm))
            return
        if self._stt_audio_frames not in (1, 2, 3) and self._stt_audio_frames % 10 != 0:
            return
        samples = array.array("h")
        samples.frombytes(pcm)
        if sys.byteorder != "little":
            samples.byteswap()
        if not samples:
            return
        max_abs = max(abs(s) for s in samples)
        rms = math.sqrt(sum(s * s for s in samples) / len(samples))
        log.info(
            "stt audio frame=%d bytes=%d samples=%d rms=%.0f peak=%d",
            self._stt_audio_frames,
            len(pcm),
            len(samples),
            rms,
            max_abs,
        )
