from pathlib import Path

import pytest

from app.services.review_agent import AgentRole
from app.services.review_planner import DeterministicReviewPlanner
from evals.schemas import load_dataset

SEED_PATH = Path("evals/cases/review_seed_v1.json")
HOLDOUT_PATH = Path("evals/cases/review_holdout_v2.json")


def _case_context(dataset_path: Path, case_id: str):
    dataset = load_dataset(dataset_path)
    case = next(case for case in dataset.cases if case.id == case_id)
    return case.to_review_context()


@pytest.mark.parametrize(
    ("dataset_path", "case_id", "expected_role"),
    [
        (SEED_PATH, "security-sql-injection", AgentRole.SECURITY),
        (SEED_PATH, "security-path-traversal", AgentRole.SECURITY),
        (SEED_PATH, "performance-n-plus-one", AgentRole.PERFORMANCE),
        (SEED_PATH, "performance-quadratic-membership", AgentRole.PERFORMANCE),
        (SEED_PATH, "maintainability-duplicated-rule", AgentRole.MAINTAINABILITY),
        (HOLDOUT_PATH, "holdout-security-command-injection", AgentRole.SECURITY),
        (HOLDOUT_PATH, "holdout-performance-blocking-async-sleep", AgentRole.PERFORMANCE),
        (
            HOLDOUT_PATH,
            "holdout-maintainability-duplicated-normalization",
            AgentRole.MAINTAINABILITY,
        ),
    ],
)
def test_planner_selects_expected_specialist_from_repository_signals(
    dataset_path: Path,
    case_id: str,
    expected_role: AgentRole,
) -> None:
    plan = DeterministicReviewPlanner().plan(_case_context(dataset_path, case_id))

    assert plan.roles[0] == AgentRole.CORRECTNESS
    assert expected_role in plan.roles
    assert len(plan.roles) == len(set(plan.roles))
    assert all(planned.reasons for planned in plan.agents)


@pytest.mark.parametrize(
    "case_id",
    [
        "holdout-negative-safe-subprocess",
        "holdout-negative-contained-upload-path",
    ],
)
def test_planner_routes_security_sensitive_negative_samples_without_calling_them_defects(
    case_id: str,
) -> None:
    plan = DeterministicReviewPlanner().plan(_case_context(HOLDOUT_PATH, case_id))

    assert AgentRole.SECURITY in plan.roles


def test_planner_always_includes_correctness_and_reliability_baseline() -> None:
    context = _case_context(SEED_PATH, "reliability-unclosed-file")

    plan = DeterministicReviewPlanner().plan(context)

    assert plan.roles == (AgentRole.CORRECTNESS,)
    assert "reliability" in plan.agents[0].reasons[0]
