import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock

import pytest

from app.providers.llm.protocol import LLMReviewResult, LLMTokenUsage, ReviewPrompt
from app.providers.llm.schemas import LLMReviewFinding, LLMReviewOutput
from evals.baseline import GitState
from evals.runner import (
    UnsafeEvaluationModeError,
    evaluate_case,
    main,
    run_live_evaluation,
    run_mock_evaluation,
    validate_run_mode,
)
from evals.schemas import load_dataset

DATASET_PATH = Path("evals/cases/review_seed_v1.json")


class _UsageProvider:
    model_name = "usage-test-model"

    def __init__(self, output: LLMReviewOutput) -> None:
        self._output = output
        self.closed = False

    def generate_review(self, prompt: ReviewPrompt) -> LLMReviewResult:
        del prompt
        return LLMReviewResult(
            output=self._output,
            usage=LLMTokenUsage(input_tokens=70, output_tokens=30, total_tokens=100),
        )

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        del exc_type, exc_value, traceback
        self.closed = True


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


def test_evaluate_case_exposes_provider_usage_without_computing_cost() -> None:
    case = load_dataset(DATASET_PATH).cases[0]
    expected = case.expected_findings[0]
    provider = _UsageProvider(
        LLMReviewOutput(
            summary="One issue found.",
            findings=(
                LLMReviewFinding(
                    title=expected.description,
                    explanation=(
                        "Matching concepts: "
                        + "; ".join(group[0] for group in expected.match_terms)
                    ),
                    suggestion="Guard the empty input.",
                    file_path=expected.file_path,
                    category=expected.category,
                    severity=expected.severity,
                    confidence=1.0,
                ),
            ),
        )
    )

    result = evaluate_case(case, provider)

    assert result.case_pass is True
    assert result.usage.input_tokens == 70
    assert result.usage.output_tokens == 30
    assert result.usage.total_tokens == 100
    assert result.usage.cost_usd is None


def test_non_mock_provider_requires_explicit_live_flag() -> None:
    with pytest.raises(UnsafeEvaluationModeError, match="explicit --live"):
        validate_run_mode(provider="deepseek", live=False)


def test_live_deepseek_requires_both_explicit_flags() -> None:
    validate_run_mode(provider="deepseek", live=True)


def test_only_full_live_deepseek_run_can_request_baseline_save() -> None:
    validate_run_mode(provider="deepseek", live=True, save_baseline=True)

    with pytest.raises(UnsafeEvaluationModeError, match="partial"):
        validate_run_mode(
            provider="deepseek",
            live=True,
            save_baseline=True,
            max_cases=1,
        )

    with pytest.raises(UnsafeEvaluationModeError, match="requires explicit"):
        validate_run_mode(provider="mock", live=False, save_baseline=True)


def test_baseline_output_and_allow_dirty_require_save_flag() -> None:
    with pytest.raises(UnsafeEvaluationModeError, match="baseline-output"):
        validate_run_mode(
            provider="deepseek",
            live=True,
            baseline_output=Path("result.json"),
        )

    with pytest.raises(UnsafeEvaluationModeError, match="allow-dirty"):
        validate_run_mode(provider="deepseek", live=True, allow_dirty=True)


def test_live_runner_captures_reproducibility_and_usage_metadata() -> None:
    dataset = load_dataset(DATASET_PATH)
    case = dataset.cases[0]
    expected = case.expected_findings[0]
    provider = _UsageProvider(
        LLMReviewOutput(
            summary="One issue found.",
            findings=(
                LLMReviewFinding(
                    title=expected.description,
                    explanation=(
                        "Matching concepts: "
                        + "; ".join(group[0] for group in expected.match_terms)
                    ),
                    suggestion="Guard empty input.",
                    file_path=expected.file_path,
                    category=expected.category,
                    severity=expected.severity,
                    confidence=1.0,
                ),
            ),
        )
    )
    executed_at = datetime(2026, 9, 25, 9, 0, tzinfo=UTC)

    result = run_live_evaluation(
        dataset,
        provider,
        dataset_digest="a" * 64,
        git_state=GitState(commit="abc123", dirty=False),
        max_cases=1,
        executed_at=executed_at,
    )

    assert result.run_kind == "live_evaluation"
    assert result.provider == "deepseek"
    assert result.model_name == "usage-test-model"
    assert result.prompt_version == "review-v1"
    assert result.is_model_baseline is False
    assert result.dataset_case_count == 10
    assert result.executed_case_count == 1
    assert result.dataset_sha256 == "a" * 64
    assert result.git_commit == "abc123"
    assert result.git_dirty is False
    assert result.executed_at == executed_at
    assert result.metrics.usage.total_tokens == 100
    assert result.cases[0].actual_findings[0].title == expected.description


def test_default_cli_ignores_real_provider_environment(monkeypatch, capsys) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("LLM_API_KEY", "must-not-be-read")

    exit_code = main(["--dataset", str(DATASET_PATH)])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["provider"] == "mock"
    assert payload["is_model_baseline"] is False
    assert payload["metrics"]["total_cases"] == 10


def test_dirty_baseline_is_rejected_before_provider_creation(monkeypatch) -> None:
    provider_factory = Mock()
    monkeypatch.setattr(
        "evals.runner.collect_git_state",
        lambda: GitState(commit="abc123", dirty=True),
    )
    monkeypatch.setattr("evals.runner._create_deepseek_provider", provider_factory)

    with pytest.raises(SystemExit):
        main(
            [
                "--dataset",
                str(DATASET_PATH),
                "--provider",
                "deepseek",
                "--live",
                "--save-baseline",
            ]
        )

    provider_factory.assert_not_called()


def test_live_cli_runs_only_after_explicit_flags_and_closes_provider(
    monkeypatch,
    capsys,
) -> None:
    provider = _UsageProvider(
        LLMReviewOutput(summary="No issue found.", findings=())
    )
    monkeypatch.setattr(
        "evals.runner.collect_git_state",
        lambda: GitState(commit="abc123", dirty=False),
    )
    monkeypatch.setattr("evals.runner._create_deepseek_provider", lambda: provider)

    exit_code = main(
        [
            "--dataset",
            str(DATASET_PATH),
            "--provider",
            "deepseek",
            "--live",
            "--max-cases",
            "1",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["run_kind"] == "live_evaluation"
    assert payload["is_model_baseline"] is False
    assert payload["executed_case_count"] == 1
    assert payload["git_commit"] == "abc123"
    assert provider.closed is True


def test_live_cli_saves_full_baseline_only_with_explicit_flag(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    provider = _UsageProvider(
        LLMReviewOutput(summary="No issue found.", findings=())
    )
    output = tmp_path / "deepseek-baseline.json"
    monkeypatch.setattr(
        "evals.runner.collect_git_state",
        lambda: GitState(commit="abc123", dirty=False),
    )
    monkeypatch.setattr("evals.runner._create_deepseek_provider", lambda: provider)

    exit_code = main(
        [
            "--dataset",
            str(DATASET_PATH),
            "--provider",
            "deepseek",
            "--live",
            "--save-baseline",
            "--baseline-output",
            str(output),
        ]
    )
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert exit_code == 0
    assert payload["is_model_baseline"] is True
    assert payload["executed_case_count"] == 10
    assert output.exists()
    assert "baseline_saved=" in captured.err
    assert provider.closed is True
