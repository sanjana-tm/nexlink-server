"""
NexLink Server — Screenshot ORM Model
=======================================
Table: screenshots

Stores metadata for captured device screenshots.
The actual image file lives on disk at file_path — only metadata is in the DB.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from server.db.base import Base


class Screenshot(Base):
    """
    Screenshot capture metadata.

    One row per screenshot taken from a device. The binary image is stored
    at file_path (local disk or object storage), not in the database.
    """
    __tablename__ = "screenshots"
    __table_args__ = (
        Index(
            "ix_screenshots_serial_time",
            "serial_number",
            "captured_at",
            postgresql_ops={"captured_at": "DESC"},
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    serial_number: Mapped[str] = mapped_column(
        String(50),
        ForeignKey("devices.serial_number", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # ── File info ─────────────────────────────────────────────────────────────
    file_path: Mapped[str] = mapped_column(String(500), nullable=False)
    file_size_bytes: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    width: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    height: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    format: Mapped[str] = mapped_column(String(10), default="png", nullable=False)

    # ── Provenance ────────────────────────────────────────────────────────────
    requested_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:
        return (
            f"<Screenshot id={self.id} serial={self.serial_number} "
            f"{self.width}x{self.height} {self.format}>"
        )
