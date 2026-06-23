"""
NexLink Server — Orchestration Engine (Phase 5)
==================================================
Top-level service that wires all orchestration components and
integrates them into the server lifecycle.

Components:
  DependencyGraph       — in-memory device relationship graph
  PairingManager        — CRUD for device pairs (DB + graph sync)
  SessionOrchestrator   — cross-device session lifecycle
  ReconnectOrchestrator — coordinate reconnection impact across pairs
  NotificationEngine    — alerts, webhooks, real-time push
  StateSynchronizer     — broadcast state changes to all clients

Startup:
  1. Load dependency graph from DB
  2. Subscribe to EventBus events
  3. Ready to handle device connect/disconnect orchestration

Shutdown:
  1. Unsubscribe from events (prevent in-flight processing)

Event subscriptions:
  device.connected         → StateSynchronizer, ReconnectOrchestrator, NotificationEngine
  device.disconnected      → StateSynchronizer, ReconnectOrchestrator, NotificationEngine
  pair.created             → StateSynchronizer
  pair.dissolved           → StateSynchronizer
  orchestration.session.*  → StateSynchronizer
  heartbeat.received       → (health correlation, future)
"""
from __future__ import annotations

import logging
from typing import Optional

from server.db.session import AsyncSessionFactory
from server.services.event_bus import event_bus

from server.automation.engine import automation_engine
from .dependency_graph import DependencyGraph
from .notification_engine import NotificationEngine
from .pairing_manager import PairingManager
from .reconnect_orchestrator import ReconnectOrchestrator
from .session_orchestrator import SessionOrchestrator
from .state_sync import StateSynchronizer

logger = logging.getLogger(__name__)


class OrchestrationEngine:
    """
    Central orchestration service.

    Provides access to all orchestration subsystems and manages
    their lifecycle and event subscriptions.

    Usage:
        engine = OrchestrationEngine()
        await engine.start()
        # ... server runs ...
        await engine.stop()
    """

    def __init__(
        self,
        webhook_urls: Optional[list[str]] = None,
    ) -> None:
        # ── Core components ───────────────────────────────────────────────────
        self.graph = DependencyGraph()
        self.pairing = PairingManager(self.graph)
        self.sessions = SessionOrchestrator()
        self.reconnect = ReconnectOrchestrator(self.graph, self.sessions)
        self.notifications = NotificationEngine(webhook_urls=webhook_urls)
        self.state_sync = StateSynchronizer(self.graph)
        self.automation = automation_engine

        self._started = False

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """
        Start the orchestration engine.

        1. Load dependency graph from database
        2. Subscribe to EventBus events
        """
        if self._started:
            return

        # Load graph from DB
        async with AsyncSessionFactory() as db:
            try:
                edge_count = await self.pairing.load_graph_from_db(db)
            except Exception as exc:
                logger.error("Failed to load dependency graph: %s", exc)
                edge_count = 0

        # Subscribe to events
        self._subscribe_events()

        # Start automation engine (Phase 8)
        await self.automation.start()

        self._started = True
        logger.info(
            "OrchestrationEngine started | graph_edges=%d",
            edge_count,
        )

    async def stop(self) -> None:
        """Stop the orchestration engine."""
        if not self._started:
            return

        # Stop automation engine first (LIFO)
        await self.automation.stop()

        self._unsubscribe_events()
        self._started = False
        logger.info("OrchestrationEngine stopped")

    # ── Event Subscriptions ───────────────────────────────────────────────────

    def _subscribe_events(self) -> None:
        """Wire all components to the EventBus."""
        # Device connect/disconnect → multiple handlers
        event_bus.subscribe("device.connected", self._on_device_connected)
        event_bus.subscribe("device.disconnected", self._on_device_disconnected)

        # Pair events → state sync
        event_bus.subscribe("pair.created", self.state_sync.on_pair_change)
        event_bus.subscribe("pair.dissolved", self.state_sync.on_pair_change)

        # Session events → state sync
        event_bus.subscribe("orchestration.session.created", self.state_sync.on_session_state_change)
        event_bus.subscribe("orchestration.session.activated", self.state_sync.on_session_state_change)
        event_bus.subscribe("orchestration.session.paused", self.state_sync.on_session_state_change)
        event_bus.subscribe("orchestration.session.completed", self.state_sync.on_session_state_change)
        event_bus.subscribe("orchestration.session.failed", self.state_sync.on_session_state_change)
        event_bus.subscribe("orchestration.session.aborted", self.state_sync.on_session_state_change)
        event_bus.subscribe("orchestration.session.resumed", self.state_sync.on_session_state_change)

        logger.debug("Orchestration event subscriptions registered")

    def _unsubscribe_events(self) -> None:
        """Remove all event subscriptions."""
        event_bus.unsubscribe("device.connected", self._on_device_connected)
        event_bus.unsubscribe("device.disconnected", self._on_device_disconnected)
        event_bus.unsubscribe("pair.created", self.state_sync.on_pair_change)
        event_bus.unsubscribe("pair.dissolved", self.state_sync.on_pair_change)

        for evt in (
            "orchestration.session.created",
            "orchestration.session.activated",
            "orchestration.session.paused",
            "orchestration.session.completed",
            "orchestration.session.failed",
            "orchestration.session.aborted",
            "orchestration.session.resumed",
        ):
            event_bus.unsubscribe(evt, self.state_sync.on_session_state_change)

    async def _on_device_connected(self, event: dict) -> None:
        """Fan-out: device connected → reconnect + state sync + notification."""
        # State sync (broadcast to dashboards)
        await self.state_sync.on_device_state_change(event)

        # Send full state snapshot to the newly connected device
        device_id = event.get("payload", {}).get("device_id", "")
        if device_id:
            await self.state_sync.broadcast_full_state(device_id)

        # Reconnect orchestration (resume paused sessions)
        await self.reconnect.on_device_connected(event)

    async def _on_device_disconnected(self, event: dict) -> None:
        """Fan-out: device disconnected → reconnect + state sync + notification."""
        payload = event.get("payload", {})
        device_id = payload.get("device_id", "")

        # State sync
        await self.state_sync.on_device_state_change(event)

        # Reconnect orchestration (pause sessions, notify pairs)
        await self.reconnect.on_device_disconnected(event)

        # Notifications (based on severity)
        if device_id:
            impact = self.graph.impact_of(device_id)
            severity = impact["severity"]

            if severity != "none":
                await self.notifications.device_offline(
                    device_id=device_id,
                    device_name=f"Device-{device_id[:8]}",
                    severity=severity,
                )

            if severity == "critical":
                await self.notifications.cascade_failure(
                    trigger_device_id=device_id,
                    affected_count=len(impact["transitive_affected"]),
                )

    # ── Status ────────────────────────────────────────────────────────────────

    async def status(self) -> dict:
        """Comprehensive orchestration status."""
        automation_status = await self.automation.status() if self._started else {}
        return {
            "started": self._started,
            "graph": self.graph.to_dict(),
            "notifications": self.notifications.stats,
            "state_sync": self.state_sync.stats,
            "automation": automation_status,
        }


# ── Global singleton ──────────────────────────────────────────────────────────
orchestration_engine = OrchestrationEngine()
