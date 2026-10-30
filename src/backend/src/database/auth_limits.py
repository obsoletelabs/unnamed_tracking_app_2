"""Small independent quota pool so authentication cannot exhaust business transactions.

The database URL and deployment credentials still come from the existing settings.
Only short counter transactions use this pool; it never reads user/business rows.
"""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.core.config import settings
from src.core.database_metrics import (
    ObservedAsyncPool,
    ObservedSyncSession,
    install_database_metrics,
    install_session_metrics,
)

auth_limit_engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DEBUG,
    poolclass=ObservedAsyncPool,
    pool_logging_name="authentication",
    pool_size=2,
    max_overflow=0,
    pool_timeout=2,
    pool_pre_ping=True,
)
install_database_metrics(auth_limit_engine.sync_engine, pool="authentication")
install_session_metrics()
AuthLimitSession = async_sessionmaker(
    auth_limit_engine,
    class_=AsyncSession,
    sync_session_class=ObservedSyncSession,
    expire_on_commit=False,
)
