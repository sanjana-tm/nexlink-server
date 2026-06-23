"""
NexLink Server -- Automation API Endpoints
============================================
POST /api/v1/devices/{serial}/automation       -- queue an automation run
GET  /api/v1/devices/{serial}/automation       -- automation history for device
GET  /api/v1/automation/{run_id}               -- run detail by UUID
POST /api/v1/automation/{run_id}/cancel        -- cancel a queued/running run

Identity:
  SERIAL_NUMBER in the URL path is the permanent device identifier.
  run_id is a UUID v4 assigned at queue time.

Lifecycle: queued -> running -> passed / failed / error / timeout / cancelled
"""
from __future__ import annotations

import logging
import uuid as uuid_mod
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from server.api.deps import get_current_serial, get_db
from server.core.exceptions import DeviceNotFoundError
from server.db.models.automation import AutomationRun
from server.db.models.device import Device
from server.schemas.automation import (
    AutomationListResponse,
    AutomationRunSchema,
    CreateAutomationRequest,
)
from server.schemas.common import MessageResponse

logger = logging.getLogger(__name__)
router = APIRouter(tags=["automation"])


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
    "/devices/{serial}/automation",
    response_model=AutomationRunSchema,
    status_code=status.HTTP_201_CREATED,
    summary="Queue automation run",
    description="""
Queue a new automation run targeting the specified device.

The run enters the 'queued' state. The automation engine will pick it up
and dispatch it to the agent when resources are available.
    """,
)
async def queue_automation_run(
    serial: str,
    req: CreateAutomationRequest,
    db: AsyncSession = Depends(get_db),
    _auth: str = Depends(get_current_serial),
) -> AutomationRunSchema:
    """Queue a new automation run for a device."""
    await _verify_device(serial, db)

    run = AutomationRun(
        serial_number=serial,
        name=req.name,
        test_type=req.test_type,
        test_config=req.test_config,
        priority=req.priority,
        status="queued",
    )
    db.add(run)
    await db.flush()

    logger.info(
        "Automation queued: serial=%s name=%s id=%s",
        serial,
        req.name,
        run.id,
    )

    return AutomationRunSchema.model_validate(run)


@router.get(
    "/devices/{serial}/automation",
    response_model=AutomationListResponse,
    summary="Automation history",
    description="Return paginated automation run history for a device (newest first).",
)
async def list_automation_runs(
    serial: str,
    status_filter: str | None = Query(
        None, alias="status",
        description="Filter by status: queued, running, passed, failed, error, timeout, cancelled",
    ),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _auth: str = Depends(get_current_serial),
) -> AutomationListResponse:
    """Return paginated automation run history for a device."""
    query = select(AutomationRun).where(AutomationRun.serial_number == serial)
    count_query = (
        select(func.count())
        .select_from(AutomationRun)
        .where(AutomationRun.serial_number == serial)
    )

    if status_filter:
        query = query.where(AutomationRun.status == status_filter)
        count_query = count_query.where(AutomationRun.status == status_filter)

    total = (await db.execute(count_query)).scalar() or 0

    query = query.order_by(AutomationRun.queued_at.desc())
    query = query.offset((page - 1) * per_page).limit(per_page)

    result = await db.execute(query)
    runs = result.scalars().all()

    return AutomationListResponse(
        runs=[AutomationRunSchema.model_validate(r) for r in runs],
        total=total,
    )


@router.get(
    "/automation/{run_id}",
    response_model=AutomationRunSchema,
    summary="Automation run detail",
    description="Return the full detail for a specific automation run by UUID.",
)
async def get_automation_run(
    run_id: uuid_mod.UUID,
    db: AsyncSession = Depends(get_db),
    _auth: str = Depends(get_current_serial),
) -> AutomationRunSchema:
    """Return a specific automation run by ID."""
    result = await db.execute(
        select(AutomationRun).where(AutomationRun.id == run_id)
    )
    run = result.scalar_one_or_none()
    if not run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Automation run {run_id} not found",
        )

    return AutomationRunSchema.model_validate(run)


@router.post(
    "/automation/{run_id}/cancel",
    response_model=MessageResponse,
    summary="Cancel automation run",
    description="""
Cancel a queued or running automation run. Only runs in 'queued' or
'running' state can be cancelled.
    """,
)
async def cancel_automation_run(
    run_id: uuid_mod.UUID,
    db: AsyncSession = Depends(get_db),
    current_serial: str = Depends(get_current_serial),
) -> MessageResponse:
    """Cancel a queued or running automation run."""
    result = await db.execute(
        select(AutomationRun).where(AutomationRun.id == run_id)
    )
    run = result.scalar_one_or_none()
    if not run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Automation run {run_id} not found",
        )

    if run.status not in ("queued", "running"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot cancel run in '{run.status}' state. "
            "Only 'queued' or 'running' runs can be cancelled.",
        )

    now = datetime.now(timezone.utc)
    run.status = "cancelled"
    run.ended_at = now
    if run.started_at:
        run.duration_seconds = (now - run.started_at).total_seconds()
    await db.flush()

    logger.info("Automation run cancelled: id=%s by=%s", run_id, current_serial)

    return MessageResponse(message=f"Automation run {run_id} cancelled")
