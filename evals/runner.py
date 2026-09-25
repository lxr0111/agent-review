import argparse
from collections.abc import Sequence
from pathlib import Path
from time import perf_counter

from app.providers.llm.mock import MockLLMProvider
from app.providers.llm.protocol import LLMProvider
from app.providers.llm.schemas import LLMReviewFinding, LLMReviewOutput
from app.services.ai_review_service import AIReviewService
from evals.metrics import (
    CaseEvaluation,
    EvaluationRun,
    aggregate_metrics,
    error_case_evaluation,
    successful_case_evaluation,
)
from evals.schemas import EvalCase, EvalDataset, load_dataset

DEFAULT_DATASET_PATH = Path(__file__).resolve().parent / "cases" / "review_seed_v1.json"


class UnsafeEvaluationModeError(ValueError):
    pass


def validate_run_mode(*, provider: str, live: bool) -> None:
    if provider == "mock" and live:
        raise UnsafeEvaluationModeError("--live cannot be used with the mock provider")
    if provider != "mock" and not live:
        raise UnsafeEvaluationModeError(
            "a non-mock provider requires the explicit --live safety flag"
        )
    if provider == "deepseek" and live:
        raise NotImplementedError("DeepSeek live evaluation is not implemented in batch one")
    if provider != "mock":
        raise UnsafeEvaluationModeError(f"unsupported evaluation provider: {provider}")


def run_mock_evaluation(dataset: EvalDataset) -> EvaluationRun:
    case_results: list[CaseEvaluation] = []
    for case in dataset.cases:
        provider = MockLLMProvider(_mock_output(case), model_name="eval-self-test-mock")
        case_results.append(evaluate_case(case, provider))

    results = tuple(case_results)
    return EvaluationRun(
        run_kind="evaluator_self_test",
        provider="mock",
        is_model_baseline=False,
        schema_version=dataset.schema_version,
        dataset_version=dataset.dataset_version,
        cases=results,
        metrics=aggregate_metrics(dataset, results),
    )


def evaluate_case(case: EvalCase, provider: LLMProvider) -> CaseEvaluation:
    service = AIReviewService(provider)
    started = perf_counter()
    try:
        review_result = service.review_with_metadata(case.to_review_context())
    # The runner boundary must turn every per-case failure into an evaluation outcome
    # so one malformed response cannot abort the remaining seed cases.
    except Exception as error:  # noqa: BLE001
        return error_case_evaluation(
            case,
            error,
            latency_ms=(perf_counter() - started) * 1_000,
        )
    return successful_case_evaluation(
        case,
        review_result.report,
        latency_ms=(perf_counter() - started) * 1_000,
        usage=review_result.usage,
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


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the offline AgentReview evaluator self-test."
    )
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET_PATH)
    parser.add_argument("--provider", choices=("mock", "deepseek"), default="mock")
    parser.add_argument("--live", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _argument_parser()
    args = parser.parse_args(argv)
    try:
        validate_run_mode(provider=args.provider, live=args.live)
    except (UnsafeEvaluationModeError, NotImplementedError) as error:
        parser.error(str(error))

    dataset = load_dataset(args.dataset)
    result = run_mock_evaluation(dataset)
    print(result.model_dump_json(indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
