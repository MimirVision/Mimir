"""Train a non-promotable stationary-activity shadow model from MEVA."""

from __future__ import annotations

import argparse
from pathlib import Path

from mimir_core_v2.meva_auxiliary import run_training


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--epochs", type=int, default=2500)
    args = parser.parse_args()
    model = run_training(Path(args.manifest).resolve(), Path(args.output).resolve(), args.epochs)
    print("Mimir MEVA auxiliary shadow training complete")
    print(f"rows: {model['row_count']}")
    print(f"validation accuracy: {model['validation_metrics']['accuracy']:.4f}")
    print(f"validation recall: {model['validation_metrics']['recall']:.4f}")
    print("production promotion eligible: no")
    print(f"output: {Path(args.output).resolve() / 'meva_auxiliary_model.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
