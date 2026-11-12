import json
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.exceptions import InvalidWebhookPayload
from app.domain.review.enums import ReviewStatus, WebhookEventStatus
from app.models import Base
from app.models.review_dispatch_outbox import ReviewDispatchOutbox
from app.models.review_task import ReviewTask
from app.models.webhook_event import WebhookEvent
from app.services.webhook_service import GitHubWebhookService


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as database_session:
        yield database_session
    engine.dispose()


def _pull_request_body(action: str = "opened", head_sha: str = "abcdef123456") -> bytes:
    return json.dumps(
        {
            "action": action,
            "number": 17,
            "pull_request": {
                "number": 17,
                "base": {"sha": "123456abcdef"},
                "head": {"sha": head_sha},
            },
            "repository": {"id": 42, "full_name": "owner/repository"},
            "installation": {"id": 99},
        }
    ).encode()


def test_pull_request_event_creates_queued_review_and_audit_event(session: Session) -> None:
    result = GitHubWebhookService(session).handle(
        "delivery-1", "pull_request", _pull_request_body()
    )

    task = session.get(ReviewTask, result.review_task_id)
    event = session.scalar(select(WebhookEvent))

    assert result.status == "accepted"
    assert task is not None
    assert task.status == ReviewStatus.QUEUED
    assert task.repository == "owner/repository"
    assert task.github_repository_id == 42
    assert task.installation_id == 99
    assert task.base_sha == "123456abcdef"
    assert event is not None
    assert event.status == WebhookEventStatus.ACCEPTED
    assert event.review_task_id == task.id
    assert len(event.payload_digest) == 64
    assert session.scalar(select(func.count()).select_from(ReviewDispatchOutbox)) == 1


def test_same_delivery_is_idempotent(session: Session) -> None:
    service = GitHubWebhookService(session)
    first = service.handle("delivery-1", "pull_request", _pull_request_body())
    second = service.handle("delivery-1", "pull_request", _pull_request_body())

    task_count = session.scalar(select(func.count()).select_from(ReviewTask))
    event_count = session.scalar(select(func.count()).select_from(WebhookEvent))

    assert second.status == "duplicate"
    assert second.review_task_id == first.review_task_id
    assert task_count == 1
    assert event_count == 1
    assert session.scalar(select(func.count()).select_from(ReviewDispatchOutbox)) == 1


def test_same_delivery_with_different_body_is_rejected(session: Session) -> None:
    service = GitHubWebhookService(session)
    service.handle("delivery-1", "pull_request", _pull_request_body())

    with pytest.raises(InvalidWebhookPayload, match="reused"):
        service.handle(
            "delivery-1",
            "pull_request",
            _pull_request_body(head_sha="999999abcdef"),
        )

    task_count = session.scalar(select(func.count()).select_from(ReviewTask))
    event_count = session.scalar(select(func.count()).select_from(WebhookEvent))
    assert task_count == 1
    assert event_count == 1


def test_new_delivery_for_same_pr_commit_reuses_review_task(session: Session) -> None:
    service = GitHubWebhookService(session)
    first = service.handle("delivery-1", "pull_request", _pull_request_body())
    second = service.handle("delivery-2", "pull_request", _pull_request_body())

    task_count = session.scalar(select(func.count()).select_from(ReviewTask))
    event_count = session.scalar(select(func.count()).select_from(WebhookEvent))

    assert second.status == "duplicate"
    assert second.review_task_id == first.review_task_id
    assert task_count == 1
    assert event_count == 2
    assert session.scalar(select(func.count()).select_from(ReviewDispatchOutbox)) == 1


def test_outbox_failure_rolls_back_webhook_event_and_task(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = GitHubWebhookService(session)

    def fail_to_record(_task_id: object) -> None:
        raise RuntimeError("outbox insert failed")

    monkeypatch.setattr(service._outbox, "add_for_task", fail_to_record)
    with pytest.raises(RuntimeError, match="outbox insert failed"):
        service.handle("delivery-1", "pull_request", _pull_request_body())
    session.rollback()

    assert session.scalar(select(func.count()).select_from(WebhookEvent)) == 0
    assert session.scalar(select(func.count()).select_from(ReviewTask)) == 0
    assert session.scalar(select(func.count()).select_from(ReviewDispatchOutbox)) == 0


@pytest.mark.parametrize(
    ("event_type", "body", "expected_status"),
    [
        ("ping", b'{"zen":"Keep it logically awesome."}', "pong"),
        ("push", b'{"ref":"refs/heads/main"}', "ignored"),
        ("pull_request", _pull_request_body("closed"), "ignored"),
    ],
)
def test_irrelevant_events_are_recorded_without_creating_review(
    session: Session,
    event_type: str,
    body: bytes,
    expected_status: str,
) -> None:
    result = GitHubWebhookService(session).handle("delivery-1", event_type, body)

    task_count = session.scalar(select(func.count()).select_from(ReviewTask))
    event_count = session.scalar(select(func.count()).select_from(WebhookEvent))

    assert result.status == expected_status
    assert result.review_task_id is None
    assert task_count == 0
    assert event_count == 1
    assert session.scalar(select(func.count()).select_from(ReviewDispatchOutbox)) == 0
