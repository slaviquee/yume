"""Tests for whitespace-only TTS chunking. See docs/spec.md section 8.2."""
from __future__ import annotations

import pytest

from voice_service.tts_stream import split_for_tts


def test_basic_words():
    chunks = split_for_tts("hello world")
    # We get word + trailing space pairs and the trailing word.
    assert chunks == ["hello ", "world"]


def test_does_not_split_inside_words():
    chunks = split_for_tts("checklisting")
    assert chunks == ["checklisting"]


def test_punctuation_stays_with_word():
    chunks = split_for_tts("hello, world.")
    # Comma sticks to hello; period sticks to world.
    assert chunks == ["hello, ", "world."]


def test_multiple_whitespace_collapses_into_one_chunk():
    chunks = split_for_tts("hello   world")
    assert chunks == ["hello   ", "world"]


def test_concat_reproduces_input():
    text = "I'll start that in the background; should be quick."
    chunks = split_for_tts(text)
    assert "".join(chunks) == text


def test_empty():
    assert split_for_tts("") == []


def test_only_whitespace():
    chunks = split_for_tts("   \n  ")
    assert "".join(chunks) == "   \n  "


def test_leading_whitespace():
    chunks = split_for_tts("   hello world")
    assert "".join(chunks) == "   hello world"
