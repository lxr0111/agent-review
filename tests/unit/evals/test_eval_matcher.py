from pathlib import Path

from app.domain.review.report import (
    FindingCategory,
    FindingSeverity,
    ReviewFinding,
)
from evals.baseline import load_model_baseline
from evals.matcher import MatcherVersion, match_findings
from evals.schemas import EvalExpectedFinding, EvalLineRange, load_dataset

HOLDOUT_DATASET_PATH = Path("evals/cases/review_holdout_v1.json")
HOLDOUT_BASELINE_PATH = Path("evals/baselines/deepseek-flash__review-v2__holdout-v1.json")


def _expected(
    *, finding_id: str = "division", file_path: str = "src/stats.py"
) -> EvalExpectedFinding:
    return EvalExpectedFinding(
        id=finding_id,
        description="Empty input can cause division by zero.",
        category=FindingCategory.CORRECTNESS,
        severity=FindingSeverity.MEDIUM,
        file_path=file_path,
        range=EvalLineRange(start_line=4, end_line=4),
        match_terms=(("empty input", "empty list"), ("division by zero", "zero division")),
    )


def _actual(
    *,
    file_path: str = "src/stats.py",
    category: FindingCategory = FindingCategory.PERFORMANCE,
    severity: FindingSeverity = FindingSeverity.CRITICAL,
) -> ReviewFinding:
    return ReviewFinding(
        title="Empty list triggers zero division",
        explanation="The denominator is zero for empty input.",
        suggestion="Guard the empty list before division by zero.",
        file_path=file_path,
        category=category,
        severity=severity,
        confidence=0.9,
    )


def test_matcher_uses_file_and_text_but_not_category_or_severity() -> None:
    result = match_findings((_expected(),), (_actual(),))

    assert len(result.matches) == 1
    assert result.unmatched_expected_indices == ()
    assert result.unmatched_actual_indices == ()


def test_matcher_requires_the_same_file() -> None:
    result = match_findings((_expected(),), (_actual(file_path="src/other.py"),))

    assert result.matches == ()
    assert result.unmatched_expected_indices == (0,)
    assert result.unmatched_actual_indices == (0,)


def test_matcher_requires_sufficient_textual_evidence() -> None:
    unrelated = ReviewFinding(
        title="Rename this variable",
        explanation="A longer name may be clearer.",
        suggestion="Choose another identifier.",
        file_path="src/stats.py",
        category=FindingCategory.MAINTAINABILITY,
        severity=FindingSeverity.LOW,
        confidence=0.5,
    )

    result = match_findings((_expected(),), (unrelated,))

    assert result.matches == ()


def test_matcher_assigns_each_actual_finding_at_most_once() -> None:
    expected = (
        _expected(finding_id="division-a"),
        _expected(finding_id="division-b"),
    )

    result = match_findings(expected, (_actual(),))

    assert len(result.matches) == 1
    assert len(result.unmatched_expected_indices) == 1
    assert result.unmatched_actual_indices == ()


def test_text_v2_matches_reordered_terms_and_normalized_operators() -> None:
    dataset = load_dataset(HOLDOUT_DATASET_PATH)
    baseline = load_model_baseline(HOLDOUT_BASELINE_PATH)
    expected_by_id = {case.id: case for case in dataset.cases}
    actual_by_id = {case.case_id: case for case in baseline.evaluation.cases}
    affected_case_ids = (
        "holdout-correctness-exact-balance",
        "holdout-maintainability-duplicated-normalization",
    )

    for case_id in affected_case_ids:
        case = expected_by_id[case_id]
        actual = actual_by_id[case_id]
        legacy = match_findings(
            case.expected_findings,
            actual.actual_findings,
            matcher_version=MatcherVersion.TEXT_V1,
        )
        current = match_findings(
            case.expected_findings,
            actual.actual_findings,
            matcher_version=MatcherVersion.TEXT_V2,
        )

        assert legacy.matches == ()
        assert len(current.matches) == 1
        assert current.unmatched_expected_indices == ()
        assert current.unmatched_actual_indices == ()
