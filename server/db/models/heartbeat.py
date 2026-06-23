"""
NexLink Server — Heartbeat ORM Model (serial-number-keyed)
============================================================
Table: heartbeats

Every heartbeat POST from an agent is stored here.
High write volume — this table grows 1 row per device per heartbeat interval.

Performance notes:
  - Composite index on (serial_number, received_at DESC) for latest-heartbeat queries.
  - Consider PostgreSQL table partitioning by month for large deployments.
  - payload (JSONB) stores the full agent payload for debugging without schema churn.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, DateTime, Float, ForeignKey, Index, Integer, String, func
from server.db.compat import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from server.db.base import Base


class Heartbeat(Base):
    """
    Single heartbeat record from an agent.

    Key fields extracted for fast querying; full payload stored in JSONB
    for completeness and future schema additions.
    """
    __tablename__ = "heartbeats"
    __table_args__ = (
        Index(
            "ix_heartbeats_serial_time",
            "serial_number",
            "received_at",
            postgresql_ops={"received_at": "DESC"},
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    serial_number: Mapped[str] = mapped_column(
        String(50),
        ForeignKey("devices.serial_number", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # ── Extracted metrics ─────────────────────────────────────────────────────
    cpu_percent: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    memory_percent: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    storage_percent: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    screen_status: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    uptime_seconds: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    battery_level: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    wifi_signal_dbm: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # ── Timestamps ────────────────────────────────────────────────────────────
    agent_timestamp: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    # ── Full payload ──────────────────────────────────────────────────────────
    payload: Mapped[Optional[dict]] = mapped_column(JSONB, default=dict, nullable=True)

    def __repr__(self) -> str:
        return (
            f"<Heartbeat id={self.id} serial={self.serial_number} "
            f"cpu={self.cpu_percent}% mem={self.memory_percent}%>"
        )
