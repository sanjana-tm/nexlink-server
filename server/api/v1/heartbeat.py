"""
NexLink Server — Heartbeat API Endpoints
==========================================
POST /api/v1/devices/{serial}/heartbeat    — receive heartbeat (agent -> server)
GET  /api/v1/devices/{serial}/heartbeats   — paginated heartbeat history

Identity:
  SERIAL_NUMBER in the URL path is the permanent device identifier.
  The heartbeat payload is validated against the authenticated serial.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from server.api.deps import get_current_serial, get_db
from server.core.exceptions import DeviceNotFoundError
from server.db.models.device import Device
from server.db.models.heartbeat import Heartbeat
from server.schemas.heartbeat import (
    HeartbeatHistoryResponse,
    HeartbeatRecord,
    HeartbeatRequest,
    HeartbeatResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["heartbeat"])


def _compute_health_score(
    cpu: float | None,
    mem: float | None,
    storage: float | None,
) -> int:
    """
    Compute a composite health score 0-100 from system metrics.

    Weights: CPU 40%, Memory 35%, Storage 25%.
    Each component score = 100 - percent_used (higher is healthier).
    Missing metrics are assumed healthy (100).
    """
    cpu_score = (100.0 - cpu) if cpu is not None else 100.0
    mem_score = (100.0 - mem) if mem is not None else 100.0
    storage_score = (100.0 - storage) if storage is not None else 100.0

    score = cpu_score * 0.40 + mem_score * 0.35 + storage_score * 0.25
    return max(0, min(100, round(score)))


def _status_from_health(score: int) -> str:
    """Derive device status string from health score."""
    if score >= 80:
        return "healthy"
    if score >= 50:
        return "warning"
    return "critical"


@router.post(
    "/devices/{serial}/heartbeat",
    response_model=HeartbeatResponse,
    status_code=status.HTTP_200_OK,
    summary="Submit heartbeat",
    description="""
Called by the NexLink Agent every N seconds (default: 15s).
Inserts a heartbeat record, computes health score, and updates the device.
    """,
)
async def receive_heartbeat(
    serial: str,
    req: HeartbeatRequest,
    db: AsyncSession = Depends(get_db),
    _auth_serial: str = Depends(get_current_serial),
) -> HeartbeatResponse:
    """Process an incoming heartbeat from an agent."""
    # Look up device
    result = await db.execute(
        select(Device).where(Device.serial_number == serial)
    )
    device = result.scalar_one_or_none()
    if not device:
        raise DeviceNotFoundError(f"Device {serial} not found")

    now = datetime.now(timezone.utc)

    # 1. Insert heartbeat record
    heartbeat = Heartbeat(
        serial_number=serial,
        cpu_percent=req.cpu_percent,
        memory_percent=req.memory_percent,
        storage_percent=req.storage_percent,
        screen_status=req.screen_status,
        uptime_seconds=req.uptime_seconds,
        battery_level=req.battery_level,
        wifi_signal_dbm=req.wifi_signal_dbm,
        agent_timestamp=req.timestamp,
        payload=req.model_dump(exclude_none=True),
    )
    db.add(heartbeat)

    # 2. Compute health score
    health_score = _compute_health_score(
        req.cpu_percent, req.memory_percent, req.storage_percent
    )
    device_status = _status_from_health(health_score)

    # 3. Update device record
    device.is_online = True
    device.status = device_status
    device.health_score = health_score
    device.last_heartbeat_at = now
    device.cpu_percent = req.cpu_percent
    device.memory_percent = req.memory_percent
    device.storage_percent = req.storage_percent

    if req.screen_status:
        device.screen_status = req.screen_status
    if req.uptime_seconds is not None:
        device.uptime_seconds = req.uptime_seconds
    if req.ip_address:
        device.ip_address = req.ip_address
    if req.wifi_ssid:
        device.wifi_ssid = req.wifi_ssid
    if req.agent_version:
        device.agent_version = req.agent_version
    if req.sdk_version is not None:
        device.sdk_version = req.sdk_version

    await db.flush()

    return HeartbeatResponse(
        success=True,
        serial_number=serial,
        health_score=health_score,
        status=device_status,
    )


@router.get(
    "/devices/{serial}/heartbeats",
    response_model=HeartbeatHistoryResponse,
    summary="Get heartbeat history",
)
async def get_heartbeat_history(
    serial: str,
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    _: str = Depends(get_current_serial),
) -> HeartbeatHistoryResponse:
    """Return paginated heartbeat history for a device (newest first)."""
    count_result = await db.execute(
        select(func.count())
        .select_from(Heartbeat)
        .where(Heartbeat.serial_number == serial)
    )
    total = count_result.scalar() or 0

    result = await db.execute(
        select(Heartbeat)
        .where(Heartbeat.serial_number == serial)
        .order_by(Heartbeat.received_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
    )
    records = result.scalars().all()

    return HeartbeatHistoryResponse(
        items=[HeartbeatRecord.model_validate(r) for r in records],
        total=total,
    )
