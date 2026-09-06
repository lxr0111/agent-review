from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.domain.review.report import FindingCategory, FindingSeverity
from app.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.review_report import ReviewReportRecord


class ReviewFindingRecord(TimestampMixin, Base):
    __tablename__ = "review_findings"
    __table_args__ = (
        UniqueConstraint(
            "review_report_id",
            "position",
            name="uq_review_finding_report_position",
        ),
        Index(
            "ix_review_findings_report_severity",
            "review_report_id",
            "severity",
        ),
        CheckConstraint("position >= 0", name="ck_review_finding_position_non_negative"),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_review_finding_confidence_range",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    review_report_id: Mapped[UUID] = mapped_column(
        ForeignKey("review_reports.id", ondelete="CASCADE"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    file_path_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    suggestion: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[FindingSeverity] = mapped_column(
        Enum(FindingSeverity, native_enum=False, length=20), nullable=False
    )
    category: Mapped[FindingCategory] = mapped_column(
        Enum(FindingCategory, native_enum=False, length=30), nullable=False
    )
    confidence: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False)

    review_report: Mapped["ReviewReportRecord"] = relationship(back_populates="findings")
