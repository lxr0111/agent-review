import argparse
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import TYPE_CHECKING

from app.providers.llm.exceptions import LLMConfigurationError
from app.providers.llm.mock import MockLLMProvider
from app.providers.llm.protocol import LLMProvider
from app.providers.llm.schemas import LLMReviewFinding, LLMReviewOutput
from app.services.planned_review_orchestrator import PlannedReviewOrchestrator
from app.services.review_agent import AgentRole, ReviewAgent
from app.services.review_agent_registry import create_default_agent_registry
from app.services.review_aggregator import (
    AGGREGATOR_PROMPT_VERSION,
    DeterministicReviewAggregator,
)
from app.services.review_planner import DeterministicReviewPlanner
from app.services.specialist_prompt_builder import agent_prompt_version
from evals.baseline import (
    BaselineSafetyError,
    GitState,
    build_model_baseline,
    collect_git_state,
    dataset_sha256,
    default_baseline_path,
    save_model_baseline,
)
from evals.matcher import CURRENT_MATCHER_VERSION
from evals.metrics import (
    CaseEvaluation,
    EvaluationExecutionMode,
    EvaluationRun,
    aggregate_metrics,
    error_case_evaluation,
    successful_case_evaluation,
)
from evals.multi_agent import evaluate_multi_agent_case
from evals.planning import AgentPlanningRun, build_agent_planning_run
from evals.schemas import EvalCase, EvalDataset, load_dataset

if TYPE_CHECKING:
    from app.providers.llm.deepseek import DeepSeekResponsesProvider

DEFAULT_DATASET_PATH = Path(__file__).resolve().parent / "cases" / "review_seed_v1.json"


class UnsafeEvaluationModeError(ValueError):
    pass


class EvaluationSelectionError(ValueError):
    pass


def validate_run_mode(
    *,
    provider: str,
    live: bool,
    save_baseline: bool = False,
    max_cases: int | None = None,
    case_id: str | None = None,
    baseline_output: Path | None = None,
    allow_dirty: bool = False,
    agent_role: AgentRole = AgentRole.GENERAL,
    plan_only: bool = False,
    multi_agent: bool = False,
) -> None:
    if provider == "mock" and live:
        raise UnsafeEvaluationModeError("--live cannot be used with the mock provider")
    if provider != "mock" and not live:
        raise UnsafeEvaluationModeError(
            "a non-mock provider requires the explicit --live safety flag"
        )
    if provider not in {"mock", "deepseek"}:
        raise UnsafeEvaluationModeError(f"unsupported evaluation provider: {provider}")
    if save_baseline and (provider != "deepseek" or not live):
        raise UnsafeEvaluationModeError(
            "--save-baseline requires explicit --provider deepseek --live"
        )
    if max_cases is not None and case_id is not None:
        raise UnsafeEvaluationModeError("--max-cases and --case-id are mutually exclusive")
    if save_baseline and (max_cases is not None or case_id is not None):
        raise UnsafeEvaluationModeError(
            "a partial --max-cases or --case-id run cannot be saved as a baseline"
        )
    if baseline_output is not None and not save_baseline:
        raise UnsafeEvaluationModeError("--baseline-output requires --save-baseline")
    if allow_dirty and not save_baseline:
        raise UnsafeEvaluationModeError("--allow-dirty is only meaningful with --save-baseline")
    if plan_only and (provider != "mock" or live or save_baseline):
        raise UnsafeEvaluationModeError(
            "--plan-only cannot use a live provider or save a model baseline"
        )
    if plan_only and (baseline_output is not None or allow_dirty):
        raise UnsafeEvaluationModeError(
            "--plan-only cannot use baseline output or dirty-worktree options"
        )
    if plan_only and agent_role != AgentRole.GENERAL:
        raise UnsafeEvaluationModeError(
            "--plan-only selects roles itself and cannot be combined with --agent-role"
        )
    if multi_agent and plan_only:
        raise UnsafeEvaluationModeError("--multi-agent cannot be combined with --plan-only")
    if multi_agent and agent_role != AgentRole.GENERAL:
        raise UnsafeEvaluationModeError(
            "--multi-agent selects roles itself and cannot be combined with --agent-role"
        )
    if multi_agent and save_baseline:
        raise UnsafeEvaluationModeError(
            "--multi-agent baseline saving is not supported until its schema is stabilized"
        )


def run_mock_evaluation(
    dataset: EvalDataset,
    *,
    max_cases: int | None = None,
    case_id: str | None = None,
    agent_role: AgentRole = AgentRole.GENERAL,
) -> EvaluationRun:
    case_results: list[CaseEvaluation] = []
    selected_cases = _select_cases(dataset, max_cases=max_cases, case_id=case_id)
    for case in selected_cases:
        provider = MockLLMProvider(_mock_output(case), model_name="eval-self-test-mock")
        agent = create_default_agent_registry(provider).get(agent_role)
        case_results.append(evaluate_agent_case(case, agent))

    results = tuple(case_results)
    return EvaluationRun(
        run_kind="evaluator_self_test",
        provider="mock",
        model_name="eval-self-test-mock",
        prompt_version=agent_prompt_version(agent_role),
        agent_role=agent_role,
        matcher_version=CURRENT_MATCHER_VERSION,
        is_model_baseline=False,
        schema_version=dataset.schema_version,
        dataset_version=dataset.dataset_version,
        dataset_case_count=len(dataset.cases),
        executed_case_count=len(selected_cases),
        cases=results,
        metrics=aggregate_metrics(dataset, results),
    )


def run_live_evaluation(
    dataset: EvalDataset,
    provider: LLMProvider,
    *,
    dataset_digest: str,
    git_state: GitState,
    max_cases: int | None = None,
    case_id: str | None = None,
    executed_at: datetime | None = None,
    agent_role: AgentRole = AgentRole.GENERAL,
) -> EvaluationRun:
    selected_cases = _select_cases(dataset, max_cases=max_cases, case_id=case_id)
    agent = create_default_agent_registry(provider).get(agent_role)
    results = tuple(evaluate_agent_case(case, agent) for case in selected_cases)
    return EvaluationRun(
        run_kind="live_evaluation",
        provider="deepseek",
        model_name=provider.model_name,
        prompt_version=agent.prompt_version,
        agent_role=agent_role,
        matcher_version=CURRENT_MATCHER_VERSION,
        is_model_baseline=max_cases is None and case_id is None,
        schema_version=dataset.schema_version,
        dataset_version=dataset.dataset_version,
        dataset_sha256=dataset_digest,
        dataset_case_count=len(dataset.cases),
        executed_case_count=len(selected_cases),
        git_commit=git_state.commit,
        git_dirty=git_state.dirty,
        executed_at=executed_at or datetime.now(UTC),
        cases=results,
        metrics=aggregate_metrics(dataset, results),
    )


def run_mock_multi_agent_evaluation(
    dataset: EvalDataset,
    *,
    max_cases: int | None = None,
    case_id: str | None = None,
) -> EvaluationRun:
    selected_cases = _select_cases(dataset, max_cases=max_cases, case_id=case_id)
    results: list[CaseEvaluation] = []
    for case in selected_cases:
        provider = MockLLMProvider(_mock_output(case), model_name="eval-self-test-mock")
        orchestrator = _create_planned_orchestrator(provider)
        results.append(evaluate_multi_agent_case(case, orchestrator))
    cases = tuple(results)
    return EvaluationRun(
        run_kind="multi_agent_evaluator_self_test",
        provider="mock",
        model_name="eval-self-test-mock",
        prompt_version=AGGREGATOR_PROMPT_VERSION,
        execution_mode=EvaluationExecutionMode.PLANNED_MULTI_AGENT,
        agent_role=None,
        matcher_version=CURRENT_MATCHER_VERSION,
        is_model_baseline=False,
        schema_version=dataset.schema_version,
        dataset_version=dataset.dataset_version,
        dataset_case_count=len(dataset.cases),
        executed_case_count=len(selected_cases),
        cases=cases,
        metrics=aggregate_metrics(dataset, cases),
    )


def run_live_multi_agent_evaluation(
    dataset: EvalDataset,
    provider: LLMProvider,
    *,
    dataset_digest: str,
    git_state: GitState,
    max_cases: int | None = None,
    case_id: str | None = None,
    executed_at: datetime | None = None,
) -> EvaluationRun:
    selected_cases = _select_cases(dataset, max_cases=max_cases, case_id=case_id)
    orchestrator = _create_planned_orchestrator(provider)
    cases = tuple(
        evaluate_multi_agent_case(case, orchestrator) for case in selected_cases
    )
    return EvaluationRun(
        run_kind="multi_agent_live_evaluation",
        provider="deepseek",
        model_name=provider.model_name,
        prompt_version=AGGREGATOR_PROMPT_VERSION,
        execution_mode=EvaluationExecutionMode.PLANNED_MULTI_AGENT,
        agent_role=None,
        matcher_version=CURRENT_MATCHER_VERSION,
        is_model_baseline=False,
        schema_version=dataset.schema_version,
        dataset_version=dataset.dataset_version,
        dataset_sha256=dataset_digest,
        dataset_case_count=len(dataset.cases),
        executed_case_count=len(selected_cases),
        git_commit=git_state.commit,
        git_dirty=git_state.dirty,
        executed_at=executed_at or datetime.now(UTC),
        cases=cases,
        metrics=aggregate_metrics(dataset, cases),
    )


def evaluate_case(case: EvalCase, provider: LLMProvider) -> CaseEvaluation:
    agent = create_default_agent_registry(provider).get(AgentRole.GENERAL)
    return evaluate_agent_case(case, agent)


def evaluate_agent_case(case: EvalCase, agent: ReviewAgent) -> CaseEvaluation:
    started = perf_counter()
    try:
        review_result = agent.review(case.to_review_context())
    # The runner boundary must turn every per-case failure into an evaluation outcome
    # so one malformed response cannot abort the remaining seed cases.
    except Exception as error:  # noqa: BLE001
        return error_case_evaluation(
            case,
            error,
            latency_ms=(perf_counter() - started) * 1_000,
            agent_role=agent.role,
        )
    return successful_case_evaluation(
        case,
        review_result.report,
        latency_ms=(perf_counter() - started) * 1_000,
        usage=review_result.usage,
        agent_role=agent.role,
    )


def run_agent_planning(
    dataset: EvalDataset,
    *,
    max_cases: int | None = None,
    case_id: str | None = None,
) -> AgentPlanningRun:
    selected_cases = _select_cases(dataset, max_cases=max_cases, case_id=case_id)
    return build_agent_planning_run(dataset, selected_cases)


def _create_planned_orchestrator(provider: LLMProvider) -> PlannedReviewOrchestrator:
    return PlannedReviewOrchestrator(
        planner=DeterministicReviewPlanner(),
        registry=create_default_agent_registry(provider),
        aggregator=DeterministicReviewAggregator(),
    )


def _mock_output(case: EvalCase) -> LLMReviewOutput:
    findings = tuple(
        LLMReviewFinding(
            title=expected.description,
            explanation=(
                f"{expected.description}. Matching concepts: "
                + "; ".join(alternatives[0] for alternatives in expected.match_terms)
            ),
            suggestion="Apply the deterministic fix described by this evaluation fixture.",
            file_path=expected.file_path,
            severity=expected.severity,
            category=expected.category,
            confidence=1.0,
        )
        for expected in case.expected_findings
    )
    summary = (
        "Deterministic evaluator self-test findings were generated."
        if findings
        else "No expected findings are defined for this negative sample."
    )
    return LLMReviewOutput(summary=summary, findings=findings)


def _select_cases(
    dataset: EvalDataset,
    *,
    max_cases: int | None,
    case_id: str | None,
) -> tuple[EvalCase, ...]:
    if max_cases is not None and case_id is not None:
        raise EvaluationSelectionError("max_cases and case_id are mutually exclusive")
    if case_id is not None:
        selected = tuple(case for case in dataset.cases if case.id == case_id)
        if not selected:
            raise EvaluationSelectionError(f"unknown case ID: {case_id}")
        return selected
    if max_cases is None:
        return dataset.cases
    if max_cases < 1:
        raise ValueError("max_cases must be positive")
    return dataset.cases[:max_cases]


def _create_deepseek_provider() -> "DeepSeekResponsesProvider":
    # Settings are intentionally imported and loaded only after CLI live-mode validation.
    from app.core.config import Settings
    from app.providers.llm.deepseek import DeepSeekResponsesProvider

    return DeepSeekResponsesProvider.from_settings(Settings())


def _positive_integer(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("value must be a positive integer")
    return parsed


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the offline AgentReview evaluator self-test."
    )
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET_PATH)
    parser.add_argument("--provider", choices=("mock", "deepseek"), default="mock")
    parser.add_argument("--live", action="store_true")
    parser.add_argument(
        "--agent-role",
        choices=tuple(role.value for role in AgentRole),
        default=AgentRole.GENERAL.value,
    )
    parser.add_argument("--plan-only", action="store_true")
    parser.add_argument("--multi-agent", action="store_true")
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--max-cases", type=_positive_integer)
    selection.add_argument("--case-id")
    parser.add_argument("--save-baseline", action="store_true")
    parser.add_argument("--baseline-output", type=Path)
    parser.add_argument("--allow-dirty", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _argument_parser()
    args = parser.parse_args(argv)
    agent_role = AgentRole(args.agent_role)
    try:
        validate_run_mode(
            provider=args.provider,
            live=args.live,
            save_baseline=args.save_baseline,
            max_cases=args.max_cases,
            case_id=args.case_id,
            baseline_output=args.baseline_output,
            allow_dirty=args.allow_dirty,
            agent_role=agent_role,
            plan_only=args.plan_only,
            multi_agent=args.multi_agent,
        )
    except UnsafeEvaluationModeError as error:
        parser.error(str(error))

    dataset_path = args.dataset.resolve()
    dataset = load_dataset(dataset_path)
    try:
        _select_cases(
            dataset,
            max_cases=args.max_cases,
            case_id=args.case_id,
        )
    except EvaluationSelectionError as error:
        parser.error(str(error))

    if args.plan_only:
        plan = run_agent_planning(
            dataset,
            max_cases=args.max_cases,
            case_id=args.case_id,
        )
        print(plan.model_dump_json(indent=2))
        return 0

    if args.provider == "mock":
        if args.multi_agent:
            result = run_mock_multi_agent_evaluation(
                dataset,
                max_cases=args.max_cases,
                case_id=args.case_id,
            )
        else:
            result = run_mock_evaluation(
                dataset,
                max_cases=args.max_cases,
                case_id=args.case_id,
                agent_role=agent_role,
            )
        print(result.model_dump_json(indent=2))
        return 0

    try:
        git_state = collect_git_state()
        if args.save_baseline and git_state.dirty and not args.allow_dirty:
            raise BaselineSafetyError(
                "refusing to run a baseline from a dirty worktree; commit changes or pass "
                "--allow-dirty explicitly"
            )
        provider = _create_deepseek_provider()
        with provider:
            if args.multi_agent:
                result = run_live_multi_agent_evaluation(
                    dataset,
                    provider,
                    dataset_digest=dataset_sha256(dataset_path),
                    git_state=git_state,
                    max_cases=args.max_cases,
                    case_id=args.case_id,
                )
            else:
                result = run_live_evaluation(
                    dataset,
                    provider,
                    dataset_digest=dataset_sha256(dataset_path),
                    git_state=git_state,
                    max_cases=args.max_cases,
                    case_id=args.case_id,
                    agent_role=agent_role,
                )
        if args.save_baseline:
            baseline = build_model_baseline(result)
            baseline_path = args.baseline_output or default_baseline_path(result)
            save_model_baseline(baseline, baseline_path)
            print(f"baseline_saved={baseline_path}", file=sys.stderr)
    except (BaselineSafetyError, EvaluationSelectionError, LLMConfigurationError) as error:
        parser.error(str(error))

    print(result.model_dump_json(indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
