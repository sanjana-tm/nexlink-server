"""
NexLink Server — Database Compatibility Layer
===============================================
Provides JSONB and UUID types that work with both PostgreSQL and SQLite.

PostgreSQL: uses native JSONB and UUID types.
SQLite: falls back to JSON (Text) and String(36).

Usage in models:
    from server.db.compat import JSONB, UUID
"""
from __future__ import annotations

from sqlalchemy import JSON, String
from sqlalchemy.types import TypeDecorator

try:
    from sqlalchemy.dialects.postgresql import JSONB as PG_JSONB, UUID as PG_UUID
    _HAS_PG = True
except ImportError:
    _HAS_PG = False

from server.config.settings import get_settings

_settings = get_settings()
_is_sqlite = _settings.database_url.startswith("sqlite")

if _is_sqlite or not _HAS_PG:
    # SQLite mode: use JSON (stored as text) and String(36)
    JSONB = JSON
    UUID = lambda as_uuid=True: String(36)  # noqa: E731
else:
    JSONB = PG_JSONB
    UUID = PG_UUID
