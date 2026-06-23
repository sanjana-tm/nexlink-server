"""
NexLink Server — SQLAlchemy 2.0 Declarative Base
=================================================
All ORM models inherit from `Base`. Alembic uses `Base.metadata`
to autogenerate migration scripts.

Why SQLAlchemy 2.0 style?
- `Mapped[T]` provides full type inference — IDEs understand your columns.
- `mapped_column()` replaces `Column()` — cleaner, type-safe.
- `AsyncSession` is a first-class citizen with `async with` context managers.
- `select()` returns a typed `Select` object — no more `.query()` patterns.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    """Return current UTC datetime (timezone-aware)."""
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    """
    Base class for all SQLAlchemy ORM models.

    Common columns (id, created_at, updated_at) are defined here
    so every table gets them automatically.

    Subclasses define their own __tablename__ and additional columns.
    """
    pass


class TimestampMixin:
    """
    Mixin adding created_at and updated_at columns.

    Import order matters — put this before Base in your model:
        class MyModel(TimestampMixin, Base): ...
    """
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        nullable=False,
        index=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
        nullable=False,
    )
