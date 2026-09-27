from collections.abc import Iterator
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
from app.providers.llm.exceptions import LLMTransientError
from app.services.planned_review_orchestrator import (
    PlannedAgentExecutionError,
    PlannedReviewAggregationError,
    PlannedReviewOrchestrator,
)
from app.services.review_agent import AgentReviewResult, AgentRole
from app.services.review_agent_registry import ReviewAgentRegistry
from app.services.review_aggregator import DeterministicReviewAggregator
from app.services.review_planner import DeterministicReviewPlanner


class _Agent:
    def __init__(
        self,
        role: AgentRole,
        result: AgentReviewResult | Exception,
    ) -> None:
        self._role = role
        self._result = result
        self.calls = 0

    @property
    def role(self) -> AgentRole:
        return self._role

    @property
    def prompt_version(self) -> str:
        return f"review-{self.role.value}-v1"

    def review(self, context: ReviewContext) -> AgentReviewResult:
        del context
        self.calls += 1
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


def _clock(*values: float):
    iterator: Iterator[float] = iter(values)
    return lambda: next(iterator)


def _context() -> ReviewContext:
    changed_file = ChangedFile(
        path="src/users.py",
        status=FileChangeStatus.MODIFIED,
        additions=1,
        deletions=1,
        changes=2,
        patch=(
            "@@ -1 +1 @@\n"
            "-cursor.execute(query, (email,))\n"
            "+cursor.execute(f\"SELECT * FROM users WHERE email = '{email}'\")"
        ),
    )
    return ReviewContext(
        pull_request=PullRequestSnapshot(
            repository="owner/repository",
            number=9,
            title="Change SQL user lookup",
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
        title="SQL injection through interpolated email",
        explanation="The interpolated email changes the SQL query structure.",
        suggestion="Use a parameterized query.",
        file_path="src/users.py",
        severity=FindingSeverity.HIGH,
        category=FindingCategory.SECURITY,
        confidence=0.9,
    )


def _result(role: AgentRole, finding: ReviewFinding | None = None) -> AgentReviewResult:
    findings = (finding,) if finding is not None else ()
    return AgentReviewResult(
        role=role,
        report=ReviewReport(
            summary=f"{role.value} review completed.",
            risk_level=derive_review_risk(findings),
            findings=findings,
            model_name="test-model",
            prompt_version=f"review-{role.value}-v1",
            reviewed_file_count=1,
            skipped_file_count=0,
        ),
    )


def _orchestrator(
    correctness: _Agent,
    security: _Agent,
    *,
    clock=None,
) -> PlannedReviewOrchestrator:
    return PlannedReviewOrchestrator(
        planner=DeterministicReviewPlanner(),
        registry=ReviewAgentRegistry((correctness, security)),
        aggregator=DeterministicReviewAggregator(),
        clock=clock or _clock(0.0, 0.1, 0.1, 0.3),
    )


def test_planned_orchestrator_runs_selected_agents_and_aggregates() -> None:
    finding = _finding()
    correctness = _Agent(AgentRole.CORRECTNESS, _result(AgentRole.CORRECTNESS, finding))
    security_finding = replace(finding, confidence=0.95)
    security = _Agent(AgentRole.SECURITY, _result(AgentRole.SECURITY, security_finding))

    result = _orchestrator(correctness, security).review_with_metadata(_context())

    assert result.plan.roles == (AgentRole.CORRECTNESS, AgentRole.SECURITY)
    assert [execution.role for execution in result.executions] == [
        AgentRole.CORRECTNESS,
        AgentRole.SECURITY,
    ]
    assert [execution.latency_ms for execution in result.executions] == pytest.approx(
        [100.0, 200.0]
    )
    assert result.report.findings == (security_finding,)
    assert len(result.aggregation.decisions) == 2
    assert correctness.calls == 1
    assert security.calls == 1


def test_planned_orchestrator_preserves_completed_executions_on_agent_failure() -> None:
    correctness = _Agent(AgentRole.CORRECTNESS, _result(AgentRole.CORRECTNESS))
    failure = LLMTransientError("provider unavailable")
    security = _Agent(AgentRole.SECURITY, failure)

    with pytest.raises(PlannedAgentExecutionError) as raised:
        _orchestrator(correctness, security).review_with_metadata(_context())

    assert raised.value.failed_role == AgentRole.SECURITY
    assert raised.value.cause is failure
    assert [execution.role for execution in raised.value.completed_executions] == [
        AgentRole.CORRECTNESS
    ]
    assert raised.value.latency_ms == pytest.approx(200.0)


def test_planned_orchestrator_preserves_executions_on_aggregation_failure() -> None:
    invalid = replace(_finding(), file_path="src/not-reviewed.py")
    correctness = _Agent(AgentRole.CORRECTNESS, _result(AgentRole.CORRECTNESS))
    security = _Agent(AgentRole.SECURITY, _result(AgentRole.SECURITY, invalid))

    with pytest.raises(PlannedReviewAggregationError) as raised:
        _orchestrator(correctness, security).review_with_metadata(_context())

    assert [execution.role for execution in raised.value.executions] == [
        AgentRole.CORRECTNESS,
        AgentRole.SECURITY,
    ]
    assert "unknown file" in str(raised.value.cause)
