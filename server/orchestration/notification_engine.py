"""
NexLink Server — Notification Engine (Phase 5)
=================================================
Centralized notification system for alerts, events, and status updates.

Channels:
  1. WebSocket (real-time push to connected clients — default)
  2. Webhook (HTTP POST to external URL — for Slack, PagerDuty, etc.)
  3. Persistent (saved to DB for dashboard history)

Notification levels:
  info     — status updates, session events
  warning  — device offline, recovery in progress
  error    — session failed, recovery failed
  critical — multiple devices offline, cascade failure

The engine subscribes to high-level orchestration events and
translates them into user-facing notifications.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from server.db.models.pairing import Notification
from server.db.session import AsyncSessionFactory
from server.ws.manager import connection_manager

logger = logging.getLogger(__name__)


class NotificationEngine:
    """
    Creates, persists, and delivers notifications.

    Notifications are always persisted to DB (audit trail).
    Delivery is best-effort — WebSocket sends may fail if
    no clients are connected.
    """

    def __init__(self, webhook_urls: Optional[list[str]] = None) -> None:
        self._webhook_urls = webhook_urls or []
        self._notification_count = 0

    # ── Public API ────────────────────────────────────────────────────────────

    async def notify(
        self,
        title: str,
        message: str,
        level: str = "info",
        category: str = "system",
        source_device_id: Optional[str] = None,
        session_id: Optional[str] = None,
        data: Optional[dict[str, Any]] = None,
        channel: str = "websocket",
    ) -> str:
        """
        Create and deliver a notification.

        Args:
            title:            Short notification title.
            message:          Detailed message body.
            level:            info / warning / error / critical
            category:         system / device / session / pairing / recovery
            source_device_id: Related device UUID.
            session_id:       Related session UUID.
            data:             Extra structured data.
            channel:          Delivery channel.

        Returns:
            notification_id (UUID string).
        """
        notification_id = uuid.uuid4()

        # Persist to DB
        async with AsyncSessionFactory() as db:
            try:
                record = Notification(
                    notification_id=notification_id,
                    level=level,
                    category=category,
                    title=title,
                    message=message,
                    source_device_id=uuid.UUID(source_device_id) if source_device_id else None,
                    session_id=uuid.UUID(session_id) if session_id else None,
                    channel=channel,
                    data=data,
                )
                db.add(record)
                await db.commit()
            except Exception as exc:
                logger.error("Failed to persist notification: %s", exc)
                await db.rollback()

        # Deliver
        payload = {
            "notification_id": str(notification_id),
            "level": level,
            "category": category,
            "title": title,
            "message": message,
            "source_device_id": source_device_id,
            "session_id": session_id,
            "data": data,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        delivered = False

        if channel in ("websocket", "all"):
            delivered = await self._deliver_websocket(payload)

        if channel in ("webhook", "all") and self._webhook_urls:
            await self._deliver_webhooks(payload)
            delivered = True

        # Mark as delivered
        if delivered:
            async with AsyncSessionFactory() as db:
                try:
                    from sqlalchemy import update
                    await db.execute(
                        update(Notification)
                        .where(Notification.notification_id == notification_id)
                        .values(
                            delivered=True,
                            delivered_at=datetime.now(timezone.utc),
                        )
                    )
                    await db.commit()
                except Exception:
                    await db.rollback()

        self._notification_count += 1

        log_method = {
            "info": logger.info,
            "warning": logger.warning,
            "error": logger.error,
            "critical": logger.critical,
        }.get(level, logger.info)

        log_method(
            "Notification [%s/%s]: %s — %s",
            level.upper(), category, title, message[:100],
        )

        return str(notification_id)

    # ── Convenience Methods ───────────────────────────────────────────────────

    async def device_offline(
        self,
        device_id: str,
        device_name: str,
        severity: str,
    ) -> str:
        return await self.notify(
            title=f"Device Offline: {device_name}",
            message=f"Device {device_name} ({device_id[:8]}) has gone offline. Severity: {severity}.",
            level="warning" if severity in ("medium", "low") else "error",
            category="device",
            source_device_id=device_id,
            data={"severity": severity},
        )

    async def device_online(
        self,
        device_id: str,
        device_name: str,
        downtime: float,
    ) -> str:
        return await self.notify(
            title=f"Device Online: {device_name}",
            message=f"Device {device_name} ({device_id[:8]}) is back online after {downtime:.0f}s downtime.",
            level="info",
            category="recovery",
            source_device_id=device_id,
            data={"downtime_seconds": downtime},
        )

    async def session_failed(
        self,
        session_id: str,
        session_name: str,
        error: str,
    ) -> str:
        return await self.notify(
            title=f"Session Failed: {session_name}",
            message=f"Orchestration session {session_name} failed: {error}",
            level="error",
            category="session",
            session_id=session_id,
            data={"error": error},
        )

    async def cascade_failure(
        self,
        trigger_device_id: str,
        affected_count: int,
    ) -> str:
        return await self.notify(
            title="Cascade Failure Detected",
            message=f"Device {trigger_device_id[:8]} offline — {affected_count} dependent devices affected.",
            level="critical",
            category="system",
            source_device_id=trigger_device_id,
            data={"affected_device_count": affected_count},
        )

    # ── Delivery ──────────────────────────────────────────────────────────────

    async def _deliver_websocket(self, payload: dict) -> bool:
        """Push notification to all connected WebSocket clients."""
        message = {
            "type": "notification",
            "payload": payload,
        }
        count = await connection_manager.broadcast(message)
        return count > 0

    async def _deliver_webhooks(self, payload: dict) -> None:
        """POST notification to configured webhook URLs."""
        try:
            import httpx
        except ImportError:
            logger.debug("httpx not installed — skipping webhook delivery")
            return

        for url in self._webhook_urls:
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.post(url, json=payload)
                    if resp.status_code >= 400:
                        logger.warning(
                            "Webhook delivery failed: url=%s status=%d",
                            url, resp.status_code,
                        )
            except Exception as exc:
                logger.warning("Webhook delivery error: url=%s error=%s", url, exc)

    # ── Status ────────────────────────────────────────────────────────────────

    @property
    def stats(self) -> dict:
        return {
            "total_sent": self._notification_count,
            "webhook_urls": len(self._webhook_urls),
        }
