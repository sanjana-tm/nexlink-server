"""
NexLink Server -- Screenshot API Endpoints
============================================
POST   /api/v1/devices/{serial}/screenshots/capture       -- trigger capture via WS
POST   /api/v1/devices/{serial}/screenshots/{id}/upload   -- agent uploads the PNG/JPEG
GET    /api/v1/devices/{serial}/screenshots/{id}/image    -- serve the image file
GET    /api/v1/devices/{serial}/screenshots               -- paginated history
GET    /api/v1/devices/{serial}/screenshots/latest        -- latest metadata
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, Query, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import func, select, update
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
from server.ws.manager import connection_manager

logger = logging.getLogger(__name__)
router = APIRouter(tags=["screenshots"])

# Screenshots live under data/screenshots/<serial>/<id>.jpg
_SCREENSHOT_DIR = Path(os.getenv("SCREENSHOT_DIR", "data/screenshots"))


def _screenshot_path(serial: str, screenshot_id: int, fmt: str = "jpg") -> Path:
    d = _SCREENSHOT_DIR / serial
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{screenshot_id}.{fmt}"


async def _verify_device(serial: str, db: AsyncSession) -> Device:
    result = await db.execute(select(Device).where(Device.serial_number == serial))
    device = result.scalar_one_or_none()
    if not device:
        raise DeviceNotFoundError(f"Device {serial} not found")
    return device


# ── Capture ───────────────────────────────────────────────────────────────────

@router.post(
    "/devices/{serial}/screenshots/capture",
    response_model=ScreenshotSchema,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Request screenshot capture",
)
async def capture_screenshot(
    serial: str,
    req: ScreenshotCaptureRequest | None = None,
    db: AsyncSession = Depends(get_db),
    current_serial: str = Depends(get_current_serial),
) -> ScreenshotSchema:
    await _verify_device(serial, db)
    if req is None:
        req = ScreenshotCaptureRequest()

    now = datetime.now(timezone.utc)
    screenshot = Screenshot(
        serial_number=serial,
        format="jpeg",  # agent always uploads JPEG
        requested_by=current_serial,
        captured_at=now,
        file_path="pending",  # updated on upload
    )
    db.add(screenshot)
    await db.flush()
    await db.commit()

    # Tell the agent to capture + upload back to us
    sent = await connection_manager.send(serial, {
        "type": "screenshot.capture",
        "screenshot_id": screenshot.id,
        "quality": req.quality,
    })

    if not sent:
        logger.warning("screenshot.capture sent to offline device %s (id=%d) — will complete when online", serial, screenshot.id)

    logger.info("Screenshot %d requested for device %s (ws_sent=%s)", screenshot.id, serial, sent)
    return ScreenshotSchema.model_validate(screenshot)


# ── Agent upload ──────────────────────────────────────────────────────────────

@router.post(
    "/devices/{serial}/screenshots/{screenshot_id}/upload",
    response_model=ScreenshotSchema,
    summary="Agent uploads captured image",
    description="Called by the Android/ADB agent to deliver the captured JPEG.",
)
async def upload_screenshot(
    serial: str,
    screenshot_id: int,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    _auth: str = Depends(get_current_serial),
) -> ScreenshotSchema:
    # Verify record belongs to this device
    result = await db.execute(
        select(Screenshot).where(
            Screenshot.id == screenshot_id,
            Screenshot.serial_number == serial,
        )
    )
    record = result.scalar_one_or_none()
    if not record:
        raise DeviceNotFoundError(f"Screenshot {screenshot_id} not found for device {serial}")

    # Save file to disk
    img_path = _screenshot_path(serial, screenshot_id, "jpg")
    content = await file.read()
    img_path.write_bytes(content)

    # Detect dimensions from JPEG header (cheap, no PIL needed)
    width, height = _jpeg_dimensions(content)

    # Update DB record
    await db.execute(
        update(Screenshot)
        .where(Screenshot.id == screenshot_id)
        .values(
            file_path=str(img_path),
            file_size_bytes=len(content),
            width=width,
            height=height,
            format="jpeg",
        )
    )
    await db.commit()
    await db.refresh(record)

    logger.info("Screenshot %d uploaded for device %s (%dx%d, %d bytes)", screenshot_id, serial, width or 0, height or 0, len(content))
    return ScreenshotSchema.model_validate(record)


def _jpeg_dimensions(data: bytes) -> tuple[int | None, int | None]:
    """Extract width/height from JPEG SOF marker. Returns (None, None) on failure."""
    try:
        i = 0
        while i < len(data) - 9:
            if data[i] != 0xFF:
                break
            marker = data[i + 1]
            if marker in (0xC0, 0xC1, 0xC2):  # SOF0/SOF1/SOF2
                height = (data[i + 5] << 8) | data[i + 6]
                width = (data[i + 7] << 8) | data[i + 8]
                return width, height
            seg_len = (data[i + 2] << 8) | data[i + 3]
            i += 2 + seg_len
    except Exception:
        pass
    return None, None


# ── Serve image ───────────────────────────────────────────────────────────────

@router.get(
    "/devices/{serial}/screenshots/{screenshot_id}/image",
    summary="Serve screenshot image",
    response_class=FileResponse,
)
async def get_screenshot_image(
    serial: str,
    screenshot_id: int,
    db: AsyncSession = Depends(get_db),
    _auth: str = Depends(get_current_serial),
) -> FileResponse:
    result = await db.execute(
        select(Screenshot).where(
            Screenshot.id == screenshot_id,
            Screenshot.serial_number == serial,
        )
    )
    record = result.scalar_one_or_none()
    if not record:
        raise DeviceNotFoundError(f"Screenshot {screenshot_id} not found for device {serial}")

    img_path = Path(record.file_path) if record.file_path != "pending" else None

    # Fall back: check the standard location even if file_path is stale
    if not img_path or not img_path.exists():
        img_path = _screenshot_path(serial, screenshot_id, "jpg")

    if not img_path.exists():
        raise DeviceNotFoundError(f"Screenshot {screenshot_id} image not yet available")

    return FileResponse(
        path=str(img_path),
        media_type="image/jpeg",
        filename=f"screenshot_{screenshot_id}.jpg",
    )


# ── List / Latest ─────────────────────────────────────────────────────────────

@router.get(
    "/devices/{serial}/screenshots",
    response_model=ScreenshotListResponse,
    summary="Screenshot history",
)
async def list_screenshots(
    serial: str,
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _auth: str = Depends(get_current_serial),
) -> ScreenshotListResponse:
    count_result = await db.execute(
        select(func.count()).select_from(Screenshot).where(Screenshot.serial_number == serial)
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
    summary="Latest screenshot metadata",
)
async def get_latest_screenshot(
    serial: str,
    db: AsyncSession = Depends(get_db),
    _auth: str = Depends(get_current_serial),
) -> ScreenshotSchema:
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
