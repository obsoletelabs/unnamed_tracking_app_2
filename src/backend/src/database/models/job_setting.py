import time
from uuid import UUID

from sqlalchemy import BigInteger, Boolean, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from src.database.base import Base


class JobSetting(Base):
    # Repeated declarations preserve independent database table/enum contracts.
    # pylint: disable=duplicate-code
    """How one cleanup job is scheduled: whether it runs by itself, how
    often, and when it last ran. A job with no row has never been set up, which
    means the job's own default. Server jobs have no user_id and are managed by an
    administrator; personal plugin jobs are bound to their owning account."""

    __tablename__ = "job_settings"

    job_id: Mapped[str] = mapped_column(String(50), primary_key=True)
    user_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    interval_minutes: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1440, server_default="1440"
    )
    last_run_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    last_result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Run control and health. The lease makes overlap protection survive restarts and workers;
    # an expired lease from a crashed process simply lets the saved checkpoint resume.
    paused: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    cancel_requested: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="idle", server_default="idle"
    )
    last_success_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    failure_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    retry_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    lease_token: Mapped[str | None] = mapped_column(String(32), nullable=True)
    lease_expires_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    updated_at: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=lambda: int(time.time()),
        onupdate=lambda: int(time.time()),
    )
