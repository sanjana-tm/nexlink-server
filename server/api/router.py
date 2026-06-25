"""NexLink Server -- Main API Router"""
from __future__ import annotations

from fastapi import APIRouter

from server.api.v1 import (
    alerts,
    apk,
    auth,
    automation,
    commands,
    devices,
    health,
    heartbeat,
    logs,
    screenshots,
    xml,
)

api_router = APIRouter(prefix="/api/v1")

api_router.include_router(auth.router)
api_router.include_router(apk.router)
api_router.include_router(devices.router)
api_router.include_router(heartbeat.router)
api_router.include_router(screenshots.router)
api_router.include_router(xml.router)
api_router.include_router(commands.router)
api_router.include_router(automation.router)
api_router.include_router(health.router)
api_router.include_router(logs.router)
api_router.include_router(alerts.router)
