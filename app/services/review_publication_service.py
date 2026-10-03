import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError

from app.core.exceptions import ReviewTaskNotFound
from app.domain.review.entities import PullRequestReview
from app.domain.review.enums import ReviewPublicationProvider, ReviewPublicationStatus
from app.domain.review.exceptions import (
    MissingGitHubInstallation,
    PullRequestHeadChanged,
    PullRequestIdentityMismatch,
    ReviewPublicationConflict,
    ReviewReportNotFound,
)
from app.domain.review.report import ReviewFinding, ReviewReport, optional_review_line_range
from app.models.review_publication import ReviewPublicationRecord
from app.models.review_report import ReviewReportRecord
from app.providers.github.protocol import GitHubProvider
from app.repositories.review_publication_repository import ReviewPublicationRepository
from app.repositories.review_report_repository import ReviewReportRepository
from app.repositories.review_task_repository import ReviewTaskRepository
from app.services.review_markdown_formatter import ReviewMarkdownFormatter, review_marker
from app.services.review_service import ReviewService


@dataclass(frozen=True, slots=True)
class _PublicationInput:
    task_id: UUID
    report_id: UUID
    report_digest: str
    report: ReviewReport
    repository: str
    pull_request_number: int
    installation_id: int
    head_sha: str


class ReviewPublicationService:
    def __init__(
        self,
        *,
        review_tasks: ReviewTaskRepository,
        review_reports: ReviewReportRepository,
        publications: ReviewPublicationRepository,
        github: GitHubProvider,
        formatter: ReviewMarkdownFormatter | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._review_tasks = review_tasks
        self._review_reports = review_reports
        self._publications = publications
        self._github = github
        self._formatter = formatter or ReviewMarkdownFormatter()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._review_service = ReviewService(review_tasks)

    def publish(self, task_id: UUID) -> PullRequestReview:
        publication_input, published = self._load(task_id)
        if published is not None:
            return published

        self._review_service.start(task_id, stage="publishing_review")
        pull_request = self._github.get_pull_request(
            installation_id=publication_input.installation_id,
            repository=publication_input.repository,
            pull_request_number=publication_input.pull_request_number,
        )
        if (
            pull_request.repository != publication_input.repository
            or pull_request.number != publication_input.pull_request_number
        ):
            identity_error = PullRequestIdentityMismatch(
                "GitHub provider returned a different pull request identity before publication"
            )
            self._review_service.fail(
                task_id,
                "pull_request_identity_mismatch",
                str(identity_error),
            )
            raise identity_error
        if pull_request.head_sha != publication_input.head_sha:
            head_error = PullRequestHeadChanged(
                publication_input.head_sha,
                pull_request.head_sha,
            )
            self._review_service.mark_stale(
                task_id,
                "pull_request_head_changed_before_publication",
                str(head_error),
            )
            raise head_error

        body = self._formatter.format(
            publication_input.report,
            task_id=task_id,
            report_digest=publication_input.report_digest,
        )
        marker = review_marker(task_id, publication_input.report_digest)
        body_digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
        prepared = self._prepare(publication_input, body_digest)
        if prepared is not None:
            return prepared

        try:
            existing_review = next(
                (
                    review
                    for review in self._github.list_pull_request_reviews(
                        installation_id=publication_input.installation_id,
                        repository=publication_input.repository,
                        pull_request_number=publication_input.pull_request_number,
                    )
                    if review.commit_sha == publication_input.head_sha
                    and review.body is not None
                    and marker in review.body
                ),
                None,
            )
            review = existing_review or self._github.create_pull_request_review(
                installation_id=publication_input.installation_id,
                repository=publication_input.repository,
                pull_request_number=publication_input.pull_request_number,
                commit_sha=publication_input.head_sha,
                body=body,
            )
            if review.commit_sha != publication_input.head_sha:
                raise ReviewPublicationConflict("GitHub review was attached to a different commit")
        except Exception as error:
            self._mark_failed(publication_input.report_id, error)
            raise

        self._mark_published(publication_input.report_id, review)
        return review

    def _load(
        self,
        task_id: UUID,
    ) -> tuple[_PublicationInput, PullRequestReview | None]:
        try:
            task = self._review_tasks.get(task_id)
            if task is None:
                raise ReviewTaskNotFound(f"Review task {task_id} was not found")
            report_record = self._review_reports.get_for_task(task_id)
            if report_record is None:
                raise ReviewReportNotFound(f"Review task {task_id} does not have a report")
            publication = self._publications.get_for_report(
                report_record.id,
                ReviewPublicationProvider.GITHUB,
            )
            published = _published_review(publication)
            if task.installation_id is None:
                raise MissingGitHubInstallation(
                    "Review task does not have a GitHub installation ID"
                )
            publication_input = _PublicationInput(
                task_id=task_id,
                report_id=report_record.id,
                report_digest=report_record.content_digest,
                report=_domain_report(report_record),
                repository=task.repository,
                pull_request_number=task.pull_request_number,
                installation_id=task.installation_id,
                head_sha=task.head_sha,
            )
            self._review_tasks.commit()
            return publication_input, published
        except Exception:
            self._review_tasks.rollback()
            raise

    def _prepare(
        self,
        publication_input: _PublicationInput,
        body_digest: str,
    ) -> PullRequestReview | None:
        try:
            report = self._review_reports.get_for_task(
                publication_input.task_id,
                for_update=True,
            )
            if report is None or report.id != publication_input.report_id:
                raise ReviewReportNotFound("Review report disappeared before publication")
            if report.content_digest != publication_input.report_digest:
                raise ReviewPublicationConflict(
                    "Review report changed while publication was being prepared"
                )

            publication = self._publications.get_for_report(
                publication_input.report_id,
                ReviewPublicationProvider.GITHUB,
                for_update=True,
            )
            published = _published_review(publication)
            if published is not None:
                self._publications.commit()
                return published
            if publication is None:
                publication = ReviewPublicationRecord(
                    review_report_id=publication_input.report_id,
                    provider=ReviewPublicationProvider.GITHUB,
                    status=ReviewPublicationStatus.PENDING,
                    commit_sha=publication_input.head_sha,
                    body_digest=body_digest,
                    attempt_count=1,
                )
                self._publications.add(publication)
            else:
                publication.status = ReviewPublicationStatus.PENDING
                publication.commit_sha = publication_input.head_sha
                publication.body_digest = body_digest
                publication.attempt_count += 1
                publication.last_error_code = None
                publication.last_error_message = None
            self._publications.commit()
            return None
        except Exception:
            self._publications.rollback()
            raise

    def _mark_published(self, report_id: UUID, review: PullRequestReview) -> None:
        try:
            publication = self._publications.get_for_report(
                report_id,
                ReviewPublicationProvider.GITHUB,
                for_update=True,
            )
            if publication is None:
                raise ReviewPublicationConflict("Review publication record was not prepared")
            if (
                publication.status == ReviewPublicationStatus.PUBLISHED
                and publication.external_review_id != review.id
            ):
                raise ReviewPublicationConflict(
                    "Review publication was already linked to a different GitHub review"
                )
            publication.status = ReviewPublicationStatus.PUBLISHED
            publication.external_review_id = review.id
            publication.external_review_url = review.html_url
            publication.external_state = review.state
            publication.last_error_code = None
            publication.last_error_message = None
            publication.published_at = self._now()
            self._publications.commit()
        except Exception:
            self._publications.rollback()
            raise

    def _mark_failed(self, report_id: UUID, error: Exception) -> None:
        try:
            publication = self._publications.get_for_report(
                report_id,
                ReviewPublicationProvider.GITHUB,
                for_update=True,
            )
            if publication is None or publication.status == ReviewPublicationStatus.PUBLISHED:
                self._publications.rollback()
                return
            publication.status = ReviewPublicationStatus.FAILED
            publication.last_error_code = _error_code(error)
            publication.last_error_message = str(error)[:2_000]
            self._publications.commit()
        except SQLAlchemyError:
            self._publications.rollback()

    def _now(self) -> datetime:
        now = self._clock()
        if now.tzinfo is None:
            raise ValueError("Review publication clock must be timezone-aware")
        return now.astimezone(UTC)


def _domain_report(record: ReviewReportRecord) -> ReviewReport:
    return ReviewReport(
        summary=record.summary,
        risk_level=record.risk_level,
        findings=tuple(
            ReviewFinding(
                title=finding.title,
                explanation=finding.explanation,
                suggestion=finding.suggestion,
                file_path=finding.file_path,
                severity=finding.severity,
                category=finding.category,
                confidence=float(finding.confidence),
                location=optional_review_line_range(
                    finding.start_line,
                    finding.end_line,
                ),
            )
            for finding in record.findings
        ),
        model_name=record.model_name,
        prompt_version=record.prompt_version,
        reviewed_file_count=record.reviewed_file_count,
        skipped_file_count=record.skipped_file_count,
    )


def _published_review(
    publication: ReviewPublicationRecord | None,
) -> PullRequestReview | None:
    if publication is None or publication.status != ReviewPublicationStatus.PUBLISHED:
        return None
    if publication.external_review_id is None:
        raise ReviewPublicationConflict("Published review does not have an external ID")
    return PullRequestReview(
        id=publication.external_review_id,
        commit_sha=publication.commit_sha,
        state=publication.external_state or "COMMENTED",
        body=None,
        html_url=publication.external_review_url,
    )


def _error_code(error: Exception) -> str:
    name = type(error).__name__.replace("GitHub", "Github")
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()[:100]
