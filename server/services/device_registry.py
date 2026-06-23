"""
NexLink Server — Device Registry Service
==========================================
CRUD operations for the device table.
The device registry is the source of truth for all connected devices.

Key Invariant:
  DEVICE_ID is always the primary identity.
  IP addresses, hostnames, and session IDs are mutable metadata.
  A device should never be identified by its IP or ADB TCP ID.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from server.core.exceptions import DeviceNotFoundError
from server.db.models.device import Device, DeviceCapability
from server.schemas.device import DeviceListResponse, DeviceSchema, DeviceSummarySchema, DeviceUpdateRequest

logger = logging.getLogger(__name__)


class DeviceRegistry:
    """Device registry operations."""

    async def get_device(
        self,
        device_id: uuid.UUID,
        db: AsyncSession,
    ) -> Device:
        """
        Fetch a device by its stable DEVICE_ID.
        Eagerly loads capabilities relationship.
        Raises DeviceNotFoundError if not found.
        """
        result = await db.execute(
            select(Device)
            .where(Device.device_id == device_id, Device.is_active == True)
            .options(selectinload(Device.capabilities))
        )
        device = result.scalar_one_or_none()
        if device is None:
            raise DeviceNotFoundError(f"Device {device_id} not found")
        return device

    async def list_devices(
        self,
        db: AsyncSession,
        page: int = 1,
        per_page: int = 20,
        online_only: bool = False,
        platform: str | None = None,
    ) -> DeviceListResponse:
        """
        Return a paginated list of devices.
        Supports filtering by online state and platform.
        """
        query = select(Device).where(Device.is_active == True)

        if online_only:
            query = query.where(Device.is_online == True)
        if platform:
            query = query.where(Device.platform == platform)

        # Total count
        count_result = await db.execute(
            select(func.count()).select_from(query.subquery())
        )
        total = count_result.scalar_one()

        # Counts by online state
        online_result = await db.execute(
            select(func.count()).where(Device.is_active == True, Device.is_online == True)
        )
        online_count = online_result.scalar_one()

        # Paginated results
        result = await db.execute(
            query
            .order_by(Device.last_seen.desc().nulls_last(), Device.created_at.desc())
            .offset((page - 1) * per_page)
            .limit(per_page)
        )
        devices = result.scalars().all()

        return DeviceListResponse(
            devices=[DeviceSummarySchema.model_validate(d) for d in devices],
            total=total,
            online_count=online_count,
            offline_count=total - online_count,
        )

    async def update_device(
        self,
        device_id: uuid.UUID,
        req: DeviceUpdateRequest,
        db: AsyncSession,
    ) -> Device:
        """Update mutable device fields."""
        device = await self.get_device(device_id, db)

        if req.agent_name is not None:
            device.agent_name = req.agent_name
        if req.agent_version is not None:
            device.agent_version = req.agent_version
        if req.metadata is not None:
            device.metadata_ = req.metadata

        if req.capabilities is not None:
            # Replace all capabilities
            existing = await db.execute(
                select(DeviceCapability).where(DeviceCapability.device_id == device_id)
            )
            for cap in existing.scalars():
                await db.delete(cap)
            for cap_name in req.capabilities:
                db.add(DeviceCapability(device_id=device_id, capability=cap_name))

        device.updated_at = datetime.now(timezone.utc)
        await db.flush()
        logger.debug("Device updated: %s", device_id)
        return device

    async def mark_online(
        self,
        device_id: uuid.UUID,
        db: AsyncSession,
    ) -> None:
        """Mark device as online. Called when WebSocket connects."""
        now = datetime.now(timezone.utc)
        await db.execute(
            update(Device)
            .where(Device.device_id == device_id)
            .values(is_online=True, last_seen=now, updated_at=now)
        )
        logger.info("Device ONLINE: %s", device_id)

    async def mark_offline(
        self,
        device_id: uuid.UUID,
        db: AsyncSession,
    ) -> None:
        """Mark device as offline. Called when WebSocket disconnects or heartbeat times out."""
        now = datetime.now(timezone.utc)
        await db.execute(
            update(Device)
            .where(Device.device_id == device_id)
            .values(is_online=False, updated_at=now)
        )
        logger.info("Device OFFLINE: %s", device_id)

    async def update_last_heartbeat(
        self,
        device_id: uuid.UUID,
        db: AsyncSession,
    ) -> None:
        """Update last_heartbeat_at and last_seen timestamps."""
        now = datetime.now(timezone.utc)
        await db.execute(
            update(Device)
            .where(Device.device_id == device_id)
            .values(last_seen=now, last_heartbeat_at=now, is_online=True, updated_at=now)
        )

    async def deactivate_device(
        self,
        device_id: uuid.UUID,
        db: AsyncSession,
    ) -> None:
        """Soft-delete a device. Sets is_active=False."""
        await db.execute(
            update(Device)
            .where(Device.device_id == device_id)
            .values(is_active=False, is_online=False, updated_at=datetime.now(timezone.utc))
        )
        logger.warning("Device deactivated (soft-delete): %s", device_id)
