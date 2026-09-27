from datetime import UTC, datetime
from pathlib import Path

from app.domain.review.report import FindingCategory, FindingSeverity
from app.providers.llm.exceptions import LLMTransientError
from app.providers.llm.protocol import LLMReviewResult, LLMTokenUsage, ReviewPrompt
from app.providers.llm.schemas import LLMReviewFinding, LLMReviewOutput
from app.services.planned_review_orchestrator import PlannedReviewOrchestrator
from app.services.review_agent import AgentRole
from app.services.review_agent_registry import create_default_agent_registry
from app.services.review_aggregator import AggregationAction, DeterministicReviewAggregator
from app.services.review_planner import DeterministicReviewPlanner
from evals.baseline import GitState
from evals.metrics import EvaluationExecutionMode, EvaluationOutcome
from evals.multi_agent import evaluate_multi_agent_case
from evals.runner import (
    run_live_multi_agent_evaluation,
    run_mock_multi_agent_evaluation,
)
from evals.schemas import EvalCase, load_dataset

DATASET_PATH = Path("evals/cases/review_seed_v1.json")
SECURITY_CASE_ID = "security-sql-injection"


class _UsageProvider:
    model_name = "usage-test-model"

    def __init__(self, output: LLMReviewOutput, *, fail_on_call: int | None = None) -> None:
        self._output = output
        self._fail_on_call = fail_on_call
        self.prompts: list[ReviewPrompt] = []

    def generate_review(self, prompt: ReviewPrompt) -> LLMReviewResult:
        self.prompts.append(prompt)
        if self._fail_on_call == len(self.prompts):
            raise LLMTransientError("provider unavailable")
        return LLMReviewResult(
            output=self._output,
            usage=LLMTokenUsage(input_tokens=70, output_tokens=30, total_tokens=100),
        )


class _RoleAwareConflictProvider:
    model_name = "conflict-test-model"

    def generate_review(self, prompt: ReviewPrompt) -> LLMReviewResult:
        is_security = prompt.version == "review-security-v1"
        return LLMReviewResult(
            output=LLMReviewOutput(
                summary="One issue found.",
                findings=(
                    LLMReviewFinding(
                        title="SQL injection through interpolated email",
                        explanation=(
                            "The query interpolates `email` directly into SQL, allowing "
                            "untrusted input to alter the statement."
                        ),
                        suggestion="Use a parameterized query.",
                        file_path="src/users.py",
                        category=(
                            FindingCategory.SECURITY
                            if is_security
                            else FindingCategory.CORRECTNESS
                        ),
                        severity=(
                            FindingSeverity.HIGH
                            if is_security
                            else FindingSeverity.MEDIUM
                        ),
                        confidence=0.95 if is_security else 0.90,
                    ),
                ),
            ),
            usage=None,
        )


def _security_case() -> EvalCase:
    return next(
        case
        for case in load_dataset(DATASET_PATH).cases
        if case.id == SECURITY_CASE_ID
    )


def _output(case: EvalCase) -> LLMReviewOutput:
    expected = case.expected_findings[0]
    return LLMReviewOutput(
        summary="One security issue found.",
        findings=(
            LLMReviewFinding(
                title=expected.description,
                explanation=(
                    "Matching concepts: "
                    + "; ".join(group[0] for group in expected.match_terms)
                ),
                suggestion="Use a parameterized query.",
                file_path=expected.file_path,
                category=expected.category,
                severity=expected.severity,
                confidence=1.0,
            ),
        ),
    )


def _orchestrator(provider: _UsageProvider) -> PlannedReviewOrchestrator:
    return PlannedReviewOrchestrator(
        planner=DeterministicReviewPlanner(),
        registry=create_default_agent_registry(provider),
        aggregator=DeterministicReviewAggregator(),
    )


def test_mock_multi_agent_runner_self_tests_planning_and_aggregation() -> None:
    result = run_mock_multi_agent_evaluation(
        load_dataset(DATASET_PATH),
        case_id=SECURITY_CASE_ID,
    )

    assert result.run_kind == "multi_agent_evaluator_self_test"
    assert result.execution_mode == EvaluationExecutionMode.PLANNED_MULTI_AGENT
    assert result.agent_role is None
    assert result.finding_identity_version == "finding-text-v2"
    assert result.is_model_baseline is False
    assert result.metrics.case_pass_rate == 1.0
    assert result.metrics.total_agent_calls == 2
    assert result.metrics.pre_aggregation_findings == 2
    assert result.metrics.post_aggregation_findings == 1
    assert result.metrics.merged_findings == 1
    assert result.metrics.duplicate_rate == 0.5
    assert result.metrics.usage.total_tokens is None

    case = result.cases[0]
    assert [agent.role for agent in case.planned_agents] == [
        AgentRole.CORRECTNESS,
        AgentRole.SECURITY,
    ]
    assert [execution.role for execution in case.agent_executions] == [
        AgentRole.CORRECTNESS,
        AgentRole.SECURITY,
    ]
    assert [execution.finding_count for execution in case.agent_executions] == [1, 1]
    assert all(
        len(execution.findings) == execution.finding_count
        for execution in case.agent_executions
    )
    assert case.post_aggregation_finding_count == 1
    assert [decision.action for decision in case.aggregation_decisions] == [
        AggregationAction.KEPT,
        AggregationAction.MERGED,
    ]
    assert [decision.source_role for decision in case.aggregation_decisions] == [
        AgentRole.SECURITY,
        AgentRole.CORRECTNESS,
    ]
    assert len(
        {
            decision.canonical_fingerprint
            for decision in case.aggregation_decisions
        }
    ) == 1
    assert all(decision.reason for decision in case.aggregation_decisions)
    assert case.aggregation_conflicts == ()
    assert {
        contribution.role: (
            contribution.raw_finding_count,
            contribution.kept_finding_count,
            contribution.merged_finding_count,
            contribution.unique_finding_count,
        )
        for contribution in case.agent_contributions
    } == {
        AgentRole.CORRECTNESS: (1, 0, 1, 0),
        AgentRole.SECURITY: (1, 1, 0, 0),
    }


def test_full_mock_multi_agent_run_records_expected_planner_call_count() -> None:
    result = run_mock_multi_agent_evaluation(load_dataset(DATASET_PATH))

    assert result.metrics.total_cases == 10
    assert result.metrics.passed_cases == 10
    assert result.metrics.total_agent_calls == 16
    assert result.metrics.merged_findings == 5
    assert result.metrics.false_positives == 0


def test_live_multi_agent_runner_sums_usage_and_reproducibility_metadata() -> None:
    dataset = load_dataset(DATASET_PATH)
    case = _security_case()
    provider = _UsageProvider(_output(case))
    executed_at = datetime(2026, 9, 27, 9, 0, tzinfo=UTC)

    result = run_live_multi_agent_evaluation(
        dataset,
        provider,
        dataset_digest="a" * 64,
        git_state=GitState(commit="abc123", dirty=False),
        case_id=case.id,
        executed_at=executed_at,
    )

    assert result.run_kind == "multi_agent_live_evaluation"
    assert result.is_model_baseline is False
    assert result.git_commit == "abc123"
    assert result.executed_at == executed_at
    assert result.metrics.total_agent_calls == 2
    assert result.metrics.usage.input_tokens == 140
    assert result.metrics.usage.output_tokens == 60
    assert result.metrics.usage.total_tokens == 200
    assert [prompt.version for prompt in provider.prompts] == [
        "review-correctness-v1",
        "review-security-v1",
    ]


def test_multi_agent_case_preserves_raw_labels_and_explains_conflict_resolution() -> None:
    case = _security_case()
    provider = _RoleAwareConflictProvider()

    result = evaluate_multi_agent_case(case, _orchestrator(provider))

    raw_findings = {
        execution.role: execution.findings[0]
        for execution in result.agent_executions
    }
    assert raw_findings[AgentRole.CORRECTNESS].category == FindingCategory.CORRECTNESS
    assert raw_findings[AgentRole.CORRECTNESS].severity == FindingSeverity.MEDIUM
    assert raw_findings[AgentRole.SECURITY].category == FindingCategory.SECURITY
    assert raw_findings[AgentRole.SECURITY].severity == FindingSeverity.HIGH

    assert result.actual_findings[0].category == FindingCategory.SECURITY
    assert result.actual_findings[0].severity == FindingSeverity.HIGH
    assert result.aggregation_conflict_count == 2
    assert {
        conflict.field: (
            conflict.selected_value,
            conflict.observed_values,
        )
        for conflict in result.aggregation_conflicts
    } == {
        "category": ("security", ("correctness", "security")),
        "severity": ("high", ("high", "medium")),
    }
    assert all(conflict.reason for conflict in result.aggregation_conflicts)
    assert {
        conflict.canonical_fingerprint
        for conflict in result.aggregation_conflicts
    } == {
        decision.canonical_fingerprint
        for decision in result.aggregation_decisions
    }


def test_multi_agent_case_records_failed_role_and_completed_execution() -> None:
    case = _security_case()
    provider = _UsageProvider(_output(case), fail_on_call=2)

    result = evaluate_multi_agent_case(case, _orchestrator(provider))

    assert result.outcome == EvaluationOutcome.TRANSPORT_ERROR
    assert result.case_pass is False
    assert result.execution_mode == EvaluationExecutionMode.PLANNED_MULTI_AGENT
    assert result.agent_role is None
    assert [execution.role for execution in result.agent_executions] == [
        AgentRole.CORRECTNESS,
        AgentRole.SECURITY,
    ]
    assert result.agent_executions[0].outcome == EvaluationOutcome.SUCCESSFUL_CASE
    assert len(result.agent_executions[0].findings) == 1
    assert result.agent_executions[1].outcome == EvaluationOutcome.TRANSPORT_ERROR
    assert result.agent_executions[1].findings == ()
    assert result.agent_executions[1].error_type == "LLMTransientError"
    assert result.usage.total_tokens is None
