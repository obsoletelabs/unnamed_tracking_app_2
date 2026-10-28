from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from src.core.config import settings
from src.core.database_metrics import (
    ObservedAsyncPool,
    ObservedSyncSession,
    install_database_metrics,
    install_session_metrics,
)


class ObservedAsyncSession(AsyncSession):  # pylint: disable=abstract-method
    """Application session with a metrics-observed synchronous core."""

    sync_session_class = ObservedSyncSession

# The engine manages the connection pool to PostgreSQL.
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DEBUG,
    poolclass=ObservedAsyncPool,
)
install_database_metrics(engine.sync_engine)
install_session_metrics()


# Factory for creating AsyncSession objects.
SessionLocal = async_sessionmaker(
    bind=engine,
    class_=ObservedAsyncSession,
    expire_on_commit=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency that provides one database session
    for the lifetime of a request.
    """
    async with SessionLocal() as session:
        yield session
