"""
NexLink Server — Execution Queue (Phase 8)
=============================================
Priority FIFO queue backed by the database.

Why DB-backed queue instead of in-memory?
  - Survives server restarts (queued jobs don't vanish)
  - Supports distributed scheduling (future multi-server)
  - Auditable (every queue operation is a DB row)
  - No message loss (unlike in-memory asyncio.Queue)

Priority ordering:
  Lower number = higher priority (1 is most urgent, 10 is background).
  Within the same priority, FIFO ordering by queued_at timestamp.

Queue SQL: SELECT * FROM automation_executions
           WHERE status = 'queued'
           ORDER BY priority ASC, queued_at ASC
           LIMIT 1 FOR UPDATE SKIP LOCKED

  FOR UPDATE SKIP LOCKED ensures that two concurrent scheduler
  invocations never grab the same execution (database-level locking).
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select, func, update, text
from sqlalchemy.ext.asyncio import AsyncSession

from server.db.models.automation import AutomationExecution
from server.db.base import utcnow

logger = logging.getLogger(__name__)


class ExecutionQueue:
    """
    Database-backed priority execution queue.

    Provides atomic dequeue (claim next job) using SELECT ... FOR UPDATE SKIP LOCKED.
    """

    async def enqueue(
        self,
        name: str,
        db: AsyncSession,
        test_type: str = "appium",
        test_config: Optional[dict] = None,
        platform_filter: Optional[str] = None,
        device_filter: Optional[dict] = None,
        device_id: Optional[uuid.UUID] = None,
        priority: int = 5,
        max_retries: int = 2,
        initiated_by: Optional[str] = None,
        session_id: Optional[uuid.UUID] = None,
    ) -> AutomationExecution:
        """Add an execution to the queue."""
        execution = AutomationExecution(
            execution_id=uuid.uuid4(),
            name=name,
            test_type=test_type,
            test_config=test_config,
            platform_filter=platform_filter,
            device_filter=device_filter,
            device_id=device_id,
            priority=priority,
            max_retries=max_retries,
            queued_at=utcnow(),
            status="queued",
            initiated_by=initiated_by,
            session_id=session_id,
        )
        db.add(execution)
        await db.flush()

        logger.info(
            "Queued execution: %s (%s) priority=%d",
            name, str(execution.execution_id)[:8], priority,
        )
        return execution

    async def dequeue(self, db: AsyncSession) -> Optional[AutomationExecution]:
        """
        Atomically claim the next queued execution.

        Uses FOR UPDATE SKIP LOCKED to prevent double-claiming
        in concurrent scheduler runs.

        Returns the claimed execution, or None if queue is empty.
        """
        # PostgreSQL-specific: FOR UPDATE SKIP LOCKED
        result = await db.execute(
            select(AutomationExecution)
            .where(AutomationExecution.status == "queued")
            .order_by(AutomationExecution.priority.asc(), AutomationExecution.queued_at.asc())
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        execution = result.scalar_one_or_none()

        if execution:
            execution.status = "allocated"
            execution.allocated_at = utcnow()
            await db.flush()
            logger.info(
                "Dequeued execution: %s (%s)",
                execution.name, str(execution.execution_id)[:8],
            )

        return execution

    async def requeue(
        self,
        execution_id: uuid.UUID,
        db: AsyncSession,
        priority_boost: int = 0,
    ) -> bool:
        """Put a failed execution back in the queue for retry."""
        result = await db.execute(
            select(AutomationExecution).where(
                AutomationExecution.execution_id == execution_id,
            )
        )
        execution = result.scalar_one_or_none()
        if not execution:
            return False

        if execution.retry_count >= execution.max_retries:
            logger.warning(
                "Execution %s exhausted retries (%d/%d)",
                str(execution_id)[:8], execution.retry_count, execution.max_retries,
            )
            return False

        execution.status = "queued"
        execution.retry_count += 1
        execution.device_id = None  # Release device for reallocation
        execution.allocated_at = None
        execution.started_at = None
        execution.ended_at = None
        execution.error_message = None
        # Boost priority for retries (lower number = higher priority)
        execution.priority = max(1, execution.priority - priority_boost)
        execution.queued_at = utcnow()

        await db.flush()
        logger.info(
            "Requeued execution: %s (retry %d/%d)",
            str(execution_id)[:8], execution.retry_count, execution.max_retries,
        )
        return True

    async def cancel(
        self,
        execution_id: uuid.UUID,
        db: AsyncSession,
    ) -> bool:
        """Cancel a queued or allocated execution."""
        result = await db.execute(
            select(AutomationExecution).where(
                AutomationExecution.execution_id == execution_id,
                AutomationExecution.status.in_(["queued", "allocated"]),
            )
        )
        execution = result.scalar_one_or_none()
        if not execution:
            return False

        execution.cancel()
        await db.flush()
        logger.info("Cancelled execution: %s", str(execution_id)[:8])
        return True

    async def queue_depth(self, db: AsyncSession) -> dict[str, int]:
        """Get queue statistics."""
        counts = {}
        for status in ("queued", "allocated", "running", "installing"):
            result = await db.execute(
                select(func.count()).select_from(AutomationExecution).where(
                    AutomationExecution.status == status,
                )
            )
            counts[status] = result.scalar() or 0
        return counts
