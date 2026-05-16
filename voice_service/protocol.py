"""Message types exchanged between the Swift app and voice_service.

All messages are JSON over a localhost WebSocket. Binary audio is base64-encoded
inside the `pcm_b64` field — keeping a single transport simplifies the Swift
client significantly.
"""
from __future__ import annotations

from typing import Literal, TypedDict, Union


# ── App → voice_service ────────────────────────────────────────────────────
class SttStart(TypedDict):
    type: Literal["stt.start"]
    turnId: str
    mode: Literal["push_to_talk", "continuous"]


class SttAudio(TypedDict):
    type: Literal["stt.audio"]
    turnId: str
    pcm_b64: str  # 24 kHz mono PCM s16le


class SttFlush(TypedDict):
    type: Literal["stt.flush"]
    turnId: str


class SttStop(TypedDict):
    type: Literal["stt.stop"]
    turnId: str


class TtsSpeak(TypedDict, total=False):
    type: Literal["tts.speak"]
    utteranceId: str
    text: str
    voiceId: str
    interruptible: bool


class TtsAppend(TypedDict, total=False):
    type: Literal["tts.append"]
    utteranceId: str
    text: str
    flush: bool


class TtsStop(TypedDict):
    type: Literal["tts.stop"]
    utteranceId: str


Inbound = Union[SttStart, SttAudio, SttFlush, SttStop, TtsSpeak, TtsAppend, TtsStop]


# ── voice_service → App ────────────────────────────────────────────────────
class SttTranscript(TypedDict, total=False):
    type: Literal["stt.transcript"]
    turnId: str
    text: str
    isFinal: bool
    finalizedBy: str
    startedAt: str
    endedAt: str


class TtsAudio(TypedDict):
    type: Literal["tts.audio"]
    utteranceId: str
    pcm_b64: str  # 48 kHz mono PCM s16le


class TtsDone(TypedDict):
    type: Literal["tts.done"]
    utteranceId: str


class ErrorMsg(TypedDict, total=False):
    type: Literal["error"]
    code: str
    message: str
    turnId: str
    utteranceId: str


Outbound = Union[SttTranscript, TtsAudio, TtsDone, ErrorMsg]
