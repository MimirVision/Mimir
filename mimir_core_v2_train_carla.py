"""Extract features and train a quarantined CARLA collision-timing initializer."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from mimir_core_v2.carla_auxiliary import train_carla_auxiliary
from mimir_core_v2.temporal_training import extract_feature_dataset


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    extract = commands.add_parser("extract")
    extract.add_argument("--prepared", required=True)
    extract.add_argument("--output", required=True)
    extract.add_argument("--sample-fps", type=float, default=10.0)
    extract.add_argument("--workers", type=int, default=4)

    train = commands.add_parser("train")
    train.add_argument("--prepared", required=True)
    train.add_argument("--features", required=True)
    train.add_argument("--output", required=True)
    train.add_argument("--epochs", type=int, default=18)
    train.add_argument("--learning-rate", type=float, default=0.001)
    train.add_argument("--min-sequences", type=int, default=30)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "extract":
            result = extract_feature_dataset(
                Path(args.prepared).resolve(),
                Path(args.output).resolve(),
                sample_fps=args.sample_fps,
                workers=args.workers,
            )
            print(f"CARLA temporal sequences: {len(result.get('sequences') or [])}")
            return 0
        result = train_carla_auxiliary(
            Path(args.features).resolve(),
            Path(args.prepared).resolve(),
            Path(args.output).resolve(),
            epochs=args.epochs,
            learning_rate=args.learning_rate,
            min_sequences=args.min_sequences,
        )
        print("Mimir CARLA shadow training complete")
        print(json.dumps(result.get("heldout_synthetic_test_metrics"), indent=2))
        print("Production promotion: blocked pending consented real Tesla evaluation")
        return 0
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"CARLA training error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
