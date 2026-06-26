"""
NexLink Server — Automation Scheduler
=======================================
Polls queued AutomationRun records and dispatches them to connected devices
via WebSocket. Handles results and updates run status.

This is intentionally simple — it works directly with the serial-number-keyed
AutomationRun model and the connection_manager's serial-number-keyed connections.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import select

from server.db.models.automation import AutomationRun
from server.db.session import AsyncSessionFactory
from server.services.event_bus import event_bus
from server.ws.manager import connection_manager

logger = logging.getLogger(__name__)

RUN_TIMEOUT_SECONDS = 600.0  # 10 minutes


class AutomationScheduler:
    """
    Background scheduler that dispatches queued automation runs to agents.

    Lifecycle:
      1. Poll DB every N seconds for runs with status='queued'
      2. For each, check if the device's serial is connected via WebSocket
      3. Mark run as 'running', send automation.execute command to device
      4. On command_result, update run with pass/fail counts and final status
      5. Timeout any run stuck in 'running' longer than RUN_TIMEOUT_SECONDS
    """

    def __init__(self, poll_interval: float = 5.0) -> None:
        self._poll_interval = poll_interval
        self._task: asyncio.Task | None = None
        self._started = False
        # run_id -> started_at for in-flight timeout tracking
        self._in_flight: dict[str, datetime] = {}

    async def start(self) -> None:
        if self._started:
            return
        self._started = True

        # Reset any runs left in 'running' state from a prior crash (re-queue them)
        await self._requeue_orphaned_runs()

        event_bus.subscribe("command.completed", self._handle_result)
        event_bus.subscribe("command.failed", self._handle_result)

        self._task = asyncio.create_task(self._loop())
        logger.info(
            "AutomationScheduler started (poll=%.0fs, timeout=%.0fs)",
            self._poll_interval, RUN_TIMEOUT_SECONDS,
        )

    async def stop(self) -> None:
        if not self._started:
            return
        self._started = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        event_bus.unsubscribe("command.completed", self._handle_result)
        event_bus.unsubscribe("command.failed", self._handle_result)
        logger.info("AutomationScheduler stopped")

    # ── Scheduler Loop ────────────────────────────────────────────────────────

    async def _loop(self) -> None:
        while self._started:
            try:
                await asyncio.sleep(self._poll_interval)
                if not self._started:
                    break
                await self._tick()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("AutomationScheduler tick error: %s", exc, exc_info=True)

    async def _tick(self) -> None:
        """One scheduler cycle: dispatch new runs + timeout stale ones."""
        await self._dispatch_queued()
        await self._timeout_stale()

    async def _dispatch_queued(self) -> None:
        """
        Find queued runs whose device is online and send automation.execute.
        Only one run is dispatched per device at a time — if a device already
        has a 'running' run we skip all its queued runs this tick.
        """
        async with AsyncSessionFactory() as db:
            try:
                # Build the set of devices that already have a run in-flight
                busy_result = await db.execute(
                    select(AutomationRun.serial_number)
                    .where(AutomationRun.status == "running")
                    .distinct()
                )
                busy_serials: set[str] = {row[0] for row in busy_result.all() if row[0]}

                result = await db.execute(
                    select(AutomationRun)
                    .where(AutomationRun.status == "queued")
                    .order_by(
                        AutomationRun.priority.asc(),
                        AutomationRun.queued_at.asc(),
                    )
                    .limit(10)
                )
                runs = list(result.scalars().all())

                dispatched_this_tick: set[str] = set()
                for run in runs:
                    if not run.serial_number:
                        continue
                    if run.serial_number in busy_serials:
                        continue
                    if run.serial_number in dispatched_this_tick:
                        continue
                    if not connection_manager.is_connected(run.serial_number):
                        continue

                    now = datetime.now(timezone.utc)
                    run.status = "running"
                    run.started_at = now
                    await db.flush()

                    message = {
                        "type": "command",
                        "payload": {
                            "command_id": run.id,
                            "command_type": "automation.execute",
                            "params": {
                                "name": run.name,
                                "test_type": run.test_type,
                                "test_config": run.test_config or {},
                            },
                            "issued_at": now.isoformat(),
                        },
                        "ts": now.isoformat(),
                    }
                    sent = await connection_manager.send(run.serial_number, message)

                    if not sent:
                        run.status = "queued"
                        run.started_at = None
                        logger.warning(
                            "Failed to send automation run %s to %s — back to queued",
                            run.id[:8], run.serial_number,
                        )
                    else:
                        self._in_flight[run.id] = now
                        dispatched_this_tick.add(run.serial_number)
                        logger.info(
                            "Dispatched automation run %s (%s) to %s",
                            run.id[:8], run.test_type, run.serial_number,
                        )

                await db.commit()

            except Exception as exc:
                logger.error("Dispatch error: %s", exc, exc_info=True)
                await db.rollback()

    async def _timeout_stale(self) -> None:
        """Mark runs that have been 'running' too long as 'timeout'."""
        now = datetime.now(timezone.utc)
        timed_out = [
            run_id for run_id, started in self._in_flight.items()
            if (now - started).total_seconds() > RUN_TIMEOUT_SECONDS
        ]

        if not timed_out:
            return

        async with AsyncSessionFactory() as db:
            try:
                for run_id in timed_out:
                    self._in_flight.pop(run_id, None)
                    result = await db.execute(
                        select(AutomationRun).where(
                            AutomationRun.id == run_id,
                            AutomationRun.status == "running",
                        )
                    )
                    run = result.scalar_one_or_none()
                    if run:
                        run.status = "timeout"
                        run.ended_at = now
                        if run.started_at:
                            run.duration_seconds = (now - run.started_at).total_seconds()
                        run.error_message = "Timed out after %.0f seconds" % RUN_TIMEOUT_SECONDS
                        logger.warning("Automation run %s timed out", run_id[:8])
                await db.commit()
            except Exception as exc:
                logger.error("Timeout sweep error: %s", exc, exc_info=True)
                await db.rollback()

    # ── Result Handler ────────────────────────────────────────────────────────

    async def _handle_result(self, event: dict) -> None:
        """
        Process command_result from an agent.

        The agent sends back:
          {
            "type": "command_result",
            "payload": {
              "command_id": "<run.id>",
              "success": true/false,
              "result": {"passed": N, "failed": N, "skipped": N, "total": N, "steps": [...]}
            }
          }
        """
        payload = event.get("payload", {})
        command_id = payload.get("command_id", "")
        if not command_id:
            return

        # Only handle runs this scheduler dispatched in this process lifetime.
        # For runs dispatched before restart, _requeue_orphaned_runs already
        # reset them to queued, so there's no dangling 'running' record to match.
        if command_id not in self._in_flight:
            return

        started_at = self._in_flight.pop(command_id)
        success = payload.get("success", False)

        result_data = payload.get("result", {})
        if not isinstance(result_data, dict):
            result_data = {}

        passed = int(result_data.get("passed", 0))
        failed = int(result_data.get("failed", 0))
        skipped = int(result_data.get("skipped", 0))
        total = int(result_data.get("total", passed + failed + skipped))

        now = datetime.now(timezone.utc)
        duration = (now - started_at).total_seconds()

        async with AsyncSessionFactory() as db:
            try:
                result = await db.execute(
                    select(AutomationRun).where(AutomationRun.id == command_id)
                )
                run = result.scalar_one_or_none()
                if not run:
                    logger.warning("Result for unknown run id: %s", command_id)
                    return

                run.status = "passed" if success else "failed"
                run.ended_at = now
                run.duration_seconds = duration
                run.total_tests = total
                run.passed_tests = passed
                run.failed_tests = failed
                run.skipped_tests = skipped
                run.result_summary = result_data
                if not success:
                    run.error_message = payload.get("error") or (
                        f"{failed}/{total} tests failed"
                    )

                await db.commit()
                logger.info(
                    "Automation run %s → %s: %d/%d passed (%.1fs)",
                    command_id[:8], run.status, passed, total, duration,
                )

            except Exception as exc:
                logger.error("Handle result error: %s", exc, exc_info=True)
                await db.rollback()

    # ── Startup Recovery ──────────────────────────────────────────────────────

    async def _requeue_orphaned_runs(self) -> None:
        """
        On startup, reset any 'running' runs back to 'queued'.

        These are runs that were dispatched but the server restarted before
        receiving the result — the agent is no longer waiting for them.
        """
        async with AsyncSessionFactory() as db:
            try:
                result = await db.execute(
                    select(AutomationRun).where(AutomationRun.status == "running")
                )
                orphans = result.scalars().all()
                for run in orphans:
                    run.status = "queued"
                    run.started_at = None
                if orphans:
                    await db.commit()
                    logger.info(
                        "Requeued %d orphaned 'running' automation runs on startup",
                        len(orphans),
                    )
            except Exception as exc:
                logger.error("Requeue orphans error: %s", exc, exc_info=True)
                await db.rollback()


# ── Global singleton ──────────────────────────────────────────────────────────
automation_scheduler = AutomationScheduler()
