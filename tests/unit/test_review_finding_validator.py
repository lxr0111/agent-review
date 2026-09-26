from dataclasses import replace

import pytest

from app.domain.review.entities import ChangedFile, PullRequestSnapshot, ReviewContext
from app.domain.review.enums import FileChangeStatus
from app.domain.review.report import (
    FindingCategory,
    FindingSeverity,
    ReviewFinding,
    ReviewReport,
    derive_review_risk,
)
from app.services.review_agent import AgentReviewResult, AgentRole
from app.services.review_finding_validator import (
    ReviewFindingValidationError,
    ReviewFindingValidator,
)


def _context() -> ReviewContext:
    changed_file = ChangedFile(
        path="src/auth.py",
        status=FileChangeStatus.MODIFIED,
        additions=1,
        deletions=0,
        changes=1,
        patch="@@ -0,0 +1 @@\n+check_access()",
    )
    return ReviewContext(
        pull_request=PullRequestSnapshot(
            repository="owner/repository",
            number=7,
            title="Update access handling",
            body=None,
            base_sha="base-sha",
            head_sha="head-sha",
            draft=False,
            author_login="octocat",
        ),
        reviewable_files=(changed_file,),
        skipped_files=(),
        total_patch_characters=len(changed_file.patch or ""),
    )


def _finding() -> ReviewFinding:
    return ReviewFinding(
        title="Authorization is bypassed",
        explanation="The new branch skips the required access check.",
        suggestion="Keep the access check on every branch.",
        file_path="src/auth.py",
        severity=FindingSeverity.HIGH,
        category=FindingCategory.SECURITY,
        confidence=0.9,
    )


def _result(*findings: ReviewFinding) -> AgentReviewResult:
    return AgentReviewResult(
        role=AgentRole.SECURITY,
        report=ReviewReport(
            summary="Security review completed.",
            risk_level=derive_review_risk(findings),
            findings=findings,
            model_name="test-model",
            prompt_version="review-security-v1",
            reviewed_file_count=1,
            skipped_file_count=0,
        ),
    )


def test_validator_preserves_valid_finding_and_source_role() -> None:
    finding = _finding()

    validated = ReviewFindingValidator().validate(
        context=_context(),
        result=_result(finding),
    )

    assert len(validated) == 1
    assert validated[0].role == AgentRole.SECURITY
    assert validated[0].finding is finding


def test_validator_rejects_unknown_file() -> None:
    finding = replace(_finding(), file_path="src/unknown.py")

    with pytest.raises(ReviewFindingValidationError, match="unknown file"):
        ReviewFindingValidator().validate(context=_context(), result=_result(finding))


@pytest.mark.parametrize(
    ("finding", "message"),
    [
        (replace(_finding(), title=" "), "title"),
        (replace(_finding(), explanation=" "), "explanation"),
        (replace(_finding(), suggestion=" "), "suggestion"),
        (replace(_finding(), confidence=1.1), "confidence"),
    ],
)
def test_validator_rejects_invalid_finding_fields(
    finding: ReviewFinding,
    message: str,
) -> None:
    with pytest.raises(ReviewFindingValidationError, match=message):
        ReviewFindingValidator().validate(context=_context(), result=_result(finding))


def test_validator_rejects_exact_duplicate_from_one_agent() -> None:
    finding = _finding()

    with pytest.raises(ReviewFindingValidationError, match="duplicate finding"):
        ReviewFindingValidator().validate(
            context=_context(),
            result=_result(finding, finding),
        )
