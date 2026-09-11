from enum import StrEnum
from uuid import UUID

from app.domain.review.enums import ReviewStatus
from app.services.ai_review_service import AIReviewService
from app.services.review_context_service import ReviewContextService
from app.services.review_publication_service import ReviewPublicationService
from app.services.review_report_service import ReviewReportService
from app.services.review_service import ReviewService


class ReviewPipelineOutcome(StrEnum):
    COMPLETED = "completed"
    ALREADY_COMPLETED = "already_completed"
    TERMINAL_TASK_SKIPPED = "terminal_task_skipped"


class ReviewPipelineService:
    def __init__(
        self,
        *,
        reviews: ReviewService,
        contexts: ReviewContextService,
        ai_reviews: AIReviewService,
        reports: ReviewReportService,
        publications: ReviewPublicationService,
    ) -> None:
        self._reviews = reviews
        self._contexts = contexts
        self._ai_reviews = ai_reviews
        self._reports = reports
        self._publications = publications

    def run(self, task_id: UUID) -> ReviewPipelineOutcome:
        task = self._reviews.get(task_id)
        if task.status == ReviewStatus.COMPLETED:
            return ReviewPipelineOutcome.ALREADY_COMPLETED
        if task.status in {ReviewStatus.CANCELLED, ReviewStatus.FAILED, ReviewStatus.STALE}:
            return ReviewPipelineOutcome.TERMINAL_TASK_SKIPPED

        self._reviews.begin_attempt(task_id)
        report = self._reports.get(task_id)
        if report is None:
            context = self._contexts.build(task_id)
            self._reviews.start(task_id, stage="reviewing")
            report = self._ai_reviews.review(context)
            self._reviews.start(task_id, stage="saving_report")
            self._reports.save(task_id, report)

        self._publications.publish(task_id)
        self._reviews.complete(task_id)
        return ReviewPipelineOutcome.COMPLETED
