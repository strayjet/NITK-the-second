"""
Async SQLAlchemy engine and session management.

One `AsyncEngine` is created per process (bound to `settings.database_url`)
and shared via a connection pool. `get_db` is the FastAPI dependency every
route/repository should use to obtain a scoped `AsyncSession`.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.db.base import Base

logger = logging.getLogger(__name__)

engine: AsyncEngine = create_async_engine(
    settings.database_url,
    echo=settings.database_echo,
    pool_pre_ping=True,
    pool_size=settings.database_pool_size,
    max_overflow=settings.database_max_overflow,
    pool_timeout=settings.database_pool_timeout_seconds,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency yielding a request-scoped `AsyncSession`.

    Commits on clean exit, rolls back on exception, always closes the
    session afterwards.
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


@asynccontextmanager
async def session_scope() -> AsyncGenerator[AsyncSession, None]:
    """Non-FastAPI context manager for use outside request handlers (e.g. background jobs)."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def check_connection() -> bool:
    """Best-effort connectivity check used at startup. Never raises."""
    from sqlalchemy import text

    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception as exc:  # noqa: BLE001 - startup health check, must not crash the app
        logger.warning("database connectivity check failed: %s", exc)
        return False


async def create_all_tables() -> None:
    """Dev-only convenience: create tables directly from ORM metadata (bypasses Alembic).

    Guarded by `settings.database_auto_create_tables` — production deployments
    should run `alembic upgrade head` instead.
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def dispose_engine() -> None:
    """Close the connection pool. Call once at application shutdown."""
    await engine.dispose()
