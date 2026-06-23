"""
NexLink Server — Device & ApiKey ORM Models (serial-number-keyed)
==================================================================
Tables: devices, api_keys

Identity:
  SERIAL_NUMBER is the permanent, immutable device identifier.
  It is the hardware serial burned into every Android IFP (e.g. "TMX2405A12345").
  This value NEVER changes across reboots, factory resets, or network changes.

  serial_number is the PRIMARY KEY — no surrogate UUID needed.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    func,
)
from server.db.compat import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from server.db.base import Base


class Device(Base):
    """
    Central device registry.

    One row per physical Android IFP. The serial_number is the hardware
    serial — it is the permanent, immutable identity for the device.

    Status values: unknown, healthy, warning, critical, offline.
    Screen status: unknown, on, off, standby.
    """
    __tablename__ = "devices"

    # ── Identity ──────────────────────────────────────────────────────────────
    serial_number: Mapped[str] = mapped_column(
        String(50),
        primary_key=True,
        comment="Hardware serial number — permanent device identity",
    )

    # ── Device info ───────────────────────────────────────────────────────────
    device_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    model: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    manufacturer: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    android_version: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    sdk_version: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    agent_version: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)

    # ── Online state ──────────────────────────────────────────────────────────
    is_online: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), default="unknown", nullable=False,
        comment="unknown | healthy | warning | critical | offline",
    )
    screen_status: Mapped[str] = mapped_column(
        String(20), default="unknown", nullable=False,
        comment="unknown | on | off | standby",
    )

    # ── Health metrics (latest snapshot) ──────────────────────────────────────
    cpu_percent: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    memory_percent: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    storage_percent: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    health_score: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True,
        comment="Composite health score 0-100",
    )

    # ── Network info ──────────────────────────────────────────────────────────
    ip_address: Mapped[Optional[str]] = mapped_column(String(45), nullable=True)
    wifi_ssid: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    # ── Timestamps ────────────────────────────────────────────────────────────
    last_heartbeat_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    uptime_seconds: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)

    # ── Extensible metadata ───────────────────────────────────────────────────
    metadata_: Mapped[Optional[dict]] = mapped_column(
        "metadata", JSONB, default=dict, nullable=True,
    )

    # ── Row bookkeeping ───────────────────────────────────────────────────────
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    api_keys: Mapped[list[ApiKey]] = relationship(
        "ApiKey",
        back_populates="device",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return (
            f"<Device serial={self.serial_number} model={self.model} "
            f"online={self.is_online} status={self.status}>"
        )


class ApiKey(Base):
    """
    API key for device authentication.

    Each device can have multiple API keys. Keys are stored as hashes —
    the plaintext key is shown to the user exactly once at creation time.

    key_prefix stores the first 8 characters of the key for identification
    without exposing the full key (e.g. "nxl_abc1...").
    """
    __tablename__ = "api_keys"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )

    serial_number: Mapped[str] = mapped_column(
        String(50),
        ForeignKey("devices.serial_number", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # ── Key data ──────────────────────────────────────────────────────────────
    key_hash: Mapped[str] = mapped_column(
        String(128), nullable=False,
        comment="SHA-256 hash of the API key",
    )
    key_prefix: Mapped[str] = mapped_column(
        String(8), nullable=False,
        comment="First 8 chars of the key for identification",
    )

    # ── Lifecycle ─────────────────────────────────────────────────────────────
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    last_used_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    device: Mapped[Device] = relationship(
        "Device",
        back_populates="api_keys",
    )

    def __repr__(self) -> str:
        return (
            f"<ApiKey id={self.id} prefix={self.key_prefix}... "
            f"serial={self.serial_number} active={self.is_active}>"
        )
