"""
NexLink Server — Configuration Settings
=========================================
Priority chain: ENV > .env file > defaults

All environment variables use the NEXLINK_ prefix.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ServerSettings(BaseSettings):
    """
    Pydantic v2 settings class.

    How it works:
    1. Reads env vars with prefix NEXLINK_
    2. Falls back to .env file in CWD
    3. Falls back to defaults below

    NEVER hard-code secrets here — always use env vars in production.
    """
    model_config = SettingsConfigDict(
        env_prefix="NEXLINK_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Database ───────────────────────────────────────────────────────────────
    database_url: str = Field(
        default="postgresql+asyncpg://nexlink:nexlink@localhost:5432/nexlink",
        description="SQLAlchemy async database URL (asyncpg driver)",
    )
    database_pool_size: int = Field(default=20, ge=1, le=100)
    database_max_overflow: int = Field(default=10, ge=0, le=50)
    database_pool_timeout: int = Field(default=30, ge=5)

    # ── Server ─────────────────────────────────────────────────────────────────
    host: str = Field(default="0.0.0.0")
    port: int = Field(default=9000, ge=1024, le=65535)
    debug: bool = Field(default=False)
    workers: int = Field(default=1, ge=1)

    # ── JWT ────────────────────────────────────────────────────────────────────
    jwt_secret_key: str = Field(
        default="CHANGE_ME_IN_PRODUCTION",
        description="Must be a cryptographically random 64+ byte hex string",
    )
    jwt_algorithm: str = Field(default="HS256")
    jwt_access_expire_minutes: int = Field(default=60, ge=5, le=1440)
    jwt_refresh_expire_days: int = Field(default=30, ge=1, le=365)

    # ── Heartbeat ──────────────────────────────────────────────────────────────
    heartbeat_timeout_seconds: int = Field(
        default=60,
        description="Mark device OFFLINE if no heartbeat received within this window",
    )
    heartbeat_check_interval_seconds: int = Field(
        default=15,
        description="How often the server scans for stale heartbeats",
    )

    # ── Event Bus ──────────────────────────────────────────────────────────────
    event_bus_max_queue_size: int = Field(default=10_000)

    # ── CORS ───────────────────────────────────────────────────────────────────
    cors_origins: str = Field(
        default="*",
        description="Comma-separated list of allowed origins. Use * for dev.",
    )

    # ── Logging ────────────────────────────────────────────────────────────────
    log_level: str = Field(default="INFO")
    log_format: str = Field(default="json")
    log_dir: Path = Field(default=Path("logs"))

    # ── Admin ──────────────────────────────────────────────────────────────────
    admin_api_key: str = Field(
        default="",
        description="Master admin API key. Required for admin-only endpoints.",
    )

    # ── Derived ────────────────────────────────────────────────────────────────
    @property
    def cors_origins_list(self) -> list[str]:
        """Parse comma-separated CORS origins into a list."""
        if self.cors_origins == "*":
            return ["*"]
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def sync_database_url(self) -> str:
        """Return sync psycopg2 URL for Alembic offline/sync operations."""
        return self.database_url.replace(
            "postgresql+asyncpg://", "postgresql://"
        )

    @model_validator(mode="after")
    def _fix_database_url(self) -> "ServerSettings":
        """
        Render.com provides DATABASE_URL as postgres:// but asyncpg needs
        postgresql+asyncpg://. Auto-transform so deployment just works.
        """
        url = self.database_url
        if url.startswith("postgres://"):
            self.database_url = url.replace("postgres://", "postgresql+asyncpg://", 1)
        elif url.startswith("postgresql://") and "+asyncpg" not in url:
            self.database_url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
        return self

    @field_validator("log_level")
    @classmethod
    def _validate_log_level(cls, v: str) -> str:
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper = v.upper()
        if upper not in allowed:
            raise ValueError(f"log_level must be one of {allowed}")
        return upper

    @field_validator("log_format")
    @classmethod
    def _validate_log_format(cls, v: str) -> str:
        if v not in {"json", "console"}:
            raise ValueError("log_format must be 'json' or 'console'")
        return v


@lru_cache(maxsize=1)
def get_settings() -> ServerSettings:
    """
    Return the global settings singleton.

    Cached with lru_cache so it's created once per process lifetime.
    Use this in dependency injection: Depends(get_settings)
    """
    return ServerSettings()
