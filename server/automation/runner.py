"""
NexLink Server — Execution Runner (Phase 8)
==============================================
Dispatches automation commands to agents and tracks results.

Execution flow:
  1. Runner receives an allocated execution (device assigned)
  2. Sends "automation.execute" command to agent via WebSocket
  3. Agent acknowledges (command_ack)
  4. Agent runs tests (Appium/pytest/shell)
  5. Agent reports result (command_result)
  6. Runner updates execution status and releases device

Command protocol (server → agent):
  {
    "type": "command",
    "payload": {
      "command_id": "<execution_id>",
      "command_type": "automation.execute",
      "params": {
        "name": "Login Test Suite",
        "test_type": "appium",
        "test_config": { ... },
      }
    }
  }

Result protocol (agent → server):
  {
    "type": "command_result",
    "payload": {
      "command_id": "<execution_id>",
      "success": true/false,
      "output": "...",
      "result": {
        "total_tests": 10,
        "passed": 9,
        "failed": 1,
        "steps": [ ... ]
      }
    }
  }
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from server.db.models.automation import AutomationExecution, ExecutionStep
from server.db.session import AsyncSessionFactory
from server.db.base import utcnow
from server.services.event_bus import event_bus
from server.ws.manager import connection_manager
from server.ws.handlers import build_command_message

from .allocator import DeviceAllocator

logger = logging.getLogger(__name__)


class ExecutionRunner:
    """
    Dispatches executions to agents and processes results.

    Tracks in-flight executions and applies timeouts.
    """

    def __init__(
        self,
        allocator: DeviceAllocator,
        execution_timeout: float = 600.0,
    ) -> None:
        self._allocator = allocator
        self._execution_timeout = execution_timeout

        # execution_id → asyncio.Task (timeout watcher)
        self._timeout_tasks: dict[str, asyncio.Task] = {}

    async def dispatch(self, execution: AutomationExecution) -> bool:
        """
        Send an execution command to the allocated device.

        The device must already be assigned (execution.device_id set).
        Returns True if the command was sent successfully.
        """
        device_id = str(execution.device_id)
        execution_id = str(execution.execution_id)

        if not connection_manager.is_connected(device_id):
            logger.warning(
                "Cannot dispatch %s — device %s offline",
                execution_id[:8], device_id[:8],
            )
            return False

        # Build the command message
        message = build_command_message(
            command_id=execution_id,
            command_type="automation.execute",
            params={
                "name": execution.name,
                "test_type": execution.test_type,
                "test_config": execution.test_config or {},
                "execution_id": execution_id,
            },
            session_id=str(execution.session_id) if execution.session_id else "",
        )

        # Send to agent
        sent = await connection_manager.send(device_id, message)
        if not sent:
            logger.error(
                "Failed to send execution command to device %s",
                device_id[:8],
            )
            return False

        # Start timeout watcher
        timeout = (execution.test_config or {}).get("timeout", self._execution_timeout)
        task = asyncio.create_task(
            self._timeout_watcher(execution_id, device_id, float(timeout))
        )
        self._timeout_tasks[execution_id] = task

        # Publish event
        await event_bus.publish(
            "automation.dispatched",
            payload={
                "execution_id": execution_id,
                "device_id": device_id,
                "name": execution.name,
                "test_type": execution.test_type,
            },
            source_device_id=device_id,
        )

        logger.info(
            "Dispatched execution %s to device %s (timeout=%.0fs)",
            execution_id[:8], device_id[:8], timeout,
        )
        return True

    async def handle_result(self, event: dict) -> None:
        """
        Process a command_result from an agent.

        Called by the EventBus when "command.completed" or "command.failed" fires.
        """
        payload = event.get("payload", {})
        command_id = payload.get("command_id", "")
        success = payload.get("success", False)

        # Cancel timeout watcher
        self._cancel_timeout(command_id)

        async with AsyncSessionFactory() as db:
            try:
                result = await db.execute(
                    select(AutomationExecution)
                    .where(AutomationExecution.execution_id == uuid.UUID(command_id))
                    .options(selectinload(AutomationExecution.steps))
                )
                execution = result.scalar_one_or_none()
                if not execution:
                    logger.warning("Result for unknown execution: %s", command_id[:8])
                    return

                # Extract result data
                result_data = payload.get("result", {})
                if isinstance(result_data, str):
                    result_data = {}

                passed = result_data.get("passed", result_data.get("passed_tests", 0))
                failed = result_data.get("failed", result_data.get("failed_tests", 0))
                skipped = result_data.get("skipped", result_data.get("skipped_tests", 0))
                error = payload.get("error", "")

                if success and not error:
                    execution.complete(
                        passed=passed, failed=failed, skipped=skipped,
                        summary=result_data,
                    )
                else:
                    execution.fail(error or "Execution failed")
                    execution.failed_tests = failed
                    execution.passed_tests = passed

                # Process step results
                steps = result_data.get("steps", [])
                for step_data in steps:
                    step = ExecutionStep(
                        execution_id=execution.execution_id,
                        name=step_data.get("name", "unknown"),
                        status=step_data.get("status", "unknown"),
                        duration_seconds=step_data.get("duration_seconds"),
                        error_message=step_data.get("error_message"),
                        stack_trace=step_data.get("stack_trace"),
                        output=step_data.get("output"),
                    )
                    if step_data.get("started_at"):
                        step.started_at = datetime.fromisoformat(step_data["started_at"])
                    if step_data.get("ended_at"):
                        step.ended_at = datetime.fromisoformat(step_data["ended_at"])
                    db.add(step)

                await db.commit()

                # Release device
                self._allocator.release(str(execution.device_id))

                await event_bus.publish(
                    "automation.completed",
                    payload={
                        "execution_id": command_id,
                        "status": execution.status,
                        "passed": execution.passed_tests,
                        "failed": execution.failed_tests,
                        "duration": execution.duration_seconds,
                    },
                    source_device_id=str(execution.device_id),
                )

                logger.info(
                    "Execution %s completed: status=%s passed=%d failed=%d (%.1fs)",
                    command_id[:8], execution.status,
                    execution.passed_tests, execution.failed_tests,
                    execution.duration_seconds or 0,
                )

            except Exception as exc:
                logger.error("Error processing execution result: %s", exc, exc_info=True)
                await db.rollback()

    async def handle_device_offline(self, device_id: str) -> Optional[str]:
        """
        Handle a device going offline during execution.

        Returns the execution_id that was affected, or None.
        """
        execution_id = self._allocator.get_execution_for_device(device_id)
        if not execution_id:
            return None

        self._cancel_timeout(execution_id)
        self._allocator.release(device_id)

        async with AsyncSessionFactory() as db:
            try:
                result = await db.execute(
                    select(AutomationExecution).where(
                        AutomationExecution.execution_id == uuid.UUID(execution_id),
                    )
                )
                execution = result.scalar_one_or_none()
                if execution and execution.status in ("allocated", "running", "installing"):
                    execution.fail(f"Device {device_id[:8]} went offline during execution")
                    await db.commit()

                    logger.warning(
                        "Execution %s failed — device %s offline",
                        execution_id[:8], device_id[:8],
                    )
            except Exception as exc:
                logger.error("Error handling device offline for execution: %s", exc)
                await db.rollback()

        return execution_id

    # ── Timeout Management ────────────────────────────────────────────────────

    async def _timeout_watcher(
        self, execution_id: str, device_id: str, timeout: float,
    ) -> None:
        """Background task: mark execution as timed out if it exceeds the limit."""
        try:
            await asyncio.sleep(timeout)
        except asyncio.CancelledError:
            return

        logger.warning(
            "Execution %s timed out after %.0fs on device %s",
            execution_id[:8], timeout, device_id[:8],
        )

        self._allocator.release(device_id)

        async with AsyncSessionFactory() as db:
            try:
                result = await db.execute(
                    select(AutomationExecution).where(
                        AutomationExecution.execution_id == uuid.UUID(execution_id),
                    )
                )
                execution = result.scalar_one_or_none()
                if execution and execution.status in ("allocated", "running", "installing"):
                    execution.timeout()
                    await db.commit()
            except Exception:
                await db.rollback()

        await event_bus.publish(
            "automation.timeout",
            payload={"execution_id": execution_id, "device_id": device_id},
        )

        self._timeout_tasks.pop(execution_id, None)

    def _cancel_timeout(self, execution_id: str) -> None:
        task = self._timeout_tasks.pop(execution_id, None)
        if task and not task.done():
            task.cancel()

    async def cancel_all(self) -> None:
        """Cancel all timeout watchers (shutdown)."""
        for task in self._timeout_tasks.values():
            if not task.done():
                task.cancel()
        self._timeout_tasks.clear()
