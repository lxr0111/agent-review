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
from app.providers.llm.prompt_builder import PROMPT_VERSION
from app.providers.llm.protocol import LLMReviewResult, LLMTokenUsage, ReviewPrompt
from app.providers.llm.schemas import LLMReviewFinding, LLMReviewLocation, LLMReviewOutput
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
    location: LLMReviewLocation | None = None,
) -> LLMReviewFinding:
    return LLMReviewFinding(
        title=title,
        explanation="The new branch no longer verifies the caller.",
        suggestion="Keep the authorization check before executing the branch.",
        file_path=file_path,
        severity=severity,
        category=FindingCategory.SECURITY,
        confidence=0.95,
        location=location,
    )


class _UsageProvider:
    model_name = "usage-test-model"

    def __init__(self, output: LLMReviewOutput) -> None:
        self._output = output

    def generate_review(self, prompt: ReviewPrompt) -> LLMReviewResult:
        del prompt
        return LLMReviewResult(
            output=self._output,
            usage=LLMTokenUsage(input_tokens=40, output_tokens=10, total_tokens=50),
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
    assert report.prompt_version == PROMPT_VERSION
    assert report.reviewed_file_count == 1
    assert len(provider.prompts) == 1


def test_ai_review_keeps_location_on_added_lines() -> None:
    provider = MockLLMProvider(
        LLMReviewOutput(
            summary="One problem was found.",
            findings=(_finding(location=LLMReviewLocation(start_line=1, end_line=1)),),
        )
    )

    report = AIReviewService(provider).review(_context())

    assert report.findings[0].location is not None
    assert report.findings[0].location.start_line == 1
    assert report.findings[0].location.end_line == 1


def test_ai_review_drops_location_on_deleted_or_context_lines() -> None:
    changed_file = ChangedFile(
        path="src/auth.py",
        status=FileChangeStatus.MODIFIED,
        additions=1,
        deletions=1,
        changes=3,
        patch="@@ -4,2 +4,2 @@\n context\n-old\n+new",
    )
    provider = MockLLMProvider(
        LLMReviewOutput(
            summary="One problem was found.",
            findings=(_finding(location=LLMReviewLocation(start_line=4, end_line=4)),),
        )
    )

    report = AIReviewService(provider).review(_context(reviewable_files=(changed_file,)))

    assert report.findings[0].location is None


def test_ai_review_drops_location_when_patch_cannot_be_mapped() -> None:
    changed_file = ChangedFile(
        path="src/auth.py",
        status=FileChangeStatus.MODIFIED,
        additions=1,
        deletions=0,
        changes=1,
        patch=None,
    )
    provider = MockLLMProvider(
        LLMReviewOutput(
            summary="One problem was found.",
            findings=(_finding(location=LLMReviewLocation(start_line=1, end_line=1)),),
        )
    )

    report = AIReviewService(provider).review(_context(reviewable_files=(changed_file,)))

    assert report.findings[0].location is None


def test_ai_review_metadata_exposes_usage_without_changing_domain_report() -> None:
    provider = _UsageProvider(
        LLMReviewOutput(
            summary="One high-risk authorization issue was found.",
            findings=(_finding(),),
        )
    )

    result = AIReviewService(provider).review_with_metadata(_context())

    assert result.report.summary == "One high-risk authorization issue was found."
    assert result.report.model_name == "usage-test-model"
    assert result.usage == LLMTokenUsage(input_tokens=40, output_tokens=10, total_tokens=50)


def test_mock_provider_returns_no_usage_metadata() -> None:
    provider = MockLLMProvider()

    result = provider.generate_review(ReviewPrompt(system="review", user="{}", version="review-v1"))

    assert result.output.summary
    assert result.usage is None


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


def test_ai_review_metadata_has_no_usage_when_no_model_is_called() -> None:
    skipped = SkippedFile(file=_file("package-lock.json"), reason=FileSkipReason.EXCLUDED_PATH)
    provider = MockLLMProvider()

    result = AIReviewService(provider).review_with_metadata(
        _context(reviewable_files=(), skipped_files=(skipped,))
    )

    assert result.usage is None
    assert result.report.reviewed_file_count == 0
    assert provider.prompts == []
