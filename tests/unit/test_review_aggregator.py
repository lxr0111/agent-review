from dataclasses import replace
from itertools import permutations

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
    suggestion: str = "Use a parameterized query.",
) -> ReviewFinding:
    return ReviewFinding(
        title=title,
        explanation=explanation,
        suggestion=suggestion,
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
    assert result.finding_identity_version == "finding-text-v4"
    assert result.report.prompt_version == "multi-agent-v2"
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


def test_aggregator_mixed_groups_are_independent_of_agent_result_order() -> None:
    correctness = _result(
        AgentRole.CORRECTNESS,
        _finding(
            title="SQL injection from interpolated email query",
            explanation=(
                "The code executes "
                "`f\"SELECT * FROM users WHERE email = '{email}'\"`; an email can "
                "change the SQL statement."
            ),
            category=FindingCategory.CORRECTNESS,
            severity=FindingSeverity.MEDIUM,
            confidence=0.9,
        ),
    )
    security = _result(
        AgentRole.SECURITY,
        _finding(
            title="SQL injection through interpolated email query",
            explanation=(
                "The expression "
                "`f\"SELECT * FROM users WHERE email = '{email}'\"` parses attacker "
                "input as SQL instead of bound data."
            ),
            confidence=0.97,
        ),
    )
    performance = _result(
        AgentRole.PERFORMANCE,
        _finding(
            title="N+1 customer lookup inside the order loop",
            explanation="One customer query is executed for every order in the loop.",
            file_path="src/orders.py",
            category=FindingCategory.PERFORMANCE,
            severity=FindingSeverity.MEDIUM,
            confidence=0.94,
        ),
    )
    aggregator = DeterministicReviewAggregator()

    results = tuple(
        aggregator.aggregate_with_metadata(
            context=_context(),
            results=ordered_results,
        )
        for ordered_results in permutations((correctness, security, performance))
    )

    assert all(result == results[0] for result in results[1:])
    assert len(results[0].report.findings) == 2
    assert [decision.action for decision in results[0].decisions].count(
        AggregationAction.MERGED
    ) == 1


def test_aggregator_merges_live_paraphrases_that_share_root_cause_and_code() -> None:
    correctness = _finding(
        title="SQL injection and broken lookups from f-string interpolation of email",
        explanation=(
            "The query is built as "
            "`f\"SELECT * FROM users WHERE email = '{email}'\"`, so injection and "
            "legitimate apostrophes both change the SQL text."
        ),
        confidence=0.95,
    )
    security = _finding(
        title="SQL injection via string-interpolated query in find_user",
        explanation=(
            "The code executes "
            "`f\"SELECT * FROM users WHERE email = '{email}'\"`; email is parsed as SQL "
            "instead of bound data."
        ),
        confidence=0.98,
    )

    result = DeterministicReviewAggregator().aggregate_with_metadata(
        context=_context(),
        results=(
            _result(AgentRole.CORRECTNESS, correctness),
            _result(AgentRole.SECURITY, security),
        ),
    )

    assert result.report.findings == (security,)
    assert [decision.action for decision in result.decisions] == [
        AggregationAction.KEPT,
        AggregationAction.MERGED,
    ]


def test_aggregator_replays_live_lifetime_conflict_without_duplicate_security_impact() -> None:
    context = replace(
        _context(),
        reviewable_files=(
            ChangedFile(
                path="src/api_keys.py",
                status=FileChangeStatus.MODIFIED,
                additions=5,
                deletions=0,
                changes=5,
                patch=(
                    "@@ -0,0 +1,5 @@\n"
                    "+def api_key_matches(provided_key, expected_key):\n"
                    "+    return expected_key.startswith(provided_key)\n"
                    "+\n"
                    "+def seconds_until_expiry(expires_at, now):\n"
                    "+    return int((now - expires_at).total_seconds())"
                ),
            ),
        ),
    )
    correctness = _finding(
        title="seconds_until_expiry returns a negated remaining lifetime",
        explanation=(
            "`int((now - expires_at).total_seconds())` subtracts the expiry from the "
            "current time, so a still-valid key gets a negative remaining lifetime."
        ),
        suggestion=(
            "Compute the other direction with "
            "`return int((expires_at - now).total_seconds())`."
        ),
        file_path="src/api_keys.py",
        category=FindingCategory.CORRECTNESS,
        severity=FindingSeverity.MEDIUM,
        confidence=0.95,
    )
    security = _finding(
        title="Expiry helper returns a positive value for already-expired keys",
        explanation=(
            "`seconds_until_expiry` computes `now - expires_at`, which is positive after "
            "expiry. A hypothetical consumer could treat that value as still active."
        ),
        suggestion="Compute remaining lifetime as `expires_at - now`.",
        file_path="src/api_keys.py",
        category=FindingCategory.SECURITY,
        severity=FindingSeverity.MEDIUM,
        confidence=0.80,
    )

    result = DeterministicReviewAggregator().aggregate_with_metadata(
        context=context,
        results=(
            _result(AgentRole.CORRECTNESS, correctness),
            _result(AgentRole.SECURITY, security),
        ),
    )

    assert result.report.findings == (correctness,)
    assert result.finding_identity_version == "finding-text-v4"
    assert result.report.prompt_version == "multi-agent-v2"
    assert [decision.action for decision in result.decisions] == [
        AggregationAction.KEPT,
        AggregationAction.MERGED,
    ]
    assert result.decisions[0].source_role == AgentRole.CORRECTNESS
    assert "confidence before role alignment" in result.decisions[0].reason
    assert {conflict.field: conflict.selected_value for conflict in result.conflicts} == {
        "category": FindingCategory.CORRECTNESS.value,
    }


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
