"""
NexLink Server — Application Lifecycle
=========================================
Manages startup and shutdown of all server-wide components.

Startup Order:
  1. Logging setup
  2. Database connection verification
  3. Event bus start
  4. Heartbeat monitor start

Shutdown Order (reverse):
  1. Heartbeat monitor stop
  2. Event bus drain + stop
  3. Database engine dispose

Usage in main.py:
    from server.core.lifecycle import lifespan
    app = FastAPI(lifespan=lifespan)
"""
from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI

from server.config.settings import get_settings
from server.db.session import engine
from server.services.event_bus import event_bus
from server.services.heartbeat_manager import HeartbeatMonitor
from server.services.logging_service import setup_logging

logger = logging.getLogger(__name__)

# ── Global background task handles ────────────────────────────────────────────
_heartbeat_monitor = HeartbeatMonitor()
_startup_time: float | None = None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    FastAPI lifespan context manager.
    Everything before 'yield' runs on startup.
    Everything after 'yield' runs on shutdown.
    """
    global _startup_time
    settings = get_settings()

    # ── STARTUP ───────────────────────────────────────────────────────────────
    setup_logging(settings)
    logger.info("=" * 60)
    logger.info("NexLink Server v2.0.0 starting...")
    logger.info("  host=%s:%d  debug=%s", settings.host, settings.port, settings.debug)
    logger.info("  db=%s", settings.database_url.split("@")[-1])  # hide credentials
    logger.info("=" * 60)

    # Verify database connectivity
    try:
        async with engine.connect() as conn:
            from sqlalchemy import text
            await conn.execute(text("SELECT 1"))
        logger.info("Database connection: OK")
    except Exception as e:
        logger.critical("Database connection FAILED: %s", e)
        raise

    # Start event bus
    await event_bus.start()
    logger.info("Event bus: started")

    # Start heartbeat offline detector
    await _heartbeat_monitor.start()
    logger.info("Heartbeat monitor: started")

    _startup_time = time.monotonic()
    logger.info("NexLink Server ready to accept connections")

    # ── HAND CONTROL TO FastAPI ───────────────────────────────────────────────
    yield

    # ── SHUTDOWN ──────────────────────────────────────────────────────────────
    logger.info("NexLink Server shutting down...")

    await _heartbeat_monitor.stop()
    logger.info("Heartbeat monitor: stopped")

    await event_bus.stop()
    logger.info("Event bus: stopped")

    await engine.dispose()
    logger.info("Database pool: disposed")

    uptime = time.monotonic() - _startup_time if _startup_time else 0
    logger.info("NexLink Server shutdown complete. Uptime: %.1fs", uptime)


def get_uptime() -> float | None:
    """Return server uptime in seconds, or None if not started."""
    if _startup_time is None:
        return None
    return time.monotonic() - _startup_time
