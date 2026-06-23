"""
NexLink Server — Alert ORM Model
==================================
Table: alerts

Actionable alerts raised when a device exceeds health thresholds,
loses connectivity, runs low on storage, or has an agent issue.

Alerts differ from events: an alert requires attention and can be
resolved (acknowledged). Events are informational and immutable.

Severity levels: warning, error, critical.
Categories: health, connectivity, storage, agent.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from server.db.base import Base


class Alert(Base):
    """
    Actionable device alert.

    Lifecycle:
      created (is_resolved=False) -> resolved (is_resolved=True, resolved_at set)

    Alerts are never deleted — resolved alerts remain for historical analysis.
    """
    __tablename__ = "alerts"
    __table_args__ = (
        Index(
            "ix_alerts_resolved_severity_time",
            "is_resolved",
            "severity",
            "created_at",
            postgresql_ops={"created_at": "DESC"},
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    serial_number: Mapped[Optional[str]] = mapped_column(
        String(50),
        ForeignKey("devices.serial_number", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # ── Classification ────────────────────────────────────────────────────────
    severity: Mapped[str] = mapped_column(
        String(20), nullable=False,
        comment="warning | error | critical",
    )
    category: Mapped[Optional[str]] = mapped_column(
        String(50), nullable=True,
        comment="health | connectivity | storage | agent",
    )

    # ── Content ───────────────────────────────────────────────────────────────
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # ── Resolution ────────────────────────────────────────────────────────────
    is_resolved: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    resolved_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # ── Timestamp ─────────────────────────────────────────────────────────────
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:
        status = "resolved" if self.is_resolved else "open"
        return (
            f"<Alert id={self.id} serial={self.serial_number} "
            f"severity={self.severity} [{status}]>"
        )
