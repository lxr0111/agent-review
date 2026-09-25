from pathlib import Path

from app.domain.review.report import FindingCategory
from evals.baseline import dataset_sha256
from evals.runner import run_mock_evaluation
from evals.schemas import load_dataset

SEED_PATH = Path("evals/cases/review_seed_v1.json")
HOLDOUT_PATH = Path("evals/cases/review_holdout_v1.json")
EXPECTED_HOLDOUT_SHA256 = "9902805446cea36d711bdb8450f64609633ac6599099dc5810726c78f302567c"


def test_holdout_dataset_is_frozen_and_structurally_distinct_from_seed() -> None:
    seed = load_dataset(SEED_PATH)
    holdout = load_dataset(HOLDOUT_PATH)

    assert holdout.dataset_version == "holdout-v1"
    assert len(holdout.cases) == 10
    assert sum(case.is_negative for case in holdout.cases) == 2
    assert dataset_sha256(HOLDOUT_PATH) == EXPECTED_HOLDOUT_SHA256
    assert {case.id for case in seed.cases}.isdisjoint(case.id for case in holdout.cases)
    assert {
        changed_file.patch for case in seed.cases for changed_file in case.files
    }.isdisjoint(
        changed_file.patch for case in holdout.cases for changed_file in case.files
    )


def test_holdout_covers_required_categories_and_multi_file_review() -> None:
    holdout = load_dataset(HOLDOUT_PATH)
    categories = {
        finding.category
        for case in holdout.cases
        for finding in case.expected_findings
    }

    assert categories == {
        FindingCategory.CORRECTNESS,
        FindingCategory.SECURITY,
        FindingCategory.PERFORMANCE,
        FindingCategory.RELIABILITY,
        FindingCategory.MAINTAINABILITY,
    }
    assert sum(len(case.expected_findings) for case in holdout.cases) == 8
    assert sum(len(case.files) > 1 for case in holdout.cases) >= 1


def test_holdout_mock_self_test_is_perfect_but_not_a_model_baseline() -> None:
    holdout = load_dataset(HOLDOUT_PATH)

    result = run_mock_evaluation(holdout)

    assert result.is_model_baseline is False
    assert result.dataset_version == "holdout-v1"
    assert result.dataset_case_count == 10
    assert result.executed_case_count == 10
    assert result.metrics.total_cases == 10
    assert result.metrics.case_pass_rate == 1.0
    assert result.metrics.recall == 1.0
    assert result.metrics.false_positives == 0
