"""
NexLink Server — Viewer WebSocket Manager
==========================================
Manages browser viewer connections for the remote control / screen streaming feature.

Each device serial can have multiple viewers watching simultaneously.
Frames broadcast via broadcast_frame() are queued (not sent directly) to
prevent concurrent send conflicts on WebSocket objects.

Queue maxsize=3: if the viewer is too slow to consume, oldest frames are dropped
rather than blocking the device's stream.frame handler.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Dict, List

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class ViewerConnection:
    """One browser viewer connected to watch/control a device."""

    def __init__(self, ws: WebSocket, viewer_id: str) -> None:
        self.ws = ws
        self.viewer_id = viewer_id
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=3)
        self.frame_count = 0
        self.dropped_frames = 0

    def enqueue_frame(self, message: dict) -> None:
        """Non-blocking enqueue. Drops oldest frame if queue is full."""
        try:
            self.queue.put_nowait(message)
        except asyncio.QueueFull:
            # Drop the oldest frame to make room for the new one
            try:
                self.queue.get_nowait()
                self.queue.put_nowait(message)
                self.dropped_frames += 1
            except asyncio.QueueEmpty:
                pass


class ViewerManager:
    """
    Registry of all active viewer WebSocket connections.

    Thread-safe within a single asyncio event loop (all operations are async
    and must be called from the same loop).
    """

    def __init__(self) -> None:
        # serial_number → list of active viewer connections
        self._viewers: Dict[str, List[ViewerConnection]] = {}

    def add_viewer(self, serial: str, viewer: ViewerConnection) -> None:
        if serial not in self._viewers:
            self._viewers[serial] = []
        self._viewers[serial].append(viewer)
        logger.info(
            "Viewer %s added for device %s (total=%d)",
            viewer.viewer_id[:8],
            serial[:12],
            len(self._viewers[serial]),
        )

    def remove_viewer(self, serial: str, viewer: ViewerConnection) -> None:
        viewers = self._viewers.get(serial, [])
        if viewer in viewers:
            viewers.remove(viewer)
            # Signal the sender task to stop
            try:
                viewer.queue.put_nowait(None)
            except asyncio.QueueFull:
                pass
        if not viewers:
            self._viewers.pop(serial, None)
        logger.info("Viewer %s removed from device %s", viewer.viewer_id[:8], serial[:12])

    def broadcast_frame(self, serial: str, message: dict) -> int:
        """
        Enqueue a frame for all viewers watching this device.
        Returns number of active viewers.
        Non-blocking — frames are dropped if viewer queues are full.
        """
        viewers = self._viewers.get(serial, [])
        for viewer in viewers:
            viewer.enqueue_frame(message)
            viewer.frame_count += 1
        return len(viewers)

    def viewer_count(self, serial: str) -> int:
        return len(self._viewers.get(serial, []))

    def status(self) -> dict:
        return {
            "devices_with_viewers": list(self._viewers.keys()),
            "total_viewers": sum(len(v) for v in self._viewers.values()),
        }


# Singleton
viewer_manager = ViewerManager()
