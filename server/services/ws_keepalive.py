"""
WebSocket Keep-Alive Service
=============================
Sends application-level JSON ping messages to every connected device
every PING_INTERVAL seconds. This serves two purposes:

1. Keeps the TCP connection alive through Render's reverse-proxy, which
   terminates idle WebSocket connections (protocol-level WS PING frames
   are answered by the proxy itself and don't reach the backend).

2. Automatically removes dead/zombie connections: if send() fails, the
   connection_manager removes the entry, so subsequent `connection_manager.send()`
   calls return False correctly instead of silently no-oping.
"""
from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)

PING_INTERVAL = 25  # seconds


class WsKeepAlive:
    def __init__(self) -> None:
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="ws-keepalive")
        logger.info("WS keep-alive: started (interval=%ds)", PING_INTERVAL)

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("WS keep-alive: stopped")

    async def _run(self) -> None:
        from server.ws.manager import connection_manager
        from server.ws.handlers import build_ping_message

        while True:
            await asyncio.sleep(PING_INTERVAL)
            device_ids = connection_manager.online_device_ids
            if not device_ids:
                continue

            for device_id in device_ids:
                sent = await connection_manager.send(
                    device_id,
                    build_ping_message(session_id=device_id),
                )
                if not sent:
                    logger.warning(
                        "Keep-alive ping failed for %s — connection removed", device_id
                    )
