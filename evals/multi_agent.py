from time import perf_counter

from app.providers.llm.protocol import LLMTokenUsage
from app.services.planned_review_orchestrator import (
    PlannedAgentExecution,
    PlannedAgentExecutionError,
    PlannedReviewAggregationError,
    PlannedReviewOrchestrator,
    PlannedReviewResult,
)
from app.services.review_agent import AgentRole
from app.services.review_aggregator import AggregationAction
from app.services.review_planner import ReviewPlan
from evals.metrics import (
    AgentContributionEvaluation,
    AgentExecutionEvaluation,
    AggregationConflictEvaluation,
    AggregationDecisionEvaluation,
    CaseEvaluation,
    EvaluationExecutionMode,
    EvaluationOutcome,
    EvalUsage,
    PlannedAgentEvaluation,
    classify_error,
    error_case_evaluation,
    evaluated_findings,
    successful_case_evaluation,
)
from evals.schemas import EvalCase


def evaluate_multi_agent_case(
    case: EvalCase,
    orchestrator: PlannedReviewOrchestrator,
) -> CaseEvaluation:
    started = perf_counter()
    try:
        result = orchestrator.review_with_metadata(case.to_review_context())
    except PlannedAgentExecutionError as error:
        executions = tuple(
            _successful_execution(execution) for execution in error.completed_executions
        ) + (
            AgentExecutionEvaluation(
                role=error.failed_role,
                prompt_version=error.prompt_version,
                outcome=classify_error(error.cause),
                latency_ms=error.latency_ms,
                finding_count=0,
                usage=EvalUsage(),
                error_type=type(error.cause).__name__,
            ),
        )
        return error_case_evaluation(
            case,
            error.cause,
            latency_ms=(perf_counter() - started) * 1_000,
            agent_role=None,
            execution_mode=EvaluationExecutionMode.PLANNED_MULTI_AGENT,
            planned_agents=_planned_agents(error.plan),
            agent_executions=executions,
        )
    except PlannedReviewAggregationError as error:
        return error_case_evaluation(
            case,
            error.cause,
            latency_ms=(perf_counter() - started) * 1_000,
            agent_role=None,
            execution_mode=EvaluationExecutionMode.PLANNED_MULTI_AGENT,
            planned_agents=_planned_agents(error.plan),
            agent_executions=tuple(
                _successful_execution(execution) for execution in error.executions
            ),
        )

    return _successful_multi_agent_evaluation(
        case,
        result,
        latency_ms=(perf_counter() - started) * 1_000,
    )


def _successful_multi_agent_evaluation(
    case: EvalCase,
    result: PlannedReviewResult,
    *,
    latency_ms: float,
) -> CaseEvaluation:
    executions = tuple(_successful_execution(execution) for execution in result.executions)
    pre_aggregation_count = sum(execution.finding_count for execution in executions)
    merged_count = sum(
        decision.action == AggregationAction.MERGED for decision in result.aggregation.decisions
    )
    return successful_case_evaluation(
        case,
        result.report,
        latency_ms=latency_ms,
        usage=_total_usage(result.executions),
        agent_role=None,
        execution_mode=EvaluationExecutionMode.PLANNED_MULTI_AGENT,
        planned_agents=_planned_agents(result.plan),
        agent_executions=executions,
        pre_aggregation_finding_count=pre_aggregation_count,
        merged_finding_count=merged_count,
        aggregation_conflict_count=len(result.aggregation.conflicts),
        aggregation_decisions=tuple(
            AggregationDecisionEvaluation(
                action=decision.action,
                source_role=decision.source_role,
                file_path=decision.file_path,
                title=decision.title,
                canonical_fingerprint=decision.canonical_fingerprint,
                reason=decision.reason,
            )
            for decision in result.aggregation.decisions
        ),
        aggregation_conflicts=tuple(
            AggregationConflictEvaluation(
                canonical_fingerprint=conflict.canonical_fingerprint,
                field=conflict.field,
                selected_value=conflict.selected_value,
                observed_values=conflict.observed_values,
                reason=conflict.reason,
            )
            for conflict in result.aggregation.conflicts
        ),
        agent_contributions=_agent_contributions(result),
    )


def _planned_agents(plan: ReviewPlan) -> tuple[PlannedAgentEvaluation, ...]:
    return tuple(
        PlannedAgentEvaluation(role=agent.role, reasons=agent.reasons) for agent in plan.agents
    )


def _successful_execution(
    execution: PlannedAgentExecution,
) -> AgentExecutionEvaluation:
    return AgentExecutionEvaluation(
        role=execution.role,
        prompt_version=execution.prompt_version,
        outcome=EvaluationOutcome.SUCCESSFUL_CASE,
        latency_ms=execution.latency_ms,
        finding_count=len(execution.result.report.findings),
        findings=evaluated_findings(execution.result.report.findings),
        usage=_eval_usage(execution.result.usage),
    )


def _total_usage(
    executions: tuple[PlannedAgentExecution, ...],
) -> LLMTokenUsage | None:
    usages = tuple(execution.result.usage for execution in executions)
    if any(usage is None for usage in usages):
        return None
    complete_usages = tuple(usage for usage in usages if usage is not None)
    return LLMTokenUsage(
        input_tokens=sum(usage.input_tokens for usage in complete_usages),
        output_tokens=sum(usage.output_tokens for usage in complete_usages),
        total_tokens=sum(usage.total_tokens for usage in complete_usages),
    )


def _eval_usage(usage: LLMTokenUsage | None) -> EvalUsage:
    if usage is None:
        return EvalUsage()
    return EvalUsage(
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        total_tokens=usage.total_tokens,
    )


def _agent_contributions(
    result: PlannedReviewResult,
) -> tuple[AgentContributionEvaluation, ...]:
    decisions = result.aggregation.decisions
    roles_by_fingerprint: dict[str, set[AgentRole]] = {}
    for decision in decisions:
        roles_by_fingerprint.setdefault(decision.canonical_fingerprint, set()).add(
            decision.source_role
        )

    contributions: list[AgentContributionEvaluation] = []
    for execution in result.executions:
        role_decisions = tuple(
            decision for decision in decisions if decision.source_role == execution.role
        )
        contributions.append(
            AgentContributionEvaluation(
                role=execution.role,
                raw_finding_count=len(execution.result.report.findings),
                kept_finding_count=sum(
                    decision.action == AggregationAction.KEPT for decision in role_decisions
                ),
                merged_finding_count=sum(
                    decision.action == AggregationAction.MERGED for decision in role_decisions
                ),
                unique_finding_count=sum(
                    decision.action == AggregationAction.KEPT
                    and roles_by_fingerprint[decision.canonical_fingerprint] == {execution.role}
                    for decision in role_decisions
                ),
            )
        )
    return tuple(contributions)
