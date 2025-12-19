"""
ARED Edge IOTA Anchor Service - Database Session

Async database session management with connection pooling.
"""

from collections.abc import AsyncGenerator

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings

logger = structlog.get_logger(__name__)

engine = create_async_engine(
    settings.DATABASE_URL,
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_POOL_MAX_OVERFLOW,
    pool_pre_ping=True,
    pool_recycle=3600,
    echo=settings.DEBUG,
)

async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def init_db() -> None:
    """Initialize database connection pool and verify connectivity."""
    logger.info(
        "Initializing database connection",
        host=settings.DB_HOST,
        database=settings.DB_NAME,
    )
    async with engine.begin() as conn:
        await conn.execute(text("SELECT 1"))

    # Create anchor tables if not exist
    await _ensure_anchor_tables()

    logger.info("Database connection verified")


async def _ensure_anchor_tables() -> None:
    """Ensure anchor-related tables exist."""
    async with async_session_factory() as session:
        # Create anchors table
        await session.execute(text("""
            CREATE TABLE IF NOT EXISTS anchors (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                digest VARCHAR(128) NOT NULL,
                method VARCHAR(32) NOT NULL DEFAULT 'merkle_sha256',
                start_time TIMESTAMPTZ NOT NULL,
                end_time TIMESTAMPTZ NOT NULL,
                item_count INTEGER NOT NULL DEFAULT 0,
                status VARCHAR(32) NOT NULL DEFAULT 'pending',
                iota_block_id VARCHAR(128),
                iota_network VARCHAR(32),
                explorer_url TEXT,
                error_message TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                posted_at TIMESTAMPTZ,
                confirmed_at TIMESTAMPTZ,
                UNIQUE (digest, start_time, end_time)
            )
        """))

        # Create anchor_items table for event references
        await session.execute(text("""
            CREATE TABLE IF NOT EXISTS anchor_items (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                anchor_id UUID NOT NULL REFERENCES anchors(id) ON DELETE CASCADE,
                event_id UUID,
                event_hash VARCHAR(128) NOT NULL,
                position_in_merkle INTEGER NOT NULL,
                merkle_proof JSONB,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """))

        # Create indexes
        await session.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_anchors_status
            ON anchors(status)
        """))

        await session.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_anchors_created_at
            ON anchors(created_at DESC)
        """))

        await session.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_anchor_items_anchor_id
            ON anchor_items(anchor_id)
        """))

        await session.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_anchor_items_event_hash
            ON anchor_items(event_hash)
        """))

        await session.commit()

    logger.info("Anchor tables verified")


async def close_db() -> None:
    """Close database connections gracefully."""
    await engine.dispose()
    logger.info("Database connections closed")


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Dependency for getting database session."""
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
