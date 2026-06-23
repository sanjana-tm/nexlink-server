"""
NexLink Server — Logging & Audit Service
==========================================
Two concerns handled here:

1. Structured Server Logging
   Sets up Python's logging system with JSON or console output.
   Called once at startup from core/lifecycle.py.

2. Audit Logging
   Writes AuditLog rows for every mutation to critical tables.
   Called explicitly by services after each DB write.
   NOT using DB triggers — keeps logic explicit and testable.
"""
from __future__ import annotations

import logging
import logging.handlers
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from server.config.settings import ServerSettings, get_settings
from server.db.models.audit import AuditLog

logger = logging.getLogger(__name__)


# ── Structured Logging Setup ──────────────────────────────────────────────────

class _JSONFormatter(logging.Formatter):
    """Single-line JSON log formatter for production (Loki, CloudWatch, etc.)."""

    def format(self, record: logging.LogRecord) -> str:
        import json

        log = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            log["exc"] = self.formatException(record.exc_info)
        # Merge extra fields
        for key, val in record.__dict__.items():
            if key not in (
                "msg", "args", "levelname", "levelno", "name", "pathname",
                "filename", "module", "exc_info", "exc_text", "stack_info",
                "lineno", "funcName", "created", "msecs", "relativeCreated",
                "thread", "threadName", "processName", "process", "taskName",
                "message",
            ):
                try:
                    json.dumps(val)  # check serialisable
                    log[key] = val
                except (TypeError, ValueError):
                    log[key] = str(val)
        return json.dumps(log, default=str)


class _ConsoleFormatter(logging.Formatter):
    """Colourised console formatter for development."""

    COLOURS = {
        "DEBUG":    "\033[36m",   # cyan
        "INFO":     "\033[32m",   # green
        "WARNING":  "\033[33m",   # yellow
        "ERROR":    "\033[31m",   # red
        "CRITICAL": "\033[1;31m", # bold red
    }
    RESET = "\033[0m"

    def format(self, record: logging.LogRecord) -> str:
        colour = self.COLOURS.get(record.levelname, "")
        reset = self.RESET if sys.stderr.isatty() else ""
        prefix = f"{colour}[{record.levelname:8s}]{reset}"
        return f"{prefix} {record.name}: {record.getMessage()}"


def setup_logging(settings: ServerSettings | None = None) -> None:
    """
    Configure root logger with JSON or console output.
    Also adds a rotating file handler (always JSON) for persistence.
    """
    if settings is None:
        settings = get_settings()

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(settings.log_level)

    # ── Stdout handler ────────────────────────────────────────────────────────
    stdout_handler = logging.StreamHandler(sys.stdout)
    if settings.log_format == "json":
        stdout_handler.setFormatter(_JSONFormatter())
    else:
        stdout_handler.setFormatter(_ConsoleFormatter())
    root.addHandler(stdout_handler)

    # ── Rotating file handler (always JSON) ────────────────────────────────────
    log_dir = settings.log_dir
    log_dir.mkdir(parents=True, exist_ok=True)
    file_handler = logging.handlers.RotatingFileHandler(
        log_dir / "nexlink-server.log",
        maxBytes=50 * 1024 * 1024,  # 50 MB
        backupCount=10,
        encoding="utf-8",
    )
    file_handler.setFormatter(_JSONFormatter())
    root.addHandler(file_handler)

    # ── Silence noisy libraries ────────────────────────────────────────────────
    for noisy in ("uvicorn.access", "sqlalchemy.engine", "asyncio", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    logger.info(
        "Logging configured: level=%s format=%s log_dir=%s",
        settings.log_level, settings.log_format, settings.log_dir,
    )


# ── Audit Logging Service ─────────────────────────────────────────────────────

class AuditService:
    """
    Writes AuditLog rows for mutations to critical tables.

    Usage:
        await AuditService().log(
            table_name="devices",
            record_id=str(device.device_id),
            action="INSERT",
            new_values={"device_id": ..., "hostname": ...},
            actor_device_id=current_device_id,
            db=db,
        )
    """

    async def log(
        self,
        table_name: str,
        record_id: str,
        action: str,
        db: AsyncSession,
        old_values: dict[str, Any] | None = None,
        new_values: dict[str, Any] | None = None,
        actor_device_id: uuid.UUID | None = None,
        actor_label: str | None = None,
        ip_address: str | None = None,
        description: str | None = None,
    ) -> None:
        """
        Write an audit log entry.

        Args:
            table_name:      The DB table that was mutated.
            record_id:       Primary key of the changed record (as string).
            action:          "INSERT", "UPDATE", or "DELETE".
            db:              The current AsyncSession (writes within same transaction).
            old_values:      State before change. None for INSERT.
            new_values:      State after change. None for DELETE.
            actor_device_id: DEVICE_ID of the agent that made the change (if applicable).
            actor_label:     Human-readable actor name (snapshot — device may be deleted later).
            ip_address:      Source IP for the request (audit only).
            description:     Optional human-readable description.
        """
        # Strip sensitive fields from audit values
        old_safe = self._sanitize(old_values)
        new_safe = self._sanitize(new_values)

        entry = AuditLog(
            table_name=table_name,
            record_id=record_id,
            action=action,
            actor_device_id=actor_device_id,
            actor_label=actor_label,
            ip_address=ip_address,
            old_values=old_safe,
            new_values=new_safe,
            description=description,
        )
        db.add(entry)
        # NOTE: do NOT flush here — the caller controls transaction boundaries.
        # The audit row and the data row commit together atomically.

        logger.debug(
            "Audit: %s %s.%s actor=%s",
            action, table_name, record_id, actor_device_id,
        )

    def _sanitize(self, values: dict | None) -> dict | None:
        """Remove sensitive fields from audit values before storing."""
        if values is None:
            return None
        SENSITIVE = {"key_hash", "key_prefix", "password", "secret", "token"}
        return {k: "***REDACTED***" if k in SENSITIVE else v for k, v in values.items()}
