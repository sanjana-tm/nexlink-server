"""
NexLink Server — Command Pydantic Schemas
==========================================
Request/response schemas for remote ADB/shell command execution.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class CommandExecuteRequest(BaseModel):
    """Request to execute a shell command on a device."""

    command: str
    timeout_seconds: float = Field(default=30.0, ge=1.0, le=300.0)


class CommandResultSchema(BaseModel):
    """Full command execution result record."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    serial_number: str
    command: str
    output: str | None = None
    exit_code: int | None = None
    timeout_seconds: float | None = None
    duration_seconds: float | None = None
    requested_by: str | None = None
    executed_at: datetime
    completed_at: datetime | None = None


class CommandHistoryResponse(BaseModel):
    """Paginated command execution history."""

    commands: list[CommandResultSchema]
    total: int
