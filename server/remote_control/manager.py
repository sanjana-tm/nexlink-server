"""
NexLink Server — Remote Control Manager (Phase 12)
=====================================================
Top-level service wiring coordinate mapper, gesture router,
lock manager, and audit logger into the control pipeline.

Control flow:
  1. Viewer sends gesture request (e.g., "tap at 400,300 on viewer canvas")
  2. LockManager checks viewer has control lock
  3. CoordinateMapper translates viewer coords → device coords
  4. GestureRouter decomposes compound gestures → atomic inputs
  5. Each atomic input sent to agent via WebSocket (stream.input)
  6. AuditLogger records every action to DB
  7. Result sent back to viewer
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Dict, Optional

from server.services.event_bus import event_bus
from server.ws.manager import connection_manager

from .audit_logger import AuditLogger
from .coordinate_mapper import CoordinateMapper, DisplayInfo, ViewportInfo
from .gesture_router import GestureRouter
from .lock_manager import LockManager

logger = logging.getLogger(__name__)


class ControlSession:
    """
    Tracks a remote control session for one device.

    Extends the streaming session with control-specific state:
    coordinate mapper, gesture router, and input counting.
    """

    def __init__(
        self,
        device_id: str,
        device_width: int = 1920,
        device_height: int = 1080,
        device_rotation: int = 0,
    ) -> None:
        self.device_id = device_id
        self.device_info = DisplayInfo(
            width=device_width,
            height=device_height,
            rotation=device_rotation,
        )
        self.mapper = CoordinateMapper(
            device=self.device_info,
            viewport=ViewportInfo(width=800, height=450),
        )
        self.gesture_router = GestureRouter(self.mapper)
        self.input_count = 0
        self.created_at = time.time()

    def update_viewport(self, width: int, height: int) -> None:
        """Called when the viewer's browser canvas resizes."""
        self.mapper.update_viewport(ViewportInfo(width=width, height=height))

    def update_device(self, width: int, height: int, rotation: int = 0) -> None:
        """Called when device orientation changes."""
        self.device_info = DisplayInfo(width=width, height=height, rotation=rotation)
        self.mapper.update_device(self.device_info)


class RemoteControlManager:
    """
    Manages remote control for all devices.

    One ControlSession per device being controlled.
    LockManager ensures exclusive access.
    AuditLogger records everything.
    """

    def __init__(self) -> None:
        self.locks = LockManager()
        self.audit = AuditLogger()
        self._sessions: Dict[str, ControlSession] = {}

    # ── Session Management ────────────────────────────────────────────────────

    def get_or_create_session(
        self,
        device_id: str,
        device_width: int = 1920,
        device_height: int = 1080,
        device_rotation: int = 0,
    ) -> ControlSession:
        """Get existing control session or create new one."""
        if device_id not in self._sessions:
            self._sessions[device_id] = ControlSession(
                device_id=device_id,
                device_width=device_width,
                device_height=device_height,
                device_rotation=device_rotation,
            )
        return self._sessions[device_id]

    def remove_session(self, device_id: str) -> None:
        self._sessions.pop(device_id, None)

    # ── Gesture Execution ─────────────────────────────────────────────────────

    async def execute_gesture(
        self,
        device_id: str,
        user_id: str,
        gesture: str,
        viewer_x: float = 0.0,
        viewer_y: float = 0.0,
        viewer_x2: float = 0.0,
        viewer_y2: float = 0.0,
        params: dict | None = None,
        stream_session_id: str = "",
    ) -> dict:
        """
        Execute a gesture on a device with full pipeline:
        lock check → coordinate map → gesture route → dispatch → audit.

        Returns:
            {"success": bool, "gesture": str, "steps": int, "error": str}
        """
        # 1. Lock check
        if not self.locks.check(device_id, user_id):
            lock = self.locks.get_lock(device_id)
            holder = lock.holder_name if lock else "unknown"
            return {
                "success": False,
                "gesture": gesture,
                "steps": 0,
                "error": f"Device locked by {holder}",
            }

        # Refresh lock TTL on activity
        self.locks.refresh_on_activity(device_id)

        # 2. Get/create control session
        session = self.get_or_create_session(device_id)

        # 3. Route gesture through decomposer
        result = session.gesture_router.route(
            gesture=gesture,
            viewer_x=viewer_x,
            viewer_y=viewer_y,
            viewer_x2=viewer_x2,
            viewer_y2=viewer_y2,
            params=params,
        )

        if not result.success:
            await self.audit.log_input(
                device_id=device_id, user_id=user_id,
                action="gesture", gesture_name=gesture,
                success=False, error=result.error,
            )
            return {
                "success": False,
                "gesture": gesture,
                "steps": 0,
                "error": result.error,
            }

        # 4. Dispatch each atomic input step to the agent
        dispatched = 0
        for step in result.steps:
            if step.delay_before_ms > 0:
                await asyncio.sleep(step.delay_before_ms / 1000.0)

            event_payload = self._step_to_event(step)
            message = {
                "type": "stream.input",
                "payload": {
                    "session_id": stream_session_id,
                    "event": event_payload,
                },
            }

            sent = await connection_manager.send(device_id, message)
            if sent:
                dispatched += 1

            # 5. Audit log each step
            await self.audit.log_input(
                device_id=device_id,
                user_id=user_id,
                action=step.action,
                session_id=stream_session_id,
                gesture_name=gesture,
                device_x=step.x,
                device_y=step.y,
                device_x2=step.x2,
                device_y2=step.y2,
                success=sent,
                details={"key_code": step.key_code, "text": step.text} if step.key_code or step.text else None,
            )

        session.input_count += dispatched

        return {
            "success": dispatched > 0,
            "gesture": gesture,
            "steps": dispatched,
            "error": "" if dispatched > 0 else "Failed to dispatch to agent",
        }

    # ── Direct Input (bypass gesture router) ──────────────────────────────────

    async def execute_raw_tap(
        self,
        device_id: str,
        user_id: str,
        viewer_x: float,
        viewer_y: float,
        stream_session_id: str = "",
    ) -> dict:
        """Quick tap — maps coordinates and sends directly."""
        return await self.execute_gesture(
            device_id=device_id,
            user_id=user_id,
            gesture="tap",
            viewer_x=viewer_x,
            viewer_y=viewer_y,
            stream_session_id=stream_session_id,
        )

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _step_to_event(step) -> dict:
        """Convert an AtomicInput to the WebSocket event format."""
        if step.action == "tap":
            return {
                "event_type": "tap",
                "x": step.x / 1920,  # Re-normalize for Phase 11 injector
                "y": step.y / 1080,
            }
        elif step.action == "swipe":
            return {
                "event_type": "swipe",
                "x": step.x / 1920,
                "y": step.y / 1080,
                "x2": step.x2 / 1920,
                "y2": step.y2 / 1080,
                "duration_ms": step.duration_ms,
            }
        elif step.action == "keyevent":
            return {"event_type": "key_press", "key_code": step.key_code}
        elif step.action == "text":
            return {"event_type": "text", "text": step.text}
        return {"event_type": step.action}

    # ── Status ────────────────────────────────────────────────────────────────

    def status(self) -> dict:
        return {
            "active_sessions": len(self._sessions),
            "active_locks": self.locks.active_locks,
            "total_audited": self.audit.total_logged,
        }
