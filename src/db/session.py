import logging
from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy import text
from src.core.config import settings
from src.db.models import Base

logger = logging.getLogger(__name__)

# Determine database connection URL
if settings.USE_SQLITE:
    # Use SQLite for seamless zero-dependency local runs & testing
    db_url = f"sqlite+aiosqlite:///{settings.SQLITE_DB_PATH}"
    connect_args = {"check_same_thread": False}
else:
    # Use PostgreSQL / TimescaleDB
    db_url = settings.DATABASE_URL
    connect_args = {}

engine = create_async_engine(
    db_url,
    echo=settings.DEBUG,
    connect_args=connect_args,
    pool_pre_ping=True,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


async def init_database() -> None:
    """Initialize database tables and TimescaleDB hypertable if running on PostgreSQL."""
    async with engine.begin() as conn:
        # Create all tables defined in models
        await conn.run_sync(Base.metadata.create_all)

        # If PostgreSQL, attempt to create TimescaleDB extension and hypertable
        if not settings.USE_SQLITE:
            try:
                await conn.execute(text("CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;"))
                await conn.execute(
                    text(
                        "SELECT create_hypertable('driver_telemetry', 'time', "
                        "if_not_exists => TRUE, migrate_data => TRUE);"
                    )
                )
                logger.info("TimescaleDB extension and hypertable initialized successfully.")
            except Exception as e:
                logger.warning(
                    f"TimescaleDB hypertable creation skipped or already exists: {e}"
                )


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency yielding an async database session."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
