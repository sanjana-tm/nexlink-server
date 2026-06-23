"""
NexLink Server — Reconnect Manager
=====================================
Server-side tracking of agent reconnection attempts.

When an agent loses its WebSocket connection and retries:
  - Each attempt is recorded (success=False until final success)
  - Allows analytics: "how often does device X reconnect?"
  - Detects thrashing: device reconnecting too frequently (network issue)
  - Measures backoff effectiveness: is the agent respecting backoff delays?

This is separate from the agent's RecoveryManager (Phase 1) which
handles the client-side retry logic. This tracks what the SERVER observes.
"""
from __future__ import annotations

import logging
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from server.db.models.reconnect import ReconnectAttempt

logger = logging.getLogger(__name__)


class ReconnectManager:
    """Server-side reconnect tracking."""

    async def record_attempt(
        self,
        device_id: uuid.UUID,
        db: AsyncSession,
        attempt_number: int,
        success: bool,
        error_message: str | None = None,
        backoff_seconds: float | None = None,
        session_id: uuid.UUID | None = None,
    ) -> None:
        """
        Record a single reconnection attempt.

        Call this:
          - With success=False when an agent attempts to connect but fails
          - With success=True when the agent successfully connects
        """
        attempt = ReconnectAttempt(
            device_id=device_id,
            attempt_number=attempt_number,
            success=success,
            error_message=error_message,
            backoff_seconds=backoff_seconds,
            session_id=session_id,
        )
        db.add(attempt)
        await db.flush()

        if success:
            logger.info(
                "Reconnect SUCCESS: device=%s attempt=%d",
                device_id, attempt_number,
            )
        else:
            logger.warning(
                "Reconnect FAILED: device=%s attempt=%d error=%s backoff=%.1fs",
                device_id, attempt_number, error_message, backoff_seconds or 0,
            )

    async def get_history(
        self,
        device_id: uuid.UUID,
        db: AsyncSession,
        limit: int = 100,
    ) -> list[ReconnectAttempt]:
        """Return recent reconnect attempts for a device (newest first)."""
        result = await db.execute(
            select(ReconnectAttempt)
            .where(ReconnectAttempt.device_id == device_id)
            .order_by(ReconnectAttempt.attempted_at.desc())
            .limit(limit)
        )
        return result.scalars().all()

    async def get_stats(
        self,
        device_id: uuid.UUID,
        db: AsyncSession,
    ) -> dict:
        """
        Return reconnect statistics for a device.
        Useful for diagnostics: "this device has reconnected 50 times today."
        """
        result = await db.execute(
            select(
                func.count().label("total"),
                func.sum(
                    ReconnectAttempt.success.cast(type_=None)
                ).label("successes"),
            ).where(ReconnectAttempt.device_id == device_id)
        )
        row = result.one()
        total = row.total or 0
        successes = int(row.successes or 0)

        return {
            "device_id": str(device_id),
            "total_attempts": total,
            "successful_attempts": successes,
            "failed_attempts": total - successes,
            "success_rate": round(successes / total, 3) if total > 0 else None,
        }
