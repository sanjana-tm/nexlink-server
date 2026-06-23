"""
NexLink Server — Remote Control Audit Logger (Phase 12)
=========================================================
Logs every remote control action to the database for compliance.

Why audit logging for remote control:
  - Compliance: "Who touched the production IFP at 2 AM?"
  - Debugging: "Why did the device show this screen during the test?"
  - Security: detect unauthorized access attempts
  - Analytics: usage patterns, most-used gestures, session duration

Every input event generates one audit row:
  {timestamp, device_id, user_id, gesture, coordinates, result}

Audit rows are append-only — never updated or deleted.
"""
from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import BigInteger, DateTime, Float, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from server.db.base import Base, utcnow
from server.db.session import AsyncSessionFactory

logger = logging.getLogger(__name__)


class RemoteControlAuditLog(Base):
    """Append-only audit log for every remote control action."""
    __tablename__ = "remote_control_audit"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    audit_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), default=uuid.uuid4, unique=True, nullable=False,
    )

    # Who did what to which device
    device_id: Mapped[str] = mapped_column(String(255), nullable=False)
    user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    session_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # Action details
    action: Mapped[str] = mapped_column(String(50), nullable=False)
    # tap, swipe, text, keyevent, gesture, lock_acquire, lock_release
    gesture_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    # Coordinates (device pixels, after mapping)
    device_x: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    device_y: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    device_x2: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    device_y2: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)

    # Result
    success: Mapped[bool] = mapped_column(default=True, nullable=False)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Context
    details: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    # Timestamp
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow,
        server_default=text("NOW()"), nullable=False,
    )


class AuditLogger:
    """
    Asynchronous audit logger for remote control events.

    Writes are fire-and-forget — audit logging must never block
    or fail the control action itself. Errors are logged, not raised.
    """

    def __init__(self) -> None:
        self._count = 0

    async def log_input(
        self,
        device_id: str,
        user_id: str,
        action: str,
        session_id: str = "",
        gesture_name: str = "",
        device_x: int = 0,
        device_y: int = 0,
        device_x2: int = 0,
        device_y2: int = 0,
        success: bool = True,
        error: str = "",
        details: dict | None = None,
    ) -> None:
        """Log a remote control action. Non-blocking, fire-and-forget."""
        try:
            async with AsyncSessionFactory() as db:
                entry = RemoteControlAuditLog(
                    device_id=device_id,
                    user_id=user_id,
                    session_id=session_id or None,
                    action=action,
                    gesture_name=gesture_name or None,
                    device_x=device_x or None,
                    device_y=device_y or None,
                    device_x2=device_x2 or None,
                    device_y2=device_y2 or None,
                    success=success,
                    error=error or None,
                    details=details,
                )
                db.add(entry)
                await db.commit()
                self._count += 1
        except Exception as exc:
            # Audit logging must never break the control flow
            logger.error("Audit log write failed: %s", exc)

    async def log_lock_event(
        self,
        device_id: str,
        user_id: str,
        action: str,
        details: dict | None = None,
    ) -> None:
        """Log a lock acquire/release/steal event."""
        await self.log_input(
            device_id=device_id,
            user_id=user_id,
            action=action,
            details=details,
        )

    @property
    def total_logged(self) -> int:
        return self._count
