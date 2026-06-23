"""
NexLink Server — XML Snapshot ORM Model
=========================================
Table: xml_snapshots

Stores UI hierarchy XML dumps captured from devices via
``adb shell uiautomator dump``. Used for UI analysis, element
inspection, and automation target identification.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from server.db.base import Base


class XmlSnapshot(Base):
    """
    UI hierarchy XML dump from a device.

    The full XML content is stored inline (not on disk) because it is
    typically 50-200 KB and frequently queried for element searches.
    """
    __tablename__ = "xml_snapshots"
    __table_args__ = (
        Index(
            "ix_xml_snapshots_serial_time",
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

    # ── XML content ───────────────────────────────────────────────────────────
    xml_content: Mapped[str] = mapped_column(Text, nullable=False)

    # ── Context ───────────────────────────────────────────────────────────────
    app_package: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    activity: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    node_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # ── Provenance ────────────────────────────────────────────────────────────
    requested_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:
        return (
            f"<XmlSnapshot id={self.id} serial={self.serial_number} "
            f"package={self.app_package} nodes={self.node_count}>"
        )
