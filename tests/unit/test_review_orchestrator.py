from unittest.mock import Mock

import pytest

from app.domain.review.entities import ChangedFile, PullRequestSnapshot, ReviewContext
from app.domain.review.enums import FileChangeStatus
from app.domain.review.report import ReviewReport, ReviewRiskLevel
from app.providers.llm.exceptions import LLMTransientError
from app.services.ai_review_service import AIReviewService
from app.services.review_agent import (
    AgentReviewResult,
    AgentRole,
    GeneralReviewAgent,
    ReviewAgent,
)
from app.services.review_aggregator import (
    PassthroughReviewAggregator,
    ReviewAggregator,
)
from app.services.review_engine import ReviewEngine
from app.services.review_orchestrator import ReviewOrchestrator


def _context() -> ReviewContext:
    changed_file = ChangedFile(
        path="src/auth.py",
        status=FileChangeStatus.MODIFIED,
        additions=1,
        deletions=1,
        changes=2,
        patch="@@ -1 +1 @@\n-check_access()\n+pass",
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


def _report() -> ReviewReport:
    return ReviewReport(
        summary="One issue was found.",
        risk_level=ReviewRiskLevel.HIGH,
        findings=(),
        model_name="test-model",
        prompt_version="review-v2",
        reviewed_file_count=1,
        skipped_file_count=0,
    )


def test_general_agent_delegates_to_existing_ai_review_service() -> None:
    context = _context()
    report = _report()
    review_service = Mock(spec=AIReviewService)
    review_service.review.return_value = report
    agent = GeneralReviewAgent(review_service)

    result = agent.review(context)

    assert isinstance(agent, ReviewAgent)
    assert agent.role == AgentRole.GENERAL
    assert result is report
    review_service.review.assert_called_once_with(context)


def test_orchestrator_is_review_engine_and_preserves_report() -> None:
    context = _context()
    report = _report()
    review_service = Mock(spec=AIReviewService)
    review_service.review.return_value = report
    aggregator = Mock(spec=ReviewAggregator)
    aggregator.aggregate.return_value = report
    orchestrator = ReviewOrchestrator(
        agents=(GeneralReviewAgent(review_service),),
        aggregator=aggregator,
    )

    result = orchestrator.review(context)

    assert isinstance(orchestrator, ReviewEngine)
    assert result is report
    review_service.review.assert_called_once_with(context)
    aggregate_call = aggregator.aggregate.call_args
    assert aggregate_call.kwargs["context"] is context
    agent_results = aggregate_call.kwargs["results"]
    assert len(agent_results) == 1
    assert agent_results[0].role == AgentRole.GENERAL
    assert agent_results[0].report is report


def test_passthrough_aggregator_rejects_zero_or_multiple_results() -> None:
    aggregator = PassthroughReviewAggregator()
    agent_result = AgentReviewResult(role=AgentRole.GENERAL, report=_report())

    assert isinstance(aggregator, ReviewAggregator)
    with pytest.raises(ValueError, match="exactly one result"):
        aggregator.aggregate(context=_context(), results=())
    with pytest.raises(ValueError, match="exactly one result"):
        aggregator.aggregate(context=_context(), results=(agent_result, agent_result))


def test_orchestrator_rejects_empty_agent_configuration() -> None:
    with pytest.raises(ValueError, match="at least one agent"):
        ReviewOrchestrator(agents=(), aggregator=PassthroughReviewAggregator())


def test_orchestrator_preserves_agent_error_for_worker_retry_policy() -> None:
    error = LLMTransientError("provider temporarily unavailable")
    review_service = Mock(spec=AIReviewService)
    review_service.review.side_effect = error
    orchestrator = ReviewOrchestrator(
        agents=(GeneralReviewAgent(review_service),),
        aggregator=PassthroughReviewAggregator(),
    )

    with pytest.raises(LLMTransientError) as raised:
        orchestrator.review(_context())

    assert raised.value is error
