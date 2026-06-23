"""
NexLink Server -- Common Pydantic Schemas
=========================================
Shared types and response envelopes used across all API endpoints.

API Response Convention:
  All responses are wrapped in a standard envelope:
    {
      "success": true,
      "data": { ... },
      "meta": { "total": 100, "page": 1, "per_page": 20 }
    }

  Errors use a different envelope (see core/exceptions.py):
    {
      "error": "DEVICE_NOT_FOUND",
      "detail": "...",
      "path": "/api/v1/devices/..."
    }
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class PaginationMeta(BaseModel):
    """Pagination metadata included in list responses."""
    total: int
    page: int = 1
    per_page: int = 20
    total_pages: int


class SuccessResponse(BaseModel, Generic[T]):
    """
    Standard success response envelope.

    Usage:
        return SuccessResponse(data=device_schema)
        return SuccessResponse(data=devices_list, meta=PaginationMeta(...))
    """
    success: bool = True
    data: T
    meta: PaginationMeta | None = None


class MessageResponse(BaseModel):
    """Simple message response for operations that don't return data."""
    success: bool = True
    message: str


class HealthResponse(BaseModel):
    """Server health check response."""
    status: str = "ok"
    version: str = "2.0.0"
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    database: str = "ok"
    uptime_seconds: float | None = None


class PaginationParams(BaseModel):
    """Common query parameters for paginated list endpoints."""
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


class PaginatedResponse(BaseModel, Generic[T]):
    """Generic paginated response envelope."""
    items: list[Any] = []
    total: int = 0
    page: int = 1
    page_size: int = 20
    pages: int = 0


# Keep the old name as an alias for backward compatibility
PaginationQuery = PaginationParams
