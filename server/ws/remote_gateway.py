"""
NexLink Server — Remote Control Viewer WebSocket Gateway
=========================================================
Browser viewer endpoint for watching and controlling a device in real-time.

Connection URL:
  ws://server:9000/ws/v1/remote/{serial}?token=<JWT>

What happens here:
  1. Browser connects with valid JWT
  2. Viewer is registered in ViewerManager for that device's serial
  3. A sender asyncio task drains the per-viewer frame queue → browser
  4. The main receive loop accepts input events from the browser → forwards
     them as stream.input to the device via the agent WebSocket

Messages TO browser (server → viewer):
  {"type": "stream.frame",  "serial": "...", "payload": {"data": "<b64_jpeg>", "width": W, "height": H, "frame_id": N}}
  {"type": "stream.ready",  "serial": "..."}
  {"type": "stream.stopped","serial": "..."}
  {"type": "error",         "message": "..."}

Messages FROM browser (viewer → server):
  {"type": "stream.start",  "max_fps": 2, "quality": 70}  → forwarded to device
  {"type": "stream.stop"}                                   → forwarded to device
  {"type": "stream.input",  "payload": {"event_type": "tap", "x": 0.5, "y": 0.3}}
"""
from __future__ import annotations

import asyncio
import logging
import uuid

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from server.core.security import decode_token
from server.core.exceptions import TokenExpiredError, TokenInvalidError
from server.ws.manager import connection_manager
from server.ws.viewer_manager import viewer_manager, ViewerConnection

logger = logging.getLogger(__name__)
router = APIRouter()


async def _sender_task(viewer: ViewerConnection) -> None:
    """Drain the viewer's frame queue and send to the browser WebSocket."""
    while True:
        try:
            message = await viewer.queue.get()
        except asyncio.CancelledError:
            break

        if message is None:
            break  # Sentinel: viewer was removed

        try:
            await viewer.ws.send_json(message)
        except Exception:
            break  # WebSocket closed; stop trying

    logger.debug("Sender task ended for viewer %s", viewer.viewer_id[:8])


@router.websocket("/ws/v1/remote/{serial}")
async def remote_viewer_endpoint(
    websocket: WebSocket,
    serial: str,
    token: str = Query(..., description="JWT access token"),
) -> None:
    """
    Browser connects here to view and control a remote device.
    Auth: same JWT as used for REST API calls.
    """
    # ── Auth ─────────────────────────────────────────────────────────────────
    try:
        decode_token(token, expected_type="access")
    except (TokenExpiredError, TokenInvalidError) as e:
        await websocket.close(code=4001, reason=str(e))
        return
    except Exception:
        await websocket.close(code=4002, reason="Auth error")
        return

    await websocket.accept()

    viewer_id = str(uuid.uuid4())
    viewer = ViewerConnection(ws=websocket, viewer_id=viewer_id)
    viewer_manager.add_viewer(serial, viewer)

    # Acknowledge connection
    await websocket.send_json({"type": "viewer.connected", "serial": serial, "viewer_id": viewer_id})

    # Start background sender task
    sender = asyncio.create_task(_sender_task(viewer))

    try:
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type", "")

            if msg_type == "stream.start":
                # Tell device to start sending frames
                params = {"max_fps": data.get("max_fps", 2), "quality": data.get("quality", 65)}
                sent = await connection_manager.send(serial, {"type": "stream.start", "payload": params})
                if not sent:
                    await websocket.send_json({"type": "error", "message": "Device offline or not connected"})

            elif msg_type == "stream.stop":
                # Tell device to stop sending frames
                await connection_manager.send(serial, {"type": "stream.stop", "payload": {}})

            elif msg_type == "stream.input":
                # Forward input event to device as-is
                await connection_manager.send(serial, {
                    "type": "stream.input",
                    "payload": data.get("payload", {}),
                })

    except WebSocketDisconnect:
        logger.info("Viewer %s disconnected from device %s", viewer_id[:8], serial[:12])
    except Exception as e:
        logger.error("Viewer WS error: %s", e)
    finally:
        sender.cancel()
        viewer_manager.remove_viewer(serial, viewer)
        # Ask device to stop streaming if this was the last viewer
        if viewer_manager.viewer_count(serial) == 0:
            await connection_manager.send(serial, {"type": "stream.stop", "payload": {}})
