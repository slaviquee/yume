"""Realtime PCM-to-Ogg-Opus encoder for Gradium STT.

Gradium's public STT WebSocket accepts `input_format="opus"` as an
Ogg-wrapped Opus stream. Swift still sends local 24 kHz PCM to the helper; this
module converts that local stream into browser-demo-like 24 kHz Opus pages.
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
from collections.abc import Awaitable, Callable
from typing import Optional

log = logging.getLogger(__name__)


class OggOpusEncoder:
    """Wraps ffmpeg's libopus encoder behind an async byte stream."""

    def __init__(
        self,
        *,
        sample_rate_hz: int = 24000,
        channels: int = 1,
        emit: Callable[[bytes], Awaitable[None]],
        ffmpeg_path: Optional[str] = None,
    ) -> None:
        self.sample_rate_hz = sample_rate_hz
        self.channels = channels
        self._emit = emit
        self._ffmpeg_path = ffmpeg_path or os.environ.get("YUME_FFMPEG_PATH")
        self._proc: Optional[asyncio.subprocess.Process] = None
        self._stdout_task: Optional[asyncio.Task[None]] = None
        self._stderr_task: Optional[asyncio.Task[None]] = None
        self._closed = False
        self._pcm_bytes = 0
        self._opus_bytes = 0

    async def start(self) -> None:
        if self._proc is not None:
            return
        ffmpeg = self._resolve_ffmpeg_path()
        cmd = [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "warning",
            "-fflags",
            "nobuffer",
            "-f",
            "s16le",
            "-ar",
            str(self.sample_rate_hz),
            "-ac",
            str(self.channels),
            "-i",
            "pipe:0",
            "-vn",
            "-map",
            "0:a:0",
            "-c:a",
            "libopus",
            "-application",
            "audio",
            "-frame_duration",
            "20",
            "-b:a",
            os.environ.get("YUME_STT_OPUS_BITRATE", "32000"),
            "-vbr",
            "off",
            "-compression_level",
            "0",
            "-flush_packets",
            "1",
            "-page_duration",
            "40000",
            "-f",
            "opus",
            "pipe:1",
        ]
        self._proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._stdout_task = asyncio.create_task(self._read_stdout())
        self._stderr_task = asyncio.create_task(self._read_stderr())
        log.info(
            "stt opus encoder started sample_rate=%d channels=%d bitrate=%s",
            self.sample_rate_hz,
            self.channels,
            os.environ.get("YUME_STT_OPUS_BITRATE", "32000"),
        )

    async def write(self, pcm: bytes) -> None:
        if self._closed:
            return
        if self._proc is None:
            await self.start()
        assert self._proc is not None
        if self._proc.stdin is None:
            raise RuntimeError("opus encoder stdin is unavailable")
        self._pcm_bytes += len(pcm)
        try:
            self._proc.stdin.write(pcm)
            await self._proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError) as exc:
            raise RuntimeError("opus encoder stopped accepting audio") from exc

    async def finish(self) -> None:
        """Flush final Opus pages by closing stdin and waiting for ffmpeg."""
        if self._closed:
            return
        self._closed = True
        proc = self._proc
        if proc is None:
            return
        if proc.stdin is not None and not proc.stdin.is_closing():
            proc.stdin.close()
            try:
                await proc.stdin.wait_closed()
            except (BrokenPipeError, ConnectionResetError):
                pass
        if self._stdout_task is not None:
            await self._stdout_task
        try:
            return_code = await asyncio.wait_for(proc.wait(), timeout=2)
        except asyncio.TimeoutError:
            proc.terminate()
            return_code = await proc.wait()
        if self._stderr_task is not None:
            await self._stderr_task
        log.info(
            "stt opus encoder finished pcm_bytes=%d opus_bytes=%d return_code=%s",
            self._pcm_bytes,
            self._opus_bytes,
            return_code,
        )
        if return_code not in (0, None):
            raise RuntimeError(f"opus encoder exited with code {return_code}")

    async def close(self) -> None:
        proc = self._proc
        if proc is None:
            return
        try:
            await self.finish()
        except Exception as exc:  # noqa: BLE001
            log.warning("opus encoder close failed: %s", exc)
            if proc.returncode is None:
                proc.kill()
                await proc.wait()

    async def _read_stdout(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        while True:
            chunk = await self._proc.stdout.read(4096)
            if not chunk:
                return
            self._opus_bytes += len(chunk)
            await self._emit(chunk)

    async def _read_stderr(self) -> None:
        assert self._proc is not None and self._proc.stderr is not None
        while True:
            line = await self._proc.stderr.readline()
            if not line:
                return
            message = line.decode("utf-8", errors="replace").strip()
            if message:
                log.warning("opus encoder: %s", message)

    def _resolve_ffmpeg_path(self) -> str:
        ffmpeg = self._ffmpeg_path or shutil.which("ffmpeg")
        if not ffmpeg:
            raise RuntimeError(
                "ffmpeg is required for GRADIUM_STT_INPUT_FORMAT=opus; "
                "install ffmpeg or set GRADIUM_STT_INPUT_FORMAT=pcm"
            )
        return ffmpeg
