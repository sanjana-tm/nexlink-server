"""
NexLink Server -- Command Execution API Endpoints
==================================================
POST /api/v1/devices/{serial}/commands  -- execute shell command on device
GET  /api/v1/devices/{serial}/commands  -- command history (paginated)

Identity:
  SERIAL_NUMBER in the URL path is the permanent device identifier.

Commands are dispatched to the agent via the WebSocket gateway and
recorded in the command_history table for audit.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from server.api.deps import get_current_serial, get_db
from server.core.exceptions import DeviceNotFoundError
from server.db.models.command import CommandHistory
from server.db.models.device import Device
from server.schemas.command import (
    CommandExecuteRequest,
    CommandHistoryResponse,
    CommandResultSchema,
)
from server.ws.manager import connection_manager

logger = logging.getLogger(__name__)
router = APIRouter(tags=["commands"])


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
    "/devices/{serial}/commands",
    response_model=CommandResultSchema,
    status_code=status.HTTP_201_CREATED,
    summary="Execute shell command",
    description="""
Submit a shell command for execution on the device.

The command is recorded in the audit trail immediately. The actual
execution is dispatched via the WebSocket gateway; the response
contains the initial record with output and exit_code as null
until the agent reports back.
    """,
)
async def execute_command(
    serial: str,
    req: CommandExecuteRequest,
    db: AsyncSession = Depends(get_db),
    current_serial: str = Depends(get_current_serial),
) -> CommandResultSchema:
    """Submit a shell command for execution on the device."""
    await _verify_device(serial, db)

    # Get the WebSocket gateway to send command to agent
    # For now, create a "pending request" in DB and assume agent will process it
    # The WebSocket gateway will route the command
    now = datetime.now(timezone.utc)
    record = CommandHistory(
        serial_number=serial,
        command=req.command,
        timeout_seconds=req.timeout_seconds,
        requested_by=current_serial,
        executed_at=now,
    )
    db.add(record)
    await db.flush()

    logger.info(
        "Command queued: serial=%s cmd=%s",
        serial,
        req.command[:60],
    )

    return CommandResultSchema.model_validate(record)


@router.post(
    "/devices/{serial}/update-agent",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Push APK update to device",
    description="Send an apk.update command to the device over WebSocket. The device downloads the APK from the given URL and launches the system installer.",
)
async def update_agent(
    serial: str,
    apk_url: str = Query(..., description="Public HTTPS URL of the APK to install"),
    db: AsyncSession = Depends(get_db),
    _auth: str = Depends(get_current_serial),
) -> dict:
    """Push a remote APK self-update to the device."""
    await _verify_device(serial, db)

    sent = await connection_manager.send(serial, {
        "type": "apk.update",
        "payload": {"url": apk_url},
    })

    if not sent:
        from fastapi import HTTPException
        raise HTTPException(status_code=503, detail="Device not connected")

    logger.info("apk.update dispatched to %s url=%s", serial, apk_url)
    return {"status": "dispatched", "serial": serial, "url": apk_url}


@router.get(
    "/devices/{serial}/commands",
    response_model=CommandHistoryResponse,
    summary="Command history",
    description="Return paginated command history for a device (newest first).",
)
async def list_commands(
    serial: str,
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    _auth: str = Depends(get_current_serial),
) -> CommandHistoryResponse:
    """Return paginated command history for a device."""
    count_result = await db.execute(
        select(func.count())
        .select_from(CommandHistory)
        .where(CommandHistory.serial_number == serial)
    )
    total = count_result.scalar() or 0

    result = await db.execute(
        select(CommandHistory)
        .where(CommandHistory.serial_number == serial)
        .order_by(CommandHistory.executed_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
    )
    rows = result.scalars().all()

    return CommandHistoryResponse(
        commands=[CommandResultSchema.model_validate(r) for r in rows],
        total=total,
    )
