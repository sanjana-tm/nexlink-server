"""
NexLink Server — Reconnect Orchestrator (Phase 5)
====================================================
Coordinates reconnection across paired devices when one goes offline.

When device X disconnects:
  1. DependencyGraph identifies all affected devices
  2. Affected orchestration sessions are paused
  3. Paired devices are notified of the disconnect
  4. Reconnection is monitored — when X comes back:
     a. Sessions are resumed
     b. Paired devices are notified of reconnection
     c. State is synchronized

This is the SERVER-SIDE reconnect coordinator. The AGENT-SIDE
reconnect (ADB recovery, SSH reconnect, etc.) is handled by the
agent's DeviceManager. This orchestrator coordinates the IMPACT
of disconnects across the device graph.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Optional

from server.db.session import AsyncSessionFactory
from server.services.event_bus import event_bus
from server.ws.manager import connection_manager

from .dependency_graph import DependencyGraph
from .session_orchestrator import SessionOrchestrator

logger = logging.getLogger(__name__)


class ReconnectOrchestrator:
    """
    Handles cross-device reconnection orchestration.

    Subscribes to device.connected/disconnected events and coordinates
    the impact across paired devices and sessions.
    """

    def __init__(
        self,
        graph: DependencyGraph,
        session_orch: SessionOrchestrator,
    ) -> None:
        self._graph = graph
        self._session_orch = session_orch

        # Track disconnect timestamps for duration metrics
        self._disconnect_times: dict[str, float] = {}

    async def on_device_disconnected(self, event: dict) -> None:
        """
        Handle a device going offline.

        Steps:
          1. Analyze impact via DependencyGraph
          2. Notify paired devices
          3. Pause affected sessions
          4. Record disconnect time
          5. Publish orchestration event
        """
        payload = event.get("payload", {})
        device_id = payload.get("device_id", "")
        if not device_id:
            return

        self._disconnect_times[device_id] = time.time()

        # Analyze impact
        impact = self._graph.impact_of(device_id)
        direct_dependents = impact["direct_dependents"]
        severity = impact["severity"]

        logger.warning(
            "Device disconnect orchestration: device=%s severity=%s dependents=%d",
            device_id[:8], severity, len(direct_dependents),
        )

        if severity == "none":
            return

        # Notify paired devices that their partner is offline
        for dep in direct_dependents:
            dep_device_id = dep["device_id"]
            await connection_manager.send(dep_device_id, {
                "type": "orchestration.partner_offline",
                "payload": {
                    "offline_device_id": device_id,
                    "relationship": dep["relationship"],
                    "severity": severity,
                },
            })

        # Pause affected sessions
        async with AsyncSessionFactory() as db:
            try:
                sessions = await self._session_orch.get_active_sessions_for_device(
                    uuid.UUID(device_id), db,
                )

                for session in sessions:
                    await self._session_orch.update_device_status(
                        session.session_id,
                        uuid.UUID(device_id),
                        "disconnected",
                        db,
                    )

                    # Auto-pause active sessions
                    if session.status == "active":
                        await self._session_orch.pause_session(
                            session.session_id, db,
                            reason=f"Device {device_id[:8]} disconnected",
                        )

                await db.commit()
            except Exception as exc:
                logger.error("Error handling disconnect for sessions: %s", exc)
                await db.rollback()

        # Publish orchestration-level event
        await event_bus.publish(
            "orchestration.disconnect_impact",
            payload={
                "device_id": device_id,
                "impact": impact,
                "affected_session_count": len(sessions) if 'sessions' in dir() else 0,
            },
            source_device_id=device_id,
        )

    async def on_device_connected(self, event: dict) -> None:
        """
        Handle a device coming back online.

        Steps:
          1. Calculate downtime duration
          2. Notify paired devices of reconnection
          3. Resume paused sessions
          4. Sync state
        """
        payload = event.get("payload", {})
        device_id = payload.get("device_id", "")
        if not device_id:
            return

        # Calculate downtime
        disconnect_time = self._disconnect_times.pop(device_id, None)
        downtime = time.time() - disconnect_time if disconnect_time else 0.0

        impact = self._graph.impact_of(device_id)
        direct_dependents = impact["direct_dependents"]

        if not direct_dependents:
            return

        logger.info(
            "Device reconnect orchestration: device=%s downtime=%.1fs dependents=%d",
            device_id[:8], downtime, len(direct_dependents),
        )

        # Notify paired devices
        for dep in direct_dependents:
            dep_device_id = dep["device_id"]
            await connection_manager.send(dep_device_id, {
                "type": "orchestration.partner_online",
                "payload": {
                    "online_device_id": device_id,
                    "relationship": dep["relationship"],
                    "downtime_seconds": downtime,
                },
            })

        # Resume paused sessions
        async with AsyncSessionFactory() as db:
            try:
                sessions = await self._session_orch.get_active_sessions_for_device(
                    uuid.UUID(device_id), db,
                )

                for session in sessions:
                    await self._session_orch.update_device_status(
                        session.session_id,
                        uuid.UUID(device_id),
                        "active",
                        db,
                    )

                    # Check if all devices in session are back online
                    all_active = all(
                        d.device_status in ("active", "ready", "completed")
                        for d in session.devices
                    )

                    if session.status == "paused" and all_active:
                        session.status = "active"
                        logger.info(
                            "Session auto-resumed: %s (all devices active)",
                            str(session.session_id)[:8],
                        )

                        await event_bus.publish(
                            "orchestration.session.resumed",
                            payload={
                                "session_id": str(session.session_id),
                                "trigger_device_id": device_id,
                            },
                        )

                await db.commit()
            except Exception as exc:
                logger.error("Error handling reconnect for sessions: %s", exc)
                await db.rollback()

        await event_bus.publish(
            "orchestration.reconnect_handled",
            payload={
                "device_id": device_id,
                "downtime_seconds": downtime,
                "dependents_notified": len(direct_dependents),
            },
            source_device_id=device_id,
        )
