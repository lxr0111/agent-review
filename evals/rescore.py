import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from evals.baseline import BaselineSafetyError, load_model_baseline
from evals.matcher import CURRENT_MATCHER_VERSION, MatcherVersion
from evals.rescoring import (
    RescoreSafetyError,
    artifact_sha256,
    default_rescore_path,
    rescore_model_baseline,
    save_offline_rescore,
    validate_dataset_identity,
)
from evals.schemas import load_dataset


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Re-score a saved AgentReview baseline without calling a model."
    )
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument(
        "--matcher-version",
        type=MatcherVersion,
        choices=tuple(MatcherVersion),
        default=CURRENT_MATCHER_VERSION,
    )
    parser.add_argument("--save", action="store_true")
    parser.add_argument("--output", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _argument_parser()
    args = parser.parse_args(argv)
    if args.output is not None and not args.save:
        parser.error("--output requires --save")

    baseline_path = args.baseline.resolve()
    dataset_path = args.dataset.resolve()
    try:
        baseline = load_model_baseline(baseline_path)
        validate_dataset_identity(baseline, dataset_path)
        dataset = load_dataset(dataset_path)
        rescore = rescore_model_baseline(
            baseline,
            dataset,
            source_baseline_sha256=artifact_sha256(baseline_path),
            target_matcher_version=args.matcher_version,
        )
        if args.save:
            output_path = args.output or default_rescore_path(rescore)
            save_offline_rescore(rescore, output_path)
            print(f"rescore_saved={output_path}", file=sys.stderr)
    except (BaselineSafetyError, OSError, RescoreSafetyError, ValueError) as error:
        parser.error(str(error))

    print(rescore.model_dump_json(indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
