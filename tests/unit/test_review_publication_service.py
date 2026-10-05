import hashlib
from collections.abc import Iterator
from datetime import UTC, datetime
from uuid import UUID

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker

from app.domain.review.entities import (
    PullRequestReview,
    PullRequestReviewComment,
    PullRequestSnapshot,
)
from app.domain.review.enums import ReviewPublicationProvider, ReviewPublicationStatus, ReviewStatus
from app.domain.review.exceptions import PullRequestBaseChanged, PullRequestHeadChanged
from app.domain.review.report import (
    FindingCategory,
    FindingSeverity,
    ReviewFinding,
    ReviewLineRange,
    ReviewReport,
    ReviewRiskLevel,
    derive_review_risk,
)
from app.models import Base
from app.models.review_publication import ReviewPublicationRecord
from app.models.review_task import ReviewTask
from app.providers.github.exceptions import GitHubTransientError
from app.repositories.review_publication_repository import ReviewPublicationRepository
from app.repositories.review_report_repository import ReviewReportRepository
from app.repositories.review_task_repository import ReviewTaskRepository
from app.services.review_markdown_formatter import ReviewMarkdownFormatter, review_marker
from app.services.review_publication_service import ReviewPublicationService
from app.services.review_report_service import ReviewReportService

HEAD_SHA = "abcdef123456"
PUBLISHED_AT = datetime(2026, 9, 7, 8, 0, tzinfo=UTC)


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
    def __init__(
        self,
        session: Session,
        *,
        base_sha: str = "123456abcdef",
        head_sha: str = HEAD_SHA,
    ) -> None:
        self._session = session
        self.base_sha = base_sha
        self.head_sha = head_sha
        self.reviews: tuple[PullRequestReview, ...] = ()
        self.created_bodies: list[str] = []
        self.created_comments: list[tuple[PullRequestReviewComment, ...]] = []
        self.get_calls = 0
        self.list_calls = 0
        self.create_calls = 0
        self.create_error: Exception | None = None

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
            title="Publish a review",
            body=None,
            base_sha=self.base_sha,
            head_sha=self.head_sha,
            draft=False,
            author_login="octocat",
        )

    def list_pull_request_files(self, **kwargs: object) -> tuple[()]:
        del kwargs
        return ()

    def list_pull_request_reviews(
        self,
        *,
        installation_id: int,
        repository: str,
        pull_request_number: int,
    ) -> tuple[PullRequestReview, ...]:
        self._assert_request(installation_id, repository, pull_request_number)
        assert not self._session.in_transaction()
        self.list_calls += 1
        return self.reviews

    def create_pull_request_review(
        self,
        *,
        installation_id: int,
        repository: str,
        pull_request_number: int,
        commit_sha: str,
        body: str,
        comments: tuple[PullRequestReviewComment, ...] = (),
    ) -> PullRequestReview:
        self._assert_request(installation_id, repository, pull_request_number)
        assert commit_sha == HEAD_SHA
        assert not self._session.in_transaction()
        self.create_calls += 1
        self.created_bodies.append(body)
        self.created_comments.append(comments)
        if self.create_error is not None:
            raise self.create_error
        return PullRequestReview(
            id=9001,
            commit_sha=commit_sha,
            state="COMMENTED",
            body=body,
            html_url="https://github.test/owner/repository/pull/7#pullrequestreview-9001",
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


class CapturingFormatter(ReviewMarkdownFormatter):
    def __init__(self) -> None:
        super().__init__()
        self.report: ReviewReport | None = None

    def format(self, report: ReviewReport, *, task_id: UUID, report_digest: str) -> str:
        self.report = report
        return super().format(report, task_id=task_id, report_digest=report_digest)


def _task_with_report(
    session: Session,
    report: ReviewReport | None = None,
) -> tuple[ReviewTask, str]:
    task = ReviewTask(
        repository="owner/repository",
        github_repository_id=1001,
        installation_id=42,
        pull_request_number=7,
        base_sha="123456abcdef",
        head_sha=HEAD_SHA,
        status=ReviewStatus.RUNNING,
        current_stage="review_ready",
        attempt_count=1,
        version=3,
    )
    session.add(task)
    session.commit()
    selected_report = report or ReviewReport(
        summary="No actionable problems were found.",
        risk_level=ReviewRiskLevel.NONE,
        findings=(),
        model_name="mock-review-model",
        prompt_version="review-v1",
        reviewed_file_count=1,
        skipped_file_count=0,
    )
    record = ReviewReportService(ReviewReportRepository(session)).save(task.id, selected_report)
    return task, record.content_digest


def _report_with_mixed_locations() -> ReviewReport:
    inline_finding = ReviewFinding(
        title="Missing empty-input guard",
        explanation="An empty list causes division by zero.",
        suggestion="Reject empty input before dividing.",
        file_path="src/statistics.py",
        severity=FindingSeverity.MEDIUM,
        category=FindingCategory.CORRECTNESS,
        confidence=0.95,
        location=ReviewLineRange(start_line=12, end_line=13),
    )
    summary_finding = ReviewFinding(
        title="Cross-file invariant can be violated",
        explanation="The issue cannot be attached to one added line.",
        suggestion="Validate the invariant at the service boundary.",
        file_path="src/service.py",
        severity=FindingSeverity.MEDIUM,
        category=FindingCategory.CORRECTNESS,
        confidence=0.9,
    )
    findings = (inline_finding, summary_finding)
    return ReviewReport(
        summary="Two correctness problems were found.",
        risk_level=derive_review_risk(findings),
        findings=findings,
        model_name="mock-review-model",
        prompt_version="review-v3",
        reviewed_file_count=2,
        skipped_file_count=0,
    )


def _service(
    session: Session,
    github: FakeGitHubProvider,
    formatter: ReviewMarkdownFormatter | None = None,
) -> ReviewPublicationService:
    return ReviewPublicationService(
        review_tasks=ReviewTaskRepository(session),
        review_reports=ReviewReportRepository(session),
        publications=ReviewPublicationRepository(session),
        github=github,
        formatter=formatter,
        clock=lambda: PUBLISHED_AT,
    )


def test_publish_creates_summary_review_and_persists_receipt(session: Session) -> None:
    task, report_digest = _task_with_report(session)
    github = FakeGitHubProvider(session)

    review = _service(session, github).publish(task.id)

    assert review.id == 9001
    assert github.get_calls == github.list_calls == github.create_calls == 1
    assert review_marker(task.id, report_digest) in github.created_bodies[0]
    assert github.created_comments == [()]
    publication = session.scalar(select(ReviewPublicationRecord))
    assert publication is not None
    assert publication.provider == ReviewPublicationProvider.GITHUB
    assert publication.status == ReviewPublicationStatus.PUBLISHED
    assert publication.external_review_id == 9001
    assert publication.attempt_count == 1
    assert publication.published_at == PUBLISHED_AT.replace(tzinfo=None)


def test_publish_restores_persisted_finding_location(session: Session) -> None:
    task, _ = _task_with_report(session, _report_with_mixed_locations())
    github = FakeGitHubProvider(session)
    formatter = CapturingFormatter()

    _service(session, github, formatter).publish(task.id)

    assert formatter.report is not None
    assert formatter.report.findings[0].location == ReviewLineRange(
        start_line=12,
        end_line=13,
    )
    assert len(github.created_comments) == 1
    assert github.created_comments[0][0].path == "src/statistics.py"
    assert github.created_comments[0][0].start_line == 12
    assert github.created_comments[0][0].line == 13
    assert "Missing empty-input guard" in github.created_comments[0][0].body
    assert "Missing empty-input guard" in github.created_bodies[0]
    assert "Cross-file invariant can be violated" in github.created_bodies[0]
    publication = session.scalar(select(ReviewPublicationRecord))
    assert publication is not None
    summary_only_digest = hashlib.sha256(github.created_bodies[0].encode("utf-8")).hexdigest()
    assert publication.body_digest != summary_only_digest


def test_repeated_publish_returns_stored_receipt_without_github_call(session: Session) -> None:
    task, _ = _task_with_report(session)
    github = FakeGitHubProvider(session)
    service = _service(session, github)
    first = service.publish(task.id)

    second = service.publish(task.id)

    assert second.id == first.id
    assert github.get_calls == github.list_calls == github.create_calls == 1


def test_publish_reconciles_review_created_before_database_commit(session: Session) -> None:
    task, report_digest = _task_with_report(session, _report_with_mixed_locations())
    github = FakeGitHubProvider(session)
    marker = review_marker(task.id, report_digest)
    github.reviews = (
        PullRequestReview(
            id=8001,
            commit_sha=HEAD_SHA,
            state="COMMENTED",
            body=f"Already published\n\n{marker}",
            html_url="https://github.test/review/8001",
        ),
    )

    review = _service(session, github).publish(task.id)

    assert review.id == 8001
    assert github.create_calls == 0
    publication = session.scalar(select(ReviewPublicationRecord))
    assert publication is not None
    assert publication.status == ReviewPublicationStatus.PUBLISHED
    assert publication.external_review_id == 8001


def test_publish_marks_task_stale_when_head_changed_during_analysis(session: Session) -> None:
    task, _ = _task_with_report(session)
    github = FakeGitHubProvider(session, head_sha="newer-head-sha")

    with pytest.raises(PullRequestHeadChanged):
        _service(session, github).publish(task.id)

    session.refresh(task)
    assert task.status == ReviewStatus.STALE
    assert task.error_code == "pull_request_head_changed_before_publication"
    assert github.list_calls == github.create_calls == 0
    assert session.scalar(select(ReviewPublicationRecord)) is None


def test_publish_marks_task_stale_when_base_changed_during_analysis(session: Session) -> None:
    task, _ = _task_with_report(session)
    github = FakeGitHubProvider(session, base_sha="newer-base-sha")

    with pytest.raises(PullRequestBaseChanged):
        _service(session, github).publish(task.id)

    session.refresh(task)
    assert task.status == ReviewStatus.STALE
    assert task.error_code == "pull_request_base_changed_before_publication"
    assert github.list_calls == github.create_calls == 0
    assert session.scalar(select(ReviewPublicationRecord)) is None


def test_transient_github_failure_is_recorded_for_retry(session: Session) -> None:
    task, _ = _task_with_report(session)
    github = FakeGitHubProvider(session)
    github.create_error = GitHubTransientError("GitHub temporarily unavailable")

    with pytest.raises(GitHubTransientError):
        _service(session, github).publish(task.id)

    publication = session.scalar(select(ReviewPublicationRecord))
    assert publication is not None
    assert publication.status == ReviewPublicationStatus.FAILED
    assert publication.last_error_code == "github_transient_error"
    assert publication.external_review_id is None
