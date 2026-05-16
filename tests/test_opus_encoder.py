"""Tests for the optional Ogg Opus STT transport."""
from __future__ import annotations

import math
import shutil
import struct

import pytest

from voice_service.opus_encoder import OggOpusEncoder

pytestmark = pytest.mark.asyncio


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
async def test_opus_encoder_emits_ogg_opus_stream():
    chunks: list[bytes] = []

    async def emit(chunk: bytes) -> None:
        chunks.append(chunk)

    encoder = OggOpusEncoder(sample_rate_hz=24000, channels=1, emit=emit)
    samples = []
    for i in range(24000 // 2):
        value = int(math.sin(2 * math.pi * 440 * i / 24000) * 8000)
        samples.append(struct.pack("<h", value))
    await encoder.start()
    await encoder.write(b"".join(samples))
    await encoder.finish()

    encoded = b"".join(chunks)
    assert encoded.startswith(b"OggS")
    assert b"OpusHead" in encoded[:128]
    assert len(encoded) > 100
