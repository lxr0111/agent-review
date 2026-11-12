import time

import structlog

from app.core.config import get_settings
from app.database.session import session_factory
from app.services.review_dispatch_outbox_service import ReviewDispatchOutboxService
from app.workers.review_tasks import run_review

logger = structlog.get_logger(__name__)


def _publish_review(task_id: str) -> None:
    run_review.apply_async(args=(task_id,), retry=False)


def main() -> None:
    settings = get_settings()
    dispatcher = ReviewDispatchOutboxService(
        session_factory,
        _publish_review,
        batch_size=settings.outbox_batch_size,
        lease_seconds=settings.outbox_lease_seconds,
    )
    logger.info("review_dispatcher_started")
    while True:
        try:
            dispatcher.dispatch_once()
        except Exception:
            logger.exception("review_dispatcher_cycle_failed")
        time.sleep(settings.outbox_poll_seconds)


if __name__ == "__main__":
    main()
