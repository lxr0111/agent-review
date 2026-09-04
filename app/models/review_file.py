from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
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

from app.domain.review.enums import FileChangeStatus, FileSkipReason
from app.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.review_task import ReviewTask


class ReviewFile(TimestampMixin, Base):
    __tablename__ = "review_files"
    __table_args__ = (
        UniqueConstraint("review_task_id", "path_hash", name="uq_review_file_task_path"),
        Index("ix_review_files_task_selected", "review_task_id", "selected"),
        CheckConstraint("position >= 0", name="ck_review_file_position_non_negative"),
        CheckConstraint(
            "additions >= 0 AND deletions >= 0 AND changes >= 0",
            name="ck_review_file_counts_non_negative",
        ),
        CheckConstraint(
            "(selected = 1 AND skip_reason IS NULL) OR (selected = 0 AND skip_reason IS NOT NULL)",
            name="ck_review_file_selection_reason",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    review_task_id: Mapped[UUID] = mapped_column(
        ForeignKey("review_tasks.id", ondelete="CASCADE"), nullable=False
    )
    path: Mapped[str] = mapped_column(Text, nullable=False)
    path_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    previous_path: Mapped[str | None] = mapped_column(Text)
    status: Mapped[FileChangeStatus] = mapped_column(
        Enum(FileChangeStatus, native_enum=False, length=20), nullable=False
    )
    additions: Mapped[int] = mapped_column(Integer, nullable=False)
    deletions: Mapped[int] = mapped_column(Integer, nullable=False)
    changes: Mapped[int] = mapped_column(Integer, nullable=False)
    patch_digest: Mapped[str | None] = mapped_column(String(64))
    selected: Mapped[bool] = mapped_column(Boolean, nullable=False)
    skip_reason: Mapped[FileSkipReason | None] = mapped_column(
        Enum(FileSkipReason, native_enum=False, length=30)
    )
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    review_task: Mapped["ReviewTask"] = relationship(back_populates="files")
