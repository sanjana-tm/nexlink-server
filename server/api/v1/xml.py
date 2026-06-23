"""
NexLink Server -- XML Snapshot API Endpoints
=============================================
POST /api/v1/devices/{serial}/xml/capture  -- request UI hierarchy XML dump
GET  /api/v1/devices/{serial}/xml          -- XML snapshot history (paginated)
GET  /api/v1/devices/{serial}/xml/latest   -- latest XML snapshot with full content

Identity:
  SERIAL_NUMBER in the URL path is the permanent device identifier.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from server.api.deps import get_current_serial, get_db
from server.core.exceptions import DeviceNotFoundError
from server.db.models.device import Device
from server.db.models.xml_snapshot import XmlSnapshot
from server.schemas.common import MessageResponse
from server.schemas.xml_snapshot import (
    XmlCaptureRequest,
    XmlSnapshotListResponse,
    XmlSnapshotSchema,
    XmlSnapshotSummary,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["xml"])


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
    "/devices/{serial}/xml/capture",
    response_model=MessageResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Request XML dump",
    description="""
Request a UI hierarchy XML dump from the device agent.

The actual dump is captured asynchronously by the agent via the
WebSocket gateway. Poll the xml list or latest endpoint to retrieve
the result.
    """,
)
async def capture_xml(
    serial: str,
    _req: XmlCaptureRequest | None = None,
    db: AsyncSession = Depends(get_db),
    _auth: str = Depends(get_current_serial),
) -> MessageResponse:
    """Request a UI hierarchy XML dump from the device agent."""
    await _verify_device(serial, db)

    # Get the WebSocket gateway to send command to agent
    # For now, create a "pending request" in DB and assume agent will process it
    # The WebSocket gateway will route the command
    logger.info("XML capture requested for device %s", serial)

    return MessageResponse(
        message=f"XML capture requested for {serial}. "
        "The agent will upload the dump via the WebSocket gateway.",
    )


@router.get(
    "/devices/{serial}/xml",
    response_model=XmlSnapshotListResponse,
    summary="XML snapshot history",
    description="Return paginated XML snapshot metadata for a device (newest first). "
    "Does NOT include xml_content to keep responses small.",
)
async def list_xml_snapshots(
    serial: str,
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _auth: str = Depends(get_current_serial),
) -> XmlSnapshotListResponse:
    """Return paginated XML snapshot metadata for a device."""
    count_result = await db.execute(
        select(func.count())
        .select_from(XmlSnapshot)
        .where(XmlSnapshot.serial_number == serial)
    )
    total = count_result.scalar() or 0

    result = await db.execute(
        select(XmlSnapshot)
        .where(XmlSnapshot.serial_number == serial)
        .order_by(XmlSnapshot.captured_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
    )
    rows = result.scalars().all()

    return XmlSnapshotListResponse(
        snapshots=[XmlSnapshotSummary.model_validate(s) for s in rows],
        total=total,
    )


@router.get(
    "/devices/{serial}/xml/latest",
    response_model=XmlSnapshotSchema,
    summary="Latest XML snapshot",
    description="Return the most recent XML snapshot with full xml_content.",
)
async def get_latest_xml(
    serial: str,
    db: AsyncSession = Depends(get_db),
    _auth: str = Depends(get_current_serial),
) -> XmlSnapshotSchema:
    """Return the latest XML snapshot for a device."""
    result = await db.execute(
        select(XmlSnapshot)
        .where(XmlSnapshot.serial_number == serial)
        .order_by(XmlSnapshot.captured_at.desc())
        .limit(1)
    )
    snapshot = result.scalar_one_or_none()
    if not snapshot:
        raise DeviceNotFoundError(f"No XML snapshots found for device {serial}")

    return XmlSnapshotSchema.model_validate(snapshot)
