from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
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

from app.domain.review.enums import ReviewPublicationProvider, ReviewPublicationStatus
from app.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.review_report import ReviewReportRecord


class ReviewPublicationRecord(TimestampMixin, Base):
    __tablename__ = "review_publications"
    __table_args__ = (
        UniqueConstraint(
            "review_report_id",
            "provider",
            name="uq_review_publication_report_provider",
        ),
        UniqueConstraint(
            "provider",
            "external_review_id",
            name="uq_review_publication_provider_external_id",
        ),
        Index("ix_review_publications_status_updated", "status", "updated_at"),
        CheckConstraint(
            "attempt_count >= 0",
            name="ck_review_publication_attempt_count_non_negative",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    review_report_id: Mapped[UUID] = mapped_column(
        ForeignKey("review_reports.id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[ReviewPublicationProvider] = mapped_column(
        Enum(ReviewPublicationProvider, native_enum=False, length=20), nullable=False
    )
    status: Mapped[ReviewPublicationStatus] = mapped_column(
        Enum(ReviewPublicationStatus, native_enum=False, length=20), nullable=False
    )
    commit_sha: Mapped[str] = mapped_column(String(64), nullable=False)
    body_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    external_review_id: Mapped[int | None] = mapped_column(BigInteger)
    external_review_url: Mapped[str | None] = mapped_column(Text)
    external_state: Mapped[str | None] = mapped_column(String(50))
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error_code: Mapped[str | None] = mapped_column(String(100))
    last_error_message: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    review_report: Mapped["ReviewReportRecord"] = relationship(back_populates="publication")
