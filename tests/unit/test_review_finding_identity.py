from dataclasses import replace

from app.domain.review.report import FindingCategory, FindingSeverity, ReviewFinding
from app.services.review_finding_identity import (
    FINDING_IDENTITY_VERSION,
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


def test_identity_v3_preserves_v2_live_sql_paraphrase_matching() -> None:
    correctness = replace(
        _finding(),
        title="SQL injection and broken lookups from f-string interpolation of email",
        explanation=(
            "The query is built as "
            "`f\"SELECT * FROM users WHERE email = '{email}'\"`, so an attacker can "
            "change its semantics and an apostrophe in a legitimate address breaks lookup."
        ),
        confidence=0.95,
    )
    security = replace(
        _finding(),
        title="SQL injection via string-interpolated query in find_user",
        explanation=(
            "The code executes "
            "`f\"SELECT * FROM users WHERE email = '{email}'\"`; email is parsed as SQL "
            "instead of being bound as data."
        ),
        confidence=0.98,
    )

    assert FINDING_IDENTITY_VERSION == "finding-text-v3"
    assert findings_describe_same_issue(correctness, security)


def test_identity_v3_merges_live_path_paraphrases_with_different_titles() -> None:
    correctness = replace(
        _finding(),
        title="Unvalidated filename allows escaping the upload root",
        explanation=(
            "`UPLOAD_ROOT / filename` performs no validation of `filename`. A relative "
            "value containing `..` resolves outside `/srv/uploads`, and because `pathlib` "
            "replaces the base when the right operand is absolute, a filename such as "
            "`/etc/passwd` ignores `UPLOAD_ROOT` entirely. The function can be used to "
            "read arbitrary files readable by the process."
        ),
        file_path="src/uploads.py",
    )
    security = replace(
        _finding(),
        title="Path traversal / arbitrary file read in read_upload",
        explanation=(
            "`UPLOAD_ROOT / filename` performs no containment check. An absolute value "
            "such as `/etc/passwd` discards the root entirely, while relative traversal "
            "resolves outside `/srv/uploads`. Both let a caller read arbitrary readable "
            "files as the service account."
        ),
        file_path="src/uploads.py",
    )

    assert findings_describe_same_issue(correctness, security)


def test_identity_v3_does_not_merge_different_issue_on_same_code_evidence() -> None:
    injection = replace(
        _finding(),
        title="SQL injection through interpolated email",
        explanation=(
            "The code executes "
            "`f\"SELECT * FROM users WHERE email = '{email}'\"` with untrusted input."
        ),
    )
    missing_index = replace(
        _finding(),
        title="Email lookup query lacks a supporting index",
        explanation=(
            "The expression "
            "`f\"SELECT * FROM users WHERE email = '{email}'\"` scans the users table "
            "without an index on email."
        ),
        category=FindingCategory.PERFORMANCE,
        severity=FindingSeverity.MEDIUM,
    )

    assert not findings_describe_same_issue(injection, missing_index)


def test_identity_v3_keeps_path_traversal_and_resource_leak_distinct() -> None:
    path_traversal = replace(
        _finding(),
        title="Path traversal through an unvalidated filename",
        explanation=(
            "`open(UPLOAD_ROOT / filename, 'rb')` accepts traversal segments without "
            "resolving and checking containment under the upload root."
        ),
        file_path="src/uploads.py",
    )
    resource_leak = replace(
        _finding(),
        title="Upload file handle is never closed",
        explanation=(
            "`open(UPLOAD_ROOT / filename, 'rb')` returns after reading without closing "
            "the file handle, causing a resource leak."
        ),
        file_path="src/uploads.py",
        category=FindingCategory.RELIABILITY,
        severity=FindingSeverity.MEDIUM,
    )

    assert not findings_describe_same_issue(path_traversal, resource_leak)


def test_identity_requires_the_same_file() -> None:
    first = _finding()
    second = replace(first, file_path="src/admin.py")

    assert not findings_describe_same_issue(first, second)


def test_normalization_handles_case_punctuation_and_identifiers() -> None:
    assert normalize_finding_text("Calculate_Average() FAILS!") == "calculate average fails"
