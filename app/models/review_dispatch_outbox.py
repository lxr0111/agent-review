from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.review.enums import ReviewDispatchStatus
from app.models.base import Base, TimestampMixin


class ReviewDispatchOutbox(TimestampMixin, Base):
    __tablename__ = "review_dispatch_outbox"
    __table_args__ = (
        UniqueConstraint("review_task_id", name="uq_review_dispatch_outbox_task"),
        Index("ix_review_dispatch_outbox_status_available", "status", "available_at"),
        Index("ix_review_dispatch_outbox_status_lease", "status", "lease_until"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    review_task_id: Mapped[UUID] = mapped_column(
        ForeignKey("review_tasks.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[ReviewDispatchStatus] = mapped_column(
        Enum(ReviewDispatchStatus, native_enum=False, length=20),
        nullable=False,
        default=ReviewDispatchStatus.PENDING,
    )
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_token: Mapped[str | None] = mapped_column(String(36))
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error_code: Mapped[str | None] = mapped_column(String(100))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
