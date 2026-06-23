"""
NexLink Server — WebSocket Gateway
=====================================
Agent WebSocket connection endpoint.

Connection URL:
  ws://server:9000/ws/v1/connect?token=<JWT>&serial=<SERIAL_NUMBER>

Auth:
  JWT access token passed as query parameter.
  Serial number identifies the device.

Session Lifecycle:
  1. Validate JWT from ?token= query param
  2. Extract serial_number (from token subject or query param)
  3. Register WebSocket with ConnectionManager
  4. Mark device online in DB
  5. Send welcome message
  6. Loop: receive messages → dispatch to handlers
  7. On disconnect: mark offline, publish event
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from server.core.security import decode_token
from server.core.exceptions import TokenExpiredError, TokenInvalidError
from server.db.session import AsyncSessionFactory
from server.db.models.device import Device
from server.db.models.event import DeviceEvent
from server.services.event_bus import event_bus
from server.ws.manager import connection_manager
from server.ws.handlers import MessageDispatcher, build_connected_message

from sqlalchemy import update

logger = logging.getLogger(__name__)
router = APIRouter()


@router.websocket("/ws/v1/connect")
async def websocket_endpoint(
    websocket: WebSocket,
    token: str = Query(..., description="JWT access token"),
    serial: str = Query("", description="Device serial number"),
) -> None:
    """
    Agent WebSocket connection endpoint.

    Identifies devices by SERIAL_NUMBER (not UUID).
    """
    # ── Step 1: Authenticate ─────────────────────────────────────────────────
    try:
        payload = decode_token(token, expected_type="access")
        serial_number = payload.get("sub", serial)
        if not serial_number:
            await websocket.close(code=4001, reason="No serial number")
            return
    except (TokenExpiredError, TokenInvalidError) as e:
        logger.warning("WebSocket auth rejected: %s", e)
        await websocket.close(code=4001, reason=str(e))
        return
    except Exception as e:
        logger.error("WebSocket auth unexpected error: %s", e)
        await websocket.close(code=4002, reason="Auth error")
        return

    # ── Step 2: Accept + Register Connection ─────────────────────────────────
    await connection_manager.connect(
        device_id=serial_number,
        session_id=serial_number,  # session = serial for simplicity
        websocket=websocket,
    )

    # ── Step 3: Mark Device Online ───────────────────────────────────────────
    async with AsyncSessionFactory() as db:
        try:
            await db.execute(
                update(Device)
                .where(Device.serial_number == serial_number)
                .values(
                    is_online=True,
                    status="healthy",
                    updated_at=datetime.now(timezone.utc),
                )
            )
            await db.commit()
        except Exception as e:
            logger.error("Failed to mark device online: %s", e)

    # ── Step 4: Send Welcome Message ─────────────────────────────────────────
    await connection_manager.send(serial_number, build_connected_message(
        device_id=serial_number,
        session_id=serial_number,
    ))

    # ── Step 5: Publish Connected Event ──────────────────────────────────────
    await event_bus.publish(
        "device.connected",
        payload={"serial_number": serial_number},
        source_device_id=serial_number,
        session_id=serial_number,
    )

    logger.info("Agent connected: serial=%s", serial_number)

    # ── Step 6: Message Loop ─────────────────────────────────────────────────
    dispatcher = MessageDispatcher(
        manager=connection_manager,
        device_id=serial_number,
        session_id=serial_number,
    )

    disconnect_reason = "normal_close"

    try:
        while True:
            try:
                data = await websocket.receive_json()
                await dispatcher.dispatch(data)
            except ValueError as e:
                logger.warning("Bad JSON from %s: %s", serial_number, e)

    except WebSocketDisconnect as e:
        disconnect_reason = f"client_disconnect(code={e.code})"
        logger.info("Agent disconnected: serial=%s code=%s", serial_number, e.code)
    except Exception as e:
        disconnect_reason = f"error: {type(e).__name__}"
        logger.error("WebSocket error for %s: %s", serial_number, e, exc_info=True)

    # ── Step 7: Cleanup ──────────────────────────────────────────────────────
    await connection_manager.disconnect(serial_number)

    async with AsyncSessionFactory() as db:
        try:
            await db.execute(
                update(Device)
                .where(Device.serial_number == serial_number)
                .values(
                    is_online=False,
                    status="offline",
                    updated_at=datetime.now(timezone.utc),
                )
            )
            # Record disconnect event
            db.add(DeviceEvent(
                serial_number=serial_number,
                event_type="device.disconnected",
                severity="info",
                message=f"Agent disconnected: {disconnect_reason}",
            ))
            await db.commit()
        except Exception as e:
            logger.error("Cleanup error for %s: %s", serial_number, e)

    await event_bus.publish(
        "device.disconnected",
        payload={
            "serial_number": serial_number,
            "reason": disconnect_reason,
        },
        source_device_id=serial_number,
        session_id=serial_number,
    )

    logger.info("Agent session ended: serial=%s reason=%s", serial_number, disconnect_reason)
