"""Entrypoint: python -m agent_service"""
from __future__ import annotations

import asyncio
import logging
import os
import signal

from .server import AgentServer

logging.basicConfig(
    level=logging.DEBUG if os.environ.get("YUME_DEBUG") == "1" else logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)


async def main() -> None:
    port = int(os.environ.get("YUME_AGENT_PORT", "7422"))
    server = AgentServer(host="127.0.0.1", port=port)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    serve_task = asyncio.create_task(server.serve_forever())
    await stop.wait()
    serve_task.cancel()
    try:
        await serve_task
    except asyncio.CancelledError:
        pass


if __name__ == "__main__":
    asyncio.run(main())
