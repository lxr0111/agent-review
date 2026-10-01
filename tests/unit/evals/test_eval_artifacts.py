from pathlib import Path

import pytest

from evals.artifacts import (
    EvaluationArtifactError,
    build_evaluation_artifact,
    load_evaluation_artifact,
    save_evaluation_artifact,
)
from evals.runner import run_mock_evaluation
from evals.schemas import load_dataset

DATASET_PATH = Path("evals/cases/review_seed_v1.json")


def test_evaluation_artifact_round_trip_preserves_run_metadata(tmp_path: Path) -> None:
    evaluation = run_mock_evaluation(
        load_dataset(DATASET_PATH),
        case_id="security-sql-injection",
    )
    artifact = build_evaluation_artifact(evaluation)
    output = tmp_path / "experiment.json"

    save_evaluation_artifact(artifact, output)
    loaded = load_evaluation_artifact(output)

    assert loaded == artifact
    assert loaded.artifact_schema_version == "agent-review-evaluation-artifact-v1"
    assert loaded.evaluation.is_model_baseline is False
    assert output.read_text(encoding="utf-8").endswith("\n")


def test_evaluation_artifact_refuses_to_overwrite_existing_file(
    tmp_path: Path,
) -> None:
    artifact = build_evaluation_artifact(
        run_mock_evaluation(load_dataset(DATASET_PATH), max_cases=1)
    )
    output = tmp_path / "experiment.json"
    save_evaluation_artifact(artifact, output)

    with pytest.raises(EvaluationArtifactError, match="refusing to overwrite"):
        save_evaluation_artifact(artifact, output)


def test_evaluation_artifact_loader_rejects_invalid_json(tmp_path: Path) -> None:
    invalid = tmp_path / "invalid.json"
    invalid.write_text("not-json", encoding="utf-8")

    with pytest.raises(EvaluationArtifactError, match="invalid evaluation artifact"):
        load_evaluation_artifact(invalid)
