from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, DateTime, Enum, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.domain.review.enums import ReviewStatus
from app.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.review_file import ReviewFile
    from app.models.review_report import ReviewReportRecord


class ReviewTask(TimestampMixin, Base):
    __tablename__ = "review_tasks"
    __table_args__ = (
        UniqueConstraint(
            "repository", "pull_request_number", "head_sha", name="uq_review_task_identity"
        ),
        Index("ix_review_tasks_status_created", "status", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    repository: Mapped[str] = mapped_column(String(255), nullable=False)
    github_repository_id: Mapped[int | None] = mapped_column(BigInteger)
    installation_id: Mapped[int | None] = mapped_column(BigInteger)
    pull_request_number: Mapped[int] = mapped_column(Integer, nullable=False)
    base_sha: Mapped[str | None] = mapped_column(String(64))
    head_sha: Mapped[str] = mapped_column(String(64), nullable=False)
    delivery_id: Mapped[str | None] = mapped_column(String(100), index=True)
    status: Mapped[ReviewStatus] = mapped_column(
        Enum(ReviewStatus, native_enum=False, length=20), default=ReviewStatus.RECEIVED
    )
    current_stage: Mapped[str] = mapped_column(String(50), default="received")
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(100))
    error_message: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    files: Mapped[list["ReviewFile"]] = relationship(
        back_populates="review_task",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    report: Mapped["ReviewReportRecord | None"] = relationship(
        back_populates="review_task",
        cascade="all, delete-orphan",
        passive_deletes=True,
        uselist=False,
    )
