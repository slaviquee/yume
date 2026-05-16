"""Configuration loaded from environment. Keys live in env/Keychain, never logs."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class GradiumConfig:
    api_key: str
    stt_url: str = "wss://api.gradium.ai/api/speech/asr"
    tts_url: str = "wss://api.gradium.ai/api/speech/tts"

    stt_input_format: str = "opus"
    stt_sample_rate_hz: int = 24000
    stt_chunk_samples: int = 1920  # 80 ms at 24 kHz, 3840 bytes
    stt_json_config: dict[str, Any] = field(
        default_factory=lambda: {"language": "en", "temp": 0.0, "delay_in_frames": 16}
    )
    stt_model: str = "default"
    stt_continuous_vad: bool = True

    tts_sample_rate_hz: int = 48000
    tts_chunk_samples: int = 3840  # 80 ms at 48 kHz, 7680 bytes
    tts_model: str = "default"
    tts_voice_id: str = "YTpq7expH9539ERJ"


def load_config() -> GradiumConfig:
    api_key = os.environ.get("GRADIUM_API_KEY", "")
    return GradiumConfig(
        api_key=api_key,
        stt_url=os.environ.get("GRADIUM_STT_URL", "wss://api.gradium.ai/api/speech/asr"),
        tts_url=os.environ.get("GRADIUM_TTS_URL", "wss://api.gradium.ai/api/speech/tts"),
        stt_input_format=os.environ.get("GRADIUM_STT_INPUT_FORMAT", "opus"),
        stt_json_config=_load_stt_json_config(),
        tts_voice_id=os.environ.get("GRADIUM_TTS_VOICE_ID", "YTpq7expH9539ERJ"),
    )


def _load_stt_json_config() -> dict[str, Any]:
    raw = os.environ.get("GRADIUM_STT_JSON_CONFIG")
    if raw:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("GRADIUM_STT_JSON_CONFIG must be valid JSON") from exc
        if not isinstance(parsed, dict):
            raise ValueError("GRADIUM_STT_JSON_CONFIG must be a JSON object")
        return parsed
    return {
        "language": os.environ.get("GRADIUM_STT_LANGUAGE", "en"),
        "temp": float(os.environ.get("GRADIUM_STT_TEMP", "0.0")),
        "delay_in_frames": int(os.environ.get("GRADIUM_STT_DELAY_IN_FRAMES", "16")),
    }
