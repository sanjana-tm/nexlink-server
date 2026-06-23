"""
NexLink Server — Automation Engine (Phase 8)
===============================================
Top-level service orchestrating the automation execution pipeline.

Pipeline:
  1. User submits execution via REST API
  2. ExecutionQueue persists it in DB (status=queued)
  3. Scheduler loop picks next queued execution
  4. DeviceAllocator finds and locks a matching device
  5. ExecutionRunner dispatches command to agent via WebSocket
  6. Agent runs tests and reports result
  7. Runner processes result, releases device
  8. If failed → RecoveryEngine evaluates retry
  9. If retried → back to step 3

The scheduler runs as a background asyncio task, checking the
queue every N seconds. When devices become available (execution
completes or device comes online), the next queued job is dispatched.

Integration with OrchestrationEngine:
  AutomationEngine is created as a component of OrchestrationEngine.
  It subscribes to EventBus events for command results and device
  state changes. It's started/stopped via the orchestration lifecycle.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Optional

from server.db.session import AsyncSessionFactory
from server.services.event_bus import event_bus

from .allocator import DeviceAllocator
from .queue import ExecutionQueue
from .recovery import RecoveryEngine
from .runner import ExecutionRunner

logger = logging.getLogger(__name__)


class AutomationEngine:
    """
    Manages the full automation execution pipeline.

    Created inside OrchestrationEngine.__init__().
    """

    def __init__(
        self,
        scheduler_interval: float = 5.0,
        execution_timeout: float = 600.0,
    ) -> None:
        self.queue = ExecutionQueue()
        self.allocator = DeviceAllocator()
        self.runner = ExecutionRunner(
            allocator=self.allocator,
            execution_timeout=execution_timeout,
        )
        self.recovery = RecoveryEngine(queue=self.queue)

        self._scheduler_interval = scheduler_interval
        self._scheduler_task: Optional[asyncio.Task] = None
        self._started = False

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Start the automation scheduler and subscribe to events."""
        if self._started:
            return

        # Sync device locks from DB (recover from restart)
        async with AsyncSessionFactory() as db:
            await self.allocator.sync_from_db(db)

        # Subscribe to events
        event_bus.subscribe("command.completed", self.runner.handle_result)
        event_bus.subscribe("command.failed", self.runner.handle_result)
        event_bus.subscribe("device.disconnected", self._on_device_offline)
        event_bus.subscribe("automation.completed", self._on_execution_done)
        event_bus.subscribe("automation.timeout", self._on_execution_done)

        # Start scheduler loop
        self._scheduler_task = asyncio.create_task(self._scheduler_loop())
        self._started = True

        logger.info(
            "AutomationEngine started (interval=%.0fs, timeout=%.0fs)",
            self._scheduler_interval, self.runner._execution_timeout,
        )

    async def stop(self) -> None:
        """Stop the scheduler and cancel in-flight executions."""
        if not self._started:
            return

        self._started = False

        if self._scheduler_task and not self._scheduler_task.done():
            self._scheduler_task.cancel()
            try:
                await self._scheduler_task
            except asyncio.CancelledError:
                pass

        await self.runner.cancel_all()

        # Unsubscribe
        event_bus.unsubscribe("command.completed", self.runner.handle_result)
        event_bus.unsubscribe("command.failed", self.runner.handle_result)
        event_bus.unsubscribe("device.disconnected", self._on_device_offline)
        event_bus.unsubscribe("automation.completed", self._on_execution_done)
        event_bus.unsubscribe("automation.timeout", self._on_execution_done)

        logger.info("AutomationEngine stopped")

    # ── Scheduler Loop ────────────────────────────────────────────────────────

    async def _scheduler_loop(self) -> None:
        """
        Background task: poll the queue and dispatch executions.

        Runs every scheduler_interval seconds. On each tick:
          1. Check if any devices are available
          2. Dequeue next execution
          3. Allocate a device
          4. Dispatch to the agent
        """
        while self._started:
            try:
                await asyncio.sleep(self._scheduler_interval)
                if not self._started:
                    break
                await self._process_queue()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Scheduler loop error: %s", exc, exc_info=True)

    async def _process_queue(self) -> None:
        """Process one execution from the queue (if devices available)."""
        if self.allocator.available_count() == 0:
            return  # No devices available — skip

        async with AsyncSessionFactory() as db:
            try:
                execution = await self.queue.dequeue(db)
                if not execution:
                    return  # Queue empty

                # Allocate a device
                device_id = await self.allocator.allocate(execution, db)
                if not device_id:
                    # No matching device — requeue
                    execution.status = "queued"
                    execution.allocated_at = None
                    await db.commit()
                    return

                # Update execution with allocated device
                execution.allocate(uuid.UUID(device_id))
                execution.start()
                await db.commit()

                # Dispatch to agent
                sent = await self.runner.dispatch(execution)
                if not sent:
                    # Dispatch failed — mark error and release
                    execution.fail("Failed to dispatch command to agent")
                    self.allocator.release(device_id)
                    await db.commit()

            except Exception as exc:
                logger.error("Queue processing error: %s", exc, exc_info=True)
                await db.rollback()

    # ── Event Handlers ────────────────────────────────────────────────────────

    async def _on_device_offline(self, event: dict) -> None:
        """Handle a device going offline — fail any running execution on it."""
        payload = event.get("payload", {})
        device_id = payload.get("device_id", "")
        if not device_id:
            return

        execution_id = await self.runner.handle_device_offline(device_id)
        if execution_id:
            # Evaluate for retry
            async with AsyncSessionFactory() as db:
                try:
                    result = await self.recovery.evaluate_failure(
                        uuid.UUID(execution_id), db,
                    )
                    await db.commit()
                    logger.info(
                        "Device offline recovery for %s: %s",
                        execution_id[:8], result,
                    )
                except Exception:
                    await db.rollback()

    async def _on_execution_done(self, event: dict) -> None:
        """
        After execution completes, evaluate if retry is needed.

        Only evaluates failed/error/timeout executions — passed
        executions don't need recovery.
        """
        payload = event.get("payload", {})
        execution_id = payload.get("execution_id", "")
        status = payload.get("status", "")

        if status in ("failed", "error", "timeout"):
            async with AsyncSessionFactory() as db:
                try:
                    result = await self.recovery.evaluate_failure(
                        uuid.UUID(execution_id), db,
                    )
                    await db.commit()
                except Exception:
                    await db.rollback()

    # ── Status ────────────────────────────────────────────────────────────────

    async def status(self) -> dict:
        async with AsyncSessionFactory() as db:
            queue_depth = await self.queue.queue_depth(db)

        return {
            "started": self._started,
            "queue": queue_depth,
            "allocator": {
                "locked_devices": self.allocator.locked_count,
                "available_devices": self.allocator.available_count(),
            },
            "recovery": self.recovery.stats,
        }


# ── Global singleton ──────────────────────────────────────────────────────────
automation_engine = AutomationEngine()
