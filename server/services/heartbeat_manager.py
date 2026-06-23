"""
NexLink Server — Heartbeat Manager
=====================================
Background monitor that detects stale heartbeats and marks devices offline.
Uses SERIAL_NUMBER as device identity.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update

from server.config.settings import get_settings
from server.db.models.device import Device
from server.db.session import AsyncSessionFactory
from server.services.event_bus import event_bus

logger = logging.getLogger(__name__)
_settings = get_settings()


class HeartbeatMonitor:
    """
    Background task that detects stale heartbeats and marks devices offline.
    """

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._running = False

    async def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(self._run(), name="heartbeat_monitor")
        logger.info(
            "HeartbeatMonitor started (timeout=%ds, check_interval=%ds)",
            _settings.heartbeat_timeout_seconds,
            _settings.heartbeat_check_interval_seconds,
        )

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("HeartbeatMonitor stopped")

    async def _run(self) -> None:
        while self._running:
            await asyncio.sleep(_settings.heartbeat_check_interval_seconds)
            try:
                await self._check_stale_devices()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("HeartbeatMonitor error: %s", e, exc_info=True)

    async def _check_stale_devices(self) -> None:
        """Find devices marked online but with stale heartbeats. Mark them offline."""
        cutoff = datetime.now(timezone.utc) - timedelta(
            seconds=_settings.heartbeat_timeout_seconds
        )

        async with AsyncSessionFactory() as db:
            try:
                result = await db.execute(
                    select(Device).where(
                        Device.is_online == True,  # noqa: E712
                        Device.last_heartbeat_at < cutoff,
                    )
                )
                stale_devices = result.scalars().all()

                for device in stale_devices:
                    await db.execute(
                        update(Device)
                        .where(Device.serial_number == device.serial_number)
                        .values(
                            is_online=False,
                            status="offline",
                            updated_at=datetime.now(timezone.utc),
                        )
                    )
                    logger.warning(
                        "Device went OFFLINE (heartbeat timeout): %s (last_heartbeat=%s)",
                        device.serial_number,
                        device.last_heartbeat_at,
                    )
                    await event_bus.publish(
                        "device.offline",
                        payload={
                            "serial_number": device.serial_number,
                            "last_heartbeat_at": (
                                device.last_heartbeat_at.isoformat()
                                if device.last_heartbeat_at else None
                            ),
                            "reason": "heartbeat_timeout",
                        },
                        source_device_id=device.serial_number,
                    )

                if stale_devices:
                    await db.commit()
                    logger.info(
                        "HeartbeatMonitor: marked %d device(s) offline", len(stale_devices)
                    )

            except Exception:
                await db.rollback()
                raise
