"""
NexLink Server — Audit Log ORM Model (serial-number-keyed)
============================================================
Table: audit_logs

Append-only audit trail of all significant actions in the system.
Who did what, to which resource, and when.

Audit rows are written by the service layer (not DB triggers) to keep
the logic explicit and testable.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, DateTime, Index, Integer, String, func
from server.db.compat import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from server.db.base import Base


class AuditLog(Base):
    """
    Immutable audit record.

    Tracks user/system actions against resources (devices, automation runs,
    etc.) for compliance, forensics, and debugging.
    """
    __tablename__ = "audit_logs"
    __table_args__ = (
        Index(
            "ix_audit_logs_resource_time",
            "resource_type",
            "resource_id",
            "created_at",
            postgresql_ops={"created_at": "DESC"},
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # ── Who ───────────────────────────────────────────────────────────────────
    user_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # ── What ──────────────────────────────────────────────────────────────────
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    resource_type: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    resource_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # ── Details ───────────────────────────────────────────────────────────────
    details: Mapped[Optional[dict]] = mapped_column(JSONB, default=dict, nullable=True)
    ip_address: Mapped[Optional[str]] = mapped_column(
        String(45), nullable=True,
        comment="Source IP — for audit only",
    )

    # ── Timestamp ─────────────────────────────────────────────────────────────
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:
        return (
            f"<AuditLog id={self.id} action={self.action} "
            f"resource={self.resource_type}/{self.resource_id}>"
        )
