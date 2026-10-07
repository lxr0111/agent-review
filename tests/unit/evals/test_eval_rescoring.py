import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from evals.baseline import load_model_baseline
from evals.matcher import MatcherVersion
from evals.rescore import main
from evals.rescoring import (
    OfflineRescore,
    RescoreSafetyError,
    artifact_sha256,
    default_rescore_path,
    load_offline_rescore,
    rescore_model_baseline,
    save_offline_rescore,
    validate_dataset_identity,
)
from evals.schemas import load_dataset

HOLDOUT_DATASET_PATH = Path("evals/cases/review_holdout_v1.json")
HOLDOUT_BASELINE_PATH = Path("evals/baselines/deepseek-flash__review-v2__holdout-v1.json")
HOLDOUT_RESCORE_PATH = Path("evals/rescores/deepseek-flash__review-v2__holdout-v1__text-v2.json")
RESCORED_AT = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)


def _rescore() -> OfflineRescore:
    baseline = load_model_baseline(HOLDOUT_BASELINE_PATH)
    dataset = load_dataset(HOLDOUT_DATASET_PATH)
    return rescore_model_baseline(
        baseline,
        dataset,
        source_baseline_sha256=artifact_sha256(HOLDOUT_BASELINE_PATH),
        target_matcher_version=MatcherVersion.TEXT_V2,
        rescored_at=RESCORED_AT,
    )


def test_text_v2_rescores_saved_findings_without_changing_source_baseline() -> None:
    baseline = load_model_baseline(HOLDOUT_BASELINE_PATH)
    source_before = baseline.model_dump_json()

    rescore = _rescore()

    assert baseline.model_dump_json() == source_before
    assert rescore.source_matcher_version == MatcherVersion.TEXT_V1
    assert rescore.target_matcher_version == MatcherVersion.TEXT_V2
    assert rescore.evaluation.run_kind == "offline_rescore"
    assert rescore.evaluation.is_model_baseline is False
    assert rescore.evaluation.matcher_version == MatcherVersion.TEXT_V2
    assert rescore.evaluation.metrics.recall == 1.0
    assert rescore.evaluation.metrics.false_positives == 1
    assert rescore.evaluation.metrics.case_pass_rate == 0.5
    assert rescore.evaluation.metrics.category_match == 0.875
    assert rescore.evaluation.metrics.severity_match == 0.625
    assert rescore.evaluation.metrics.usage == baseline.evaluation.metrics.usage
    assert rescore.evaluation.metrics.latency_mean_ms == baseline.evaluation.metrics.latency_mean_ms
    assert rescore.source_metrics.precision == pytest.approx(2 / 3)
    assert rescore.source_metrics.f1 == pytest.approx(12 / 17)
    assert rescore.rescored_metrics.precision == pytest.approx(8 / 9)
    assert rescore.rescored_metrics.f1 == pytest.approx(16 / 17)


def test_rescore_rejects_dataset_with_different_identity(tmp_path: Path) -> None:
    changed_dataset = tmp_path / "changed.json"
    changed_dataset.write_text(
        HOLDOUT_DATASET_PATH.read_text(encoding="utf-8") + "\n",
        encoding="utf-8",
    )
    baseline = load_model_baseline(HOLDOUT_BASELINE_PATH)

    with pytest.raises(RescoreSafetyError, match="SHA-256"):
        validate_dataset_identity(baseline, changed_dataset)


def test_rescore_rejects_different_case_ids() -> None:
    baseline = load_model_baseline(HOLDOUT_BASELINE_PATH)
    dataset = load_dataset(HOLDOUT_DATASET_PATH)
    incomplete_dataset = dataset.model_copy(update={"cases": dataset.cases[:-1]})

    with pytest.raises(RescoreSafetyError, match="case IDs"):
        rescore_model_baseline(
            baseline,
            incomplete_dataset,
            source_baseline_sha256=artifact_sha256(HOLDOUT_BASELINE_PATH),
        )


def test_rescore_refuses_to_overwrite_an_artifact(tmp_path: Path) -> None:
    rescore = _rescore()
    output = tmp_path / "rescore.json"

    save_offline_rescore(rescore, output)

    assert load_offline_rescore(output) == rescore
    with pytest.raises(RescoreSafetyError, match="refusing to overwrite"):
        save_offline_rescore(rescore, output)


def test_rescore_default_path_contains_all_scoring_dimensions() -> None:
    path = default_rescore_path(_rescore())

    assert path.name == "deepseek-flash__review-v2__holdout-v1__text-v2.json"
    assert path.parent.name == "rescores"


def test_checked_in_holdout_rescore_is_bound_to_the_source_baseline() -> None:
    rescore = load_offline_rescore(HOLDOUT_RESCORE_PATH)

    assert rescore.source_baseline_sha256 == artifact_sha256(HOLDOUT_BASELINE_PATH)
    assert rescore.evaluation.dataset_sha256 == (
        load_model_baseline(HOLDOUT_BASELINE_PATH).evaluation.dataset_sha256
    )
    assert rescore.evaluation.metrics.recall == 1.0
    assert rescore.evaluation.metrics.false_positives == 1


def test_rescore_cli_is_offline_and_can_save_explicit_output(
    tmp_path: Path,
    capsys,
) -> None:
    output = tmp_path / "rescore.json"

    exit_code = main(
        [
            "--baseline",
            str(HOLDOUT_BASELINE_PATH),
            "--dataset",
            str(HOLDOUT_DATASET_PATH),
            "--matcher-version",
            "text-v2",
            "--save",
            "--output",
            str(output),
        ]
    )
    stdout = capsys.readouterr().out
    payload = json.loads(stdout)

    assert exit_code == 0
    assert output.exists()
    assert payload["rescore_schema_version"] == "agent-review-offline-rescore-v1"
    assert payload["evaluation"]["provider"] == "deepseek"
    assert payload["evaluation"]["run_kind"] == "offline_rescore"
    assert payload["evaluation"]["metrics"]["recall"] == 1.0
