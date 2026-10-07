from pathlib import Path

from app.domain.review.report import FindingCategory
from evals.baseline import dataset_sha256
from evals.runner import run_mock_evaluation
from evals.schemas import load_dataset

SEED_PATH = Path("evals/cases/review_seed_v1.json")
HOLDOUT_PATH = Path("evals/cases/review_holdout_v1.json")
HOLDOUT_V2_PATH = Path("evals/cases/review_holdout_v2.json")
EXPECTED_HOLDOUT_SHA256 = "9902805446cea36d711bdb8450f64609633ac6599099dc5810726c78f302567c"
EXPECTED_HOLDOUT_V2_SHA256 = "ece7d8102bb1973afa1c290928e4051e9f497f5e99045974ab7e04b30e2b3303"


def test_holdout_dataset_is_frozen_and_structurally_distinct_from_seed() -> None:
    seed = load_dataset(SEED_PATH)
    holdout = load_dataset(HOLDOUT_PATH)

    assert holdout.dataset_version == "holdout-v1"
    assert len(holdout.cases) == 10
    assert sum(case.is_negative for case in holdout.cases) == 2
    assert dataset_sha256(HOLDOUT_PATH) == EXPECTED_HOLDOUT_SHA256
    assert {case.id for case in seed.cases}.isdisjoint(case.id for case in holdout.cases)
    assert {changed_file.patch for case in seed.cases for changed_file in case.files}.isdisjoint(
        changed_file.patch for case in holdout.cases for changed_file in case.files
    )


def test_holdout_covers_required_categories_and_multi_file_review() -> None:
    holdout = load_dataset(HOLDOUT_PATH)
    categories = {finding.category for case in holdout.cases for finding in case.expected_findings}

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


def test_holdout_v2_is_frozen_and_only_repairs_the_known_fixture() -> None:
    holdout_v1 = load_dataset(HOLDOUT_PATH)
    holdout_v2 = load_dataset(HOLDOUT_V2_PATH)

    assert dataset_sha256(HOLDOUT_V2_PATH) == EXPECTED_HOLDOUT_V2_SHA256
    assert holdout_v2.dataset_version == "holdout-v2"
    assert len(holdout_v2.cases) == 10
    assert sum(case.is_negative for case in holdout_v2.cases) == 2
    assert [case.id for case in holdout_v2.cases] == [case.id for case in holdout_v1.cases]

    v1_by_id = {case.id: case for case in holdout_v1.cases}
    v2_by_id = {case.id: case for case in holdout_v2.cases}
    repaired_case_id = "holdout-negative-contained-upload-path"
    assert {case_id for case_id in v1_by_id if v1_by_id[case_id] != v2_by_id[case_id]} == {
        repaired_case_id
    }

    v1_case = v1_by_id[repaired_case_id]
    v2_case = v2_by_id[repaired_case_id]
    expected_pull_request = v1_case.pull_request.model_copy(
        update={"head_sha": "head-holdout-contained-upload-path-v2"}
    )
    expected_files = tuple(
        changed_file.model_copy(
            update={
                "additions": 4,
                "changes": 4,
                "patch": (
                    "@@ -0,0 +1,4 @@\n"
                    "+from .upload_paths import resolve_upload\n"
                    "+\n"
                    "+def read_upload(filename):\n"
                    "+    return resolve_upload(filename).read_bytes()"
                ),
            }
        )
        if changed_file.path == "src/upload_api.py"
        else changed_file
        for changed_file in v1_case.files
    )
    expected_case = v1_case.model_copy(
        update={"pull_request": expected_pull_request, "files": expected_files}
    )

    assert v2_case == expected_case
    assert v2_case.expected_findings == ()


def test_holdout_v2_mock_self_test_is_perfect_but_not_a_model_baseline() -> None:
    holdout = load_dataset(HOLDOUT_V2_PATH)

    result = run_mock_evaluation(holdout)

    assert result.is_model_baseline is False
    assert result.dataset_version == "holdout-v2"
    assert result.dataset_case_count == 10
    assert result.executed_case_count == 10
    assert result.metrics.total_cases == 10
    assert result.metrics.case_pass_rate == 1.0
    assert result.metrics.recall == 1.0
    assert result.metrics.false_positives == 0
