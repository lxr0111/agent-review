from uuid import UUID

import pytest

from app.domain.review.report import (
    FindingCategory,
    FindingSeverity,
    ReviewFinding,
    ReviewReport,
    derive_review_risk,
)
from app.services.review_markdown_formatter import ReviewMarkdownFormatter, review_marker

TASK_ID = UUID("11111111-1111-1111-1111-111111111111")
REPORT_DIGEST = "a" * 64


def _finding(title: str, severity: FindingSeverity) -> ReviewFinding:
    return ReviewFinding(
        title=title,
        explanation="Notify @maintainer and reject <script>alert(1)</script>.",
        suggestion="Add a guard before using `value`.",
        file_path="src/a`b.py",
        severity=severity,
        category=FindingCategory.CORRECTNESS,
        confidence=0.914,
    )


def _report(*findings: ReviewFinding, summary: str = "Review completed.") -> ReviewReport:
    return ReviewReport(
        summary=summary,
        risk_level=derive_review_risk(findings),
        findings=tuple(findings),
        model_name="review-model",
        prompt_version="review-v1",
        reviewed_file_count=max(1, len(findings)),
        skipped_file_count=2,
    )


def test_formatter_orders_findings_and_sanitizes_untrusted_text() -> None:
    low = _finding("Low *risk*", FindingSeverity.LOW)
    critical = _finding("Critical [risk]", FindingSeverity.CRITICAL)

    body = ReviewMarkdownFormatter().format(
        _report(low, critical),
        task_id=TASK_ID,
        report_digest=REPORT_DIGEST,
    )

    assert body.index("[CRITICAL]") < body.index("[LOW]")
    assert "@\u200bmaintainer" in body
    assert "&lt;script&gt;" in body
    assert "<script>" not in body
    assert "<code>src/a`b.py</code>" in body
    assert body.endswith(review_marker(TASK_ID, REPORT_DIGEST))


def test_formatter_handles_report_without_findings() -> None:
    body = ReviewMarkdownFormatter().format(
        _report(),
        task_id=TASK_ID,
        report_digest=REPORT_DIGEST,
    )

    assert "No actionable findings" in body
    assert "Overall risk:** `NONE`" in body


def test_formatter_truncates_content_but_preserves_marker() -> None:
    body = ReviewMarkdownFormatter(max_characters=1_000).format(
        _report(summary="x" * 2_000),
        task_id=TASK_ID,
        report_digest=REPORT_DIGEST,
    )

    assert len(body) <= 1_000
    assert "Report truncated" in body
    assert body.endswith(review_marker(TASK_ID, REPORT_DIGEST))


def test_marker_rejects_non_sha256_digest() -> None:
    with pytest.raises(ValueError, match="SHA-256"):
        review_marker(TASK_ID, "not-a-digest")
