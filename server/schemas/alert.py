"""
NexLink Server — Alert Pydantic Schemas
========================================
Request/response schemas for device alert management.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class AlertSchema(BaseModel):
    """Full alert record representation."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    serial_number: str | None = None
    severity: str
    category: str | None = None
    title: str
    message: str | None = None
    is_resolved: bool = False
    resolved_at: datetime | None = None
    resolved_by: str | None = None
    created_at: datetime


class AlertListResponse(BaseModel):
    """Paginated alert list."""

    alerts: list[AlertSchema]
    total: int


class ResolveAlertRequest(BaseModel):
    """Request to resolve an alert."""

    resolved_by: str | None = None
