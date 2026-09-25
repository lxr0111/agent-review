import math
from datetime import datetime
from enum import StrEnum

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.domain.review.exceptions import ReviewOutputValidationError
from app.domain.review.report import (
    FindingCategory,
    FindingSeverity,
    ReviewFinding,
    ReviewReport,
)
from app.providers.llm.exceptions import LLMOutputError, LLMProviderError, LLMTransientError
from app.providers.llm.protocol import LLMTokenUsage
from evals.matcher import (
    CURRENT_MATCHER_VERSION,
    LEGACY_MATCHER_VERSION,
    FindingMatchResult,
    MatcherVersion,
    match_findings,
)
from evals.schemas import EvalCase, EvalDataset, EvalLineRange


class EvaluationOutcome(StrEnum):
    TRANSPORT_ERROR = "transport_error"
    PROVIDER_ERROR = "provider_error"
    SCHEMA_ERROR = "schema_error"
    SUCCESSFUL_CASE = "successful_case"


class EvalUsage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    cost_usd: float | None = Field(default=None, ge=0)


class FindingMatchDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_id: str
    actual_title: str
    file_path: str
    expected_range: EvalLineRange
    score: float = Field(ge=0, le=1)
    category_match: bool
    severity_match: bool


class EvaluatedFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    explanation: str
    suggestion: str
    file_path: str
    severity: FindingSeverity
    category: FindingCategory
    confidence: float = Field(ge=0, le=1)


class CaseEvaluation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    outcome: EvaluationOutcome
    schema_valid: bool | None
    latency_ms: float = Field(ge=0)
    expected_finding_count: int = Field(ge=0)
    actual_finding_count: int = Field(ge=0)
    matched_finding_count: int = Field(ge=0)
    false_positive_count: int = Field(ge=0)
    false_negative_count: int = Field(ge=0)
    category_match_count: int = Field(ge=0)
    severity_match_count: int = Field(ge=0)
    case_pass: bool
    summary: str | None = None
    model_name: str | None = None
    prompt_version: str | None = None
    actual_findings: tuple[EvaluatedFinding, ...] = ()
    matches: tuple[FindingMatchDetail, ...] = ()
    error_type: str | None = None
    usage: EvalUsage = Field(default_factory=EvalUsage)


class AggregateMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total_cases: int = Field(ge=0)
    successful_cases: int = Field(ge=0)
    transport_errors: int = Field(ge=0)
    provider_errors: int = Field(ge=0)
    schema_errors: int = Field(ge=0)
    passed_cases: int = Field(ge=0)
    case_pass_rate: float = Field(ge=0, le=1)
    expected_findings: int = Field(ge=0)
    matched_findings: int = Field(ge=0)
    recall: float | None = Field(default=None, ge=0, le=1)
    false_positives: int = Field(ge=0)
    false_positives_per_case: float = Field(ge=0)
    negative_sample_false_positives: int = Field(ge=0)
    category_match: float | None = Field(default=None, ge=0, le=1)
    severity_match: float | None = Field(default=None, ge=0, le=1)
    schema_validity: float | None = Field(default=None, ge=0, le=1)
    latency_mean_ms: float | None = Field(default=None, ge=0)
    latency_p50_ms: float | None = Field(default=None, ge=0)
    latency_p95_ms: float | None = Field(default=None, ge=0)
    usage: EvalUsage


class EvaluationRun(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_kind: str
    provider: str
    model_name: str | None
    prompt_version: str
    matcher_version: MatcherVersion = LEGACY_MATCHER_VERSION
    is_model_baseline: bool
    schema_version: str
    dataset_version: str
    dataset_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    dataset_case_count: int = Field(ge=1)
    executed_case_count: int = Field(ge=1)
    git_commit: str | None = None
    git_dirty: bool | None = None
    executed_at: datetime | None = None
    cases: tuple[CaseEvaluation, ...]
    metrics: AggregateMetrics


def successful_case_evaluation(
    case: EvalCase,
    report: ReviewReport,
    *,
    latency_ms: float,
    usage: LLMTokenUsage | None = None,
    matcher_version: MatcherVersion = CURRENT_MATCHER_VERSION,
) -> CaseEvaluation:
    result, details, category_matches, severity_matches = _score_findings(
        case,
        report.findings,
        matcher_version=matcher_version,
    )
    matched_count = len(result.matches)
    case_pass = _case_passes(
        result,
        category_matches=category_matches,
        severity_matches=severity_matches,
    )
    return CaseEvaluation(
        case_id=case.id,
        outcome=EvaluationOutcome.SUCCESSFUL_CASE,
        schema_valid=True,
        latency_ms=latency_ms,
        expected_finding_count=len(case.expected_findings),
        actual_finding_count=len(report.findings),
        matched_finding_count=matched_count,
        false_positive_count=len(result.unmatched_actual_indices),
        false_negative_count=len(result.unmatched_expected_indices),
        category_match_count=category_matches,
        severity_match_count=severity_matches,
        case_pass=case_pass,
        summary=report.summary,
        model_name=report.model_name,
        prompt_version=report.prompt_version,
        actual_findings=tuple(
            EvaluatedFinding(
                title=finding.title,
                explanation=finding.explanation,
                suggestion=finding.suggestion,
                file_path=finding.file_path,
                severity=finding.severity,
                category=finding.category,
                confidence=finding.confidence,
            )
            for finding in report.findings
        ),
        matches=details,
        usage=_eval_usage(usage),
    )


def rescore_successful_case(
    case: EvalCase,
    previous: CaseEvaluation,
    *,
    matcher_version: MatcherVersion,
) -> CaseEvaluation:
    if previous.outcome != EvaluationOutcome.SUCCESSFUL_CASE:
        return previous

    result, details, category_matches, severity_matches = _score_findings(
        case,
        previous.actual_findings,
        matcher_version=matcher_version,
    )
    return previous.model_copy(
        update={
            "expected_finding_count": len(case.expected_findings),
            "actual_finding_count": len(previous.actual_findings),
            "matched_finding_count": len(result.matches),
            "false_positive_count": len(result.unmatched_actual_indices),
            "false_negative_count": len(result.unmatched_expected_indices),
            "category_match_count": category_matches,
            "severity_match_count": severity_matches,
            "case_pass": _case_passes(
                result,
                category_matches=category_matches,
                severity_matches=severity_matches,
            ),
            "matches": details,
        }
    )


def _score_findings(
    case: EvalCase,
    findings: tuple[ReviewFinding | EvaluatedFinding, ...],
    *,
    matcher_version: MatcherVersion,
) -> tuple[FindingMatchResult, tuple[FindingMatchDetail, ...], int, int]:
    result = match_findings(
        case.expected_findings,
        findings,
        matcher_version=matcher_version,
    )
    details: list[FindingMatchDetail] = []
    category_matches = 0
    severity_matches = 0
    for match in result.matches:
        expected = case.expected_findings[match.expected_index]
        actual = findings[match.actual_index]
        category_match = expected.category == actual.category
        severity_match = expected.severity == actual.severity
        category_matches += int(category_match)
        severity_matches += int(severity_match)
        details.append(
            FindingMatchDetail(
                expected_id=expected.id,
                actual_title=actual.title,
                file_path=actual.file_path,
                expected_range=expected.range,
                score=match.score,
                category_match=category_match,
                severity_match=severity_match,
            )
        )
    return result, tuple(details), category_matches, severity_matches


def _case_passes(
    result: FindingMatchResult,
    *,
    category_matches: int,
    severity_matches: int,
) -> bool:
    matched_count = len(result.matches)
    return (
        not result.unmatched_expected_indices
        and not result.unmatched_actual_indices
        and category_matches == matched_count
        and severity_matches == matched_count
    )


def error_case_evaluation(
    case: EvalCase,
    error: Exception,
    *,
    latency_ms: float,
) -> CaseEvaluation:
    outcome = classify_error(error)
    return CaseEvaluation(
        case_id=case.id,
        outcome=outcome,
        schema_valid=False if outcome == EvaluationOutcome.SCHEMA_ERROR else None,
        latency_ms=latency_ms,
        expected_finding_count=len(case.expected_findings),
        actual_finding_count=0,
        matched_finding_count=0,
        false_positive_count=0,
        false_negative_count=len(case.expected_findings),
        category_match_count=0,
        severity_match_count=0,
        case_pass=False,
        error_type=type(error).__name__,
        usage=EvalUsage(),
    )


def classify_error(error: Exception) -> EvaluationOutcome:
    if isinstance(error, (httpx.TransportError, ConnectionError, TimeoutError)):
        return EvaluationOutcome.TRANSPORT_ERROR
    if isinstance(error, LLMTransientError) and error.status_code is None:
        return EvaluationOutcome.TRANSPORT_ERROR
    if isinstance(error, (LLMOutputError, ReviewOutputValidationError, ValidationError)):
        return EvaluationOutcome.SCHEMA_ERROR
    if isinstance(error, LLMProviderError):
        return EvaluationOutcome.PROVIDER_ERROR
    return EvaluationOutcome.PROVIDER_ERROR


def aggregate_metrics(
    dataset: EvalDataset,
    cases: tuple[CaseEvaluation, ...],
) -> AggregateMetrics:
    total_cases = len(cases)
    successful_cases = sum(
        case.outcome == EvaluationOutcome.SUCCESSFUL_CASE for case in cases
    )
    expected_findings = sum(case.expected_finding_count for case in cases)
    matched_findings = sum(case.matched_finding_count for case in cases)
    matched_denominator = matched_findings
    schema_results = [case.schema_valid for case in cases if case.schema_valid is not None]
    latencies = sorted(case.latency_ms for case in cases)
    negative_case_ids = {case.id for case in dataset.cases if case.is_negative}

    return AggregateMetrics(
        total_cases=total_cases,
        successful_cases=successful_cases,
        transport_errors=sum(case.outcome == EvaluationOutcome.TRANSPORT_ERROR for case in cases),
        provider_errors=sum(case.outcome == EvaluationOutcome.PROVIDER_ERROR for case in cases),
        schema_errors=sum(case.outcome == EvaluationOutcome.SCHEMA_ERROR for case in cases),
        passed_cases=sum(case.case_pass for case in cases),
        case_pass_rate=_ratio(sum(case.case_pass for case in cases), total_cases) or 0.0,
        expected_findings=expected_findings,
        matched_findings=matched_findings,
        recall=_ratio(matched_findings, expected_findings),
        false_positives=sum(case.false_positive_count for case in cases),
        false_positives_per_case=(
            sum(case.false_positive_count for case in cases) / total_cases if total_cases else 0.0
        ),
        negative_sample_false_positives=sum(
            case.false_positive_count for case in cases if case.case_id in negative_case_ids
        ),
        category_match=_ratio(
            sum(case.category_match_count for case in cases), matched_denominator
        ),
        severity_match=_ratio(
            sum(case.severity_match_count for case in cases), matched_denominator
        ),
        schema_validity=_ratio(sum(value is True for value in schema_results), len(schema_results)),
        latency_mean_ms=(sum(latencies) / len(latencies) if latencies else None),
        latency_p50_ms=_percentile(latencies, 0.50),
        latency_p95_ms=_percentile(latencies, 0.95),
        usage=_aggregate_usage(cases),
    )


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    index = max(0, math.ceil(percentile * len(values)) - 1)
    return values[index]


def _aggregate_usage(cases: tuple[CaseEvaluation, ...]) -> EvalUsage:
    return EvalUsage(
        input_tokens=_sum_if_complete([case.usage.input_tokens for case in cases]),
        output_tokens=_sum_if_complete([case.usage.output_tokens for case in cases]),
        total_tokens=_sum_if_complete([case.usage.total_tokens for case in cases]),
        cost_usd=_sum_float_if_complete([case.usage.cost_usd for case in cases]),
    )


def _eval_usage(usage: LLMTokenUsage | None) -> EvalUsage:
    if usage is None:
        return EvalUsage()
    return EvalUsage(
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        total_tokens=usage.total_tokens,
        cost_usd=None,
    )


def _sum_if_complete(values: list[int | None]) -> int | None:
    return sum(value for value in values if value is not None) if all(
        value is not None for value in values
    ) else None


def _sum_float_if_complete(values: list[float | None]) -> float | None:
    return sum(value for value in values if value is not None) if all(
        value is not None for value in values
    ) else None
