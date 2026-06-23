"""
NexLink Server — WebSocket Connection Manager
=============================================
In-memory registry of all active WebSocket connections.

One WebSocket per device_id (latest connection wins).
When an agent reconnects, the old WebSocket is gracefully closed before
registering the new one — preventing duplicate connections.

Thread Safety:
  All operations are protected by asyncio.Lock. Since FastAPI's async
  handlers all run on the same event loop, the lock prevents race conditions
  when two connection tasks touch the same device_id concurrently.

Scaling:
  This in-memory approach works for single-server deployments.
  For horizontal scaling (multiple server instances), replace with
  Redis pub/sub + Redis session tracking in Phase 3+.

Connection Map:
  device_id (UUID str) → WebSocket
  device_id (UUID str) → session_id (UUID str)
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)


class ConnectionManager:
    """
    Thread-safe registry of active WebSocket connections.

    Usage:
        mgr = ConnectionManager()  # singleton, created once

        # On connect
        await mgr.connect(device_id, session_id, websocket)

        # Send to specific device
        await mgr.send(device_id, {"type": "command", "payload": {...}})

        # Broadcast to all
        await mgr.broadcast({"type": "server_shutdown"})

        # On disconnect
        await mgr.disconnect(device_id)
    """

    def __init__(self) -> None:
        # device_id → WebSocket
        self._connections: dict[str, WebSocket] = {}
        # device_id → session_id
        self._session_map: dict[str, str] = {}
        self._lock = asyncio.Lock()

    async def connect(
        self,
        device_id: str,
        session_id: str,
        websocket: WebSocket,
    ) -> None:
        """
        Register a new WebSocket connection.

        If the device already has an active connection (duplicate connect /
        agent reconnected without proper close), the old WebSocket is closed
        with code 4001 (replaced) before registering the new one.

        WebSocket.accept() is called here — must be called before sending
        or receiving any messages.
        """
        await websocket.accept()

        async with self._lock:
            existing = self._connections.get(device_id)
            if existing is not None:
                logger.warning(
                    "Device %s already connected — closing old session %s",
                    device_id,
                    self._session_map.get(device_id),
                )
                try:
                    await existing.close(code=4001, reason="Replaced by new connection")
                except Exception:
                    pass  # old connection may already be dead

            self._connections[device_id] = websocket
            self._session_map[device_id] = session_id

        logger.info(
            "WebSocket connected: device_id=%s session_id=%s total=%d",
            device_id, session_id, len(self._connections),
        )

    async def disconnect(self, device_id: str) -> None:
        """Remove a device's WebSocket registration. Does NOT close the socket."""
        async with self._lock:
            self._connections.pop(device_id, None)
            self._session_map.pop(device_id, None)

        logger.info(
            "WebSocket disconnected: device_id=%s total=%d",
            device_id, len(self._connections),
        )

    async def send(
        self,
        device_id: str,
        message: dict[str, Any],
    ) -> bool:
        """
        Send a JSON message to a specific device.

        Returns True if sent successfully, False if device not connected.
        Does NOT raise if device is disconnected — callers should check
        the return value and handle accordingly.
        """
        ws = self._connections.get(device_id)
        if ws is None:
            logger.debug("send: device %s not connected", device_id)
            return False

        try:
            await ws.send_json(message)
            return True
        except Exception as e:
            logger.warning("send failed for device %s: %s", device_id, e)
            await self.disconnect(device_id)
            return False

    async def broadcast(
        self,
        message: dict[str, Any],
        exclude_device_id: str | None = None,
    ) -> int:
        """
        Broadcast a JSON message to all connected devices.

        Args:
            message:           The message dict to send.
            exclude_device_id: Skip this device (e.g., the sender).

        Returns:
            Number of devices the message was successfully sent to.
        """
        sent_count = 0
        # Snapshot the connections dict to avoid mutation during iteration
        snapshot = dict(self._connections)

        for device_id, ws in snapshot.items():
            if device_id == exclude_device_id:
                continue
            try:
                await ws.send_json(message)
                sent_count += 1
            except Exception as e:
                logger.warning("broadcast: failed for device %s: %s", device_id, e)
                await self.disconnect(device_id)

        return sent_count

    def is_connected(self, device_id: str) -> bool:
        """Return True if device currently has an active WebSocket."""
        return device_id in self._connections

    def get_session_id(self, device_id: str) -> str | None:
        """Return the session_id for a connected device, or None."""
        return self._session_map.get(device_id)

    @property
    def online_device_ids(self) -> list[str]:
        """Return list of all currently connected device IDs."""
        return list(self._connections.keys())

    @property
    def connection_count(self) -> int:
        """Return total number of active WebSocket connections."""
        return len(self._connections)


# ── Global singleton ──────────────────────────────────────────────────────────
# Shared across all WebSocket endpoint handlers via dependency injection.
connection_manager = ConnectionManager()
