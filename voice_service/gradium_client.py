"""Low-level Gradium WebSocket client primitives.

These wrap the documented endpoints described in docs/spec.md section 8.
The voice_service modules build STT and TTS streams on top of these.

API keys are passed in an Authorization header. They are never logged or
serialized into events.
"""
from __future__ import annotations

import logging
from typing import Optional

import websockets
from websockets.client import WebSocketClientProtocol

log = logging.getLogger(__name__)


async def open_gradium_ws(url: str, api_key: str) -> WebSocketClientProtocol:
    """Open a Gradium WS connection authenticated with the provided key."""
    if not api_key:
        raise RuntimeError(
            "GRADIUM_API_KEY is not set; configure it in the environment or Keychain"
        )
    headers = {"Authorization": f"Bearer {api_key}"}
    log.debug("connecting to %s", url)
    return await websockets.connect(url, extra_headers=headers, max_size=2**24)


async def safe_close(ws: Optional[WebSocketClientProtocol]) -> None:
    if ws is None:
        return
    try:
        await ws.close()
    except Exception:  # noqa: BLE001
        log.debug("error closing ws", exc_info=True)
