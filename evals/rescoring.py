import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from evals.baseline import ModelBaseline, dataset_sha256
from evals.matcher import CURRENT_MATCHER_VERSION, MatcherVersion
from evals.metrics import EvaluationRun, aggregate_metrics, rescore_successful_case
from evals.schemas import EvalDataset

RESCORES_DIR = Path(__file__).resolve().parent / "rescores"


class RescoreSafetyError(ValueError):
    pass


class RescoreQualityMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recall: float | None = Field(default=None, ge=0, le=1)
    precision: float | None = Field(default=None, ge=0, le=1)
    f1: float | None = Field(default=None, ge=0, le=1)
    false_positives: int = Field(ge=0)
    false_positives_per_case: float = Field(ge=0)
    negative_sample_case_pass_rate: float | None = Field(default=None, ge=0, le=1)
    category_match: float | None = Field(default=None, ge=0, le=1)
    severity_match: float | None = Field(default=None, ge=0, le=1)
    case_pass_rate: float = Field(ge=0, le=1)


class OfflineRescore(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rescore_schema_version: Literal["agent-review-offline-rescore-v1"]
    source_baseline_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_matcher_version: MatcherVersion
    target_matcher_version: MatcherVersion
    rescored_at: datetime
    source_metrics: RescoreQualityMetrics
    rescored_metrics: RescoreQualityMetrics
    evaluation: EvaluationRun


def artifact_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rescore_model_baseline(
    baseline: ModelBaseline,
    dataset: EvalDataset,
    *,
    source_baseline_sha256: str,
    target_matcher_version: MatcherVersion = CURRENT_MATCHER_VERSION,
    rescored_at: datetime | None = None,
) -> OfflineRescore:
    source = baseline.evaluation
    if source.dataset_sha256 is None:
        raise RescoreSafetyError("source baseline has no dataset SHA-256")
    if source.dataset_version != dataset.dataset_version:
        raise RescoreSafetyError("dataset versions do not match")

    dataset_case_ids = [case.id for case in dataset.cases]
    source_case_ids = [case.case_id for case in source.cases]
    if len(source_case_ids) != len(set(source_case_ids)):
        raise RescoreSafetyError("source baseline contains duplicate case IDs")
    if set(source_case_ids) != set(dataset_case_ids):
        raise RescoreSafetyError("dataset and source baseline case IDs do not match")

    cases_by_id = {case.id: case for case in dataset.cases}
    rescored_cases = tuple(
        rescore_successful_case(
            cases_by_id[source_case.case_id],
            source_case,
            matcher_version=target_matcher_version,
        )
        for source_case in source.cases
    )
    rescored_evaluation = source.model_copy(
        update={
            "run_kind": "offline_rescore",
            "matcher_version": target_matcher_version,
            "is_model_baseline": False,
            "cases": rescored_cases,
            "metrics": aggregate_metrics(dataset, rescored_cases),
        }
    )
    return OfflineRescore(
        rescore_schema_version="agent-review-offline-rescore-v1",
        source_baseline_sha256=source_baseline_sha256,
        source_matcher_version=source.matcher_version,
        target_matcher_version=target_matcher_version,
        rescored_at=rescored_at or datetime.now(UTC),
        source_metrics=_quality_metrics(source),
        rescored_metrics=_quality_metrics(rescored_evaluation),
        evaluation=rescored_evaluation,
    )


def validate_dataset_identity(
    baseline: ModelBaseline,
    dataset_path: Path,
) -> None:
    expected_digest = baseline.evaluation.dataset_sha256
    if expected_digest is None or dataset_sha256(dataset_path) != expected_digest:
        raise RescoreSafetyError("dataset SHA-256 does not match the source baseline")


def default_rescore_path(rescore: OfflineRescore) -> Path:
    evaluation = rescore.evaluation
    if evaluation.model_name is None:
        raise RescoreSafetyError("model name is required for a rescore filename")
    components = (
        evaluation.model_name,
        evaluation.prompt_version,
        evaluation.dataset_version,
        rescore.target_matcher_version.value,
    )
    filename = "__".join(_filename_component(value) for value in components) + ".json"
    return RESCORES_DIR / filename


def save_offline_rescore(rescore: OfflineRescore, path: Path) -> None:
    serialized = rescore.model_dump_json(indent=2) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8", newline="\n") as output:
            output.write(serialized)
    except FileExistsError:
        raise RescoreSafetyError(f"refusing to overwrite existing rescore: {path}") from None


def load_offline_rescore(path: Path) -> OfflineRescore:
    try:
        serialized = path.read_text(encoding="utf-8")
    except OSError:
        raise RescoreSafetyError(f"unable to read offline rescore: {path}") from None
    try:
        return OfflineRescore.model_validate_json(serialized)
    except (ValueError, ValidationError):
        raise RescoreSafetyError(f"invalid offline rescore: {path}") from None


def _filename_component(value: str) -> str:
    normalized = "".join(
        character if character.isalnum() or character in "._-" else "-"
        for character in value.strip()
    ).strip("-._")
    if not normalized:
        raise RescoreSafetyError("rescore filename metadata must not be empty")
    return normalized.lower()


def _quality_metrics(evaluation: EvaluationRun) -> RescoreQualityMetrics:
    metrics = evaluation.metrics
    finding_denominator = metrics.matched_findings + metrics.false_positives
    precision = (
        metrics.matched_findings / finding_denominator if finding_denominator else None
    )
    recall = metrics.recall
    if precision is None or recall is None:
        f1 = None
    elif precision + recall == 0:
        f1 = 0.0
    else:
        f1 = 2 * precision * recall / (precision + recall)
    negative_cases = [case for case in evaluation.cases if case.expected_finding_count == 0]
    negative_sample_case_pass_rate = (
        sum(case.case_pass for case in negative_cases) / len(negative_cases)
        if negative_cases
        else None
    )
    return RescoreQualityMetrics(
        recall=recall,
        precision=precision,
        f1=f1,
        false_positives=metrics.false_positives,
        false_positives_per_case=metrics.false_positives_per_case,
        negative_sample_case_pass_rate=negative_sample_case_pass_rate,
        category_match=metrics.category_match,
        severity_match=metrics.severity_match,
        case_pass_rate=metrics.case_pass_rate,
    )
