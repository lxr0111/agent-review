import argparse
from collections.abc import Sequence
from pathlib import Path

from evals.baseline import BaselineSafetyError, load_model_baseline
from evals.comparison import BaselineComparisonError, compare_model_baselines


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compare two saved AgentReview model baselines offline."
    )
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _argument_parser()
    args = parser.parse_args(argv)
    try:
        baseline = load_model_baseline(args.baseline)
        candidate = load_model_baseline(args.candidate)
        comparison = compare_model_baselines(baseline, candidate)
    except (BaselineSafetyError, BaselineComparisonError) as error:
        parser.error(str(error))
    print(comparison.model_dump_json(indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
