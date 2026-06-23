"""
NexLink Server — Device Allocator (Phase 8)
==============================================
Matches execution requirements to available devices and manages
device locking during execution.

Allocation strategy:
  1. Query online, active devices from the registry
  2. Filter by platform_filter (android, ios, etc.)
  3. Filter by device_filter (capabilities, SDK version, etc.)
  4. Exclude devices already locked for another execution
  5. Pick the least-recently-used device (spread load evenly)
  6. Lock the device (mark as allocated)
  7. Return the device_id

Why device locking?
  Two executions on the same device simultaneously would interfere:
  both trying to install APKs, both sending touch events.
  The allocator guarantees one execution per device.

Lock tracking:
  In-memory set + DB status field. The in-memory set is the fast
  check; the DB field is the durable truth (survives restarts).
"""
from __future__ import annotations

import logging
import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.db.models.automation import AutomationExecution
from server.db.models.device import Device
from server.ws.manager import connection_manager

logger = logging.getLogger(__name__)


class DeviceAllocator:
    """
    Allocates devices for automation executions.

    Maintains an in-memory set of locked device_ids for fast
    availability checks. Synchronized with DB on startup.
    """

    def __init__(self) -> None:
        # device_id (str) → execution_id (str)
        self._locked: dict[str, str] = {}

    async def allocate(
        self,
        execution: AutomationExecution,
        db: AsyncSession,
    ) -> Optional[str]:
        """
        Find and lock a device for the given execution.

        If execution.device_id is already set (explicit allocation),
        verify that device is available and lock it.

        If device_id is None, auto-allocate based on filters.

        Returns:
            device_id string on success, None if no device available.
        """
        if execution.device_id:
            # Explicit device requested
            did = str(execution.device_id)
            if did in self._locked:
                logger.warning(
                    "Device %s already locked for execution %s",
                    did[:8], self._locked[did][:8],
                )
                return None

            # Verify device is online
            if not connection_manager.is_connected(did):
                logger.warning("Requested device %s is offline", did[:8])
                return None

            self._lock(did, str(execution.execution_id))
            return did

        # Auto-allocate: find best matching device
        candidates = await self._find_candidates(execution, db)

        for device in candidates:
            did = str(device.device_id)
            if did in self._locked:
                continue
            if not connection_manager.is_connected(did):
                continue

            self._lock(did, str(execution.execution_id))
            logger.info(
                "Auto-allocated device %s for execution %s",
                did[:8], str(execution.execution_id)[:8],
            )
            return did

        logger.warning(
            "No available device for execution %s (platform=%s)",
            str(execution.execution_id)[:8], execution.platform_filter,
        )
        return None

    def release(self, device_id: str) -> None:
        """Release a device lock after execution completes."""
        if device_id in self._locked:
            exec_id = self._locked.pop(device_id)
            logger.info(
                "Released device %s (was locked for %s)",
                device_id[:8], exec_id[:8],
            )

    def is_locked(self, device_id: str) -> bool:
        return device_id in self._locked

    def get_execution_for_device(self, device_id: str) -> Optional[str]:
        """Return the execution_id that has this device locked, or None."""
        return self._locked.get(device_id)

    @property
    def locked_count(self) -> int:
        return len(self._locked)

    @property
    def locked_devices(self) -> list[str]:
        return list(self._locked.keys())

    def available_count(self) -> int:
        """Count of online devices that are NOT locked."""
        online = set(connection_manager.online_device_ids)
        locked = set(self._locked.keys())
        return len(online - locked)

    # ── Internal ──────────────────────────────────────────────────────────────

    def _lock(self, device_id: str, execution_id: str) -> None:
        self._locked[device_id] = execution_id

    async def _find_candidates(
        self,
        execution: AutomationExecution,
        db: AsyncSession,
    ) -> list[Device]:
        """
        Find devices matching execution requirements.

        Filters:
          - is_online = True
          - is_active = True
          - platform matches (if specified)
          - NOT already locked
        """
        query = select(Device).where(
            Device.is_online == True,
            Device.is_active == True,
        )

        if execution.platform_filter:
            query = query.where(Device.platform == execution.platform_filter)

        # Order by last_seen ascending (least recently used first)
        query = query.order_by(Device.last_seen.asc().nullslast())

        result = await db.execute(query)
        devices = list(result.scalars().all())

        # Apply device_filter if present
        if execution.device_filter:
            devices = self._apply_device_filter(devices, execution.device_filter)

        return devices

    @staticmethod
    def _apply_device_filter(
        devices: list[Device],
        device_filter: dict,
    ) -> list[Device]:
        """Apply capability/attribute filters to candidate devices."""
        filtered = []
        required_caps = device_filter.get("capabilities", [])

        for device in devices:
            # Check capabilities
            if required_caps:
                device_caps = set()
                if device.capabilities:
                    device_caps = {c.capability for c in device.capabilities}
                if not all(cap in device_caps for cap in required_caps):
                    continue

            filtered.append(device)

        return filtered

    async def sync_from_db(self, db: AsyncSession) -> int:
        """
        On startup, rebuild lock state from running executions.

        Any execution in 'allocated' or 'running' status has a device locked.
        """
        self._locked.clear()

        result = await db.execute(
            select(AutomationExecution).where(
                AutomationExecution.status.in_(["allocated", "running", "installing"]),
                AutomationExecution.device_id.isnot(None),
            )
        )
        executions = result.scalars().all()

        for ex in executions:
            self._locked[str(ex.device_id)] = str(ex.execution_id)

        logger.info("Synced %d device locks from database", len(self._locked))
        return len(self._locked)
