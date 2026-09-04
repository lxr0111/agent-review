from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.review_task import ReviewTask


class ReviewTaskRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, task: ReviewTask) -> ReviewTask:
        self._session.add(task)
        self._session.flush()
        return task

    def get(self, task_id: UUID, *, for_update: bool = False) -> ReviewTask | None:
        statement = select(ReviewTask).where(ReviewTask.id == task_id)
        if for_update:
            statement = statement.with_for_update()
        return self._session.scalar(statement)

    def get_by_identity(
        self, repository: str, pull_request_number: int, head_sha: str
    ) -> ReviewTask | None:
        statement = select(ReviewTask).where(
            ReviewTask.repository == repository,
            ReviewTask.pull_request_number == pull_request_number,
            ReviewTask.head_sha == head_sha,
        )
        return self._session.scalar(statement)

    def commit(self) -> None:
        self._session.commit()

    def rollback(self) -> None:
        self._session.rollback()
