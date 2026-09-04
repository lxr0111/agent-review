from datetime import UTC, datetime
from uuid import UUID

from app.core.exceptions import ReviewTaskNotFound
from app.domain.review.enums import ReviewStatus
from app.domain.review.state_machine import ensure_transition
from app.models.review_task import ReviewTask
from app.repositories.review_task_repository import ReviewTaskRepository
from app.schemas.review import ReviewCreate
from app.schemas.webhook import GitHubPullRequestPayload


class ReviewService:
    def __init__(self, repository: ReviewTaskRepository) -> None:
        self._repository = repository

    def create(self, payload: ReviewCreate) -> ReviewTask:
        task = ReviewTask(
            repository=payload.repository,
            pull_request_number=payload.pull_request_number,
            head_sha=payload.head_sha,
            status=ReviewStatus.RECEIVED,
            current_stage="received",
            attempt_count=0,
            version=1,
        )
        self._repository.add(task)
        self._transition(task, ReviewStatus.QUEUED, "queued")
        self._repository.commit()
        return task

    def create_from_github(self, payload: GitHubPullRequestPayload, delivery_id: str) -> ReviewTask:
        task = ReviewTask(
            repository=payload.repository.full_name,
            github_repository_id=payload.repository.id,
            installation_id=payload.installation.id,
            pull_request_number=payload.number,
            base_sha=payload.pull_request.base.sha,
            head_sha=payload.pull_request.head.sha,
            delivery_id=delivery_id,
            status=ReviewStatus.RECEIVED,
            current_stage="received",
            attempt_count=0,
            version=1,
        )
        self._repository.add(task)
        self._transition(task, ReviewStatus.QUEUED, "queued")
        return task

    def get(self, task_id: UUID) -> ReviewTask:
        task = self._repository.get(task_id)
        if task is None:
            raise ReviewTaskNotFound(f"Review task {task_id} was not found")
        return task

    def start(self, task_id: UUID, stage: str = "simulated_review") -> ReviewTask:
        task = self._get_for_update(task_id)
        if task.status == ReviewStatus.RUNNING:
            if task.current_stage != stage:
                task.current_stage = stage
                task.version += 1
                self._repository.commit()
            return task
        ensure_transition(task.status, ReviewStatus.RUNNING)
        task.status = ReviewStatus.RUNNING
        task.current_stage = stage
        task.started_at = datetime.now(UTC)
        task.attempt_count += 1
        task.version += 1
        self._repository.commit()
        return task

    def mark_stale(self, task_id: UUID, error_code: str, message: str) -> ReviewTask:
        task = self._get_for_update(task_id)
        if task.status == ReviewStatus.STALE:
            return task
        ensure_transition(task.status, ReviewStatus.STALE)
        task.status = ReviewStatus.STALE
        task.current_stage = "stale"
        task.error_code = error_code
        task.error_message = message[:2000]
        task.finished_at = datetime.now(UTC)
        task.version += 1
        self._repository.commit()
        return task

    def complete(self, task_id: UUID) -> ReviewTask:
        task = self._get_for_update(task_id)
        if task.status == ReviewStatus.COMPLETED:
            return task
        ensure_transition(task.status, ReviewStatus.COMPLETED)
        task.status = ReviewStatus.COMPLETED
        task.current_stage = "completed"
        task.finished_at = datetime.now(UTC)
        task.version += 1
        self._repository.commit()
        return task

    def fail(self, task_id: UUID, error_code: str, message: str) -> ReviewTask:
        task = self._get_for_update(task_id)
        if task.status == ReviewStatus.FAILED:
            return task
        ensure_transition(task.status, ReviewStatus.FAILED)
        task.status = ReviewStatus.FAILED
        task.current_stage = "failed"
        task.error_code = error_code
        task.error_message = message[:2000]
        task.finished_at = datetime.now(UTC)
        task.version += 1
        self._repository.commit()
        return task

    def _get_for_update(self, task_id: UUID) -> ReviewTask:
        task = self._repository.get(task_id, for_update=True)
        if task is None:
            raise ReviewTaskNotFound(f"Review task {task_id} was not found")
        return task

    @staticmethod
    def _transition(task: ReviewTask, status: ReviewStatus, stage: str) -> None:
        ensure_transition(task.status, status)
        task.status = status
        task.current_stage = stage
        task.version += 1
