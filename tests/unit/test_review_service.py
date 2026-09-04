from unittest.mock import Mock
from uuid import uuid4

from app.domain.review.enums import ReviewStatus
from app.models.review_task import ReviewTask
from app.schemas.review import ReviewCreate
from app.services.review_service import ReviewService


def test_create_review_queues_task() -> None:
    repository = Mock()
    repository.add.side_effect = lambda task: task
    service = ReviewService(repository)

    task = service.create(
        ReviewCreate(repository="owner/repo", pull_request_number=7, head_sha="abcdef123456")
    )

    assert task.status == ReviewStatus.QUEUED
    assert task.current_stage == "queued"
    repository.add.assert_called_once_with(task)
    repository.commit.assert_called_once()


def test_start_review_is_idempotent_when_already_running() -> None:
    task = ReviewTask(
        id=uuid4(),
        repository="owner/repo",
        pull_request_number=7,
        head_sha="abcdef123456",
        status=ReviewStatus.RUNNING,
        current_stage="simulated_review",
    )
    repository = Mock()
    repository.get.return_value = task

    result = ReviewService(repository).start(task.id)

    assert result is task
    repository.commit.assert_not_called()


def test_start_review_updates_stage_when_already_running() -> None:
    task = ReviewTask(
        id=uuid4(),
        repository="owner/repo",
        pull_request_number=7,
        head_sha="abcdef123456",
        status=ReviewStatus.RUNNING,
        current_stage="simulated_review",
        version=2,
    )
    repository = Mock()
    repository.get.return_value = task

    result = ReviewService(repository).start(task.id, stage="fetching_context")

    assert result.current_stage == "fetching_context"
    assert result.version == 3
    repository.commit.assert_called_once()


def test_mark_stale_records_reason_and_finishes_task() -> None:
    task = ReviewTask(
        id=uuid4(),
        repository="owner/repo",
        pull_request_number=7,
        head_sha="abcdef123456",
        status=ReviewStatus.RUNNING,
        current_stage="fetching_context",
        version=2,
    )
    repository = Mock()
    repository.get.return_value = task

    result = ReviewService(repository).mark_stale(
        task.id,
        "pull_request_head_changed",
        "The pull request received a newer commit",
    )

    assert result.status == ReviewStatus.STALE
    assert result.current_stage == "stale"
    assert result.error_code == "pull_request_head_changed"
    assert result.finished_at is not None
    assert result.version == 3
    repository.commit.assert_called_once()
