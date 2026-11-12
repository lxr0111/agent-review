from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.domain.review.enums import ReviewDispatchStatus, ReviewStatus
from app.models import Base
from app.models.review_dispatch_outbox import ReviewDispatchOutbox
from app.models.review_task import ReviewTask
from app.repositories.review_dispatch_outbox_repository import ReviewDispatchOutboxRepository
from app.repositories.review_task_repository import ReviewTaskRepository
from app.schemas.review import ReviewCreate
from app.services.review_dispatch_outbox_service import ReviewDispatchOutboxService
from app.services.review_service import ReviewService

NOW = datetime(2026, 11, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def factory() -> Iterator[sessionmaker[Session]]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine, expire_on_commit=False)
    engine.dispose()


def _add_pending(factory: sessionmaker[Session]) -> ReviewDispatchOutbox:
    with factory() as session:
        task = ReviewTask(
            id=uuid4(),
            repository="owner/repo",
            pull_request_number=1,
            head_sha="abcdef123456",
            status=ReviewStatus.QUEUED,
        )
        outbox = ReviewDispatchOutbox(
            review_task_id=task.id,
            status=ReviewDispatchStatus.PENDING,
            available_at=NOW.replace(tzinfo=None),
            attempt_count=0,
        )
        session.add_all((task, outbox))
        session.commit()
        return outbox


def test_review_creation_commits_task_and_dispatch_intent_together(
    factory: sessionmaker[Session],
) -> None:
    with factory() as session:
        service = ReviewService(
            ReviewTaskRepository(session), ReviewDispatchOutboxRepository(session)
        )
        task = service.create(
            ReviewCreate(repository="owner/repo", pull_request_number=1, head_sha="abcdef123456")
        )

    with factory() as session:
        record = session.scalar(select(ReviewDispatchOutbox))
        assert record is not None
        assert record.review_task_id == task.id
        assert record.status == ReviewDispatchStatus.PENDING


def test_review_creation_rolls_back_task_if_outbox_insert_fails(
    factory: sessionmaker[Session],
) -> None:
    with factory() as session:
        outbox = Mock()
        outbox.add_for_task.side_effect = RuntimeError("outbox unavailable")
        service = ReviewService(ReviewTaskRepository(session), outbox)

        with pytest.raises(RuntimeError, match="outbox unavailable"):
            service.create(
                ReviewCreate(
                    repository="owner/repo", pull_request_number=1, head_sha="abcdef123456"
                )
            )

    with factory() as session:
        assert session.scalar(select(func.count()).select_from(ReviewTask)) == 0
        assert session.scalar(select(func.count()).select_from(ReviewDispatchOutbox)) == 0


def test_dispatch_publishes_once_and_marks_sent(factory: sessionmaker[Session]) -> None:
    record = _add_pending(factory)
    publish = Mock()
    dispatcher = ReviewDispatchOutboxService(factory, publish, clock=lambda: NOW)

    assert dispatcher.dispatch_once() == 1
    assert dispatcher.dispatch_once() == 0
    publish.assert_called_once_with(str(record.review_task_id))

    with factory() as session:
        stored = session.get(ReviewDispatchOutbox, record.id)
        assert stored is not None
        assert stored.status == ReviewDispatchStatus.SENT
        assert stored.attempt_count == 1
        assert stored.lease_token is None


def test_redis_failure_leaves_intent_for_retry(factory: sessionmaker[Session]) -> None:
    record = _add_pending(factory)
    publish = Mock(side_effect=[ConnectionError("broker down"), None])
    current_time = [NOW]
    dispatcher = ReviewDispatchOutboxService(factory, publish, clock=lambda: current_time[0])

    assert dispatcher.dispatch_once() == 0
    with factory() as session:
        stored = session.get(ReviewDispatchOutbox, record.id)
        assert stored is not None
        assert stored.status == ReviewDispatchStatus.PENDING
        assert stored.last_error_code == "ConnectionError"
        assert stored.available_at == (NOW + timedelta(seconds=2)).replace(tzinfo=None)

    assert dispatcher.dispatch_once() == 0
    current_time[0] += timedelta(seconds=3)
    assert dispatcher.dispatch_once() == 1
    assert publish.call_count == 2

    with factory() as session:
        stored = session.get(ReviewDispatchOutbox, record.id)
        assert stored is not None
        assert stored.status == ReviewDispatchStatus.SENT
        assert stored.attempt_count == 2


def test_expired_lease_is_reclaimed(factory: sessionmaker[Session]) -> None:
    record = _add_pending(factory)
    with factory() as session:
        stored = session.get(ReviewDispatchOutbox, record.id)
        assert stored is not None
        stored.status = ReviewDispatchStatus.LEASED
        stored.lease_token = str(uuid4())
        stored.lease_until = (NOW - timedelta(seconds=1)).replace(tzinfo=None)
        session.commit()

    publish = Mock()
    dispatcher = ReviewDispatchOutboxService(factory, publish, clock=lambda: NOW)
    assert dispatcher.dispatch_once() == 1
    publish.assert_called_once_with(str(record.review_task_id))


def test_crash_after_publish_can_republish_after_lease_expiry(
    factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    record = _add_pending(factory)
    publish = Mock()
    current_time = [NOW]
    dispatcher = ReviewDispatchOutboxService(
        factory, publish, clock=lambda: current_time[0], lease_seconds=5
    )
    original_mark_sent = dispatcher._mark_sent
    monkeypatch.setattr(
        dispatcher, "_mark_sent", Mock(side_effect=RuntimeError("dispatcher crashed"))
    )

    with pytest.raises(RuntimeError, match="dispatcher crashed"):
        dispatcher.dispatch_once()
    current_time[0] += timedelta(seconds=6)
    monkeypatch.setattr(dispatcher, "_mark_sent", original_mark_sent)

    assert dispatcher.dispatch_once() == 1
    assert publish.call_count == 2
    with factory() as session:
        stored = session.get(ReviewDispatchOutbox, record.id)
        assert stored is not None
        assert stored.status == ReviewDispatchStatus.SENT


def test_stale_lease_owner_cannot_mark_new_claim_sent(factory: sessionmaker[Session]) -> None:
    _add_pending(factory)
    current_time = [NOW]
    dispatcher = ReviewDispatchOutboxService(
        factory, Mock(), clock=lambda: current_time[0], lease_seconds=5
    )
    first_claim = dispatcher._claim_due()[0]
    current_time[0] += timedelta(seconds=6)
    second_claim = dispatcher._claim_due()[0]

    assert first_claim.lease_token != second_claim.lease_token
    assert dispatcher._mark_sent(first_claim) is False
    assert dispatcher._mark_sent(second_claim) is True
