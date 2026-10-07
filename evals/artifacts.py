from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from evals.metrics import EvaluationRun


class EvaluationArtifactError(ValueError):
    pass


class EvaluationArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_schema_version: Literal["agent-review-evaluation-artifact-v1"]
    evaluation: EvaluationRun


def build_evaluation_artifact(evaluation: EvaluationRun) -> EvaluationArtifact:
    return EvaluationArtifact(
        artifact_schema_version="agent-review-evaluation-artifact-v1",
        evaluation=evaluation,
    )


def load_evaluation_artifact(path: Path) -> EvaluationArtifact:
    try:
        serialized = path.read_text(encoding="utf-8")
    except OSError:
        raise EvaluationArtifactError(f"unable to read evaluation artifact: {path}") from None
    try:
        return EvaluationArtifact.model_validate_json(serialized)
    except (ValueError, ValidationError):
        raise EvaluationArtifactError(f"invalid evaluation artifact: {path}") from None


def ensure_evaluation_artifact_path_available(path: Path) -> None:
    if path.exists():
        raise EvaluationArtifactError(f"refusing to overwrite existing evaluation artifact: {path}")


def save_evaluation_artifact(artifact: EvaluationArtifact, path: Path) -> None:
    serialized = artifact.model_dump_json(indent=2) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8", newline="\n") as output:
            output.write(serialized)
    except FileExistsError:
        raise EvaluationArtifactError(
            f"refusing to overwrite existing evaluation artifact: {path}"
        ) from None
