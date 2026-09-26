from collections.abc import Iterable

from app.domain.review.entities import ReviewContext
from app.domain.review.report import ReviewReport
from app.services.review_agent import AgentReviewResult, ReviewAgent
from app.services.review_aggregator import ReviewAggregator


class ReviewOrchestrator:
    """Runs configured review agents and delegates result composition."""

    def __init__(
        self,
        *,
        agents: Iterable[ReviewAgent],
        aggregator: ReviewAggregator,
    ) -> None:
        configured_agents = tuple(agents)
        if not configured_agents:
            raise ValueError("Review orchestrator requires at least one agent")
        self._agents = configured_agents
        self._aggregator = aggregator

    def review(self, context: ReviewContext) -> ReviewReport:
        results = tuple(
            AgentReviewResult(role=agent.role, report=agent.review(context))
            for agent in self._agents
        )
        return self._aggregator.aggregate(context=context, results=results)
