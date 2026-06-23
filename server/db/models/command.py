"""
NexLink Server — Command History ORM Model
============================================
Table: command_history

Append-only log of every shell command executed on a device via
the NexLink agent. Provides full audit trail and debugging context.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, DateTime, Float, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from server.db.base import Base


class CommandHistory(Base):
    """
    Record of a single command executed on a device.

    Captures the command text, output, exit code, and timing information.
    Commands are never deleted — this table is an append-only audit trail.
    """
    __tablename__ = "command_history"
    __table_args__ = (
        Index(
            "ix_command_history_serial_time",
            "serial_number",
            "executed_at",
            postgresql_ops={"executed_at": "DESC"},
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    serial_number: Mapped[str] = mapped_column(
        String(50),
        ForeignKey("devices.serial_number", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # ── Command ───────────────────────────────────────────────────────────────
    command: Mapped[str] = mapped_column(Text, nullable=False)
    output: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    exit_code: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # ── Timing ────────────────────────────────────────────────────────────────
    timeout_seconds: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    duration_seconds: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # ── Provenance ────────────────────────────────────────────────────────────
    requested_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    executed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )

    def __repr__(self) -> str:
        cmd_preview = self.command[:40] + "..." if len(self.command) > 40 else self.command
        return (
            f"<CommandHistory id={self.id} serial={self.serial_number} "
            f"cmd={cmd_preview!r} exit={self.exit_code}>"
        )
