from collections import Counter
from pathlib import Path

from app.domain.review.report import FindingCategory, FindingSeverity
from app.providers.llm.protocol import LLMProvider, LLMReviewResult, ReviewPrompt
from app.providers.llm.schemas import LLMReviewFinding, LLMReviewOutput
from app.services.planned_review_orchestrator import PlannedReviewOrchestrator
from app.services.review_agent import AgentRole
from app.services.review_agent_registry import create_default_agent_registry
from app.services.review_aggregator import AggregationAction, DeterministicReviewAggregator
from app.services.review_planner import DeterministicReviewPlanner
from evals.baseline import dataset_sha256
from evals.multi_agent import evaluate_multi_agent_case
from evals.runner import run_mock_multi_agent_evaluation
from evals.schemas import EvalCase, load_dataset

DATASET_PATH = Path("evals/cases/review_aggregation_v1.json")
EXPECTED_DATASET_SHA256 = (
    "f85b494489d1d5c52cf370f3d5e57a80143090056205f237659252524caf19b5"
)


class _ScriptedRoleProvider:
    model_name = "aggregation-scripted-mock"

    def __init__(self, outputs: dict[str, LLMReviewOutput]) -> None:
        self._outputs = outputs
        self.prompts: list[ReviewPrompt] = []

    def generate_review(self, prompt: ReviewPrompt) -> LLMReviewResult:
        self.prompts.append(prompt)
        return LLMReviewResult(
            output=self._outputs.get(prompt.version, _output()),
            usage=None,
        )


def _output(*findings: LLMReviewFinding) -> LLMReviewOutput:
    return LLMReviewOutput(
        summary=("Scripted findings returned." if findings else "No findings."),
        findings=findings,
    )


def _finding(
    *,
    title: str,
    explanation: str,
    suggestion: str,
    file_path: str,
    category: FindingCategory,
    severity: FindingSeverity,
    confidence: float,
) -> LLMReviewFinding:
    return LLMReviewFinding(
        title=title,
        explanation=explanation,
        suggestion=suggestion,
        file_path=file_path,
        category=category,
        severity=severity,
        confidence=confidence,
    )


def _case(case_id: str) -> EvalCase:
    return next(case for case in load_dataset(DATASET_PATH).cases if case.id == case_id)


def _orchestrator(provider: LLMProvider) -> PlannedReviewOrchestrator:
    return PlannedReviewOrchestrator(
        planner=DeterministicReviewPlanner(),
        registry=create_default_agent_registry(provider),
        aggregator=DeterministicReviewAggregator(),
    )


def test_aggregation_dataset_is_frozen_and_covers_required_shapes() -> None:
    dataset = load_dataset(DATASET_PATH)

    assert dataset.dataset_version == "aggregation-v1"
    assert dataset_sha256(DATASET_PATH) == EXPECTED_DATASET_SHA256
    assert len(dataset.cases) == 4
    assert sum(case.is_negative for case in dataset.cases) == 1
    assert sum(len(case.expected_findings) for case in dataset.cases) == 6
    assert sum(len(case.expected_findings) > 1 for case in dataset.cases) == 3
    assert sum(len(case.files) > 1 for case in dataset.cases) == 1

    categories = Counter(
        finding.category
        for case in dataset.cases
        for finding in case.expected_findings
    )
    assert categories == {
        FindingCategory.SECURITY: 2,
        FindingCategory.PERFORMANCE: 2,
        FindingCategory.RELIABILITY: 1,
        FindingCategory.MAINTAINABILITY: 1,
    }


def test_aggregation_dataset_plans_expected_specialists() -> None:
    planner = DeterministicReviewPlanner()
    dataset = load_dataset(DATASET_PATH)

    assert {
        case.id: planner.plan(case.to_review_context()).roles
        for case in dataset.cases
    } == {
        "aggregation-overlap-sql-injection-n-plus-one": (
            AgentRole.CORRECTNESS,
            AgentRole.SECURITY,
            AgentRole.PERFORMANCE,
        ),
        "aggregation-adjacent-path-and-resource": (
            AgentRole.CORRECTNESS,
            AgentRole.SECURITY,
        ),
        "aggregation-cross-file-performance-maintainability": (
            AgentRole.CORRECTNESS,
            AgentRole.PERFORMANCE,
            AgentRole.MAINTAINABILITY,
        ),
        "aggregation-negative-parameterized-lookup": (
            AgentRole.CORRECTNESS,
            AgentRole.SECURITY,
        ),
    }


def test_default_mock_exercises_complete_aggregation_dataset_offline() -> None:
    result = run_mock_multi_agent_evaluation(load_dataset(DATASET_PATH))

    assert result.is_model_baseline is False
    assert result.metrics.total_cases == 4
    assert result.metrics.passed_cases == 4
    assert result.metrics.total_agent_calls == 10
    assert result.metrics.pre_aggregation_findings == 16
    assert result.metrics.post_aggregation_findings == 6
    assert result.metrics.merged_findings == 10
    assert result.metrics.duplicate_rate == 0.625
    assert result.metrics.false_positives == 0
    assert result.metrics.usage.total_tokens is None


def test_overlap_case_merges_duplicate_and_preserves_distinct_root_cause() -> None:
    shared_code = "`query = f\"SELECT * FROM users WHERE email = '{email}'\"`"
    provider = _ScriptedRoleProvider(
        {
            "review-correctness-v1": _output(
                _finding(
                    title="SQL injection from interpolated email query",
                    explanation=(
                        f"{shared_code} lets an email alter SQL and also breaks valid "
                        "addresses containing apostrophes."
                    ),
                    suggestion="Bind the email with a parameterized query.",
                    file_path="src/users.py",
                    category=FindingCategory.CORRECTNESS,
                    severity=FindingSeverity.MEDIUM,
                    confidence=0.90,
                )
            ),
            "review-security-v1": _output(
                _finding(
                    title="SQL injection through interpolated email query",
                    explanation=(
                        f"{shared_code} treats attacker-controlled email input as SQL "
                        "instead of data."
                    ),
                    suggestion="Use a parameterized query for the email value.",
                    file_path="src/users.py",
                    category=FindingCategory.SECURITY,
                    severity=FindingSeverity.HIGH,
                    confidence=0.97,
                )
            ),
            "review-performance-v1": _output(
                _finding(
                    title="N+1 user query executed once per email",
                    explanation=(
                        "The loop executes one query per email, so database round trips "
                        "grow linearly instead of using one batch query."
                    ),
                    suggestion="Fetch all requested users with one batched IN query.",
                    file_path="src/users.py",
                    category=FindingCategory.PERFORMANCE,
                    severity=FindingSeverity.MEDIUM,
                    confidence=0.94,
                )
            ),
        }
    )

    result = evaluate_multi_agent_case(
        _case("aggregation-overlap-sql-injection-n-plus-one"),
        _orchestrator(provider),
    )

    assert result.case_pass is True
    assert result.pre_aggregation_finding_count == 3
    assert result.post_aggregation_finding_count == 2
    assert result.merged_finding_count == 1
    assert result.aggregation_conflict_count == 2
    assert {finding.category for finding in result.actual_findings} == {
        FindingCategory.SECURITY,
        FindingCategory.PERFORMANCE,
    }
    assert {
        conflict.field: (conflict.selected_value, conflict.observed_values)
        for conflict in result.aggregation_conflicts
    } == {
        "category": ("security", ("correctness", "security")),
        "severity": ("high", ("high", "medium")),
    }

    decisions_by_role = {
        decision.source_role: decision for decision in result.aggregation_decisions
    }
    assert decisions_by_role[AgentRole.SECURITY].action == AggregationAction.KEPT
    assert decisions_by_role[AgentRole.CORRECTNESS].action == AggregationAction.MERGED
    assert decisions_by_role[AgentRole.PERFORMANCE].action == AggregationAction.KEPT
    assert (
        decisions_by_role[AgentRole.SECURITY].canonical_fingerprint
        == decisions_by_role[AgentRole.CORRECTNESS].canonical_fingerprint
    )
    assert (
        decisions_by_role[AgentRole.PERFORMANCE].canonical_fingerprint
        != decisions_by_role[AgentRole.SECURITY].canonical_fingerprint
    )
    assert {
        contribution.role: contribution.unique_finding_count
        for contribution in result.agent_contributions
    } == {
        AgentRole.CORRECTNESS: 0,
        AgentRole.SECURITY: 0,
        AgentRole.PERFORMANCE: 1,
    }


def test_adjacent_issues_with_shared_code_evidence_remain_distinct() -> None:
    shared_code = "`open(UPLOAD_ROOT / filename, 'rb')`"
    provider = _ScriptedRoleProvider(
        {
            "review-correctness-v1": _output(
                _finding(
                    title="Upload file handle is never closed",
                    explanation=(
                        f"{shared_code} returns after reading without closing the file "
                        "handle, causing a resource leak."
                    ),
                    suggestion="Use a with open context manager.",
                    file_path="src/uploads.py",
                    category=FindingCategory.RELIABILITY,
                    severity=FindingSeverity.MEDIUM,
                    confidence=0.93,
                )
            ),
            "review-security-v1": _output(
                _finding(
                    title="Unvalidated upload filename permits path traversal",
                    explanation=(
                        f"{shared_code} accepts traversal segments without resolving and "
                        "checking containment under the upload root."
                    ),
                    suggestion="Resolve the path and enforce upload-root containment.",
                    file_path="src/uploads.py",
                    category=FindingCategory.SECURITY,
                    severity=FindingSeverity.HIGH,
                    confidence=0.96,
                )
            ),
        }
    )

    result = evaluate_multi_agent_case(
        _case("aggregation-adjacent-path-and-resource"),
        _orchestrator(provider),
    )

    assert result.case_pass is True
    assert result.pre_aggregation_finding_count == 2
    assert result.post_aggregation_finding_count == 2
    assert result.merged_finding_count == 0
    assert result.aggregation_conflicts == ()
    assert len(
        {
            decision.canonical_fingerprint
            for decision in result.aggregation_decisions
        }
    ) == 2
    assert all(
        decision.action == AggregationAction.KEPT
        for decision in result.aggregation_decisions
    )


def test_cross_file_case_records_only_specialist_unique_contributions() -> None:
    provider = _ScriptedRoleProvider(
        {
            "review-performance-v1": _output(
                _finding(
                    title="N+1 customer lookup inside the order loop",
                    explanation=(
                        "Calling db.get inside the loop issues one customer query per "
                        "order instead of eager loading or batching."
                    ),
                    suggestion="Eager load customers or fetch all customer IDs in one batch.",
                    file_path="src/orders.py",
                    category=FindingCategory.PERFORMANCE,
                    severity=FindingSeverity.MEDIUM,
                    confidence=0.95,
                )
            ),
            "review-maintainability-v1": _output(
                _finding(
                    title="Free-shipping threshold duplicated across three channels",
                    explanation=(
                        "The same free shipping business rule is copied into three helpers, "
                        "so future threshold changes can diverge."
                    ),
                    suggestion="Centralize the threshold and eligibility predicate.",
                    file_path="src/shipping.py",
                    category=FindingCategory.MAINTAINABILITY,
                    severity=FindingSeverity.LOW,
                    confidence=0.91,
                )
            ),
        }
    )

    result = evaluate_multi_agent_case(
        _case("aggregation-cross-file-performance-maintainability"),
        _orchestrator(provider),
    )

    assert result.case_pass is True
    assert [execution.finding_count for execution in result.agent_executions] == [0, 1, 1]
    assert result.pre_aggregation_finding_count == 2
    assert result.post_aggregation_finding_count == 2
    assert result.merged_finding_count == 0
    assert {
        contribution.role: contribution.unique_finding_count
        for contribution in result.agent_contributions
    } == {
        AgentRole.CORRECTNESS: 0,
        AgentRole.PERFORMANCE: 1,
        AgentRole.MAINTAINABILITY: 1,
    }


def test_negative_case_runs_multiple_agents_without_findings() -> None:
    provider = _ScriptedRoleProvider({})

    result = evaluate_multi_agent_case(
        _case("aggregation-negative-parameterized-lookup"),
        _orchestrator(provider),
    )

    assert result.case_pass is True
    assert [execution.role for execution in result.agent_executions] == [
        AgentRole.CORRECTNESS,
        AgentRole.SECURITY,
    ]
    assert [execution.finding_count for execution in result.agent_executions] == [0, 0]
    assert result.pre_aggregation_finding_count == 0
    assert result.post_aggregation_finding_count == 0
    assert result.aggregation_decisions == ()
    assert result.aggregation_conflicts == ()
    assert result.false_positive_count == 0
