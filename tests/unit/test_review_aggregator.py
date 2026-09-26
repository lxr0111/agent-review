from dataclasses import replace

import pytest

from app.domain.review.entities import ChangedFile, PullRequestSnapshot, ReviewContext
from app.domain.review.enums import FileChangeStatus
from app.domain.review.report import (
    FindingCategory,
    FindingSeverity,
    ReviewFinding,
    ReviewReport,
    ReviewRiskLevel,
    derive_review_risk,
)
from app.services.review_agent import AgentReviewResult, AgentRole
from app.services.review_aggregator import (
    AGGREGATOR_PROMPT_VERSION,
    AggregationAction,
    DeterministicReviewAggregator,
    ReviewAggregator,
)
from app.services.review_finding_validator import ReviewFindingValidationError


def _context() -> ReviewContext:
    files = (
        ChangedFile(
            path="src/users.py",
            status=FileChangeStatus.MODIFIED,
            additions=3,
            deletions=1,
            changes=4,
            patch="@@ -1 +1 @@\n-old\n+new",
        ),
        ChangedFile(
            path="src/orders.py",
            status=FileChangeStatus.MODIFIED,
            additions=2,
            deletions=0,
            changes=2,
            patch="@@ -1 +1 @@\n+load_orders()",
        ),
    )
    return ReviewContext(
        pull_request=PullRequestSnapshot(
            repository="owner/repository",
            number=11,
            title="Update queries",
            body=None,
            base_sha="base-sha",
            head_sha="head-sha",
            draft=False,
            author_login="octocat",
        ),
        reviewable_files=files,
        skipped_files=(),
        total_patch_characters=sum(len(file.patch or "") for file in files),
    )


def _finding(
    *,
    title: str = "SQL injection through interpolated email",
    explanation: str = "The interpolated email changes the SQL query structure.",
    file_path: str = "src/users.py",
    severity: FindingSeverity = FindingSeverity.HIGH,
    category: FindingCategory = FindingCategory.SECURITY,
    confidence: float = 0.9,
) -> ReviewFinding:
    return ReviewFinding(
        title=title,
        explanation=explanation,
        suggestion="Use a parameterized query.",
        file_path=file_path,
        severity=severity,
        category=category,
        confidence=confidence,
    )


def _result(
    role: AgentRole,
    *findings: ReviewFinding,
    model_name: str | None = "test-model",
) -> AgentReviewResult:
    return AgentReviewResult(
        role=role,
        report=ReviewReport(
            summary=f"{role.value} review completed.",
            risk_level=derive_review_risk(findings),
            findings=findings,
            model_name=model_name,
            prompt_version=f"review-{role.value}-v1",
            reviewed_file_count=2,
            skipped_file_count=0,
        ),
    )


def test_aggregator_merges_duplicate_and_prefers_role_aligned_label() -> None:
    general = _finding(
        severity=FindingSeverity.CRITICAL,
        category=FindingCategory.CORRECTNESS,
        confidence=0.99,
    )
    specialist = _finding(confidence=0.8)
    aggregator = DeterministicReviewAggregator()

    result = aggregator.aggregate_with_metadata(
        context=_context(),
        results=(
            _result(AgentRole.GENERAL, general),
            _result(AgentRole.SECURITY, specialist),
        ),
    )

    assert isinstance(aggregator, ReviewAggregator)
    assert result.report.findings == (specialist,)
    assert result.report.risk_level == ReviewRiskLevel.HIGH
    assert result.report.prompt_version == AGGREGATOR_PROMPT_VERSION
    assert [decision.action for decision in result.decisions] == [
        AggregationAction.KEPT,
        AggregationAction.MERGED,
    ]
    assert result.decisions[0].source_role == AgentRole.SECURITY
    assert {conflict.field for conflict in result.conflicts} == {"category", "severity"}
    assert {conflict.field: conflict.selected_value for conflict in result.conflicts} == {
        "category": FindingCategory.SECURITY.value,
        "severity": FindingSeverity.HIGH.value,
    }


def test_aggregator_keeps_distinct_issues_in_the_same_file() -> None:
    email_injection = _finding()
    sort_injection = _finding(
        title="SQL injection through sort column",
        explanation="The sort column is interpolated into ORDER BY without an allowlist.",
    )

    report = DeterministicReviewAggregator().aggregate(
        context=_context(),
        results=(
            _result(AgentRole.SECURITY, email_injection),
            _result(AgentRole.CORRECTNESS, sort_injection),
        ),
    )

    assert len(report.findings) == 2
    assert {finding.title for finding in report.findings} == {
        email_injection.title,
        sort_injection.title,
    }


def test_aggregator_output_is_independent_of_agent_result_order() -> None:
    general = _result(AgentRole.GENERAL, _finding(confidence=0.95))
    security = _result(
        AgentRole.SECURITY,
        _finding(title="Interpolated email enables SQL injection", confidence=0.9),
    )
    aggregator = DeterministicReviewAggregator()

    first = aggregator.aggregate_with_metadata(
        context=_context(),
        results=(general, security),
    )
    second = aggregator.aggregate_with_metadata(
        context=_context(),
        results=(security, general),
    )

    assert first == second


def test_aggregator_handles_multiple_empty_agent_reports() -> None:
    result = DeterministicReviewAggregator().aggregate_with_metadata(
        context=_context(),
        results=(
            _result(AgentRole.CORRECTNESS),
            _result(AgentRole.SECURITY),
        ),
    )

    assert result.report.findings == ()
    assert result.report.risk_level == ReviewRiskLevel.NONE
    assert result.report.summary == "Multi-agent review found no actionable findings."
    assert result.source_roles == (AgentRole.CORRECTNESS, AgentRole.SECURITY)
    assert result.decisions == ()
    assert result.conflicts == ()


def test_aggregator_rejects_no_results_or_duplicate_roles() -> None:
    aggregator = DeterministicReviewAggregator()
    result = _result(AgentRole.SECURITY)

    with pytest.raises(ValueError, match="at least one result"):
        aggregator.aggregate(context=_context(), results=())
    with pytest.raises(ValueError, match="unique agent roles"):
        aggregator.aggregate(context=_context(), results=(result, result))


def test_aggregator_propagates_boundary_validation_failure() -> None:
    invalid = replace(_finding(), file_path="src/not-reviewed.py")

    with pytest.raises(ReviewFindingValidationError, match="unknown file"):
        DeterministicReviewAggregator().aggregate(
            context=_context(),
            results=(_result(AgentRole.SECURITY, invalid),),
        )


def test_aggregator_uses_multi_agent_model_name_for_mixed_models() -> None:
    report = DeterministicReviewAggregator().aggregate(
        context=_context(),
        results=(
            _result(AgentRole.CORRECTNESS, model_name="model-a"),
            _result(AgentRole.SECURITY, model_name="model-b"),
        ),
    )

    assert report.model_name == "multi-agent"
