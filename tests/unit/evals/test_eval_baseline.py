from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.services.review_agent import AgentRole
from evals.baseline import (
    BaselineSafetyError,
    build_model_baseline,
    dataset_sha256,
    default_baseline_path,
    load_model_baseline,
    save_model_baseline,
)
from evals.matcher import MatcherVersion
from evals.metrics import EvaluationRun
from evals.runner import run_mock_evaluation
from evals.schemas import load_dataset

DATASET_PATH = Path("evals/cases/review_seed_v1.json")


def _live_evaluation() -> EvaluationRun:
    evaluation = run_mock_evaluation(load_dataset(DATASET_PATH))
    return evaluation.model_copy(
        update={
            "run_kind": "live_evaluation",
            "provider": "deepseek",
            "model_name": "deepseek-flash",
            "is_model_baseline": True,
            "dataset_sha256": dataset_sha256(DATASET_PATH),
            "git_commit": "abc123",
            "git_dirty": False,
            "executed_at": datetime(2026, 9, 25, 9, 0, tzinfo=UTC),
        }
    )


def test_model_baseline_requires_full_deepseek_live_run() -> None:
    mock_evaluation = run_mock_evaluation(load_dataset(DATASET_PATH))

    with pytest.raises(BaselineSafetyError, match="DeepSeek live"):
        build_model_baseline(mock_evaluation)

    partial = _live_evaluation().model_copy(
        update={"is_model_baseline": False, "executed_case_count": 1}
    )
    with pytest.raises(BaselineSafetyError, match="partial"):
        build_model_baseline(partial)


def test_baseline_contains_reproducibility_metadata_without_source_patches() -> None:
    baseline = build_model_baseline(_live_evaluation())
    serialized = baseline.model_dump_json()

    assert baseline.baseline_schema_version == "agent-review-model-baseline-v1"
    assert baseline.evaluation.dataset_sha256 == dataset_sha256(DATASET_PATH)
    assert baseline.evaluation.git_commit == "abc123"
    assert '"git_dirty":false' in serialized
    assert "return sum(values) / len(values)" not in serialized


def test_historical_baseline_without_matcher_metadata_defaults_to_text_v1() -> None:
    baseline = load_model_baseline(
        Path("evals/baselines/deepseek-flash__review-v2__holdout-v1.json")
    )

    assert baseline.evaluation.matcher_version == MatcherVersion.TEXT_V1
    assert baseline.evaluation.agent_role == AgentRole.GENERAL
    assert {case.agent_role for case in baseline.evaluation.cases} == {AgentRole.GENERAL}


def test_default_baseline_path_is_stable_and_safe() -> None:
    path = default_baseline_path(_live_evaluation())

    assert path.name == "deepseek-flash__review-v2__seed-v1.json"
    assert path.parent.name == "baselines"


def test_save_baseline_refuses_to_overwrite_existing_file(tmp_path: Path) -> None:
    baseline = build_model_baseline(_live_evaluation())
    output = tmp_path / "baseline.json"

    save_model_baseline(baseline, output)

    assert output.read_text(encoding="utf-8").endswith("\n")
    with pytest.raises(BaselineSafetyError, match="refusing to overwrite"):
        save_model_baseline(baseline, output)
