"""
NexLink Server — Async SQLAlchemy Session Factory
==================================================
Provides:
  - `engine`          — shared async engine (connection pool)
  - `AsyncSessionFactory` — session factory (call to get an AsyncSession)
  - `get_db()`        — FastAPI dependency that yields a session per request

Supports both PostgreSQL (asyncpg) and SQLite (aiosqlite) backends.
"""
from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from server.config.settings import get_settings

_settings = get_settings()

# ── Engine ────────────────────────────────────────────────────────────────────
_is_sqlite = _settings.database_url.startswith("sqlite")

if _is_sqlite:
    # SQLite doesn't support pool_size, max_overflow, pool_timeout
    engine = create_async_engine(
        _settings.database_url,
        echo=_settings.debug,
        connect_args={"check_same_thread": False},
    )
else:
    engine = create_async_engine(
        _settings.database_url,
        pool_size=_settings.database_pool_size,
        max_overflow=_settings.database_max_overflow,
        pool_timeout=_settings.database_pool_timeout,
        pool_pre_ping=True,
        echo=_settings.debug,
        echo_pool=False,
    )

# ── Session Factory ───────────────────────────────────────────────────────────
AsyncSessionFactory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=True,
    autocommit=False,
)


# ── FastAPI Dependency ─────────────────────────────────────────────────────────
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency. Yields a database session per HTTP request.
    Committed on success, rolled back on error, always closed.
    """
    async with AsyncSessionFactory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
