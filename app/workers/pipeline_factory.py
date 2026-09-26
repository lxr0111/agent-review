from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from typing import Protocol, runtime_checkable

from app.core.config import Settings
from app.database.session import session_factory
from app.domain.review.file_filter import FileFilterPolicy
from app.providers.github import GitHubApiClient
from app.providers.llm.factory import create_llm_provider
from app.repositories.review_file_repository import ReviewFileRepository
from app.repositories.review_publication_repository import ReviewPublicationRepository
from app.repositories.review_report_repository import ReviewReportRepository
from app.repositories.review_task_repository import ReviewTaskRepository
from app.services.ai_review_service import AIReviewService
from app.services.review_agent import GeneralReviewAgent
from app.services.review_aggregator import PassthroughReviewAggregator
from app.services.review_context_service import ReviewContextService
from app.services.review_markdown_formatter import ReviewMarkdownFormatter
from app.services.review_orchestrator import ReviewOrchestrator
from app.services.review_pipeline_service import ReviewPipelineService
from app.services.review_publication_service import ReviewPublicationService
from app.services.review_report_service import ReviewReportService
from app.services.review_service import ReviewService


@runtime_checkable
class _Closable(Protocol):
    def close(self) -> None: ...


@contextmanager
def review_pipeline_context(settings: Settings) -> Iterator[ReviewPipelineService]:
    with ExitStack() as stack:
        session = stack.enter_context(session_factory())
        github = stack.enter_context(GitHubApiClient.from_settings(settings))
        llm = create_llm_provider(settings)
        if isinstance(llm, _Closable):
            stack.callback(llm.close)

        review_tasks = ReviewTaskRepository(session)
        review_reports = ReviewReportRepository(session)
        pipeline = ReviewPipelineService(
            reviews=ReviewService(review_tasks),
            contexts=ReviewContextService(
                review_tasks=review_tasks,
                review_files=ReviewFileRepository(session),
                github=github,
                file_policy=FileFilterPolicy(
                    max_changes=settings.review_max_file_changes,
                    max_patch_characters=settings.review_max_file_patch_characters,
                ),
                max_total_patch_characters=settings.review_max_total_patch_characters,
            ),
            review_engine=ReviewOrchestrator(
                agents=(GeneralReviewAgent(AIReviewService(llm)),),
                aggregator=PassthroughReviewAggregator(),
            ),
            reports=ReviewReportService(review_reports),
            publications=ReviewPublicationService(
                review_tasks=review_tasks,
                review_reports=review_reports,
                publications=ReviewPublicationRepository(session),
                github=github,
                formatter=ReviewMarkdownFormatter(
                    max_characters=settings.github_review_body_max_characters
                ),
            ),
        )
        yield pipeline
