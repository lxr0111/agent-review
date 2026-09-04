from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Enum, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.domain.review.enums import WebhookEventStatus
from app.models.base import Base, TimestampMixin
from app.models.review_task import ReviewTask


class WebhookEvent(TimestampMixin, Base):
    __tablename__ = "webhook_events"
    __table_args__ = (
        UniqueConstraint("provider", "delivery_id", name="uq_webhook_event_delivery"),
        Index("ix_webhook_events_type_created", "event_type", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    provider: Mapped[str] = mapped_column(String(30), nullable=False)
    delivery_id: Mapped[str] = mapped_column(String(100), nullable=False)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    action: Mapped[str | None] = mapped_column(String(100))
    payload_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[WebhookEventStatus] = mapped_column(
        Enum(WebhookEventStatus, native_enum=False, length=20), nullable=False
    )
    review_task_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("review_tasks.id", ondelete="SET NULL"), index=True
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(100))

    review_task: Mapped[ReviewTask | None] = relationship()
