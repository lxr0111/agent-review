from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.domain.review.report import ReviewRiskLevel
from app.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.review_finding import ReviewFindingRecord
    from app.models.review_publication import ReviewPublicationRecord
    from app.models.review_task import ReviewTask


class ReviewReportRecord(TimestampMixin, Base):
    __tablename__ = "review_reports"
    __table_args__ = (
        UniqueConstraint("review_task_id", name="uq_review_reports_review_task_id"),
        Index("ix_review_reports_risk_generated", "risk_level", "generated_at"),
        CheckConstraint(
            "reviewed_file_count >= 0 AND skipped_file_count >= 0",
            name="ck_review_report_file_counts_non_negative",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    review_task_id: Mapped[UUID] = mapped_column(
        ForeignKey("review_tasks.id", ondelete="CASCADE"), nullable=False
    )
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    risk_level: Mapped[ReviewRiskLevel] = mapped_column(
        Enum(ReviewRiskLevel, native_enum=False, length=20), nullable=False
    )
    model_name: Mapped[str | None] = mapped_column(String(255))
    prompt_version: Mapped[str] = mapped_column(String(50), nullable=False)
    reviewed_file_count: Mapped[int] = mapped_column(Integer, nullable=False)
    skipped_file_count: Mapped[int] = mapped_column(Integer, nullable=False)
    content_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    review_task: Mapped["ReviewTask"] = relationship(back_populates="report")
    findings: Mapped[list["ReviewFindingRecord"]] = relationship(
        back_populates="review_report",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="ReviewFindingRecord.position",
    )
    publication: Mapped["ReviewPublicationRecord | None"] = relationship(
        back_populates="review_report",
        cascade="all, delete-orphan",
        passive_deletes=True,
        uselist=False,
    )
