from collections import Counter
from pathlib import Path

from app.domain.review.report import FindingCategory, FindingSeverity
from app.services.review_agent import AgentRole
from app.services.review_planner import DeterministicReviewPlanner
from evals.baseline import dataset_sha256
from evals.runner import run_mock_evaluation, run_mock_multi_agent_evaluation
from evals.schemas import load_dataset

DATASET_PATH = Path("evals/cases/review_architecture_security_holdout_v2.json")
EXISTING_DATASET_PATHS = (
    Path("evals/cases/review_seed_v1.json"),
    Path("evals/cases/review_holdout_v1.json"),
    Path("evals/cases/review_holdout_v2.json"),
    Path("evals/cases/review_aggregation_v1.json"),
    Path("evals/cases/review_architecture_security_holdout_v1.json"),
)
EXPECTED_DATASET_SHA256 = (
    "d786fddace3bd5a58f75700fe1182edca97768f281bb6ab16014bc45c7ff531b"
)
EXPECTED_ROLES = (AgentRole.CORRECTNESS, AgentRole.SECURITY)


def test_architecture_security_holdout_v2_is_frozen_and_has_expected_shape() -> None:
    dataset = load_dataset(DATASET_PATH)

    assert dataset.dataset_version == "architecture-security-holdout-v2"
    assert dataset_sha256(DATASET_PATH) == EXPECTED_DATASET_SHA256
    assert len(dataset.cases) == 4
    assert sum(case.is_negative for case in dataset.cases) == 1
    assert sum(len(case.expected_findings) for case in dataset.cases) == 4
    assert sum(len(case.expected_findings) > 1 for case in dataset.cases) == 1
    assert sum(len(case.files) > 1 for case in dataset.cases) == 1

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
        FindingSeverity.HIGH: 3,
        FindingSeverity.MEDIUM: 1,
    }


def test_architecture_security_holdout_v2_is_distinct_from_all_existing_data() -> None:
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


def test_architecture_security_holdout_v2_ranges_reference_added_lines() -> None:
    dataset = load_dataset(DATASET_PATH)

    for case in dataset.cases:
        additions_by_path = {
            changed_file.path: changed_file.additions for changed_file in case.files
        }
        for finding in case.expected_findings:
            assert finding.range.end_line <= additions_by_path[finding.file_path]


def test_architecture_security_holdout_v2_routes_only_expected_roles() -> None:
    dataset = load_dataset(DATASET_PATH)
    planner = DeterministicReviewPlanner()

    assert {
        case.id: planner.plan(case.to_review_context()).roles
        for case in dataset.cases
    } == {case.id: EXPECTED_ROLES for case in dataset.cases}


def test_architecture_security_holdout_v2_mock_paths_are_self_tests_only() -> None:
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

    assert specialist.prompt_version == "review-security-v2"
    assert multi_agent.prompt_version == "multi-agent-v2"
    assert multi_agent.finding_identity_version == "finding-text-v4"
    assert multi_agent.metrics.total_agent_calls == 8
    assert multi_agent.metrics.pre_aggregation_findings == 8
    assert multi_agent.metrics.post_aggregation_findings == 4
    assert multi_agent.metrics.merged_findings == 4
