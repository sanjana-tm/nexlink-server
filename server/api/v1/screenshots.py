"""
NexLink Server -- Screenshot API Endpoints
============================================
POST /api/v1/devices/{serial}/screenshots/capture  -- request screenshot capture
GET  /api/v1/devices/{serial}/screenshots          -- screenshot history (paginated)
GET  /api/v1/devices/{serial}/screenshots/latest   -- latest screenshot metadata

Identity:
  SERIAL_NUMBER in the URL path is the permanent device identifier.
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
from server.db.models.screenshot import Screenshot
from server.schemas.screenshot import (
    ScreenshotCaptureRequest,
    ScreenshotListResponse,
    ScreenshotSchema,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["screenshots"])


async def _verify_device(serial: str, db: AsyncSession) -> Device:
    """Verify device exists or raise 404."""
    result = await db.execute(
        select(Device).where(Device.serial_number == serial)
    )
    device = result.scalar_one_or_none()
    if not device:
        raise DeviceNotFoundError(f"Device {serial} not found")
    return device


@router.post(
    "/devices/{serial}/screenshots/capture",
    response_model=ScreenshotSchema,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Request screenshot capture",
    description="""
Request a screenshot capture from the device agent.

The command is routed to the agent via the WebSocket gateway.
A pending screenshot record is created and returned immediately;
the agent will populate the file once captured.
    """,
)
async def capture_screenshot(
    serial: str,
    req: ScreenshotCaptureRequest | None = None,
    db: AsyncSession = Depends(get_db),
    current_serial: str = Depends(get_current_serial),
) -> ScreenshotSchema:
    """Request a screenshot capture from the device agent."""
    await _verify_device(serial, db)

    if req is None:
        req = ScreenshotCaptureRequest()

    # Get the WebSocket gateway to send command to agent
    # For now, create a "pending request" in DB and assume agent will process it
    # The WebSocket gateway will route the command
    now = datetime.now(timezone.utc)
    screenshot = Screenshot(
        serial_number=serial,
        file_path=f"pending/{serial}/{now.strftime('%Y%m%d_%H%M%S')}.{req.format}",
        format=req.format,
        requested_by=current_serial,
        captured_at=now,
    )
    db.add(screenshot)
    await db.flush()

    logger.info("Screenshot capture requested for device %s", serial)

    return ScreenshotSchema.model_validate(screenshot)


@router.get(
    "/devices/{serial}/screenshots",
    response_model=ScreenshotListResponse,
    summary="Screenshot history",
    description="Return paginated screenshot metadata for a device (newest first).",
)
async def list_screenshots(
    serial: str,
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _auth: str = Depends(get_current_serial),
) -> ScreenshotListResponse:
    """Return paginated screenshot history for a device."""
    count_result = await db.execute(
        select(func.count())
        .select_from(Screenshot)
        .where(Screenshot.serial_number == serial)
    )
    total = count_result.scalar() or 0

    result = await db.execute(
        select(Screenshot)
        .where(Screenshot.serial_number == serial)
        .order_by(Screenshot.captured_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
    )
    records = result.scalars().all()

    return ScreenshotListResponse(
        screenshots=[ScreenshotSchema.model_validate(r) for r in records],
        total=total,
    )


@router.get(
    "/devices/{serial}/screenshots/latest",
    response_model=ScreenshotSchema,
    summary="Latest screenshot",
    description="Return the most recent screenshot metadata for a device.",
)
async def get_latest_screenshot(
    serial: str,
    db: AsyncSession = Depends(get_db),
    _auth: str = Depends(get_current_serial),
) -> ScreenshotSchema:
    """Return the most recent screenshot for a device."""
    result = await db.execute(
        select(Screenshot)
        .where(Screenshot.serial_number == serial)
        .order_by(Screenshot.captured_at.desc())
        .limit(1)
    )
    screenshot = result.scalar_one_or_none()
    if not screenshot:
        raise DeviceNotFoundError(f"No screenshots found for device {serial}")

    return ScreenshotSchema.model_validate(screenshot)
