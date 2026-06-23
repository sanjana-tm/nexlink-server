"""
NexLink Server — State Synchronizer (Phase 5)
================================================
Real-time state broadcast to all connected clients.

When any device's state changes, the synchronizer pushes the
new state to ALL connected agents and dashboards within the
same event loop tick. No polling, no stale data.

Broadcast topics:
  1. Device state changes (online/offline/recovering)
  2. Session state changes (active/paused/completed/failed)
  3. Pair changes (created/dissolved)
  4. Full state snapshot on request (for newly connected clients)
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from server.ws.manager import connection_manager
from server.services.event_bus import event_bus

from .dependency_graph import DependencyGraph

logger = logging.getLogger(__name__)


class StateSynchronizer:
    """
    Pushes real-time state updates to all connected WebSocket clients.

    Subscribes to EventBus events and translates them into
    dashboard-friendly WebSocket messages.
    """

    def __init__(self, graph: DependencyGraph) -> None:
        self._graph = graph
        self._sync_count = 0

    async def on_device_state_change(self, event: dict) -> None:
        """
        Handle device.connected / device.disconnected events.

        Broadcasts a device_state_update to all connected clients
        with the affected device's new state and impact info.
        """
        payload = event.get("payload", {})
        device_id = payload.get("device_id", "")
        event_type = event.get("type", "")

        is_online = event_type == "device.connected"

        # Get affected pairs and sessions
        affected_pairs = self._graph.get_pairs_for_device(device_id)

        update = {
            "type": "state.device_update",
            "payload": {
                "device_id": device_id,
                "is_online": is_online,
                "event_type": event_type,
                "affected_pairs": affected_pairs,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        }

        await connection_manager.broadcast(update, exclude_device_id=device_id)
        self._sync_count += 1

    async def on_session_state_change(self, event: dict) -> None:
        """
        Handle orchestration.session.* events.

        Broadcasts session state updates to all connected clients.
        """
        payload = event.get("payload", {})
        event_type = event.get("type", "")

        update = {
            "type": "state.session_update",
            "payload": {
                **payload,
                "event_type": event_type,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        }

        await connection_manager.broadcast(update)
        self._sync_count += 1

    async def on_pair_change(self, event: dict) -> None:
        """
        Handle pair.created / pair.dissolved events.

        Broadcasts pair changes so dashboards can update the graph view.
        """
        payload = event.get("payload", {})
        event_type = event.get("type", "")

        update = {
            "type": "state.pair_update",
            "payload": {
                **payload,
                "event_type": event_type,
                "graph_summary": {
                    "edge_count": self._graph.edge_count,
                    "node_count": self._graph.node_count,
                },
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        }

        await connection_manager.broadcast(update)
        self._sync_count += 1

    async def broadcast_full_state(self, device_id: str) -> None:
        """
        Send a complete state snapshot to a specific device.

        Called when a device first connects — so it has the full picture
        of all paired devices, sessions, and current states.
        """
        graph_data = self._graph.to_dict()

        snapshot = {
            "type": "state.full_snapshot",
            "payload": {
                "graph": graph_data,
                "online_devices": connection_manager.online_device_ids,
                "connection_count": connection_manager.connection_count,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        }

        await connection_manager.send(device_id, snapshot)

    @property
    def stats(self) -> dict:
        return {"total_syncs": self._sync_count}
