"""
NexLink Server -- Log Collection API Endpoints
===============================================
POST /api/v1/devices/{serial}/logs/collect  -- trigger logcat collection
GET  /api/v1/devices/{serial}/logs          -- collected log history

Identity:
  SERIAL_NUMBER in the URL path is the permanent device identifier.

Log collection is implemented as shell commands (logcat) dispatched to
the agent. The command and its output are stored in the command_history
table for audit and retrieval.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from server.api.deps import get_current_serial, get_db
from server.core.exceptions import DeviceNotFoundError
from server.db.models.command import CommandHistory
from server.db.models.device import Device
from server.schemas.command import CommandHistoryResponse, CommandResultSchema
from server.schemas.common import MessageResponse

logger = logging.getLogger(__name__)
router = APIRouter(tags=["logs"])


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
    "/devices/{serial}/logs/collect",
    response_model=MessageResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Trigger log collection",
    description="""
Trigger logcat log collection from the device agent.

The command is queued for execution on the device via the WebSocket
gateway. The agent will capture system logs and store the output.
    """,
)
async def collect_logs(
    serial: str,
    db: AsyncSession = Depends(get_db),
    auth_serial: str = Depends(get_current_serial),
) -> MessageResponse:
    """Trigger log collection from a device agent."""
    await _verify_device(serial, db)

    # Get the WebSocket gateway to send command to agent
    # For now, create a "pending request" in DB and assume agent will process it
    # The WebSocket gateway will route the command
    command_text = "logcat -t 500"

    record = CommandHistory(
        serial_number=serial,
        command=command_text,
        timeout_seconds=30.0,
        requested_by=auth_serial,
    )
    db.add(record)
    await db.flush()

    logger.info("Log collection queued: serial=%s cmd=%s", serial, command_text)

    return MessageResponse(
        message=f"Log collection requested for {serial}. "
        "The agent will capture and upload logs via the WebSocket gateway.",
    )


@router.get(
    "/devices/{serial}/logs",
    response_model=CommandHistoryResponse,
    summary="Get collected logs",
    description="Return log collection commands and their output for a device (newest first). "
    "Only returns commands that start with 'logcat'.",
)
async def get_device_logs(
    serial: str,
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _auth: str = Depends(get_current_serial),
) -> CommandHistoryResponse:
    """Return log collection history for a device."""
    base_filter = (
        (CommandHistory.serial_number == serial)
        & (CommandHistory.command.like("logcat%"))
    )

    count_result = await db.execute(
        select(func.count())
        .select_from(CommandHistory)
        .where(base_filter)
    )
    total = count_result.scalar() or 0

    result = await db.execute(
        select(CommandHistory)
        .where(base_filter)
        .order_by(CommandHistory.executed_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
    )
    rows = result.scalars().all()

    return CommandHistoryResponse(
        items=[CommandResultSchema.model_validate(r) for r in rows],
        total=total,
    )
