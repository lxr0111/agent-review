from collections import Counter
from pathlib import Path

from app.domain.review.report import FindingCategory, FindingSeverity
from app.providers.llm.protocol import LLMReviewResult, ReviewPrompt
from app.providers.llm.schemas import LLMReviewFinding, LLMReviewOutput
from app.services.planned_review_orchestrator import PlannedReviewOrchestrator
from app.services.review_agent import AgentRole
from app.services.review_agent_registry import create_default_agent_registry
from app.services.review_aggregator import DeterministicReviewAggregator
from app.services.review_planner import DeterministicReviewPlanner
from evals.baseline import dataset_sha256
from evals.multi_agent import evaluate_multi_agent_case
from evals.runner import run_mock_evaluation, run_mock_multi_agent_evaluation
from evals.schemas import load_dataset

DATASET_PATH = Path("evals/cases/review_architecture_security_holdout_v1.json")
EXISTING_DATASET_PATHS = (
    Path("evals/cases/review_seed_v1.json"),
    Path("evals/cases/review_holdout_v1.json"),
    Path("evals/cases/review_holdout_v2.json"),
    Path("evals/cases/review_aggregation_v1.json"),
)
EXPECTED_DATASET_SHA256 = (
    "ef6c5dc7a804b4c89a4b051c664006d7145943f99b6ae25d526db7d95b41873e"
)


class _LiveOutputReplayProvider:
    model_name = "deepseek-live-output-replay"

    def __init__(self, outputs: dict[str, LLMReviewOutput]) -> None:
        self._outputs = outputs

    def generate_review(self, prompt: ReviewPrompt) -> LLMReviewResult:
        return LLMReviewResult(output=self._outputs[prompt.version], usage=None)


def _finding(
    *,
    title: str,
    explanation: str,
    suggestion: str,
    severity: FindingSeverity,
    category: FindingCategory,
    confidence: float,
) -> LLMReviewFinding:
    return LLMReviewFinding(
        title=title,
        explanation=explanation,
        suggestion=suggestion,
        file_path="src/api_keys.py",
        severity=severity,
        category=category,
        confidence=confidence,
    )


def test_architecture_security_holdout_is_frozen_and_has_expected_shape() -> None:
    dataset = load_dataset(DATASET_PATH)

    assert dataset.dataset_version == "architecture-security-holdout-v1"
    assert dataset_sha256(DATASET_PATH) == EXPECTED_DATASET_SHA256
    assert len(dataset.cases) == 4
    assert sum(case.is_negative for case in dataset.cases) == 1
    assert sum(len(case.expected_findings) for case in dataset.cases) == 4
    assert sum(len(case.expected_findings) > 1 for case in dataset.cases) == 1

    categories = Counter(
        finding.category
        for case in dataset.cases
        for finding in case.expected_findings
    )
    severities = Counter(
        finding.severity
        for case in dataset.cases
        for finding in case.expected_findings
    )
    assert categories == {
        FindingCategory.SECURITY: 3,
        FindingCategory.CORRECTNESS: 1,
    }
    assert severities == {
        FindingSeverity.HIGH: 2,
        FindingSeverity.MEDIUM: 2,
    }


def test_architecture_security_holdout_is_distinct_from_existing_datasets() -> None:
    dataset = load_dataset(DATASET_PATH)
    existing = tuple(load_dataset(path) for path in EXISTING_DATASET_PATHS)
    existing_case_ids = {case.id for item in existing for case in item.cases}
    existing_head_shas = {
        case.pull_request.head_sha for item in existing for case in item.cases
    }
    existing_patches = {
        changed_file.patch
        for item in existing
        for case in item.cases
        for changed_file in case.files
    }

    assert {case.id for case in dataset.cases}.isdisjoint(existing_case_ids)
    assert {
        case.pull_request.head_sha for case in dataset.cases
    }.isdisjoint(existing_head_shas)
    assert {
        changed_file.patch
        for case in dataset.cases
        for changed_file in case.files
    }.isdisjoint(existing_patches)


def test_architecture_security_holdout_ranges_reference_added_lines() -> None:
    dataset = load_dataset(DATASET_PATH)

    for case in dataset.cases:
        additions_by_path = {
            changed_file.path: changed_file.additions for changed_file in case.files
        }
        for finding in case.expected_findings:
            assert finding.range.end_line <= additions_by_path[finding.file_path]


def test_architecture_security_holdout_routes_only_correctness_and_security() -> None:
    dataset = load_dataset(DATASET_PATH)
    planner = DeterministicReviewPlanner()

    assert {
        case.id: planner.plan(case.to_review_context()).roles
        for case in dataset.cases
    } == {
        "architecture-security-api-key-prefix-and-lifetime": (
            AgentRole.CORRECTNESS,
            AgentRole.SECURITY,
        ),
        "architecture-security-admin-mass-assignment": (
            AgentRole.CORRECTNESS,
            AgentRole.SECURITY,
        ),
        "architecture-security-open-login-redirect": (
            AgentRole.CORRECTNESS,
            AgentRole.SECURITY,
        ),
        "architecture-security-negative-constant-time-secret": (
            AgentRole.CORRECTNESS,
            AgentRole.SECURITY,
        ),
    }


def test_architecture_security_holdout_mock_paths_are_self_tests_only() -> None:
    dataset = load_dataset(DATASET_PATH)

    general = run_mock_evaluation(dataset)
    specialist = run_mock_evaluation(dataset, agent_role=AgentRole.SECURITY)
    multi_agent = run_mock_multi_agent_evaluation(dataset)

    for result in (general, specialist, multi_agent):
        assert result.provider == "mock"
        assert result.is_model_baseline is False
        assert result.metrics.case_pass_rate == 1.0
        assert result.metrics.recall == 1.0
        assert result.metrics.false_positives == 0
        assert result.metrics.usage.total_tokens is None

    assert multi_agent.metrics.total_agent_calls == 8
    assert multi_agent.metrics.pre_aggregation_findings == 8
    assert multi_agent.metrics.post_aggregation_findings == 4
    assert multi_agent.metrics.merged_findings == 4


def test_live_api_key_outputs_replay_without_the_duplicate_security_impact() -> None:
    dataset = load_dataset(DATASET_PATH)
    case = next(
        case
        for case in dataset.cases
        if case.id == "architecture-security-api-key-prefix-and-lifetime"
    )
    correctness = LLMReviewOutput(
        summary="Correctness replay.",
        findings=(
            _finding(
                title="seconds_until_expiry returns a negated remaining lifetime",
                explanation=(
                    "`int((now - expires_at).total_seconds())` subtracts the expiry from "
                    "the current time, so a still-valid key gets a negative value."
                ),
                suggestion=(
                    "Use `return int((expires_at - now).total_seconds())` and test both "
                    "future and past expiry values."
                ),
                severity=FindingSeverity.MEDIUM,
                category=FindingCategory.CORRECTNESS,
                confidence=0.95,
            ),
            _finding(
                title="api_key_matches accepts any prefix of the expected key",
                explanation=(
                    "`expected_key.startswith(provided_key)` accepts a truncated prefix "
                    "or the empty string instead of requiring the full credential."
                ),
                suggestion="Use `hmac.compare_digest(provided_key, expected_key)`.",
                severity=FindingSeverity.HIGH,
                category=FindingCategory.CORRECTNESS,
                confidence=0.90,
            ),
        ),
    )
    security = LLMReviewOutput(
        summary="Security replay.",
        findings=(
            _finding(
                title="API key check accepts any prefix of the expected key",
                explanation=(
                    "`expected_key.startswith(provided_key)` accepts an attacker-supplied "
                    "prefix, including the empty string, as a complete credential."
                ),
                suggestion="Use `hmac.compare_digest(provided_key, expected_key)`.",
                severity=FindingSeverity.CRITICAL,
                category=FindingCategory.SECURITY,
                confidence=0.92,
            ),
            _finding(
                title="Expiry helper returns a positive value for already-expired keys",
                explanation=(
                    "`seconds_until_expiry` computes `now - expires_at`; a hypothetical "
                    "consumer could treat the positive result as an active credential."
                ),
                suggestion="Compute the remaining lifetime as `expires_at - now`.",
                severity=FindingSeverity.MEDIUM,
                category=FindingCategory.SECURITY,
                confidence=0.80,
            ),
        ),
    )
    provider = _LiveOutputReplayProvider(
        {
            "review-correctness-v1": correctness,
            "review-security-v2": security,
        }
    )
    orchestrator = PlannedReviewOrchestrator(
        planner=DeterministicReviewPlanner(),
        registry=create_default_agent_registry(provider),
        aggregator=DeterministicReviewAggregator(),
    )

    result = evaluate_multi_agent_case(case, orchestrator)

    assert result.pre_aggregation_finding_count == 4
    assert result.post_aggregation_finding_count == 2
    assert result.merged_finding_count == 2
    assert result.matched_finding_count == 2
    assert result.false_positive_count == 0
    assert result.false_negative_count == 0
    assert result.category_match_count == 2
    assert result.severity_match_count == 1
    assert result.case_pass is False
