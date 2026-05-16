"""Configuration loaded from environment. Keys live in env/Keychain, never logs."""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class GradiumConfig:
    api_key: str
    stt_url: str = "wss://api.gradium.ai/api/speech/asr"
    tts_url: str = "wss://api.gradium.ai/api/speech/tts"

    stt_sample_rate_hz: int = 24000
    stt_chunk_samples: int = 1920  # 80 ms at 24 kHz, 3840 bytes
    stt_model: str = "default"
    stt_continuous_vad: bool = True

    tts_sample_rate_hz: int = 48000
    tts_chunk_samples: int = 3840  # 80 ms at 48 kHz, 7680 bytes
    tts_model: str = "default"
    tts_voice_id: str = ""


def load_config() -> GradiumConfig:
    api_key = os.environ.get("GRADIUM_API_KEY", "")
    return GradiumConfig(
        api_key=api_key,
        stt_url=os.environ.get("GRADIUM_STT_URL", "wss://api.gradium.ai/api/speech/asr"),
        tts_url=os.environ.get("GRADIUM_TTS_URL", "wss://api.gradium.ai/api/speech/tts"),
        tts_voice_id=os.environ.get("GRADIUM_TTS_VOICE_ID", ""),
    )
