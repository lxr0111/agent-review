import os
from datetime import UTC, datetime
from unittest.mock import Mock
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, delete, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app.domain.review.enums import ReviewDispatchStatus, ReviewStatus
from app.models.review_dispatch_outbox import ReviewDispatchOutbox
from app.models.review_task import ReviewTask
from app.services.review_dispatch_outbox_service import ReviewDispatchOutboxService

pytestmark = pytest.mark.integration


def test_mysql_dispatcher_skips_row_claimed_by_another_transaction() -> None:
    if os.environ.get("RUN_MYSQL_INTEGRATION") != "1":
        pytest.skip("Set RUN_MYSQL_INTEGRATION=1 against an isolated migrated MySQL database")

    database_url = os.environ["DATABASE_URL"]
    database_name = make_url(database_url).database or ""
    if database_name != "agent_review_ci" and not database_name.startswith(
        "agent_review_outbox_validation_"
    ):
        pytest.fail("Outbox locking test requires a dedicated integration database")

    engine = create_engine(database_url)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    task_id = uuid4()
    outbox_id = uuid4()
    now = datetime.now(UTC)
    try:
        with factory() as session:
            session.add(
                ReviewTask(
                    id=task_id,
                    repository="outbox-integration/repo",
                    pull_request_number=1,
                    head_sha=uuid4().hex,
                    status=ReviewStatus.QUEUED,
                )
            )
            session.flush()
            session.add(
                ReviewDispatchOutbox(
                    id=outbox_id,
                    review_task_id=task_id,
                    status=ReviewDispatchStatus.PENDING,
                    available_at=now.replace(tzinfo=None),
                    attempt_count=0,
                )
            )
            session.commit()

        publish = Mock()
        dispatcher = ReviewDispatchOutboxService(factory, publish, clock=lambda: now)
        with factory() as first_session:
            locked = first_session.scalar(
                select(ReviewDispatchOutbox)
                .where(ReviewDispatchOutbox.id == outbox_id)
                .with_for_update()
            )
            assert locked is not None
            assert dispatcher.dispatch_once() == 0
            publish.assert_not_called()
            first_session.rollback()

        assert dispatcher.dispatch_once() == 1
        publish.assert_called_once_with(str(task_id))
    finally:
        with factory() as session:
            session.execute(
                delete(ReviewDispatchOutbox).where(ReviewDispatchOutbox.id == outbox_id)
            )
            session.execute(delete(ReviewTask).where(ReviewTask.id == task_id))
            session.commit()
        engine.dispose()
