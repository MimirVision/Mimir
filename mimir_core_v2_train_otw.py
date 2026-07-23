"""Build and train the non-production OTW door-articulation shadow model."""

from __future__ import annotations

import argparse
from pathlib import Path

from mimir_core_v2.otw_auxiliary import build_cache, train


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    cache_parser = subparsers.add_parser("build-cache")
    cache_parser.add_argument("--manifest", required=True)
    cache_parser.add_argument("--output", required=True)
    cache_parser.add_argument("--max-samples", type=int, default=0)

    train_parser = subparsers.add_parser("train")
    train_parser.add_argument("--manifest", required=True)
    train_parser.add_argument("--cache", required=True)
    train_parser.add_argument("--output", required=True)
    train_parser.add_argument("--epochs", type=int, default=18)
    train_parser.add_argument("--batch-size", type=int, default=48)
    train_parser.add_argument("--learning-rate", type=float, default=0.001)
    train_parser.add_argument("--seed", type=int, default=20260723)
    args = parser.parse_args()

    manifest = Path(args.manifest).resolve()
    if args.command == "build-cache":
        report = build_cache(manifest, Path(args.output).resolve(), args.max_samples)
        print("Mimir OTW temporal cache complete")
        print(f"samples: {report['sample_count']}")
        print(f"train: {report['train_count']}")
        print(f"validation: {report['validation_count']}")
        print(f"output: {report['cache_path']}")
        return 0

    output = Path(args.output).resolve()
    model = train(
        manifest,
        Path(args.cache).resolve(),
        output,
        epochs=args.epochs,
        batch_size=args.batch_size,
        learning_rate=args.learning_rate,
        seed=args.seed,
    )
    metrics = model["validation_metrics"]
    print("Mimir OTW auxiliary shadow training complete")
    print(f"validation accuracy: {metrics['accuracy']:.4f}")
    print(f"validation macro F1: {metrics['macro_f1']:.4f}")
    print("physical contact claim allowed: no")
    print("production promotion eligible: no")
    print(f"output: {output / 'otw_auxiliary_model_manifest.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
