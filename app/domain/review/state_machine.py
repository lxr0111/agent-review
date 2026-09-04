from app.core.exceptions import InvalidStateTransition
from app.domain.review.enums import ReviewStatus

ALLOWED_TRANSITIONS: dict[ReviewStatus, frozenset[ReviewStatus]] = {
    ReviewStatus.RECEIVED: frozenset({ReviewStatus.QUEUED, ReviewStatus.CANCELLED}),
    ReviewStatus.QUEUED: frozenset({ReviewStatus.RUNNING, ReviewStatus.CANCELLED}),
    ReviewStatus.RUNNING: frozenset(
        {
            ReviewStatus.COMPLETED,
            ReviewStatus.FAILED,
            ReviewStatus.CANCELLED,
            ReviewStatus.STALE,
        }
    ),
    ReviewStatus.FAILED: frozenset({ReviewStatus.QUEUED, ReviewStatus.CANCELLED}),
    ReviewStatus.COMPLETED: frozenset(),
    ReviewStatus.CANCELLED: frozenset(),
    ReviewStatus.STALE: frozenset(),
}


def ensure_transition(current: ReviewStatus, target: ReviewStatus) -> None:
    if target not in ALLOWED_TRANSITIONS[current]:
        raise InvalidStateTransition(f"Cannot transition review task from {current} to {target}")
