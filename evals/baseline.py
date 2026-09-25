import hashlib
import re
import subprocess
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from evals.metrics import EvaluationRun

BASELINES_DIR = Path(__file__).resolve().parent / "baselines"
REPOSITORY_ROOT = Path(__file__).resolve().parent.parent


class BaselineSafetyError(ValueError):
    pass


class GitState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    commit: str
    dirty: bool


class ModelBaseline(BaseModel):
    model_config = ConfigDict(extra="forbid")

    baseline_schema_version: Literal["agent-review-model-baseline-v1"]
    evaluation: EvaluationRun


def dataset_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def collect_git_state(repository_root: Path = REPOSITORY_ROOT) -> GitState:
    commit = _run_git(repository_root, "rev-parse", "HEAD")
    status = _run_git(repository_root, "status", "--porcelain", "--untracked-files=normal")
    return GitState(commit=commit, dirty=bool(status))


def build_model_baseline(evaluation: EvaluationRun) -> ModelBaseline:
    if evaluation.run_kind != "live_evaluation" or evaluation.provider != "deepseek":
        raise BaselineSafetyError("only a DeepSeek live evaluation can become a model baseline")
    if not evaluation.is_model_baseline:
        raise BaselineSafetyError("partial live evaluations cannot become model baselines")
    if evaluation.executed_case_count != evaluation.dataset_case_count:
        raise BaselineSafetyError("a model baseline must execute the complete dataset")
    if (
        evaluation.dataset_sha256 is None
        or evaluation.git_commit is None
        or evaluation.git_dirty is None
        or evaluation.executed_at is None
    ):
        raise BaselineSafetyError("model baseline reproducibility metadata is incomplete")
    return ModelBaseline(
        baseline_schema_version="agent-review-model-baseline-v1",
        evaluation=evaluation,
    )


def default_baseline_path(evaluation: EvaluationRun) -> Path:
    if evaluation.model_name is None:
        raise BaselineSafetyError("model name is required for a baseline filename")
    parts = (
        evaluation.model_name,
        evaluation.prompt_version,
        evaluation.dataset_version,
    )
    filename = "__".join(_filename_component(part) for part in parts) + ".json"
    return BASELINES_DIR / filename


def save_model_baseline(baseline: ModelBaseline, path: Path) -> None:
    serialized = baseline.model_dump_json(indent=2) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8", newline="\n") as output:
            output.write(serialized)
    except FileExistsError:
        raise BaselineSafetyError(f"refusing to overwrite existing baseline: {path}") from None


def _run_git(repository_root: Path, *arguments: str) -> str:
    try:
        completed = subprocess.run(
            ["git", *arguments],
            cwd=repository_root,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
    except (OSError, subprocess.CalledProcessError):
        raise BaselineSafetyError("unable to collect Git reproducibility metadata") from None
    return completed.stdout.strip()


def _filename_component(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9._-]+", "-", value.strip()).strip("-._")
    if not normalized:
        raise BaselineSafetyError("baseline filename metadata must not be empty")
    return normalized.lower()
