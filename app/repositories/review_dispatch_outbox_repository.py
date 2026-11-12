from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.review_dispatch_outbox import ReviewDispatchOutbox


class ReviewDispatchOutboxRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add_for_task(self, task_id: UUID) -> ReviewDispatchOutbox:
        record = ReviewDispatchOutbox(
            review_task_id=task_id,
            available_at=datetime.now(UTC).replace(tzinfo=None),
            attempt_count=0,
        )
        self._session.add(record)
        return record
