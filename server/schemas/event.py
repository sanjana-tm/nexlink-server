"""
NexLink Server — Event Pydantic Schemas
=========================================
Request/response schemas for device event log endpoints.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class DeviceEventSchema(BaseModel):
    """Device event record representation."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    serial_number: str | None = None
    event_type: str
    severity: str = "info"
    message: str | None = None
    details: dict = {}
    created_at: datetime


class EventListResponse(BaseModel):
    """Paginated event list."""

    events: list[DeviceEventSchema]
    total: int
