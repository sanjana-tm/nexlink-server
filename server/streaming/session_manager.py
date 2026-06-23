"""
NexLink Server — Stream Session Manager (Phase 11)
=====================================================
Manages the lifecycle of remote streaming sessions.

Session flow:
  1. Viewer requests stream via REST: POST /api/v1/streaming/sessions
  2. Server creates session, checks permissions
  3. Server sends "stream.start" command to agent via WebSocket
  4. Agent starts capture, responds with "stream.ready"
  5. Viewer connects via WebRTC or MJPEG WebSocket
  6. Viewer sends "stream.stop" or disconnects → session ends
"""
from __future__ import annotations

import logging
import time
import uuid
from typing import Dict, List, Optional

from server.services.event_bus import event_bus
from server.ws.manager import connection_manager
from server.ws.handlers import build_command_message

logger = logging.getLogger(__name__)


class StreamSessionInfo:
    """Server-side stream session tracking."""

    def __init__(
        self,
        session_id: str,
        device_id: str,
        viewer_id: str,
        input_enabled: bool = False,
    ) -> None:
        self.session_id = session_id
        self.device_id = device_id
        self.viewer_id = viewer_id
        self.input_enabled = input_enabled
        self.state = "pending"       # pending, active, ended, error
        self.created_at = time.time()
        self.started_at: Optional[float] = None
        self.ended_at: Optional[float] = None
        self.frame_count = 0
        self.input_count = 0

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "device_id": self.device_id,
            "viewer_id": self.viewer_id,
            "state": self.state,
            "input_enabled": self.input_enabled,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "frame_count": self.frame_count,
            "input_count": self.input_count,
        }


class StreamSessionManager:
    """
    Manages all active streaming sessions.

    Tracks which viewers are watching which devices and
    enforces permission checks on input injection.
    """

    def __init__(self) -> None:
        self._sessions: Dict[str, StreamSessionInfo] = {}
        # device_id → list of session_ids (multiple viewers per device)
        self._device_sessions: Dict[str, List[str]] = {}

    async def create_session(
        self,
        device_id: str,
        viewer_id: str,
        input_enabled: bool = False,
        max_fps: int = 15,
        quality: int = 80,
    ) -> Optional[StreamSessionInfo]:
        """
        Create a new streaming session.

        Verifies the device is online and sends the start command.
        Returns the session info, or None if the device is offline.
        """
        if not connection_manager.is_connected(device_id):
            logger.warning("Cannot stream device %s — offline", device_id[:8])
            return None

        session_id = str(uuid.uuid4())
        session = StreamSessionInfo(
            session_id=session_id,
            device_id=device_id,
            viewer_id=viewer_id,
            input_enabled=input_enabled,
        )
        self._sessions[session_id] = session

        if device_id not in self._device_sessions:
            self._device_sessions[device_id] = []
        self._device_sessions[device_id].append(session_id)

        # Send start command to agent
        command = build_command_message(
            command_id=session_id,
            command_type="stream.start",
            params={
                "session_id": session_id,
                "device_id": device_id,
                "viewer_id": viewer_id,
                "input_enabled": input_enabled,
                "max_fps": max_fps,
                "quality": quality,
            },
            session_id=session_id,
        )

        sent = await connection_manager.send(device_id, command)
        if not sent:
            self._cleanup_session(session_id)
            return None

        session.state = "active"
        session.started_at = time.time()

        await event_bus.publish(
            "streaming.session.started",
            payload=session.to_dict(),
            source_device_id=device_id,
        )

        logger.info(
            "Stream session created: %s → device %s (viewer=%s, input=%s)",
            session_id[:8], device_id[:8], viewer_id[:8], input_enabled,
        )
        return session

    async def end_session(self, session_id: str) -> bool:
        """End a streaming session and notify the agent."""
        session = self._sessions.get(session_id)
        if not session:
            return False

        # Send stop command to agent
        stop_cmd = build_command_message(
            command_id=session_id,
            command_type="stream.stop",
            params={"session_id": session_id},
            session_id=session_id,
        )
        await connection_manager.send(session.device_id, stop_cmd)

        session.state = "ended"
        session.ended_at = time.time()

        self._cleanup_session(session_id)

        await event_bus.publish(
            "streaming.session.ended",
            payload=session.to_dict(),
            source_device_id=session.device_id,
        )

        logger.info("Stream session ended: %s", session_id[:8])
        return True

    async def relay_signaling(
        self,
        session_id: str,
        signal_type: str,
        data: dict,
        direction: str = "to_agent",
    ) -> bool:
        """
        Relay WebRTC signaling messages between viewer and agent.

        signal_type: "offer", "answer", "ice"
        direction:   "to_agent" (viewer → agent) or "to_viewer" (agent → viewer)
        """
        session = self._sessions.get(session_id)
        if not session or session.state != "active":
            return False

        message = {
            "type": f"stream.{signal_type}",
            "payload": {
                "session_id": session_id,
                **data,
            },
        }

        if direction == "to_agent":
            return await connection_manager.send(session.device_id, message)
        else:
            # To viewer: broadcast on the session event channel
            await event_bus.publish(
                f"streaming.signal.{signal_type}",
                payload={"session_id": session_id, **data},
                source_device_id=session.device_id,
            )
            return True

    async def relay_input(self, session_id: str, event_data: dict) -> bool:
        """Relay an input event from viewer to agent (with permission check)."""
        session = self._sessions.get(session_id)
        if not session or not session.input_enabled:
            return False

        message = {
            "type": "stream.input",
            "payload": {
                "session_id": session_id,
                "event": event_data,
            },
        }

        sent = await connection_manager.send(session.device_id, message)
        if sent:
            session.input_count += 1
        return sent

    def get_session(self, session_id: str) -> Optional[StreamSessionInfo]:
        return self._sessions.get(session_id)

    def get_device_sessions(self, device_id: str) -> List[StreamSessionInfo]:
        session_ids = self._device_sessions.get(device_id, [])
        return [self._sessions[sid] for sid in session_ids if sid in self._sessions]

    def list_active(self) -> List[dict]:
        return [s.to_dict() for s in self._sessions.values() if s.state == "active"]

    async def handle_device_offline(self, device_id: str) -> None:
        """End all streaming sessions for a device that went offline."""
        session_ids = self._device_sessions.get(device_id, [])[:]
        for session_id in session_ids:
            session = self._sessions.get(session_id)
            if session and session.state == "active":
                session.state = "error"
                session.ended_at = time.time()
                self._cleanup_session(session_id)
                logger.warning(
                    "Stream session %s ended — device %s offline",
                    session_id[:8], device_id[:8],
                )

    def _cleanup_session(self, session_id: str) -> None:
        session = self._sessions.pop(session_id, None)
        if session:
            device_sessions = self._device_sessions.get(session.device_id, [])
            if session_id in device_sessions:
                device_sessions.remove(session_id)
            if not device_sessions:
                self._device_sessions.pop(session.device_id, None)

    @property
    def active_count(self) -> int:
        return sum(1 for s in self._sessions.values() if s.state == "active")

    def status(self) -> dict:
        return {
            "active_sessions": self.active_count,
            "total_tracked": len(self._sessions),
            "devices_streaming": len(self._device_sessions),
            "sessions": self.list_active(),
        }
