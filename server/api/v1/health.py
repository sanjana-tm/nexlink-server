"""
NexLink Server -- Health API Endpoints
=======================================
GET /api/v1/devices/{serial}/health   -- health score + metric breakdown for a device
GET /api/v1/health/overview           -- fleet-wide health summary

Identity:
  SERIAL_NUMBER in the URL path is the permanent device identifier.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from server.api.deps import get_current_serial, get_db
from server.core.exceptions import DeviceNotFoundError
from server.db.models.device import Device

logger = logging.getLogger(__name__)
router = APIRouter(tags=["health"])


@router.get(
    "/devices/{serial}/health",
    summary="Device health details",
    description="Return the current health score and metric breakdown for a single device.",
)
async def get_device_health(
    serial: str,
    db: AsyncSession = Depends(get_db),
    _auth: str = Depends(get_current_serial),
) -> dict:
    """Return health details for a specific device."""
    result = await db.execute(
        select(Device).where(Device.serial_number == serial)
    )
    device = result.scalar_one_or_none()
    if not device:
        raise DeviceNotFoundError(f"Device {serial} not found")

    return {
        "serial_number": device.serial_number,
        "health_score": device.health_score,
        "status": device.status,
        "is_online": device.is_online,
        "metrics": {
            "cpu_percent": device.cpu_percent,
            "memory_percent": device.memory_percent,
            "storage_percent": device.storage_percent,
        },
        "screen_status": device.screen_status,
        "uptime_seconds": device.uptime_seconds,
        "last_heartbeat_at": (
            device.last_heartbeat_at.isoformat()
            if device.last_heartbeat_at
            else None
        ),
    }


@router.get(
    "/health/overview",
    summary="Fleet health overview",
    description="""
Return an aggregate health summary across all registered devices.

Includes: total count, online/offline counts, breakdown by status,
and the average health score across all devices that have one.
    """,
)
async def fleet_health_overview(
    db: AsyncSession = Depends(get_db),
    _auth: str = Depends(get_current_serial),
) -> dict:
    """Return fleet-wide health summary."""
    # Total devices
    total = (
        await db.execute(select(func.count()).select_from(Device))
    ).scalar() or 0

    # Online / offline counts
    online = (
        await db.execute(
            select(func.count())
            .select_from(Device)
            .where(Device.is_online.is_(True))
        )
    ).scalar() or 0

    offline = total - online

    # Count by status
    status_rows = (
        await db.execute(
            select(Device.status, func.count())
            .group_by(Device.status)
        )
    ).all()
    by_status = {row[0]: row[1] for row in status_rows}

    # Average health score (only devices that have a score)
    avg_health = (
        await db.execute(
            select(func.avg(Device.health_score)).where(
                Device.health_score.isnot(None)
            )
        )
    ).scalar()

    return {
        "total": total,
        "online": online,
        "offline": offline,
        "by_status": by_status,
        "avg_health_score": round(avg_health, 1) if avg_health is not None else None,
    }
