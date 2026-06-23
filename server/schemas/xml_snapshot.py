"""
NexLink Server — XML Snapshot Pydantic Schemas
================================================
Request/response schemas for UI hierarchy (XML dump) capture and retrieval.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class XmlCaptureRequest(BaseModel):
    """Trigger a UI hierarchy XML capture — no parameters needed."""

    pass  # no params needed, just trigger


class XmlSnapshotSchema(BaseModel):
    """Full XML snapshot record, including the raw XML content."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    serial_number: str
    xml_content: str
    app_package: str | None = None
    activity: str | None = None
    node_count: int | None = None
    requested_by: str | None = None
    captured_at: datetime


class XmlSnapshotSummary(BaseModel):
    """Lightweight snapshot summary without xml_content for list views."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    serial_number: str
    app_package: str | None = None
    activity: str | None = None
    node_count: int | None = None
    captured_at: datetime


class XmlSnapshotListResponse(BaseModel):
    """Paginated XML snapshot list."""

    snapshots: list[XmlSnapshotSummary]
    total: int
