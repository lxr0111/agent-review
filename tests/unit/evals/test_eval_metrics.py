from pathlib import Path

import httpx

from app.domain.review.report import (
    FindingCategory,
    FindingSeverity,
    ReviewFinding,
    ReviewReport,
    ReviewRiskLevel,
)
from app.providers.llm.exceptions import (
    LLMAuthenticationError,
    LLMOutputError,
    LLMTransientError,
)
from evals.metrics import (
    EvaluationOutcome,
    aggregate_metrics,
    classify_error,
    successful_case_evaluation,
)
from evals.schemas import load_dataset

DATASET_PATH = Path("evals/cases/review_seed_v1.json")


def test_successful_case_scores_category_and_severity_after_matching() -> None:
    case = load_dataset(DATASET_PATH).cases[0]
    report = ReviewReport(
        summary="The defect was detected with deliberately wrong labels.",
        risk_level=ReviewRiskLevel.CRITICAL,
        findings=(
            ReviewFinding(
                title="Empty input divides by zero",
                explanation="An empty list makes len(values) zero and causes division by zero.",
                suggestion="Guard empty input.",
                file_path="src/statistics.py",
                category=FindingCategory.PERFORMANCE,
                severity=FindingSeverity.CRITICAL,
                confidence=0.9,
            ),
        ),
        model_name="test-model",
        prompt_version="review-v1",
        reviewed_file_count=1,
        skipped_file_count=0,
    )

    result = successful_case_evaluation(case, report, latency_ms=12.5)

    assert result.matched_finding_count == 1
    assert result.category_match_count == 0
    assert result.severity_match_count == 0
    assert result.case_pass is False
    assert result.agent_executions == ()
    assert result.pre_aggregation_finding_count == 0
    assert result.post_aggregation_finding_count == 0
    assert result.duplicate_rate is None


def test_error_classification_distinguishes_required_outcomes() -> None:
    assert classify_error(httpx.ConnectError("offline")) == EvaluationOutcome.TRANSPORT_ERROR
    assert classify_error(LLMTransientError("timeout")) == EvaluationOutcome.TRANSPORT_ERROR
    assert classify_error(LLMAuthenticationError("invalid key")) == EvaluationOutcome.PROVIDER_ERROR
    assert classify_error(LLMOutputError("invalid output")) == EvaluationOutcome.SCHEMA_ERROR


def test_aggregate_metrics_include_case_pass_rate_and_null_usage() -> None:
    dataset = load_dataset(DATASET_PATH)
    positive = dataset.cases[0]
    negative = next(case for case in dataset.cases if case.is_negative)
    expected = positive.expected_findings[0]
    positive_report = ReviewReport(
        summary="Found one issue.",
        risk_level=ReviewRiskLevel.MEDIUM,
        findings=(
            ReviewFinding(
                title=expected.description,
                explanation="Empty input leads to division by zero.",
                suggestion="Handle the empty list.",
                file_path=expected.file_path,
                category=expected.category,
                severity=expected.severity,
                confidence=1.0,
            ),
        ),
        model_name="mock",
        prompt_version="review-v1",
        reviewed_file_count=1,
        skipped_file_count=0,
    )
    negative_report = ReviewReport(
        summary="No issue.",
        risk_level=ReviewRiskLevel.NONE,
        findings=(),
        model_name="mock",
        prompt_version="review-v1",
        reviewed_file_count=1,
        skipped_file_count=0,
    )
    cases = (
        successful_case_evaluation(positive, positive_report, latency_ms=10),
        successful_case_evaluation(negative, negative_report, latency_ms=20),
    )

    metrics = aggregate_metrics(dataset, cases)

    assert metrics.case_pass_rate == 1.0
    assert metrics.recall == 1.0
    assert metrics.false_positives == 0
    assert metrics.category_match == 1.0
    assert metrics.severity_match == 1.0
    assert metrics.schema_validity == 1.0
    assert metrics.latency_p50_ms == 10
    assert metrics.latency_p95_ms == 20
    assert metrics.usage.input_tokens is None
    assert metrics.usage.output_tokens is None
    assert metrics.usage.total_tokens is None
    assert metrics.usage.cost_usd is None
    assert metrics.total_agent_calls == 0
    assert metrics.pre_aggregation_findings == 0
    assert metrics.post_aggregation_findings == 0
    assert metrics.duplicate_rate is None
