"""
NexLink Server — Device Event ORM Model (serial-number-keyed)
===============================================================
Table: device_events

Append-only event log for device lifecycle events. Everything notable
that happens to a device is recorded here: connectivity changes, health
threshold breaches, configuration updates, etc.

Severity levels: info, warning, error, critical.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, String, Text, func
from server.db.compat import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from server.db.base import Base


class DeviceEvent(Base):
    """
    Append-only device event record.

    Events are immutable once written. They provide the full audit trail
    of device activity for debugging, alerting, and analytics.
    """
    __tablename__ = "device_events"
    __table_args__ = (
        Index(
            "ix_device_events_serial_time",
            "serial_number",
            "created_at",
            postgresql_ops={"created_at": "DESC"},
        ),
        Index(
            "ix_device_events_type_time",
            "event_type",
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

    # ── Event classification ──────────────────────────────────────────────────
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    severity: Mapped[str] = mapped_column(
        String(20), default="info", nullable=False,
        comment="info | warning | error | critical",
    )

    # ── Event content ─────────────────────────────────────────────────────────
    message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    details: Mapped[Optional[dict]] = mapped_column(JSONB, default=dict, nullable=True)

    # ── Timestamp ─────────────────────────────────────────────────────────────
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:
        return (
            f"<DeviceEvent id={self.id} serial={self.serial_number} "
            f"type={self.event_type} severity={self.severity}>"
        )
