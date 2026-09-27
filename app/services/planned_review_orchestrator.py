from collections.abc import Callable
from dataclasses import dataclass
from time import perf_counter

from app.domain.review.entities import ReviewContext
from app.domain.review.report import ReviewReport
from app.services.review_agent import AgentReviewResult, AgentRole
from app.services.review_agent_registry import ReviewAgentRegistry
from app.services.review_aggregator import (
    DeterministicReviewAggregator,
    ReviewAggregationResult,
)
from app.services.review_planner import DeterministicReviewPlanner, ReviewPlan


@dataclass(frozen=True, slots=True)
class PlannedAgentExecution:
    role: AgentRole
    prompt_version: str
    result: AgentReviewResult
    latency_ms: float


@dataclass(frozen=True, slots=True)
class PlannedReviewResult:
    plan: ReviewPlan
    executions: tuple[PlannedAgentExecution, ...]
    aggregation: ReviewAggregationResult

    @property
    def report(self) -> ReviewReport:
        return self.aggregation.report


class PlannedAgentExecutionError(Exception):
    def __init__(
        self,
        *,
        plan: ReviewPlan,
        completed_executions: tuple[PlannedAgentExecution, ...],
        failed_role: AgentRole,
        prompt_version: str,
        latency_ms: float,
        cause: Exception,
    ) -> None:
        super().__init__(f"Review agent {failed_role.value} failed: {cause}")
        self.plan = plan
        self.completed_executions = completed_executions
        self.failed_role = failed_role
        self.prompt_version = prompt_version
        self.latency_ms = latency_ms
        self.cause = cause


class PlannedReviewAggregationError(Exception):
    def __init__(
        self,
        *,
        plan: ReviewPlan,
        executions: tuple[PlannedAgentExecution, ...],
        cause: Exception,
    ) -> None:
        super().__init__(f"Review aggregation failed: {cause}")
        self.plan = plan
        self.executions = executions
        self.cause = cause


class PlannedReviewOrchestrator:
    """Runs a deterministic review plan sequentially and preserves execution metadata."""

    def __init__(
        self,
        *,
        planner: DeterministicReviewPlanner,
        registry: ReviewAgentRegistry,
        aggregator: DeterministicReviewAggregator,
        clock: Callable[[], float] = perf_counter,
    ) -> None:
        self._planner = planner
        self._registry = registry
        self._aggregator = aggregator
        self._clock = clock

    def review(self, context: ReviewContext) -> ReviewReport:
        return self.review_with_metadata(context).report

    def review_with_metadata(self, context: ReviewContext) -> PlannedReviewResult:
        plan = self._planner.plan(context)
        executions: list[PlannedAgentExecution] = []
        for planned_agent in plan.agents:
            agent = self._registry.get(planned_agent.role)
            started = self._clock()
            try:
                result = agent.review(context)
            except Exception as error:
                raise PlannedAgentExecutionError(
                    plan=plan,
                    completed_executions=tuple(executions),
                    failed_role=agent.role,
                    prompt_version=agent.prompt_version,
                    latency_ms=(self._clock() - started) * 1_000,
                    cause=error,
                ) from error
            executions.append(
                PlannedAgentExecution(
                    role=agent.role,
                    prompt_version=agent.prompt_version,
                    result=result,
                    latency_ms=(self._clock() - started) * 1_000,
                )
            )

        completed = tuple(executions)
        try:
            aggregation = self._aggregator.aggregate_with_metadata(
                context=context,
                results=tuple(execution.result for execution in completed),
            )
        except Exception as error:
            raise PlannedReviewAggregationError(
                plan=plan,
                executions=completed,
                cause=error,
            ) from error
        return PlannedReviewResult(
            plan=plan,
            executions=completed,
            aggregation=aggregation,
        )
