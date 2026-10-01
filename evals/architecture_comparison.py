from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.services.review_agent import AgentRole
from evals.comparison import EvaluationRunComparison, compare_evaluation_runs
from evals.metrics import (
    CaseEvaluation,
    EvaluationExecutionMode,
    EvaluationOutcome,
    EvaluationRun,
)


class ArchitectureComparisonError(ValueError):
    pass


class ArchitectureCaseStatus(StrEnum):
    IMPROVED = "improved"
    REGRESSED = "regressed"
    MIXED = "mixed"
    UNCHANGED = "unchanged"


class ArchitectureCaseSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: EvaluationOutcome
    case_pass: bool
    matched_findings: int = Field(ge=0)
    false_positives: int = Field(ge=0)
    false_negatives: int = Field(ge=0)
    category_matches: int = Field(ge=0)
    severity_matches: int = Field(ge=0)


class ArchitectureCaseComparison(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    general: ArchitectureCaseSnapshot
    specialist: ArchitectureCaseSnapshot
    multi_agent: ArchitectureCaseSnapshot
    multi_agent_vs_general_status: ArchitectureCaseStatus
    multi_agent_vs_general_reasons: tuple[str, ...] = Field(min_length=1)


class ArchitectureEfficiency(BaseModel):
    model_config = ConfigDict(extra="forbid")

    additional_matched_findings_vs_general: int
    additional_false_positives_vs_general: int
    additional_total_tokens_vs_general: int | None
    additional_mean_latency_ms_vs_general: float | None
    matched_findings_per_extra_1k_tokens: float | None
    multi_agent_unique_findings: int = Field(ge=0)
    multi_agent_total_agent_calls: int = Field(ge=0)


class ArchitectureComparison(BaseModel):
    model_config = ConfigDict(extra="forbid")

    comparison_schema_version: Literal["agent-review-architecture-comparison-v1"]
    specialist_role: AgentRole
    specialist_vs_general: EvaluationRunComparison
    multi_agent_vs_general: EvaluationRunComparison
    multi_agent_vs_specialist: EvaluationRunComparison
    cases: tuple[ArchitectureCaseComparison, ...]
    efficiency: ArchitectureEfficiency


def compare_review_architectures(
    general: EvaluationRun,
    specialist: EvaluationRun,
    multi_agent: EvaluationRun,
) -> ArchitectureComparison:
    _validate_architecture_roles(general, specialist, multi_agent)
    specialist_vs_general = compare_evaluation_runs(general, specialist)
    multi_agent_vs_general = compare_evaluation_runs(general, multi_agent)
    multi_agent_vs_specialist = compare_evaluation_runs(specialist, multi_agent)
    specialist_role = specialist.agent_role
    if specialist_role is None:
        raise ArchitectureComparisonError("specialist run does not identify an agent role")
    return ArchitectureComparison(
        comparison_schema_version="agent-review-architecture-comparison-v1",
        specialist_role=specialist_role,
        specialist_vs_general=specialist_vs_general,
        multi_agent_vs_general=multi_agent_vs_general,
        multi_agent_vs_specialist=multi_agent_vs_specialist,
        cases=_case_comparisons(general, specialist, multi_agent),
        efficiency=_efficiency(general, multi_agent),
    )


def _validate_architecture_roles(
    general: EvaluationRun,
    specialist: EvaluationRun,
    multi_agent: EvaluationRun,
) -> None:
    for name, evaluation in (
        ("general", general),
        ("specialist", specialist),
        ("multi-agent", multi_agent),
    ):
        if evaluation.provider != "deepseek" or evaluation.run_kind not in {
            "live_evaluation",
            "multi_agent_live_evaluation",
        }:
            raise ArchitectureComparisonError(f"{name} must be a saved DeepSeek live evaluation")

    if (
        general.execution_mode != EvaluationExecutionMode.SINGLE_AGENT
        or general.agent_role != AgentRole.GENERAL
        or general.run_kind != "live_evaluation"
    ):
        raise ArchitectureComparisonError(
            "general artifact must contain a General single-agent live run"
        )
    if (
        specialist.execution_mode != EvaluationExecutionMode.SINGLE_AGENT
        or specialist.agent_role in {None, AgentRole.GENERAL}
        or specialist.run_kind != "live_evaluation"
    ):
        raise ArchitectureComparisonError(
            "specialist artifact must contain one explicit specialist live run"
        )
    if (
        multi_agent.execution_mode != EvaluationExecutionMode.PLANNED_MULTI_AGENT
        or multi_agent.agent_role is not None
        or multi_agent.run_kind != "multi_agent_live_evaluation"
    ):
        raise ArchitectureComparisonError(
            "multi-agent artifact must contain a Planned Multi-Agent live run"
        )


def _efficiency(
    general: EvaluationRun,
    multi_agent: EvaluationRun,
) -> ArchitectureEfficiency:
    matched_delta = multi_agent.metrics.matched_findings - general.metrics.matched_findings
    false_positive_delta = multi_agent.metrics.false_positives - general.metrics.false_positives
    general_tokens = general.metrics.usage.total_tokens
    multi_agent_tokens = multi_agent.metrics.usage.total_tokens
    token_delta = (
        multi_agent_tokens - general_tokens
        if general_tokens is not None and multi_agent_tokens is not None
        else None
    )
    general_latency = general.metrics.latency_mean_ms
    multi_agent_latency = multi_agent.metrics.latency_mean_ms
    latency_delta = (
        multi_agent_latency - general_latency
        if general_latency is not None and multi_agent_latency is not None
        else None
    )
    return ArchitectureEfficiency(
        additional_matched_findings_vs_general=matched_delta,
        additional_false_positives_vs_general=false_positive_delta,
        additional_total_tokens_vs_general=token_delta,
        additional_mean_latency_ms_vs_general=latency_delta,
        matched_findings_per_extra_1k_tokens=(
            matched_delta * 1_000 / token_delta
            if token_delta is not None and token_delta > 0
            else None
        ),
        multi_agent_unique_findings=sum(
            contribution.unique_finding_count
            for contribution in multi_agent.metrics.agent_contributions
        ),
        multi_agent_total_agent_calls=multi_agent.metrics.total_agent_calls,
    )


def _case_comparisons(
    general: EvaluationRun,
    specialist: EvaluationRun,
    multi_agent: EvaluationRun,
) -> tuple[ArchitectureCaseComparison, ...]:
    general_cases = {case.case_id: case for case in general.cases}
    specialist_cases = {case.case_id: case for case in specialist.cases}
    multi_agent_cases = {case.case_id: case for case in multi_agent.cases}
    return tuple(
        _case_comparison(
            general_cases[case_id],
            specialist_cases[case_id],
            multi_agent_cases[case_id],
        )
        for case_id in sorted(general_cases)
    )


def _case_comparison(
    general: CaseEvaluation,
    specialist: CaseEvaluation,
    multi_agent: CaseEvaluation,
) -> ArchitectureCaseComparison:
    improvements: list[str] = []
    regressions: list[str] = []
    _record_boolean_change(
        improvements,
        regressions,
        general.case_pass,
        multi_agent.case_pass,
        "case pass restored",
        "case pass lost",
    )
    _record_higher_is_better(
        improvements,
        regressions,
        general.matched_finding_count,
        multi_agent.matched_finding_count,
        "matched findings",
    )
    _record_lower_is_better(
        improvements,
        regressions,
        general.false_positive_count,
        multi_agent.false_positive_count,
        "false positives",
    )
    _record_lower_is_better(
        improvements,
        regressions,
        general.false_negative_count,
        multi_agent.false_negative_count,
        "false negatives",
    )
    _record_higher_is_better(
        improvements,
        regressions,
        general.category_match_count,
        multi_agent.category_match_count,
        "category matches",
    )
    _record_higher_is_better(
        improvements,
        regressions,
        general.severity_match_count,
        multi_agent.severity_match_count,
        "severity matches",
    )
    if general.outcome != multi_agent.outcome:
        message = (
            f"outcome changed from {general.outcome.value} to {multi_agent.outcome.value}"
        )
        if multi_agent.outcome == EvaluationOutcome.SUCCESSFUL_CASE:
            improvements.append(message)
        else:
            regressions.append(message)

    if improvements and regressions:
        status = ArchitectureCaseStatus.MIXED
    elif improvements:
        status = ArchitectureCaseStatus.IMPROVED
    elif regressions:
        status = ArchitectureCaseStatus.REGRESSED
    else:
        status = ArchitectureCaseStatus.UNCHANGED
    reasons = tuple(improvements + regressions) or ("no scored quality change",)
    return ArchitectureCaseComparison(
        case_id=general.case_id,
        general=_case_snapshot(general),
        specialist=_case_snapshot(specialist),
        multi_agent=_case_snapshot(multi_agent),
        multi_agent_vs_general_status=status,
        multi_agent_vs_general_reasons=reasons,
    )


def _case_snapshot(case: CaseEvaluation) -> ArchitectureCaseSnapshot:
    return ArchitectureCaseSnapshot(
        outcome=case.outcome,
        case_pass=case.case_pass,
        matched_findings=case.matched_finding_count,
        false_positives=case.false_positive_count,
        false_negatives=case.false_negative_count,
        category_matches=case.category_match_count,
        severity_matches=case.severity_match_count,
    )


def _record_boolean_change(
    improvements: list[str],
    regressions: list[str],
    reference: bool,
    candidate: bool,
    improved_message: str,
    regressed_message: str,
) -> None:
    if not reference and candidate:
        improvements.append(improved_message)
    elif reference and not candidate:
        regressions.append(regressed_message)


def _record_higher_is_better(
    improvements: list[str],
    regressions: list[str],
    reference: int,
    candidate: int,
    label: str,
) -> None:
    delta = candidate - reference
    if delta > 0:
        improvements.append(f"{label} increased by {delta}")
    elif delta < 0:
        regressions.append(f"{label} decreased by {-delta}")


def _record_lower_is_better(
    improvements: list[str],
    regressions: list[str],
    reference: int,
    candidate: int,
    label: str,
) -> None:
    delta = candidate - reference
    if delta < 0:
        improvements.append(f"{label} decreased by {-delta}")
    elif delta > 0:
        regressions.append(f"{label} increased by {delta}")
