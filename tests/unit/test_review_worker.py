from collections.abc import Iterator
from contextlib import contextmanager
from unittest.mock import Mock
from uuid import UUID, uuid4

import pytest
from celery.exceptions import Retry

from app.providers.llm.exceptions import LLMTransientError
from app.services.review_pipeline_service import ReviewPipelineOutcome
from app.workers import review_tasks


@contextmanager
def _pipeline_context(pipeline: Mock) -> Iterator[Mock]:
    yield pipeline


def test_worker_runs_pipeline_to_completion(monkeypatch: pytest.MonkeyPatch) -> None:
    pipeline = Mock()
    pipeline.run.return_value = ReviewPipelineOutcome.COMPLETED
    monkeypatch.setattr(
        review_tasks,
        "review_pipeline_context",
        lambda settings: _pipeline_context(pipeline),
    )
    task_id = str(uuid4())

    review_tasks.run_review.run(task_id)

    pipeline.run.assert_called_once_with(UUID(task_id))


def test_worker_schedules_retry_for_transient_provider_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipeline = Mock()
    error = LLMTransientError("OpenAI temporarily unavailable")
    pipeline.run.side_effect = error
    monkeypatch.setattr(
        review_tasks,
        "review_pipeline_context",
        lambda settings: _pipeline_context(pipeline),
    )
    monkeypatch.setattr(review_tasks, "retry_countdown_seconds", lambda *args, **kwargs: 17)
    mark_failed = Mock()
    monkeypatch.setattr(review_tasks, "_mark_task_failed", mark_failed)
    retry = Mock(side_effect=Retry("scheduled"))
    monkeypatch.setattr(review_tasks.run_review, "retry", retry)

    with pytest.raises(Retry, match="scheduled"):
        review_tasks.run_review.run(str(uuid4()))

    retry.assert_called_once_with(exc=error, countdown=17)
    mark_failed.assert_not_called()


def test_worker_marks_permanent_error_as_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    pipeline = Mock()
    error = ValueError("invalid review output")
    pipeline.run.side_effect = error
    monkeypatch.setattr(
        review_tasks,
        "review_pipeline_context",
        lambda settings: _pipeline_context(pipeline),
    )
    mark_failed = Mock()
    monkeypatch.setattr(review_tasks, "_mark_task_failed", mark_failed)
    task_id = uuid4()

    with pytest.raises(ValueError, match="invalid review output"):
        review_tasks.run_review.run(str(task_id))

    mark_failed.assert_called_once_with(task_id, error)


def test_worker_marks_transient_error_failed_after_retries_exhausted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipeline = Mock()
    error = LLMTransientError("OpenAI remained unavailable")
    pipeline.run.side_effect = error
    monkeypatch.setattr(
        review_tasks,
        "review_pipeline_context",
        lambda settings: _pipeline_context(pipeline),
    )
    monkeypatch.setattr(review_tasks.run_review, "max_retries", 0)
    mark_failed = Mock()
    monkeypatch.setattr(review_tasks, "_mark_task_failed", mark_failed)
    task_id = uuid4()

    with pytest.raises(LLMTransientError, match="remained unavailable"):
        review_tasks.run_review.run(str(task_id))

    mark_failed.assert_called_once_with(task_id, error)
