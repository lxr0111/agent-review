import pytest

from app.core.exceptions import InvalidStateTransition
from app.domain.review.enums import ReviewStatus
from app.domain.review.state_machine import ensure_transition


def test_received_task_can_be_queued() -> None:
    ensure_transition(ReviewStatus.RECEIVED, ReviewStatus.QUEUED)


def test_completed_task_cannot_run_again() -> None:
    with pytest.raises(InvalidStateTransition):
        ensure_transition(ReviewStatus.COMPLETED, ReviewStatus.RUNNING)
