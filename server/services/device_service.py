"""
NexLink Server — Device Management Service
============================================
CRUD and status management for devices, keyed by SERIAL_NUMBER.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from server.db.models.device import Device


class DeviceService:
    """CRUD and status management for devices."""

    @staticmethod
    async def get_all(
        db: AsyncSession,
        is_online: bool | None = None,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[Device], int]:
        """Return a filtered, paginated list of devices with total count."""
        query = select(Device)
        count_query = select(func.count()).select_from(Device)

        if is_online is not None:
            query = query.where(Device.is_online == is_online)
            count_query = count_query.where(Device.is_online == is_online)
        if status is not None:
            query = query.where(Device.status == status)
            count_query = count_query.where(Device.status == status)

        query = query.order_by(Device.serial_number).limit(limit).offset(offset)

        result = await db.execute(query)
        count_result = await db.execute(count_query)
        return list(result.scalars().all()), count_result.scalar_one()

    @staticmethod
    async def get_by_serial(
        db: AsyncSession,
        serial_number: str,
    ) -> Device | None:
        """Look up a single device by its hardware serial number."""
        result = await db.execute(
            select(Device).where(Device.serial_number == serial_number)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def register(
        db: AsyncSession,
        serial_number: str,
        model: str | None = None,
        manufacturer: str | None = None,
        android_version: str | None = None,
    ) -> Device:
        """Register a new device in the database."""
        device = Device(
            serial_number=serial_number,
            model=model,
            manufacturer=manufacturer,
            android_version=android_version,
            first_seen_at=datetime.now(timezone.utc),
        )
        db.add(device)
        await db.flush()
        return device

    @staticmethod
    async def update_from_heartbeat(
        db: AsyncSession,
        serial_number: str,
        cpu_percent: float | None = None,
        memory_percent: float | None = None,
        storage_percent: float | None = None,
        screen_status: str | None = None,
        uptime_seconds: int | None = None,
        ip_address: str | None = None,
        wifi_ssid: str | None = None,
        agent_version: str | None = None,
        device_name: str | None = None,
        model: str | None = None,
        android_version: str | None = None,
        health_score: int | None = None,
        status: str | None = None,
    ) -> None:
        """Update device row with latest heartbeat data (non-None fields only)."""
        now = datetime.now(timezone.utc)
        values: dict = {
            "is_online": True,
            "last_heartbeat_at": now,
            "updated_at": now,
        }

        # Only set non-None values
        optional_fields = [
            ("cpu_percent", cpu_percent),
            ("memory_percent", memory_percent),
            ("storage_percent", storage_percent),
            ("screen_status", screen_status),
            ("uptime_seconds", uptime_seconds),
            ("ip_address", ip_address),
            ("wifi_ssid", wifi_ssid),
            ("agent_version", agent_version),
            ("device_name", device_name),
            ("model", model),
            ("android_version", android_version),
            ("health_score", health_score),
            ("status", status),
        ]
        for field, val in optional_fields:
            if val is not None:
                values[field] = val

        await db.execute(
            update(Device)
            .where(Device.serial_number == serial_number)
            .values(**values)
        )

    @staticmethod
    async def mark_offline(db: AsyncSession, serial_number: str) -> None:
        """Mark a device as offline."""
        await db.execute(
            update(Device)
            .where(Device.serial_number == serial_number)
            .values(
                is_online=False,
                status="offline",
                updated_at=datetime.now(timezone.utc),
            )
        )
