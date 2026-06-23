"""
Alembic Environment Configuration
====================================
Supports both:
  - Online mode:  connected to a live DB (most common)
  - Offline mode: generates SQL script without connecting

SQLAlchemy 2.0 async engine is used for online mode.
sync psycopg2 URL is used for offline SQL generation.
"""
from __future__ import annotations

import asyncio
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool
from sqlalchemy.ext.asyncio import async_engine_from_config

# ── Load application models ────────────────────────────────────────────────────
# All models must be imported here so Alembic can discover them for autogenerate.
# server/db/models/__init__.py imports them all — one import covers everything.
from server.db.base import Base
import server.db.models  # noqa: F401 — registers all models with Base.metadata

# ── Alembic config ────────────────────────────────────────────────────────────
config = context.config

# Override database URL from environment (avoids hardcoding in alembic.ini)
db_url = os.environ.get("NEXLINK_DATABASE_URL", "")
if db_url:
    # Convert asyncpg URL to psycopg2 for Alembic's sync operations
    sync_url = db_url.replace("postgresql+asyncpg://", "postgresql://")
    config.set_main_option("sqlalchemy.url", sync_url)

# Logging from alembic.ini
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# The metadata object for 'autogenerate' support
target_metadata = Base.metadata


# ── Offline mode ──────────────────────────────────────────────────────────────
def run_migrations_offline() -> None:
    """
    Run migrations without a live DB connection.
    Useful for generating SQL scripts to review before applying.

    Usage: alembic upgrade head --sql > migration.sql
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


# ── Online mode ───────────────────────────────────────────────────────────────
def do_run_migrations(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,       # detect column type changes
        compare_server_default=True,  # detect server default changes
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Run migrations against a live database using async engine."""
    # Use the sync psycopg2 driver for Alembic (it doesn't support asyncpg natively)
    sync_config = config.get_section(config.config_ini_section, {})
    sync_config["sqlalchemy.url"] = config.get_main_option("sqlalchemy.url")

    connectable = engine_from_config(
        sync_config,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,  # don't pool — we only need one connection for migration
    )

    with connectable.connect() as connection:
        do_run_migrations(connection)

    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    # Alembic runs in a sync context, so we use asyncio.run() only if needed.
    # For sync psycopg2 connections, we don't need asyncio here.
    run_migrations_online()
