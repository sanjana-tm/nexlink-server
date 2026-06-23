"""
NexLink Server — Heartbeat Pydantic Schemas
============================================
Request/response schemas for heartbeat endpoints.

The agent sends periodic heartbeats containing device identity, system
metrics, and connectivity info.  The server responds with a health score
and operational status.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class HeartbeatRequest(BaseModel):
    """Heartbeat payload sent by agent to POST /api/v1/heartbeat."""

    model_config = ConfigDict(extra="allow")

    serial_number: str
    device_name: str | None = None
    model: str | None = None
    manufacturer: str | None = None
    android_version: str | None = None
    sdk_version: int | None = None
    agent_version: str | None = None
    uptime_seconds: int | None = None
    cpu_percent: float | None = None
    memory_percent: float | None = None
    storage_percent: float | None = None
    screen_status: str | None = None
    battery_level: int | None = None
    wifi_ssid: str | None = None
    wifi_signal_dbm: int | None = None
    ip_address: str | None = None
    timestamp: datetime | None = None


class HeartbeatResponse(BaseModel):
    """Server response to a heartbeat POST."""

    success: bool = True
    serial_number: str | None = None
    health_score: int | None = None
    status: str = "ok"


class HeartbeatRecord(BaseModel):
    """Heartbeat record returned by history/latest queries."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    serial_number: str
    cpu_percent: float | None = None
    memory_percent: float | None = None
    storage_percent: float | None = None
    screen_status: str | None = None
    uptime_seconds: int | None = None
    received_at: datetime


class HeartbeatHistoryResponse(BaseModel):
    """Paginated heartbeat history for a device."""

    items: list[HeartbeatRecord]
    total: int
