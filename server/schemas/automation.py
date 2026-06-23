"""
NexLink Server — Automation Pydantic Schemas
=============================================
Request/response schemas for test automation execution.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class CreateAutomationRequest(BaseModel):
    """Request to create and queue an automation run."""

    name: str
    test_type: str  # appium, pytest, shell
    test_config: dict = Field(default_factory=dict)
    priority: int = Field(default=5, ge=1, le=10)


class AutomationRunSchema(BaseModel):
    """Full automation run record."""

    model_config = ConfigDict(from_attributes=True)

    id: str  # UUID as string
    serial_number: str
    name: str
    test_type: str
    test_config: dict = {}
    status: str = "queued"
    priority: int = 5
    total_tests: int = 0
    passed_tests: int = 0
    failed_tests: int = 0
    skipped_tests: int = 0
    result_summary: dict = {}
    error_message: str | None = None
    queued_at: datetime
    started_at: datetime | None = None
    ended_at: datetime | None = None
    duration_seconds: float | None = None


class AutomationListResponse(BaseModel):
    """Paginated automation run list."""

    runs: list[AutomationRunSchema]
    total: int
