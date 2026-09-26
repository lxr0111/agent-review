from dataclasses import replace

from app.domain.review.report import FindingCategory, FindingSeverity, ReviewFinding
from app.services.review_finding_identity import (
    finding_identity,
    findings_describe_same_issue,
    normalize_finding_text,
)


def _finding() -> ReviewFinding:
    return ReviewFinding(
        title="SQL injection through interpolated email",
        explanation="The interpolated email changes the SQL query structure.",
        suggestion="Use a parameterized query for the email value.",
        file_path="src/users.py",
        severity=FindingSeverity.HIGH,
        category=FindingCategory.SECURITY,
        confidence=0.9,
    )


def test_exact_identity_ignores_labels_and_suggestion() -> None:
    first = _finding()
    second = replace(
        first,
        category=FindingCategory.CORRECTNESS,
        severity=FindingSeverity.MEDIUM,
        suggestion="Bind the value instead.",
    )

    assert finding_identity(first) == finding_identity(second)
    assert findings_describe_same_issue(first, second)


def test_semantic_identity_matches_reordered_words_in_same_file() -> None:
    first = _finding()
    second = replace(
        first,
        title="Interpolated email enables SQL injection",
        explanation="SQL query structure changes because the email is interpolated.",
    )

    assert findings_describe_same_issue(first, second)


def test_identity_does_not_merge_related_but_distinct_issues() -> None:
    email_injection = _finding()
    sort_injection = replace(
        email_injection,
        title="SQL injection through sort column",
        explanation="The sort column is interpolated into ORDER BY without an allowlist.",
    )

    assert not findings_describe_same_issue(email_injection, sort_injection)


def test_identity_requires_the_same_file() -> None:
    first = _finding()
    second = replace(first, file_path="src/admin.py")

    assert not findings_describe_same_issue(first, second)


def test_normalization_handles_case_punctuation_and_identifiers() -> None:
    assert normalize_finding_text("Calculate_Average() FAILS!") == "calculate average fails"
