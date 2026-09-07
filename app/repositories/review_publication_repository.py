from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.review.enums import ReviewPublicationProvider
from app.models.review_publication import ReviewPublicationRecord


class ReviewPublicationRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_for_report(
        self,
        report_id: UUID,
        provider: ReviewPublicationProvider,
        *,
        for_update: bool = False,
    ) -> ReviewPublicationRecord | None:
        statement = select(ReviewPublicationRecord).where(
            ReviewPublicationRecord.review_report_id == report_id,
            ReviewPublicationRecord.provider == provider,
        )
        if for_update:
            statement = statement.with_for_update()
        return self._session.scalar(statement)

    def add(self, publication: ReviewPublicationRecord) -> ReviewPublicationRecord:
        self._session.add(publication)
        self._session.flush()
        return publication

    def commit(self) -> None:
        self._session.commit()

    def rollback(self) -> None:
        self._session.rollback()
