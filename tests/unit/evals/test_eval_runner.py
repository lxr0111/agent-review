import json
from pathlib import Path

import pytest

from evals.runner import (
    UnsafeEvaluationModeError,
    main,
    run_mock_evaluation,
    validate_run_mode,
)
from evals.schemas import load_dataset

DATASET_PATH = Path("evals/cases/review_seed_v1.json")


def test_mock_runner_is_a_perfect_evaluator_self_test_not_a_baseline() -> None:
    result = run_mock_evaluation(load_dataset(DATASET_PATH))

    assert result.run_kind == "evaluator_self_test"
    assert result.provider == "mock"
    assert result.is_model_baseline is False
    assert result.metrics.total_cases == 10
    assert result.metrics.successful_cases == 10
    assert result.metrics.passed_cases == 10
    assert result.metrics.case_pass_rate == 1.0
    assert result.metrics.recall == 1.0
    assert result.metrics.false_positives == 0
    assert result.metrics.negative_sample_false_positives == 0
    assert result.metrics.category_match == 1.0
    assert result.metrics.severity_match == 1.0
    assert result.metrics.schema_validity == 1.0
    assert result.metrics.usage.input_tokens is None
    assert result.metrics.usage.output_tokens is None
    assert result.metrics.usage.total_tokens is None
    assert result.metrics.usage.cost_usd is None
    assert {case.outcome.value for case in result.cases} == {"successful_case"}


def test_non_mock_provider_requires_explicit_live_flag() -> None:
    with pytest.raises(UnsafeEvaluationModeError, match="explicit --live"):
        validate_run_mode(provider="deepseek", live=False)


def test_live_deepseek_is_deliberately_not_implemented_in_batch_one() -> None:
    with pytest.raises(NotImplementedError, match="not implemented"):
        validate_run_mode(provider="deepseek", live=True)


def test_default_cli_ignores_real_provider_environment(monkeypatch, capsys) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("LLM_API_KEY", "must-not-be-read")

    exit_code = main(["--dataset", str(DATASET_PATH)])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["provider"] == "mock"
    assert payload["is_model_baseline"] is False
    assert payload["metrics"]["total_cases"] == 10
