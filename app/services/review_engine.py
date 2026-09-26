from typing import Protocol, runtime_checkable

from app.domain.review.entities import ReviewContext
from app.domain.review.report import ReviewReport


@runtime_checkable
class ReviewEngine(Protocol):
    """Stable review boundary used by the production pipeline."""

    def review(self, context: ReviewContext) -> ReviewReport: ...
