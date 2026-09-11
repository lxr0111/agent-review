from typing import Never, Protocol
from uuid import UUID

import structlog

from app.core.config import get_settings
from app.database.session import session_factory
from app.domain.review.enums import ReviewStatus
from app.repositories.review_task_repository import ReviewTaskRepository
from app.services.review_service import ReviewService
from app.workers.celery_app import celery_app
from app.workers.pipeline_factory import review_pipeline_context
from app.workers.retry_policy import (
    is_retryable_review_error,
    retry_countdown_seconds,
    review_error_code,
)

logger = structlog.get_logger(__name__)
settings = get_settings()


class _TaskRequest(Protocol):
    retries: int


class _BoundTask(Protocol):
    request: _TaskRequest
    max_retries: int

    def retry(self, *, exc: Exception, countdown: int) -> Never: ...


@celery_app.task(  # type: ignore[untyped-decorator]
    bind=True,
    name="reviews.run",
    max_retries=settings.review_worker_max_retries,
)
def run_review(task: _BoundTask, task_id: str) -> None:
    parsed_task_id = UUID(task_id)
    try:
        with review_pipeline_context(settings) as pipeline:
            outcome = pipeline.run(parsed_task_id)
        logger.info(
            "review_pipeline_finished",
            review_task_id=task_id,
            outcome=outcome.value,
        )
    except Exception as error:
        retryable = is_retryable_review_error(error)
        if retryable and task.request.retries < task.max_retries:
            countdown = retry_countdown_seconds(
                error,
                retries=task.request.retries,
                base_seconds=settings.review_retry_base_seconds,
                max_seconds=settings.review_retry_max_seconds,
            )
            logger.warning(
                "review_pipeline_retry_scheduled",
                review_task_id=task_id,
                retry_number=task.request.retries + 1,
                countdown_seconds=countdown,
                error_code=review_error_code(error),
            )
            raise task.retry(exc=error, countdown=countdown)

        _mark_task_failed(parsed_task_id, error)
        logger.exception(
            "review_pipeline_failed",
            review_task_id=task_id,
            retryable=retryable,
            retries=task.request.retries,
            error_code=review_error_code(error),
        )
        raise


def _mark_task_failed(task_id: UUID, error: Exception) -> None:
    with session_factory() as session:
        service = ReviewService(ReviewTaskRepository(session))
        try:
            review_task = service.get(task_id)
            if review_task.status in {
                ReviewStatus.COMPLETED,
                ReviewStatus.FAILED,
                ReviewStatus.CANCELLED,
                ReviewStatus.STALE,
            }:
                session.rollback()
                return
            if review_task.status == ReviewStatus.QUEUED:
                service.start(task_id, stage="initialization_failed")
            service.fail(task_id, review_error_code(error), str(error))
        except Exception:
            session.rollback()
            logger.exception(
                "review_failure_status_update_failed",
                review_task_id=str(task_id),
            )
