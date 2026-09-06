import pytest

from app.domain.review.entities import (
    ChangedFile,
    PullRequestSnapshot,
    ReviewContext,
    SkippedFile,
)
from app.domain.review.enums import FileChangeStatus, FileSkipReason
from app.domain.review.exceptions import ReviewOutputValidationError
from app.domain.review.report import (
    FindingCategory,
    FindingSeverity,
    ReviewRiskLevel,
)
from app.providers.llm import LLMProvider, MockLLMProvider
from app.providers.llm.schemas import LLMReviewFinding, LLMReviewOutput
from app.services.ai_review_service import AIReviewService


def _file(path: str = "src/auth.py") -> ChangedFile:
    return ChangedFile(
        path=path,
        status=FileChangeStatus.MODIFIED,
        additions=1,
        deletions=1,
        changes=2,
        patch="@@ -1 +1 @@\n-check_access()\n+pass",
    )


def _context(
    *,
    reviewable_files: tuple[ChangedFile, ...] | None = None,
    skipped_files: tuple[SkippedFile, ...] = (),
) -> ReviewContext:
    files = (_file(),) if reviewable_files is None else reviewable_files
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
        reviewable_files=files,
        skipped_files=skipped_files,
        total_patch_characters=sum(len(item.patch or "") for item in files),
    )


def _finding(
    *,
    file_path: str = "src/auth.py",
    severity: FindingSeverity = FindingSeverity.HIGH,
    title: str = "Authorization is bypassed",
) -> LLMReviewFinding:
    return LLMReviewFinding(
        title=title,
        explanation="The new branch no longer verifies the caller.",
        suggestion="Keep the authorization check before executing the branch.",
        file_path=file_path,
        severity=severity,
        category=FindingCategory.SECURITY,
        confidence=0.95,
    )


def test_ai_review_maps_valid_output_to_domain_report() -> None:
    provider = MockLLMProvider(
        LLMReviewOutput(
            summary="One high-risk authorization issue was found.",
            findings=(_finding(),),
        ),
        model_name="test-model",
    )

    report = AIReviewService(provider).review(_context())

    assert isinstance(provider, LLMProvider)
    assert report.summary == "One high-risk authorization issue was found."
    assert report.risk_level == ReviewRiskLevel.HIGH
    assert report.findings[0].severity == FindingSeverity.HIGH
    assert report.model_name == "test-model"
    assert report.prompt_version == "review-v1"
    assert report.reviewed_file_count == 1
    assert len(provider.prompts) == 1


def test_ai_review_derives_overall_risk_from_highest_finding() -> None:
    provider = MockLLMProvider(
        LLMReviewOutput(
            summary="Two problems were found.",
            findings=(
                _finding(severity=FindingSeverity.LOW, title="Missing edge-case test"),
                _finding(severity=FindingSeverity.CRITICAL),
            ),
        )
    )

    report = AIReviewService(provider).review(_context())

    assert report.risk_level == ReviewRiskLevel.CRITICAL


def test_ai_review_rejects_finding_for_unknown_file() -> None:
    provider = MockLLMProvider(
        LLMReviewOutput(
            summary="A problem was found.",
            findings=(_finding(file_path="src/not-provided.py"),),
        )
    )

    with pytest.raises(ReviewOutputValidationError, match="unknown file"):
        AIReviewService(provider).review(_context())


def test_ai_review_rejects_duplicate_findings() -> None:
    finding = _finding()
    provider = MockLLMProvider(
        LLMReviewOutput(
            summary="A duplicated problem was returned.",
            findings=(finding, finding),
        )
    )

    with pytest.raises(ReviewOutputValidationError, match="duplicate finding"):
        AIReviewService(provider).review(_context())


def test_ai_review_skips_model_when_no_files_are_reviewable() -> None:
    skipped = SkippedFile(file=_file("package-lock.json"), reason=FileSkipReason.EXCLUDED_PATH)
    provider = MockLLMProvider()

    report = AIReviewService(provider).review(
        _context(reviewable_files=(), skipped_files=(skipped,))
    )

    assert report.risk_level == ReviewRiskLevel.NONE
    assert report.findings == ()
    assert report.model_name is None
    assert report.reviewed_file_count == 0
    assert report.skipped_file_count == 1
    assert provider.prompts == []
