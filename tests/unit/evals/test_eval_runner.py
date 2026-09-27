import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock

import pytest

from app.providers.llm.protocol import LLMReviewResult, LLMTokenUsage, ReviewPrompt
from app.providers.llm.schemas import LLMReviewFinding, LLMReviewOutput
from app.services.review_agent import AgentRole
from evals.baseline import GitState
from evals.matcher import CURRENT_MATCHER_VERSION
from evals.runner import (
    EvaluationSelectionError,
    UnsafeEvaluationModeError,
    evaluate_case,
    main,
    run_agent_planning,
    run_live_evaluation,
    run_mock_evaluation,
    validate_run_mode,
)
from evals.schemas import load_dataset

DATASET_PATH = Path("evals/cases/review_seed_v1.json")
HOLDOUT_V2_PATH = Path("evals/cases/review_holdout_v2.json")
CORRECTED_NEGATIVE_CASE_ID = "holdout-negative-contained-upload-path"


class _UsageProvider:
    model_name = "usage-test-model"

    def __init__(self, output: LLMReviewOutput) -> None:
        self._output = output
        self.closed = False
        self.prompts: list[ReviewPrompt] = []

    def generate_review(self, prompt: ReviewPrompt) -> LLMReviewResult:
        self.prompts.append(prompt)
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
    assert result.agent_role == AgentRole.GENERAL
    assert result.is_model_baseline is False
    assert result.matcher_version == CURRENT_MATCHER_VERSION
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
    assert {case.agent_role for case in result.cases} == {AgentRole.GENERAL}


def test_mock_runner_can_self_test_one_explicit_specialist() -> None:
    result = run_mock_evaluation(
        load_dataset(DATASET_PATH),
        case_id="security-sql-injection",
        agent_role=AgentRole.SECURITY,
    )

    assert result.agent_role == AgentRole.SECURITY
    assert result.prompt_version == "review-security-v1"
    assert result.executed_case_count == 1
    assert result.cases[0].agent_role == AgentRole.SECURITY
    assert result.cases[0].prompt_version == "review-security-v1"
    assert result.metrics.case_pass_rate == 1.0


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


def test_mock_runner_can_select_one_exact_case_by_id() -> None:
    result = run_mock_evaluation(
        load_dataset(HOLDOUT_V2_PATH),
        case_id=CORRECTED_NEGATIVE_CASE_ID,
    )

    assert result.is_model_baseline is False
    assert result.dataset_case_count == 10
    assert result.executed_case_count == 1
    assert [case.case_id for case in result.cases] == [CORRECTED_NEGATIVE_CASE_ID]
    assert result.metrics.case_pass_rate == 1.0
    assert result.metrics.false_positives == 0


def test_mock_runner_rejects_an_unknown_case_id() -> None:
    with pytest.raises(EvaluationSelectionError, match="unknown case ID"):
        run_mock_evaluation(load_dataset(HOLDOUT_V2_PATH), case_id="missing-case")


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

    with pytest.raises(UnsafeEvaluationModeError, match="partial"):
        validate_run_mode(
            provider="deepseek",
            live=True,
            save_baseline=True,
            case_id=CORRECTED_NEGATIVE_CASE_ID,
        )

    with pytest.raises(UnsafeEvaluationModeError, match="requires explicit"):
        validate_run_mode(provider="mock", live=False, save_baseline=True)


def test_case_id_and_max_cases_are_mutually_exclusive() -> None:
    with pytest.raises(UnsafeEvaluationModeError, match="mutually exclusive"):
        validate_run_mode(
            provider="mock",
            live=False,
            max_cases=1,
            case_id=CORRECTED_NEGATIVE_CASE_ID,
        )


def test_plan_only_rejects_live_baseline_and_explicit_agent_selection() -> None:
    with pytest.raises(UnsafeEvaluationModeError, match="live provider"):
        validate_run_mode(provider="deepseek", live=True, plan_only=True)

    with pytest.raises(UnsafeEvaluationModeError, match="selects roles itself"):
        validate_run_mode(
            provider="mock",
            live=False,
            plan_only=True,
            agent_role=AgentRole.SECURITY,
        )


def test_multi_agent_mode_rejects_conflicting_modes_and_baseline_save() -> None:
    with pytest.raises(UnsafeEvaluationModeError, match="plan-only"):
        validate_run_mode(
            provider="mock",
            live=False,
            multi_agent=True,
            plan_only=True,
        )

    with pytest.raises(UnsafeEvaluationModeError, match="selects roles itself"):
        validate_run_mode(
            provider="mock",
            live=False,
            multi_agent=True,
            agent_role=AgentRole.SECURITY,
        )

    with pytest.raises(UnsafeEvaluationModeError, match="schema is stabilized"):
        validate_run_mode(
            provider="deepseek",
            live=True,
            multi_agent=True,
            save_baseline=True,
        )


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
        case_id=case.id,
        executed_at=executed_at,
    )

    assert result.run_kind == "live_evaluation"
    assert result.provider == "deepseek"
    assert result.agent_role == AgentRole.GENERAL
    assert result.model_name == "usage-test-model"
    assert result.prompt_version == "review-v2"
    assert result.is_model_baseline is False
    assert result.dataset_case_count == 10
    assert result.executed_case_count == 1
    assert result.dataset_sha256 == "a" * 64
    assert result.git_commit == "abc123"
    assert result.git_dirty is False
    assert result.executed_at == executed_at
    assert result.metrics.usage.total_tokens == 100
    assert result.cases[0].case_id == case.id
    assert result.cases[0].actual_findings[0].title == expected.description


def test_live_runner_records_specialist_prompt_role_and_usage() -> None:
    dataset = load_dataset(DATASET_PATH)
    case = next(case for case in dataset.cases if case.id == "security-sql-injection")
    expected = case.expected_findings[0]
    provider = _UsageProvider(
        LLMReviewOutput(
            summary="One security issue found.",
            findings=(
                LLMReviewFinding(
                    title=expected.description,
                    explanation=(
                        "Matching concepts: "
                        + "; ".join(group[0] for group in expected.match_terms)
                    ),
                    suggestion="Use a parameterized query.",
                    file_path=expected.file_path,
                    category=expected.category,
                    severity=expected.severity,
                    confidence=1.0,
                ),
            ),
        )
    )

    result = run_live_evaluation(
        dataset,
        provider,
        dataset_digest="a" * 64,
        git_state=GitState(commit="abc123", dirty=False),
        case_id=case.id,
        agent_role=AgentRole.SECURITY,
    )

    assert result.agent_role == AgentRole.SECURITY
    assert result.prompt_version == "review-security-v1"
    assert result.cases[0].agent_role == AgentRole.SECURITY
    assert result.cases[0].prompt_version == "review-security-v1"
    assert result.metrics.usage.total_tokens == 100
    assert provider.prompts[0].version == "review-security-v1"
    assert "Specialist assignment: security" in provider.prompts[0].system


def test_agent_planning_run_is_deterministic_and_contains_no_model_output() -> None:
    result = run_agent_planning(
        load_dataset(DATASET_PATH),
        case_id="security-sql-injection",
    )

    assert result.run_kind == "agent_planning"
    assert result.planner_version == "deterministic-v1"
    assert result.executed_case_count == 1
    assert [agent.role for agent in result.cases[0].agents] == [
        AgentRole.CORRECTNESS,
        AgentRole.SECURITY,
    ]
    assert all(agent.reasons for agent in result.cases[0].agents)


def test_default_cli_ignores_real_provider_environment(monkeypatch, capsys) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("LLM_API_KEY", "must-not-be-read")

    exit_code = main(["--dataset", str(DATASET_PATH)])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["provider"] == "mock"
    assert payload["is_model_baseline"] is False
    assert payload["metrics"]["total_cases"] == 10


def test_mock_multi_agent_cli_does_not_create_real_provider(monkeypatch, capsys) -> None:
    provider_factory = Mock()
    git_state_collector = Mock()
    monkeypatch.setattr("evals.runner._create_deepseek_provider", provider_factory)
    monkeypatch.setattr("evals.runner.collect_git_state", git_state_collector)

    exit_code = main(
        [
            "--dataset",
            str(DATASET_PATH),
            "--multi-agent",
            "--case-id",
            "security-sql-injection",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["run_kind"] == "multi_agent_evaluator_self_test"
    assert payload["agent_role"] is None
    assert payload["metrics"]["total_agent_calls"] == 2
    provider_factory.assert_not_called()
    git_state_collector.assert_not_called()


def test_mock_cli_selects_exact_case_and_reports_unknown_case_cleanly(capsys) -> None:
    exit_code = main(
        [
            "--dataset",
            str(HOLDOUT_V2_PATH),
            "--case-id",
            CORRECTED_NEGATIVE_CASE_ID,
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["executed_case_count"] == 1
    assert payload["cases"][0]["case_id"] == CORRECTED_NEGATIVE_CASE_ID

    with pytest.raises(SystemExit):
        main(
            [
                "--dataset",
                str(HOLDOUT_V2_PATH),
                "--case-id",
                "missing-case",
            ]
        )
    assert "unknown case ID" in capsys.readouterr().err


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


def test_unknown_live_case_is_rejected_before_provider_creation(monkeypatch) -> None:
    provider_factory = Mock()
    git_state_collector = Mock()
    monkeypatch.setattr("evals.runner.collect_git_state", git_state_collector)
    monkeypatch.setattr("evals.runner._create_deepseek_provider", provider_factory)

    with pytest.raises(SystemExit):
        main(
            [
                "--dataset",
                str(HOLDOUT_V2_PATH),
                "--provider",
                "deepseek",
                "--live",
                "--case-id",
                "missing-case",
            ]
        )

    git_state_collector.assert_not_called()
    provider_factory.assert_not_called()


def test_plan_only_cli_does_not_create_provider(monkeypatch, capsys) -> None:
    provider_factory = Mock()
    git_state_collector = Mock()
    monkeypatch.setattr("evals.runner._create_deepseek_provider", provider_factory)
    monkeypatch.setattr("evals.runner.collect_git_state", git_state_collector)

    exit_code = main(
        [
            "--dataset",
            str(DATASET_PATH),
            "--plan-only",
            "--case-id",
            "performance-n-plus-one",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["run_kind"] == "agent_planning"
    assert payload["cases"][0]["case_id"] == "performance-n-plus-one"
    assert [agent["role"] for agent in payload["cases"][0]["agents"]] == [
        "correctness",
        "performance",
    ]
    git_state_collector.assert_not_called()
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
