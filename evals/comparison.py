import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.domain.review.report import FindingCategory, FindingSeverity
from app.services.review_agent import AgentRole
from evals.baseline import ModelBaseline
from evals.metrics import CaseEvaluation, EvaluatedFinding, EvaluationRun

MIN_FINDING_SIMILARITY = 0.30


class BaselineComparisonError(ValueError):
    pass


class PreferredDirection(StrEnum):
    HIGHER = "higher"
    LOWER = "lower"


class DeltaStatus(StrEnum):
    IMPROVED = "improved"
    REGRESSED = "regressed"
    UNCHANGED = "unchanged"
    UNAVAILABLE = "unavailable"


class BaselineDescriptor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    model_name: str | None
    prompt_version: str
    agent_role: AgentRole
    dataset_version: str
    dataset_sha256: str
    git_commit: str
    git_dirty: bool
    executed_at: datetime


class MetricComparison(BaseModel):
    model_config = ConfigDict(extra="forbid")

    baseline: float | None
    candidate: float | None
    delta: float | None
    preferred_direction: PreferredDirection
    status: DeltaStatus


class FindingSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    file_path: str
    category: FindingCategory
    severity: FindingSeverity


class FindingLabelChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    baseline: FindingSnapshot
    candidate: FindingSnapshot
    category_changed: bool
    severity_changed: bool


class CaseComparison(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    baseline_case_pass: bool
    candidate_case_pass: bool
    baseline_matched_findings: int
    candidate_matched_findings: int
    baseline_false_positives: int
    candidate_false_positives: int
    baseline_false_negatives: int
    candidate_false_negatives: int
    added_findings: tuple[FindingSnapshot, ...]
    removed_findings: tuple[FindingSnapshot, ...]
    label_changes: tuple[FindingLabelChange, ...]


class BaselineComparison(BaseModel):
    model_config = ConfigDict(extra="forbid")

    comparison_schema_version: Literal["agent-review-baseline-comparison-v1"]
    baseline: BaselineDescriptor
    candidate: BaselineDescriptor
    metrics: dict[str, MetricComparison]
    cases: tuple[CaseComparison, ...]


@dataclass(frozen=True, slots=True)
class _MetricSpec:
    baseline: float | None
    candidate: float | None
    preferred_direction: PreferredDirection


@dataclass(frozen=True, slots=True)
class _FindingMatch:
    baseline_index: int
    candidate_index: int
    similarity: float


def compare_model_baselines(
    baseline: ModelBaseline,
    candidate: ModelBaseline,
) -> BaselineComparison:
    baseline_evaluation = baseline.evaluation
    candidate_evaluation = candidate.evaluation
    _validate_comparable(baseline_evaluation, candidate_evaluation)

    baseline_cases = {case.case_id: case for case in baseline_evaluation.cases}
    candidate_cases = {case.case_id: case for case in candidate_evaluation.cases}
    case_comparisons = tuple(
        _compare_case(baseline_cases[case_id], candidate_cases[case_id])
        for case_id in sorted(baseline_cases)
    )
    return BaselineComparison(
        comparison_schema_version="agent-review-baseline-comparison-v1",
        baseline=_descriptor(baseline_evaluation),
        candidate=_descriptor(candidate_evaluation),
        metrics={
            name: _metric_comparison(spec)
            for name, spec in _metric_specs(
                baseline_evaluation,
                candidate_evaluation,
            ).items()
        },
        cases=case_comparisons,
    )


def _validate_comparable(baseline: EvaluationRun, candidate: EvaluationRun) -> None:
    for name, evaluation in (("baseline", baseline), ("candidate", candidate)):
        if not evaluation.is_model_baseline:
            raise BaselineComparisonError(f"{name} is not a complete model baseline")
        if (
            evaluation.dataset_sha256 is None
            or evaluation.git_commit is None
            or evaluation.git_dirty is None
            or evaluation.executed_at is None
        ):
            raise BaselineComparisonError(f"{name} reproducibility metadata is incomplete")
        if evaluation.executed_case_count != evaluation.dataset_case_count:
            raise BaselineComparisonError(f"{name} did not execute its complete dataset")

    if baseline.schema_version != candidate.schema_version:
        raise BaselineComparisonError("evaluation schema versions do not match")
    if baseline.dataset_version != candidate.dataset_version:
        raise BaselineComparisonError("dataset versions do not match")
    if baseline.dataset_sha256 != candidate.dataset_sha256:
        raise BaselineComparisonError("dataset SHA-256 values do not match")

    baseline_case_ids = [case.case_id for case in baseline.cases]
    candidate_case_ids = [case.case_id for case in candidate.cases]
    if len(baseline_case_ids) != len(set(baseline_case_ids)):
        raise BaselineComparisonError("baseline contains duplicate case IDs")
    if len(candidate_case_ids) != len(set(candidate_case_ids)):
        raise BaselineComparisonError("candidate contains duplicate case IDs")
    if set(baseline_case_ids) != set(candidate_case_ids):
        raise BaselineComparisonError("baseline and candidate case IDs do not match")


def _descriptor(evaluation: EvaluationRun) -> BaselineDescriptor:
    if (
        evaluation.dataset_sha256 is None
        or evaluation.git_commit is None
        or evaluation.git_dirty is None
        or evaluation.executed_at is None
    ):
        raise BaselineComparisonError("baseline reproducibility metadata is incomplete")
    return BaselineDescriptor(
        provider=evaluation.provider,
        model_name=evaluation.model_name,
        prompt_version=evaluation.prompt_version,
        agent_role=evaluation.agent_role,
        dataset_version=evaluation.dataset_version,
        dataset_sha256=evaluation.dataset_sha256,
        git_commit=evaluation.git_commit,
        git_dirty=evaluation.git_dirty,
        executed_at=evaluation.executed_at,
    )


def _metric_specs(
    baseline: EvaluationRun,
    candidate: EvaluationRun,
) -> dict[str, _MetricSpec]:
    baseline_metrics = baseline.metrics
    candidate_metrics = candidate.metrics
    return {
        "recall": _MetricSpec(
            baseline_metrics.recall,
            candidate_metrics.recall,
            PreferredDirection.HIGHER,
        ),
        "precision": _MetricSpec(
            _precision(baseline),
            _precision(candidate),
            PreferredDirection.HIGHER,
        ),
        "f1": _MetricSpec(
            _f1(baseline),
            _f1(candidate),
            PreferredDirection.HIGHER,
        ),
        "false_positives": _MetricSpec(
            float(baseline_metrics.false_positives),
            float(candidate_metrics.false_positives),
            PreferredDirection.LOWER,
        ),
        "false_positives_per_case": _MetricSpec(
            baseline_metrics.false_positives_per_case,
            candidate_metrics.false_positives_per_case,
            PreferredDirection.LOWER,
        ),
        "negative_sample_false_positives": _MetricSpec(
            float(baseline_metrics.negative_sample_false_positives),
            float(candidate_metrics.negative_sample_false_positives),
            PreferredDirection.LOWER,
        ),
        "negative_sample_case_pass_rate": _MetricSpec(
            _negative_sample_case_pass_rate(baseline),
            _negative_sample_case_pass_rate(candidate),
            PreferredDirection.HIGHER,
        ),
        "category_match": _MetricSpec(
            baseline_metrics.category_match,
            candidate_metrics.category_match,
            PreferredDirection.HIGHER,
        ),
        "severity_match": _MetricSpec(
            baseline_metrics.severity_match,
            candidate_metrics.severity_match,
            PreferredDirection.HIGHER,
        ),
        "case_pass_rate": _MetricSpec(
            baseline_metrics.case_pass_rate,
            candidate_metrics.case_pass_rate,
            PreferredDirection.HIGHER,
        ),
        "schema_validity": _MetricSpec(
            baseline_metrics.schema_validity,
            candidate_metrics.schema_validity,
            PreferredDirection.HIGHER,
        ),
        "latency_mean_ms": _MetricSpec(
            baseline_metrics.latency_mean_ms,
            candidate_metrics.latency_mean_ms,
            PreferredDirection.LOWER,
        ),
        "latency_p50_ms": _MetricSpec(
            baseline_metrics.latency_p50_ms,
            candidate_metrics.latency_p50_ms,
            PreferredDirection.LOWER,
        ),
        "latency_p95_ms": _MetricSpec(
            baseline_metrics.latency_p95_ms,
            candidate_metrics.latency_p95_ms,
            PreferredDirection.LOWER,
        ),
        "input_tokens": _MetricSpec(
            _float_or_none(baseline_metrics.usage.input_tokens),
            _float_or_none(candidate_metrics.usage.input_tokens),
            PreferredDirection.LOWER,
        ),
        "output_tokens": _MetricSpec(
            _float_or_none(baseline_metrics.usage.output_tokens),
            _float_or_none(candidate_metrics.usage.output_tokens),
            PreferredDirection.LOWER,
        ),
        "total_tokens": _MetricSpec(
            _float_or_none(baseline_metrics.usage.total_tokens),
            _float_or_none(candidate_metrics.usage.total_tokens),
            PreferredDirection.LOWER,
        ),
    }


def _metric_comparison(spec: _MetricSpec) -> MetricComparison:
    if spec.baseline is None or spec.candidate is None:
        return MetricComparison(
            baseline=spec.baseline,
            candidate=spec.candidate,
            delta=None,
            preferred_direction=spec.preferred_direction,
            status=DeltaStatus.UNAVAILABLE,
        )
    delta = spec.candidate - spec.baseline
    if delta == 0:
        status = DeltaStatus.UNCHANGED
    elif (delta > 0) == (spec.preferred_direction == PreferredDirection.HIGHER):
        status = DeltaStatus.IMPROVED
    else:
        status = DeltaStatus.REGRESSED
    return MetricComparison(
        baseline=spec.baseline,
        candidate=spec.candidate,
        delta=delta,
        preferred_direction=spec.preferred_direction,
        status=status,
    )


def _precision(evaluation: EvaluationRun) -> float | None:
    matched = evaluation.metrics.matched_findings
    denominator = matched + evaluation.metrics.false_positives
    return matched / denominator if denominator else None


def _f1(evaluation: EvaluationRun) -> float | None:
    precision = _precision(evaluation)
    recall = evaluation.metrics.recall
    if precision is None or recall is None:
        return None
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def _negative_sample_case_pass_rate(evaluation: EvaluationRun) -> float | None:
    negative_cases = [case for case in evaluation.cases if case.expected_finding_count == 0]
    if not negative_cases:
        return None
    return sum(case.case_pass for case in negative_cases) / len(negative_cases)


def _compare_case(
    baseline: CaseEvaluation,
    candidate: CaseEvaluation,
) -> CaseComparison:
    matches = _match_actual_findings(baseline.actual_findings, candidate.actual_findings)
    matched_baseline = {match.baseline_index for match in matches}
    matched_candidate = {match.candidate_index for match in matches}
    label_changes: list[FindingLabelChange] = []
    for match in matches:
        baseline_finding = baseline.actual_findings[match.baseline_index]
        candidate_finding = candidate.actual_findings[match.candidate_index]
        category_changed = baseline_finding.category != candidate_finding.category
        severity_changed = baseline_finding.severity != candidate_finding.severity
        if category_changed or severity_changed:
            label_changes.append(
                FindingLabelChange(
                    baseline=_snapshot(baseline_finding),
                    candidate=_snapshot(candidate_finding),
                    category_changed=category_changed,
                    severity_changed=severity_changed,
                )
            )

    return CaseComparison(
        case_id=baseline.case_id,
        baseline_case_pass=baseline.case_pass,
        candidate_case_pass=candidate.case_pass,
        baseline_matched_findings=baseline.matched_finding_count,
        candidate_matched_findings=candidate.matched_finding_count,
        baseline_false_positives=baseline.false_positive_count,
        candidate_false_positives=candidate.false_positive_count,
        baseline_false_negatives=baseline.false_negative_count,
        candidate_false_negatives=candidate.false_negative_count,
        added_findings=tuple(
            _snapshot(finding)
            for index, finding in enumerate(candidate.actual_findings)
            if index not in matched_candidate
        ),
        removed_findings=tuple(
            _snapshot(finding)
            for index, finding in enumerate(baseline.actual_findings)
            if index not in matched_baseline
        ),
        label_changes=tuple(label_changes),
    )


def _match_actual_findings(
    baseline: tuple[EvaluatedFinding, ...],
    candidate: tuple[EvaluatedFinding, ...],
) -> tuple[_FindingMatch, ...]:
    possible: list[_FindingMatch] = []
    for baseline_index, baseline_finding in enumerate(baseline):
        for candidate_index, candidate_finding in enumerate(candidate):
            if baseline_finding.file_path != candidate_finding.file_path:
                continue
            similarity = _finding_similarity(baseline_finding, candidate_finding)
            if similarity >= MIN_FINDING_SIMILARITY:
                possible.append(
                    _FindingMatch(
                        baseline_index=baseline_index,
                        candidate_index=candidate_index,
                        similarity=similarity,
                    )
                )

    matched_baseline: set[int] = set()
    matched_candidate: set[int] = set()
    matches: list[_FindingMatch] = []
    for match in sorted(
        possible,
        key=lambda item: (-item.similarity, item.baseline_index, item.candidate_index),
    ):
        if match.baseline_index in matched_baseline or match.candidate_index in matched_candidate:
            continue
        matched_baseline.add(match.baseline_index)
        matched_candidate.add(match.candidate_index)
        matches.append(match)
    return tuple(sorted(matches, key=lambda item: item.baseline_index))


def _finding_similarity(baseline: EvaluatedFinding, candidate: EvaluatedFinding) -> float:
    baseline_title_tokens = _tokens(baseline.title)
    candidate_title_tokens = _tokens(candidate.title)
    baseline_text_tokens = _tokens(f"{baseline.title} {baseline.explanation}")
    candidate_text_tokens = _tokens(f"{candidate.title} {candidate.explanation}")
    return max(
        _overlap_coefficient(baseline_title_tokens, candidate_title_tokens),
        _jaccard_similarity(baseline_text_tokens, candidate_text_tokens),
    )


def _overlap_coefficient(baseline: set[str], candidate: set[str]) -> float:
    denominator = min(len(baseline), len(candidate))
    return len(baseline & candidate) / denominator if denominator else 0.0


def _jaccard_similarity(baseline: set[str], candidate: set[str]) -> float:
    union = baseline | candidate
    return len(baseline & candidate) / len(union) if union else 0.0


def _tokens(value: str) -> set[str]:
    words = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", value)
    normalized = unicodedata.normalize("NFKC", words).casefold()
    return set(re.sub(r"[^\w]+", " ", normalized).split())


def _snapshot(finding: EvaluatedFinding) -> FindingSnapshot:
    return FindingSnapshot(
        title=finding.title,
        file_path=finding.file_path,
        category=finding.category,
        severity=finding.severity,
    )


def _float_or_none(value: int | None) -> float | None:
    return float(value) if value is not None else None
