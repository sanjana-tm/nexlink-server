"""
NexLink Server — Automation Run ORM Model (serial-number-keyed)
================================================================
Table: automation_runs

Tracks distributed test automation executions against devices.

Lifecycle:
  queued -> running -> passed / failed / error / timeout / cancelled
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from server.db.compat import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from server.db.base import Base


class AutomationRun(Base):
    """
    Top-level automation execution record.

    One row per test run. An execution targets ONE device (identified by
    serial_number). For parallel runs across multiple devices, create one
    AutomationRun per device.

    Status values: queued, running, passed, failed, error, timeout, cancelled.
    """
    __tablename__ = "automation_runs"
    __table_args__ = (
        Index(
            "ix_automation_runs_serial_status_queued",
            "serial_number",
            "status",
            "queued_at",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )

    serial_number: Mapped[Optional[str]] = mapped_column(
        String(50),
        ForeignKey("devices.serial_number", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # ── What to run ───────────────────────────────────────────────────────────
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    test_type: Mapped[str] = mapped_column(
        String(50), nullable=False, default="appium",
        comment="appium | pytest | shell",
    )
    test_config: Mapped[Optional[dict]] = mapped_column(
        JSONB, default=dict, nullable=True,
    )

    # ── Execution lifecycle ───────────────────────────────────────────────────
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="queued",
        comment="queued | running | passed | failed | error | timeout | cancelled",
    )
    priority: Mapped[int] = mapped_column(
        Integer, nullable=False, default=5,
        comment="1=highest, 10=lowest. Default 5 (normal)",
    )

    # ── Results ───────────────────────────────────────────────────────────────
    total_tests: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    passed_tests: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_tests: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    skipped_tests: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    result_summary: Mapped[Optional[dict]] = mapped_column(
        JSONB, default=dict, nullable=True,
    )
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # ── Timestamps ────────────────────────────────────────────────────────────
    queued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    started_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    ended_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    duration_seconds: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    def __repr__(self) -> str:
        return (
            f"<AutomationRun id={self.id} serial={self.serial_number} "
            f"name={self.name!r} status={self.status}>"
        )
