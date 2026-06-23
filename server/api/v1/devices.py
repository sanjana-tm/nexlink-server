"""
NexLink Server — Device Registry API Endpoints
================================================
GET    /api/v1/devices              — list all devices (paginated, filterable)
GET    /api/v1/devices/{serial}     — get device by serial_number
PATCH  /api/v1/devices/{serial}     — update device_name or metadata
DELETE /api/v1/devices/{serial}     — soft-delete (mark offline + inactive)

Identity:
  SERIAL_NUMBER (string like "TMX2405A12345") is the permanent device identifier.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from server.api.deps import get_current_serial, get_db, require_admin
from server.core.exceptions import DeviceNotFoundError
from server.db.models.device import Device
from server.schemas.common import MessageResponse
from server.schemas.device import DeviceListResponse, DeviceSchema, DeviceUpdateRequest

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/devices", tags=["devices"])


async def _get_device_or_404(serial: str, db: AsyncSession) -> Device:
    """Fetch device by serial_number or raise 404."""
    result = await db.execute(
        select(Device).where(Device.serial_number == serial)
    )
    device = result.scalar_one_or_none()
    if not device:
        raise DeviceNotFoundError(f"Device {serial} not found")
    return device


@router.get(
    "",
    response_model=DeviceListResponse,
    summary="List devices",
    description="Return paginated list of all registered devices with optional filters.",
)
async def list_devices(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=200),
    is_online: bool | None = Query(None, description="Filter by online status"),
    status_filter: str | None = Query(
        None, alias="status", description="Filter by status: healthy, warning, critical, offline, unknown"
    ),
    db: AsyncSession = Depends(get_db),
    _serial: str = Depends(get_current_serial),
) -> DeviceListResponse:
    """Return paginated list of all registered devices."""
    query = select(Device)
    count_query = select(func.count()).select_from(Device)

    if is_online is not None:
        query = query.where(Device.is_online == is_online)
        count_query = count_query.where(Device.is_online == is_online)

    if status_filter:
        query = query.where(Device.status == status_filter)
        count_query = count_query.where(Device.status == status_filter)

    total = (await db.execute(count_query)).scalar() or 0

    query = query.order_by(Device.serial_number)
    query = query.offset((page - 1) * per_page).limit(per_page)

    result = await db.execute(query)
    devices = result.scalars().all()

    return DeviceListResponse(
        devices=[DeviceSchema.model_validate(d) for d in devices],
        total=total,
    )


@router.get(
    "/{serial}",
    response_model=DeviceSchema,
    summary="Get device details",
)
async def get_device(
    serial: str,
    db: AsyncSession = Depends(get_db),
    _current: str = Depends(get_current_serial),
) -> DeviceSchema:
    """Return full device record by serial_number."""
    device = await _get_device_or_404(serial, db)
    return DeviceSchema.model_validate(device)


@router.patch(
    "/{serial}",
    response_model=DeviceSchema,
    summary="Update device metadata",
)
async def update_device(
    serial: str,
    req: DeviceUpdateRequest,
    db: AsyncSession = Depends(get_db),
    _current: str = Depends(get_current_serial),
) -> DeviceSchema:
    """
    Update mutable device fields (device_name, metadata).

    Only non-None fields in the request body are applied.
    """
    device = await _get_device_or_404(serial, db)

    if req.device_name is not None:
        device.device_name = req.device_name

    if req.metadata is not None:
        # Merge provided metadata into existing metadata_ JSONB,
        # preserving internal keys like api_key_hash.
        existing = device.metadata_ or {}
        existing.update(req.metadata)
        device.metadata_ = existing

    await db.flush()

    return DeviceSchema.model_validate(device)


@router.delete(
    "/{serial}",
    response_model=MessageResponse,
    status_code=status.HTTP_200_OK,
    summary="Deactivate device (admin only)",
)
async def deactivate_device(
    serial: str,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(require_admin),
) -> MessageResponse:
    """Soft-delete a device. Sets is_online=False and status='offline'. Requires admin key."""
    device = await _get_device_or_404(serial, db)
    device.is_online = False
    device.status = "offline"
    await db.flush()
    return MessageResponse(message=f"Device {serial} deactivated")
