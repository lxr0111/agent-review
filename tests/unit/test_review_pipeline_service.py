from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.domain.review.entities import ChangedFile, PullRequestReview, PullRequestSnapshot
from app.domain.review.enums import FileChangeStatus, ReviewPublicationStatus, ReviewStatus
from app.domain.review.report import FindingCategory, FindingSeverity, ReviewReport, ReviewRiskLevel
from app.models import Base
from app.models.review_file import ReviewFile
from app.models.review_publication import ReviewPublicationRecord
from app.models.review_report import ReviewReportRecord
from app.models.review_task import ReviewTask
from app.providers.github.exceptions import GitHubTransientError
from app.providers.llm.mock import MockLLMProvider
from app.providers.llm.protocol import LLMReviewResult, LLMTokenUsage, ReviewPrompt
from app.providers.llm.schemas import LLMReviewFinding, LLMReviewOutput
from app.repositories.review_file_repository import ReviewFileRepository
from app.repositories.review_publication_repository import ReviewPublicationRepository
from app.repositories.review_report_repository import ReviewReportRepository
from app.repositories.review_task_repository import ReviewTaskRepository
from app.services.ai_review_service import AIReviewService
from app.services.review_context_service import ReviewContextService
from app.services.review_pipeline_service import ReviewPipelineOutcome, ReviewPipelineService
from app.services.review_publication_service import ReviewPublicationService
from app.services.review_report_service import ReviewReportService
from app.services.review_service import ReviewService

HEAD_SHA = "abcdef123456"


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite+pysqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(dbapi_connection, connection_record) -> None:
        del connection_record
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as database_session:
        yield database_session
    engine.dispose()


class FakeGitHubProvider:
    def __init__(self, session: Session) -> None:
        self._session = session
        self.get_calls = 0
        self.file_calls = 0
        self.review_list_calls = 0
        self.review_create_calls = 0
        self.review_create_errors: list[Exception] = []

    def get_pull_request(
        self,
        *,
        installation_id: int,
        repository: str,
        pull_request_number: int,
    ) -> PullRequestSnapshot:
        self._assert_request(installation_id, repository, pull_request_number)
        assert not self._session.in_transaction()
        self.get_calls += 1
        return PullRequestSnapshot(
            repository=repository,
            number=pull_request_number,
            title="Complete the review pipeline",
            body="Run the real worker flow.",
            base_sha="123456abcdef",
            head_sha=HEAD_SHA,
            draft=False,
            author_login="octocat",
        )

    def list_pull_request_files(
        self,
        *,
        installation_id: int,
        repository: str,
        pull_request_number: int,
    ) -> tuple[ChangedFile, ...]:
        self._assert_request(installation_id, repository, pull_request_number)
        assert not self._session.in_transaction()
        self.file_calls += 1
        return (
            ChangedFile(
                path="src/auth.py",
                status=FileChangeStatus.MODIFIED,
                additions=1,
                deletions=1,
                changes=2,
                patch="@@ -1 +1 @@\n-check_access()\n+pass",
            ),
        )

    def list_pull_request_reviews(
        self,
        *,
        installation_id: int,
        repository: str,
        pull_request_number: int,
    ) -> tuple[PullRequestReview, ...]:
        self._assert_request(installation_id, repository, pull_request_number)
        assert not self._session.in_transaction()
        self.review_list_calls += 1
        return ()

    def create_pull_request_review(
        self,
        *,
        installation_id: int,
        repository: str,
        pull_request_number: int,
        commit_sha: str,
        body: str,
    ) -> PullRequestReview:
        self._assert_request(installation_id, repository, pull_request_number)
        assert not self._session.in_transaction()
        assert commit_sha == HEAD_SHA
        self.review_create_calls += 1
        if self.review_create_errors:
            raise self.review_create_errors.pop(0)
        return PullRequestReview(
            id=9001,
            commit_sha=commit_sha,
            state="COMMENTED",
            body=body,
            html_url="https://github.test/review/9001",
        )

    @staticmethod
    def _assert_request(
        installation_id: int,
        repository: str,
        pull_request_number: int,
    ) -> None:
        assert installation_id == 42
        assert repository == "owner/repository"
        assert pull_request_number == 7


def _task(session: Session, *, status: ReviewStatus = ReviewStatus.QUEUED) -> ReviewTask:
    task = ReviewTask(
        repository="owner/repository",
        github_repository_id=1001,
        installation_id=42,
        pull_request_number=7,
        head_sha=HEAD_SHA,
        status=status,
        current_stage=status.value,
        attempt_count=0,
        version=2,
    )
    session.add(task)
    session.commit()
    return task


def _llm_output() -> LLMReviewOutput:
    return LLMReviewOutput(
        summary="One authorization issue was found.",
        findings=(
            LLMReviewFinding(
                title="Authorization is bypassed",
                explanation="The changed branch no longer checks the caller.",
                suggestion="Restore the access check.",
                file_path="src/auth.py",
                severity=FindingSeverity.HIGH,
                category=FindingCategory.SECURITY,
                confidence=0.95,
            ),
        ),
    )


def _llm() -> MockLLMProvider:
    return MockLLMProvider(_llm_output())


class _UsageMockLLMProvider(MockLLMProvider):
    def generate_review(self, prompt: ReviewPrompt) -> LLMReviewResult:
        result = super().generate_review(prompt)
        return LLMReviewResult(
            output=result.output,
            usage=LLMTokenUsage(input_tokens=25, output_tokens=5, total_tokens=30),
        )


def _pipeline(
    session: Session,
    github: FakeGitHubProvider,
    llm: MockLLMProvider,
) -> ReviewPipelineService:
    task_repository = ReviewTaskRepository(session)
    report_repository = ReviewReportRepository(session)
    return ReviewPipelineService(
        reviews=ReviewService(task_repository),
        contexts=ReviewContextService(
            review_tasks=task_repository,
            review_files=ReviewFileRepository(session),
            github=github,
        ),
        ai_reviews=AIReviewService(llm),
        reports=ReviewReportService(report_repository),
        publications=ReviewPublicationService(
            review_tasks=task_repository,
            review_reports=report_repository,
            publications=ReviewPublicationRepository(session),
            github=github,
        ),
    )


def test_pipeline_runs_context_ai_persistence_and_publication(session: Session) -> None:
    task = _task(session)
    github = FakeGitHubProvider(session)
    llm = _llm()

    outcome = _pipeline(session, github, llm).run(task.id)

    session.refresh(task)
    assert outcome == ReviewPipelineOutcome.COMPLETED
    assert task.status == ReviewStatus.COMPLETED
    assert task.current_stage == "completed"
    assert task.attempt_count == 1
    assert len(llm.prompts) == 1
    assert github.get_calls == 2
    assert github.file_calls == 1
    assert github.review_create_calls == 1
    assert session.scalar(select(func.count()).select_from(ReviewFile)) == 1
    assert session.scalar(select(func.count()).select_from(ReviewReportRecord)) == 1
    publication = session.scalar(select(ReviewPublicationRecord))
    assert publication is not None
    assert publication.status == ReviewPublicationStatus.PUBLISHED


def test_pipeline_remains_compatible_when_provider_returns_usage(session: Session) -> None:
    task = _task(session)
    github = FakeGitHubProvider(session)
    llm = _UsageMockLLMProvider(_llm_output())

    outcome = _pipeline(session, github, llm).run(task.id)

    session.refresh(task)
    report = session.scalar(select(ReviewReportRecord))
    assert outcome == ReviewPipelineOutcome.COMPLETED
    assert task.status == ReviewStatus.COMPLETED
    assert len(llm.prompts) == 1
    assert report is not None
    assert report.model_name == "mock-review-model"
    assert github.review_create_calls == 1


def test_pipeline_resumes_from_saved_report_without_calling_ai_again(session: Session) -> None:
    task = _task(session, status=ReviewStatus.RUNNING)
    task.current_stage = "saving_report"
    task.attempt_count = 1
    session.commit()
    report = ReviewReport(
        summary="The report survived a worker crash.",
        risk_level=ReviewRiskLevel.NONE,
        findings=(),
        model_name="mock-review-model",
        prompt_version="review-v1",
        reviewed_file_count=1,
        skipped_file_count=0,
    )
    ReviewReportService(ReviewReportRepository(session)).save(task.id, report)
    github = FakeGitHubProvider(session)
    llm = _llm()

    outcome = _pipeline(session, github, llm).run(task.id)

    session.refresh(task)
    assert outcome == ReviewPipelineOutcome.COMPLETED
    assert task.attempt_count == 2
    assert len(llm.prompts) == 0
    assert github.file_calls == 0
    assert github.get_calls == 1
    assert github.review_create_calls == 1


def test_pipeline_retries_publication_without_calling_ai_again(session: Session) -> None:
    task = _task(session)
    github = FakeGitHubProvider(session)
    github.review_create_errors.append(GitHubTransientError("temporary publication failure"))
    llm = _llm()
    pipeline = _pipeline(session, github, llm)

    with pytest.raises(GitHubTransientError, match="temporary publication failure"):
        pipeline.run(task.id)

    session.refresh(task)
    first_publication = session.scalar(select(ReviewPublicationRecord))
    assert task.status == ReviewStatus.RUNNING
    assert task.attempt_count == 1
    assert len(llm.prompts) == 1
    assert github.file_calls == 1
    assert github.review_create_calls == 1
    assert session.scalar(select(func.count()).select_from(ReviewReportRecord)) == 1
    assert first_publication is not None
    assert first_publication.status == ReviewPublicationStatus.FAILED
    assert first_publication.attempt_count == 1

    outcome = pipeline.run(task.id)

    session.refresh(task)
    session.refresh(first_publication)
    assert outcome == ReviewPipelineOutcome.COMPLETED
    assert task.status == ReviewStatus.COMPLETED
    assert task.attempt_count == 2
    assert len(llm.prompts) == 1
    assert github.file_calls == 1
    assert github.review_create_calls == 2
    assert session.scalar(select(func.count()).select_from(ReviewReportRecord)) == 1
    assert session.scalar(select(func.count()).select_from(ReviewPublicationRecord)) == 1
    assert first_publication.status == ReviewPublicationStatus.PUBLISHED
    assert first_publication.attempt_count == 2


def test_pipeline_skips_already_completed_task(session: Session) -> None:
    task = _task(session, status=ReviewStatus.COMPLETED)
    github = FakeGitHubProvider(session)
    llm = _llm()

    outcome = _pipeline(session, github, llm).run(task.id)

    assert outcome == ReviewPipelineOutcome.ALREADY_COMPLETED
    assert github.get_calls == 0
    assert len(llm.prompts) == 0
