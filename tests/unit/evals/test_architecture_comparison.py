import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.services.review_agent import AgentRole
from evals.architecture_comparison import (
    ArchitectureCaseStatus,
    ArchitectureComparisonError,
    compare_review_architectures,
)
from evals.artifacts import build_evaluation_artifact, save_evaluation_artifact
from evals.compare import main
from evals.comparison import BaselineComparisonError
from evals.metrics import EvaluationRun, EvalUsage
from evals.runner import run_mock_evaluation, run_mock_multi_agent_evaluation
from evals.schemas import load_dataset

DATASET_PATH = Path("evals/cases/review_seed_v1.json")
CASE_ID = "security-sql-injection"


def _live_run(evaluation: EvaluationRun, *, tokens: int) -> EvaluationRun:
    return evaluation.model_copy(
        update={
            "run_kind": (
                "multi_agent_live_evaluation"
                if evaluation.agent_role is None
                else "live_evaluation"
            ),
            "provider": "deepseek",
            "model_name": "deepseek-flash",
            "dataset_sha256": "a" * 64,
            "git_commit": "abc123",
            "git_dirty": False,
            "executed_at": datetime(2026, 9, 30, 9, 0, tzinfo=UTC),
            "metrics": evaluation.metrics.model_copy(
                update={
                    "latency_mean_ms": float(tokens),
                    "usage": EvalUsage(
                        input_tokens=tokens - 20,
                        output_tokens=20,
                        total_tokens=tokens,
                    ),
                }
            ),
        }
    )


def _architecture_runs() -> tuple[EvaluationRun, EvaluationRun, EvaluationRun]:
    dataset = load_dataset(DATASET_PATH)
    general = _live_run(
        run_mock_evaluation(dataset, case_id=CASE_ID),
        tokens=100,
    )
    specialist = _live_run(
        run_mock_evaluation(
            dataset,
            case_id=CASE_ID,
            agent_role=AgentRole.SECURITY,
        ),
        tokens=110,
    )
    multi_agent = _live_run(
        run_mock_multi_agent_evaluation(dataset, case_id=CASE_ID),
        tokens=240,
    )
    return general, specialist, multi_agent


def test_architecture_comparison_reports_quality_cost_and_contribution() -> None:
    general, specialist, multi_agent = _architecture_runs()

    comparison = compare_review_architectures(general, specialist, multi_agent)

    assert comparison.comparison_schema_version == ("agent-review-architecture-comparison-v1")
    assert comparison.specialist_role == AgentRole.SECURITY
    assert comparison.multi_agent_vs_general.metrics["recall"].delta == 0
    assert comparison.multi_agent_vs_general.metrics["total_tokens"].delta == 140
    assert comparison.efficiency.additional_matched_findings_vs_general == 0
    assert comparison.efficiency.additional_false_positives_vs_general == 0
    assert comparison.efficiency.additional_total_tokens_vs_general == 140
    assert comparison.efficiency.additional_mean_latency_ms_vs_general == 140
    assert comparison.efficiency.matched_findings_per_extra_1k_tokens == 0
    assert comparison.efficiency.multi_agent_total_agent_calls == 2
    assert comparison.cases[0].multi_agent_vs_general_status == (ArchitectureCaseStatus.UNCHANGED)
    assert comparison.cases[0].multi_agent_vs_general_reasons == ("no scored quality change",)


def test_architecture_comparison_explains_per_case_quality_improvement() -> None:
    general, specialist, multi_agent = _architecture_runs()
    degraded_case = general.cases[0].model_copy(
        update={
            "case_pass": False,
            "matched_finding_count": 0,
            "false_negative_count": 1,
            "category_match_count": 0,
            "severity_match_count": 0,
        }
    )
    general = general.model_copy(update={"cases": (degraded_case,)})

    comparison = compare_review_architectures(general, specialist, multi_agent)
    case = comparison.cases[0]

    assert case.multi_agent_vs_general_status == ArchitectureCaseStatus.IMPROVED
    assert "case pass restored" in case.multi_agent_vs_general_reasons
    assert "matched findings increased by 1" in case.multi_agent_vs_general_reasons
    assert "false negatives decreased by 1" in case.multi_agent_vs_general_reasons


def test_architecture_comparison_rejects_mock_or_wrong_roles() -> None:
    dataset = load_dataset(DATASET_PATH)
    mock_general = run_mock_evaluation(dataset, case_id=CASE_ID)
    _, specialist, multi_agent = _architecture_runs()

    with pytest.raises(ArchitectureComparisonError, match="DeepSeek live"):
        compare_review_architectures(mock_general, specialist, multi_agent)

    with pytest.raises(ArchitectureComparisonError, match="explicit specialist"):
        compare_review_architectures(
            _live_run(mock_general, tokens=100),
            _live_run(mock_general, tokens=110),
            multi_agent,
        )


def test_architecture_comparison_requires_same_commit_and_cases() -> None:
    general, specialist, multi_agent = _architecture_runs()

    with pytest.raises(BaselineComparisonError, match="Git commits"):
        compare_review_architectures(
            general,
            specialist.model_copy(update={"git_commit": "different"}),
            multi_agent,
        )

    with pytest.raises(BaselineComparisonError, match="case IDs"):
        compare_review_architectures(
            general,
            specialist.model_copy(
                update={
                    "cases": (),
                    "executed_case_count": 0,
                }
            ),
            multi_agent,
        )


def test_compare_cli_reads_three_artifacts_without_external_services(
    tmp_path: Path,
    capsys,
) -> None:
    paths = [tmp_path / name for name in ("general.json", "specialist.json", "multi.json")]
    for evaluation, path in zip(_architecture_runs(), paths, strict=True):
        save_evaluation_artifact(build_evaluation_artifact(evaluation), path)

    exit_code = main(
        [
            "--general-artifact",
            str(paths[0]),
            "--specialist-artifact",
            str(paths[1]),
            "--multi-agent-artifact",
            str(paths[2]),
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["comparison_schema_version"] == ("agent-review-architecture-comparison-v1")
    assert payload["specialist_role"] == "security"
    assert payload["efficiency"]["additional_total_tokens_vs_general"] == 140
