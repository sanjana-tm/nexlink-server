"""
NexLink Server — Automation Recovery Engine (Phase 8)
=======================================================
Handles retry logic and device reallocation for failed executions.

Recovery strategies:
  1. RETRY_SAME_DEVICE:
     Re-run on the same device. Used when the failure was likely
     transient (network blip during APK install, ADB timeout).

  2. RETRY_DIFFERENT_DEVICE:
     Re-run on a different device. Used when the failure may be
     device-specific (device frozen, out of memory, storage full).

  3. NO_RETRY:
     Mark as permanently failed. Used when max retries exhausted
     or failure is clearly not infrastructure-related (test bug).

Decision logic:
  - Device went offline during execution → RETRY_DIFFERENT_DEVICE
  - Timeout → RETRY_DIFFERENT_DEVICE (device may be stuck)
  - APK install failed → RETRY_SAME_DEVICE (transient)
  - Test assertion failure → NO_RETRY (real bug)
  - Error message contains "device" or "connection" → RETRY_DIFFERENT_DEVICE
  - All retries exhausted → NO_RETRY
"""
from __future__ import annotations

import logging
import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.db.models.automation import AutomationExecution
from server.services.event_bus import event_bus

from .queue import ExecutionQueue

logger = logging.getLogger(__name__)

# Error patterns that suggest infrastructure failure (retry-worthy)
INFRA_ERROR_PATTERNS = [
    "device", "connection", "timeout", "offline", "adb",
    "socket", "transport", "network", "unreachable",
    "install failed", "session not created",
]


class RecoveryEngine:
    """
    Decides whether to retry failed executions and requeues them.
    """

    def __init__(self, queue: ExecutionQueue) -> None:
        self._queue = queue
        self._recovery_count = 0

    async def evaluate_failure(
        self,
        execution_id: uuid.UUID,
        db: AsyncSession,
    ) -> str:
        """
        Evaluate a failed execution and decide on recovery action.

        Returns:
            "retried" — execution was requeued for retry
            "exhausted" — max retries reached, permanently failed
            "no_retry" — failure is not retry-worthy
            "not_found" — execution not found
        """
        result = await db.execute(
            select(AutomationExecution).where(
                AutomationExecution.execution_id == execution_id,
            )
        )
        execution = result.scalar_one_or_none()
        if not execution:
            return "not_found"

        # Only recover from failure states
        if execution.status not in ("failed", "error", "timeout"):
            return "no_retry"

        # Check retry budget
        if execution.retry_count >= execution.max_retries:
            logger.info(
                "Execution %s exhausted retries (%d/%d) — permanent failure",
                str(execution_id)[:8], execution.retry_count, execution.max_retries,
            )
            return "exhausted"

        # Determine if failure is retry-worthy
        if not self._is_retryable(execution):
            logger.info(
                "Execution %s failure not retryable: %s",
                str(execution_id)[:8], (execution.error_message or "")[:80],
            )
            return "no_retry"

        # Requeue for retry
        requeued = await self._queue.requeue(
            execution_id, db, priority_boost=1,
        )

        if requeued:
            self._recovery_count += 1

            await event_bus.publish(
                "automation.retried",
                payload={
                    "execution_id": str(execution_id),
                    "retry_count": execution.retry_count + 1,
                    "max_retries": execution.max_retries,
                    "reason": execution.error_message or "unknown",
                },
            )

            logger.info(
                "Execution %s requeued for retry (attempt %d/%d)",
                str(execution_id)[:8],
                execution.retry_count + 1,
                execution.max_retries,
            )
            return "retried"

        return "exhausted"

    def _is_retryable(self, execution: AutomationExecution) -> bool:
        """Determine if a failure warrants a retry."""
        # Timeouts are always retryable (device may have been stuck)
        if execution.status == "timeout":
            return True

        # Check error message for infrastructure patterns
        error = (execution.error_message or "").lower()
        for pattern in INFRA_ERROR_PATTERNS:
            if pattern in error:
                return True

        # If no tests ran at all, it's likely infrastructure
        if execution.total_tests == 0:
            return True

        # If some tests passed, the failure is likely a real test bug
        if execution.passed_tests > 0 and execution.failed_tests > 0:
            return False

        return True

    @property
    def stats(self) -> dict:
        return {"total_recoveries": self._recovery_count}
