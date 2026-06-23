"""
NexLink Server — Screenshot Pydantic Schemas
=============================================
Request/response schemas for screenshot capture and retrieval endpoints.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ScreenshotCaptureRequest(BaseModel):
    """Parameters for requesting a screenshot capture."""

    format: str = "png"  # png or jpeg
    quality: int = Field(default=80, ge=10, le=100)


class ScreenshotSchema(BaseModel):
    """Full screenshot record representation."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    serial_number: str
    file_path: str
    file_size_bytes: int | None = None
    width: int | None = None
    height: int | None = None
    format: str = "png"
    requested_by: str | None = None
    captured_at: datetime


class ScreenshotListResponse(BaseModel):
    """Paginated screenshot list."""

    screenshots: list[ScreenshotSchema]
    total: int
