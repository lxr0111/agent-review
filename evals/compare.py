import argparse
from collections.abc import Sequence
from pathlib import Path

from evals.architecture_comparison import (
    ArchitectureComparison,
    ArchitectureComparisonError,
    compare_review_architectures,
)
from evals.artifacts import EvaluationArtifactError, load_evaluation_artifact
from evals.baseline import BaselineSafetyError, load_model_baseline
from evals.comparison import (
    BaselineComparison,
    BaselineComparisonError,
    compare_model_baselines,
)


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Compare saved AgentReview baselines or General/Specialist/Multi-Agent "
            "evaluation artifacts offline."
        )
    )
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--candidate", type=Path)
    parser.add_argument("--general-artifact", type=Path)
    parser.add_argument("--specialist-artifact", type=Path)
    parser.add_argument("--multi-agent-artifact", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _argument_parser()
    args = parser.parse_args(argv)
    baseline_mode = args.baseline is not None or args.candidate is not None
    architecture_mode = any(
        path is not None
        for path in (
            args.general_artifact,
            args.specialist_artifact,
            args.multi_agent_artifact,
        )
    )
    if baseline_mode == architecture_mode:
        parser.error("choose either --baseline/--candidate or all three architecture artifacts")
    comparison: BaselineComparison | ArchitectureComparison
    try:
        if baseline_mode:
            if args.baseline is None or args.candidate is None:
                parser.error("baseline comparison requires --baseline and --candidate")
            baseline = load_model_baseline(args.baseline)
            candidate = load_model_baseline(args.candidate)
            comparison = compare_model_baselines(baseline, candidate)
        else:
            if any(
                path is None
                for path in (
                    args.general_artifact,
                    args.specialist_artifact,
                    args.multi_agent_artifact,
                )
            ):
                parser.error(
                    "architecture comparison requires --general-artifact, "
                    "--specialist-artifact, and --multi-agent-artifact"
                )
            general = load_evaluation_artifact(args.general_artifact)
            specialist = load_evaluation_artifact(args.specialist_artifact)
            multi_agent = load_evaluation_artifact(args.multi_agent_artifact)
            comparison = compare_review_architectures(
                general.evaluation,
                specialist.evaluation,
                multi_agent.evaluation,
            )
    except (
        ArchitectureComparisonError,
        BaselineSafetyError,
        BaselineComparisonError,
        EvaluationArtifactError,
    ) as error:
        parser.error(str(error))
    print(comparison.model_dump_json(indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
