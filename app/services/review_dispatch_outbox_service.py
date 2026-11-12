from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID, uuid4

import structlog
from sqlalchemy import and_, or_, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from app.domain.review.enums import ReviewDispatchStatus
from app.models.review_dispatch_outbox import ReviewDispatchOutbox

logger = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class DispatchClaim:
    outbox_id: UUID
    review_task_id: UUID
    lease_token: str
    attempt_count: int


class ReviewDispatchOutboxService:
    def __init__(
        self,
        session_factory: Callable[[], Session],
        publish_task: Callable[[str], None],
        *,
        batch_size: int = 25,
        lease_seconds: int = 60,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if batch_size < 1 or lease_seconds < 1:
            raise ValueError("Outbox batch size and lease duration must be positive")
        self._session_factory = session_factory
        self._publish_task = publish_task
        self._batch_size = batch_size
        self._lease_seconds = lease_seconds
        self._clock = clock or (lambda: datetime.now(UTC))

    def dispatch_once(self) -> int:
        claims = self._claim_due()
        sent = 0
        for claim in claims:
            try:
                self._publish_task(str(claim.review_task_id))
            except Exception as error:  # noqa: BLE001 - broker failures must leave the intent retryable
                self._mark_failed(claim, error)
                logger.warning(
                    "review_dispatch_retry_scheduled",
                    review_task_id=str(claim.review_task_id),
                    attempt_count=claim.attempt_count,
                    error_type=type(error).__name__,
                )
                continue
            if self._mark_sent(claim):
                sent += 1
                logger.info("review_dispatch_sent", review_task_id=str(claim.review_task_id))
            else:
                logger.warning(
                    "review_dispatch_lease_lost_after_publish",
                    review_task_id=str(claim.review_task_id),
                )
        return sent

    def _claim_due(self) -> tuple[DispatchClaim, ...]:
        now = self._now()
        with self._session_factory() as session:
            rows = session.scalars(
                select(ReviewDispatchOutbox)
                .where(
                    or_(
                        and_(
                            ReviewDispatchOutbox.status == ReviewDispatchStatus.PENDING,
                            ReviewDispatchOutbox.available_at <= now,
                        ),
                        and_(
                            ReviewDispatchOutbox.status == ReviewDispatchStatus.LEASED,
                            ReviewDispatchOutbox.lease_until <= now,
                        ),
                    )
                )
                .order_by(ReviewDispatchOutbox.created_at, ReviewDispatchOutbox.id)
                .limit(self._batch_size)
                .with_for_update(skip_locked=True)
            ).all()
            claims = []
            for row in rows:
                token = str(uuid4())
                row.status = ReviewDispatchStatus.LEASED
                row.lease_token = token
                row.lease_until = now + timedelta(seconds=self._lease_seconds)
                row.attempt_count += 1
                claims.append(DispatchClaim(row.id, row.review_task_id, token, row.attempt_count))
            session.commit()
        return tuple(claims)

    def _mark_sent(self, claim: DispatchClaim) -> bool:
        with self._session_factory() as session:
            result = cast(
                CursorResult[Any],
                session.execute(
                    update(ReviewDispatchOutbox)
                    .where(
                        ReviewDispatchOutbox.id == claim.outbox_id,
                        ReviewDispatchOutbox.status == ReviewDispatchStatus.LEASED,
                        ReviewDispatchOutbox.lease_token == claim.lease_token,
                    )
                    .values(
                        status=ReviewDispatchStatus.SENT,
                        sent_at=self._now(),
                        lease_token=None,
                        lease_until=None,
                        last_error_code=None,
                    )
                ),
            )
            session.commit()
            return result.rowcount == 1

    def _mark_failed(self, claim: DispatchClaim, error: Exception) -> None:
        delay = min(300, 2 ** min(claim.attempt_count, 8))
        with self._session_factory() as session:
            session.execute(
                update(ReviewDispatchOutbox)
                .where(
                    ReviewDispatchOutbox.id == claim.outbox_id,
                    ReviewDispatchOutbox.status == ReviewDispatchStatus.LEASED,
                    ReviewDispatchOutbox.lease_token == claim.lease_token,
                )
                .values(
                    status=ReviewDispatchStatus.PENDING,
                    available_at=self._now() + timedelta(seconds=delay),
                    lease_token=None,
                    lease_until=None,
                    last_error_code=type(error).__name__[:100],
                )
            )
            session.commit()

    def _now(self) -> datetime:
        now = self._clock()
        if now.tzinfo is None:
            raise ValueError("Outbox clock must be timezone-aware")
        return now.astimezone(UTC).replace(tzinfo=None)
