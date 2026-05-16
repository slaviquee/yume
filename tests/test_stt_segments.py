"""Tests for STT segment assembly + flush logic. See docs/spec.md section 8.1."""
from __future__ import annotations

import asyncio
import json

import pytest

from voice_service.config import GradiumConfig
from voice_service.stt_stream import SttEvent, SttStream

pytestmark = pytest.mark.asyncio


def _cfg() -> GradiumConfig:
    return GradiumConfig(api_key="test-key")


async def _drive(stream: SttStream, messages: list[dict]) -> list[SttEvent]:
    """Drive the private message handler directly. Lets us test segment
    assembly without a real WebSocket."""
    out: list[SttEvent] = []
    async def emit(ev): out.append(ev)
    stream._emit = emit
    stream._started_at = "2026-05-16T00:00:00Z"
    for m in messages:
        await stream._handle_message(m)
    return out


async def test_push_to_talk_final_on_flushed():
    stream = SttStream(_cfg(), mode="push_to_talk", turn_id="t1")
    events = await _drive(stream, [
        {"type": "text", "text": "open"},
        {"type": "text", "text": "open TextEdit"},
        {"type": "end_text"},
        {"type": "flushed"},
    ])
    finals = [e for e in events if e.type == "final"]
    assert len(finals) == 1
    assert finals[0].text == "open TextEdit"
    assert finals[0].finalized_by == "flushed"


async def test_continuous_uses_vad_step():
    stream = SttStream(_cfg(), mode="continuous", turn_id="t2")
    events = await _drive(stream, [
        {"type": "text", "text": "what's the weather"},
        {"type": "end_text"},
        # A VAD step with high inactivity probability indicates turn-end.
        {"type": "step", "inactivity_probability": 0.9},
    ])
    finals = [e for e in events if e.type == "final"]
    assert len(finals) == 1
    assert finals[0].text == "what's the weather"
    assert finals[0].finalized_by == "vad"


async def test_continuous_skips_low_vad_probability():
    stream = SttStream(_cfg(), mode="continuous", turn_id="t3")
    events = await _drive(stream, [
        {"type": "text", "text": "hello"},
        {"type": "step", "inactivity_probability": 0.2},
    ])
    finals = [e for e in events if e.type == "final"]
    assert finals == []


async def test_segments_replaced_in_progress():
    """While a segment is still open, newer text replaces older text."""
    stream = SttStream(_cfg(), mode="push_to_talk", turn_id="t4")
    events = await _drive(stream, [
        {"type": "text", "text": "open"},
        {"type": "text", "text": "open the"},
        {"type": "text", "text": "open the doc"},
    ])
    # Only one segment is still open; partials should reflect the latest text.
    partials = [e for e in events if e.type == "partial"]
    assert partials[-1].text == "open the doc"


async def test_multiple_segments_joined():
    stream = SttStream(_cfg(), mode="push_to_talk", turn_id="t5")
    events = await _drive(stream, [
        {"type": "text", "text": "open TextEdit"},
        {"type": "end_text"},
        {"type": "text", "text": "and start writing"},
        {"type": "end_text"},
        {"type": "flushed"},
    ])
    final = [e for e in events if e.type == "final"][-1]
    assert final.text == "open TextEdit and start writing"


async def test_audio_after_opus_flush_is_ignored():
    class FakeWebSocket:
        def __init__(self) -> None:
            self.sent: list[dict] = []

        async def send(self, payload: str) -> None:
            self.sent.append(json.loads(payload))

    class FakeEncoder:
        def __init__(self) -> None:
            self.writes: list[bytes] = []
            self.finished = False

        async def write(self, pcm: bytes) -> None:
            self.writes.append(pcm)

        async def finish(self) -> None:
            self.finished = True

        async def close(self) -> None:
            self.finished = True

    ws = FakeWebSocket()
    encoder = FakeEncoder()
    stream = SttStream(
        GradiumConfig(api_key="test-key", stt_input_format="opus"),
        mode="push_to_talk",
        turn_id="t6",
    )
    stream._ws = ws  # type: ignore[assignment]
    stream._accepting_audio = True
    stream._opus_encoder = encoder  # type: ignore[assignment]

    await stream.send_audio(b"opus input")
    await stream.send_flush()
    await stream.send_audio(b"late raw pcm")

    assert encoder.writes == [b"opus input"]
    assert encoder.finished is True
    assert [msg["type"] for msg in ws.sent] == ["flush"]
