from uuid import UUID

import structlog

from app.database.session import session_factory
from app.repositories.review_task_repository import ReviewTaskRepository
from app.services.review_service import ReviewService
from app.workers.celery_app import celery_app

logger = structlog.get_logger(__name__)


@celery_app.task(  # type: ignore[untyped-decorator]
    name="reviews.run",
    autoretry_for=(ConnectionError,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=3,
)
def run_review(task_id: str) -> None:
    parsed_task_id = UUID(task_id)
    with session_factory() as session:
        service = ReviewService(ReviewTaskRepository(session))
        try:
            service.start(parsed_task_id)
            logger.info("simulated_review_started", review_task_id=task_id)
            service.complete(parsed_task_id)
            logger.info("simulated_review_completed", review_task_id=task_id)
        except Exception as exc:
            session.rollback()
            try:
                service.fail(parsed_task_id, "simulated_review_failed", str(exc))
            except Exception:
                session.rollback()
                logger.exception("review_failure_status_update_failed", review_task_id=task_id)
            raise
