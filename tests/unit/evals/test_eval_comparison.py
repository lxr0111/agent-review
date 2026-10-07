import json
from pathlib import Path

import pytest

from app.domain.review.report import FindingSeverity
from app.services.review_agent import AgentRole
from evals.baseline import BaselineSafetyError, ModelBaseline, load_model_baseline
from evals.compare import main
from evals.comparison import (
    BaselineComparisonError,
    DeltaStatus,
    compare_model_baselines,
)

BASELINE_PATH = Path("evals/baselines/deepseek-flash__review-v1__seed-v1.json")
PROMPT_V2_BASELINE_PATH = Path("evals/baselines/deepseek-flash__review-v2__seed-v1.json")


def _candidate_with_less_noise(baseline: ModelBaseline) -> ModelBaseline:
    first_case = baseline.evaluation.cases[0]
    corrected_core_finding = first_case.actual_findings[0].model_copy(
        update={
            "title": "Empty values can divide by zero",
            "explanation": (
                "An empty list leaves len(values) at zero, so the division raises "
                "ZeroDivisionError."
            ),
            "severity": FindingSeverity.MEDIUM,
        }
    )
    candidate_first_case = first_case.model_copy(
        update={
            "actual_finding_count": 1,
            "false_positive_count": 0,
            "severity_match_count": 1,
            "case_pass": True,
            "actual_findings": (corrected_core_finding,),
        }
    )
    candidate_cases = (candidate_first_case, *baseline.evaluation.cases[1:])
    candidate_metrics = baseline.evaluation.metrics.model_copy(
        update={
            "passed_cases": baseline.evaluation.metrics.passed_cases + 1,
            "case_pass_rate": 0.2,
            "false_positives": baseline.evaluation.metrics.false_positives - 1,
            "false_positives_per_case": 0.9,
            "severity_match": 0.375,
        }
    )
    candidate_evaluation = baseline.evaluation.model_copy(
        update={
            "prompt_version": "review-v2",
            "cases": candidate_cases,
            "metrics": candidate_metrics,
        }
    )
    return baseline.model_copy(update={"evaluation": candidate_evaluation})


def test_checked_in_deepseek_baseline_loads_with_derived_quality_metrics() -> None:
    baseline = load_model_baseline(BASELINE_PATH)

    comparison = compare_model_baselines(baseline, baseline)

    assert comparison.baseline.prompt_version == "review-v1"
    assert comparison.baseline.agent_role == AgentRole.GENERAL
    assert comparison.metrics["precision"].baseline == pytest.approx(8 / 18)
    assert comparison.metrics["f1"].baseline == pytest.approx(8 / 13)
    assert comparison.metrics["negative_sample_case_pass_rate"].baseline == 0.0
    assert all(metric.status == DeltaStatus.UNCHANGED for metric in comparison.metrics.values())


def test_baseline_comparison_allows_prompt_versions_from_different_commits() -> None:
    baseline = load_model_baseline(BASELINE_PATH)
    candidate = load_model_baseline(PROMPT_V2_BASELINE_PATH)

    assert baseline.evaluation.git_commit != candidate.evaluation.git_commit

    comparison = compare_model_baselines(baseline, candidate)

    assert comparison.baseline.prompt_version == "review-v1"
    assert comparison.candidate.prompt_version == "review-v2"
    assert comparison.metrics["false_positives"].status == DeltaStatus.IMPROVED


def test_comparison_reports_metric_and_per_case_finding_changes() -> None:
    baseline = load_model_baseline(BASELINE_PATH)
    candidate = _candidate_with_less_noise(baseline)

    comparison = compare_model_baselines(baseline, candidate)

    assert comparison.candidate.prompt_version == "review-v2"
    assert comparison.metrics["recall"].status == DeltaStatus.UNCHANGED
    assert comparison.metrics["precision"].status == DeltaStatus.IMPROVED
    assert comparison.metrics["false_positives"].delta == -1
    assert comparison.metrics["false_positives"].status == DeltaStatus.IMPROVED
    assert comparison.metrics["severity_match"].status == DeltaStatus.IMPROVED
    first_case = next(
        case for case in comparison.cases if case.case_id == "correctness-empty-average"
    )
    assert first_case.candidate_case_pass is True
    assert [finding.title for finding in first_case.removed_findings] == ["New helper is untested"]
    assert first_case.added_findings == ()
    assert len(first_case.label_changes) == 1
    assert first_case.label_changes[0].baseline.severity == FindingSeverity.HIGH
    assert first_case.label_changes[0].candidate.severity == FindingSeverity.MEDIUM


def test_comparison_rejects_different_dataset_content() -> None:
    baseline = load_model_baseline(BASELINE_PATH)
    candidate = _candidate_with_less_noise(baseline)
    candidate = candidate.model_copy(
        update={"evaluation": candidate.evaluation.model_copy(update={"dataset_sha256": "b" * 64})}
    )

    with pytest.raises(BaselineComparisonError, match="SHA-256"):
        compare_model_baselines(baseline, candidate)


def test_baseline_loader_rejects_invalid_json(tmp_path: Path) -> None:
    invalid = tmp_path / "invalid.json"
    invalid.write_text("not-json", encoding="utf-8")

    with pytest.raises(BaselineSafetyError, match="invalid model baseline"):
        load_model_baseline(invalid)


def test_compare_cli_prints_offline_json_report(tmp_path: Path, capsys) -> None:
    baseline = load_model_baseline(BASELINE_PATH)
    candidate = _candidate_with_less_noise(baseline)
    candidate_path = tmp_path / "candidate.json"
    candidate_path.write_text(candidate.model_dump_json(indent=2), encoding="utf-8")

    exit_code = main(
        [
            "--baseline",
            str(BASELINE_PATH),
            "--candidate",
            str(candidate_path),
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["comparison_schema_version"] == ("agent-review-baseline-comparison-v1")
    assert payload["metrics"]["precision"]["status"] == "improved"
