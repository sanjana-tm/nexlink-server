"""Create all database tables (for SQLite local testing)."""
import asyncio
from server.db.session import engine
from server.db.base import Base
# Import all models so they register with Base.metadata
from server.db.models import *  # noqa: F401, F403


async def main():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("Database tables created successfully.")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
