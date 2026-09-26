from typing import Protocol, runtime_checkable

from app.domain.review.entities import ReviewContext
from app.domain.review.report import ReviewReport
from app.services.review_agent import AgentReviewResult


@runtime_checkable
class ReviewAggregator(Protocol):
    def aggregate(
        self,
        *,
        context: ReviewContext,
        results: tuple[AgentReviewResult, ...],
    ) -> ReviewReport: ...


class PassthroughReviewAggregator:
    """Returns the only agent report without changing production output."""

    def aggregate(
        self,
        *,
        context: ReviewContext,
        results: tuple[AgentReviewResult, ...],
    ) -> ReviewReport:
        del context
        if len(results) != 1:
            raise ValueError("Passthrough review aggregation requires exactly one result")
        return results[0].report
