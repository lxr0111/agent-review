from collections import Counter
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.domain.review.report import FindingCategory
from evals.schemas import EvalCase, EvalLineRange, load_dataset

DATASET_PATH = Path("evals/cases/review_seed_v1.json")


def test_seed_dataset_has_required_case_distribution() -> None:
    dataset = load_dataset(DATASET_PATH)

    assert dataset.schema_version == "review-eval-v1"
    assert dataset.dataset_version == "seed-v1"
    assert len(dataset.cases) == 10
    assert sum(case.is_negative for case in dataset.cases) == 2
    assert sum(len(case.expected_findings) for case in dataset.cases) == 8

    categories = Counter(
        finding.category for case in dataset.cases for finding in case.expected_findings
    )
    assert categories[FindingCategory.CORRECTNESS] == 2
    assert categories[FindingCategory.SECURITY] == 2
    assert categories[FindingCategory.PERFORMANCE] == 2
    assert categories[FindingCategory.MAINTAINABILITY] == 1
    assert categories[FindingCategory.RELIABILITY] == 1


def test_every_expected_finding_has_a_valid_file_and_range() -> None:
    dataset = load_dataset(DATASET_PATH)

    for case in dataset.cases:
        file_paths = {changed_file.path for changed_file in case.files}
        for finding in case.expected_findings:
            assert finding.file_path in file_paths
            assert finding.range.start_line <= finding.range.end_line


def test_case_converts_to_current_review_context_shape() -> None:
    case = load_dataset(DATASET_PATH).cases[0]

    context = case.to_review_context()

    assert context.pull_request.repository == case.pull_request.repository
    assert context.reviewable_files[0].path == case.files[0].path
    assert context.reviewable_files[0].patch == case.files[0].patch
    assert context.skipped_files == ()
    assert context.total_patch_characters == len(case.files[0].patch)


def test_line_range_rejects_reversed_bounds() -> None:
    with pytest.raises(ValidationError, match="end_line"):
        EvalLineRange(start_line=5, end_line=4)


def test_case_rejects_expected_finding_for_unknown_file() -> None:
    source = load_dataset(DATASET_PATH).cases[0].model_dump(mode="json")
    source["expected_findings"][0]["file_path"] = "src/unknown.py"

    with pytest.raises(ValidationError, match="unknown files"):
        EvalCase.model_validate(source)
