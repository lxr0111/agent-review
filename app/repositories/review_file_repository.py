from collections.abc import Iterable
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.review_file import ReviewFile


class ReviewFileRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def replace_for_task(self, task_id: UUID, files: Iterable[ReviewFile]) -> None:
        self._session.execute(delete(ReviewFile).where(ReviewFile.review_task_id == task_id))
        self._session.add_all(files)
        self._session.flush()

    def list_for_task(self, task_id: UUID) -> tuple[ReviewFile, ...]:
        statement = (
            select(ReviewFile)
            .where(ReviewFile.review_task_id == task_id)
            .order_by(ReviewFile.position, ReviewFile.id)
        )
        return tuple(self._session.scalars(statement))
