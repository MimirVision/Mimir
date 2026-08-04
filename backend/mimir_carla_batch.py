"""Run CARLA synthetic generation in restartable, engine-safe chunks."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from mimir_carla_generate import (
    GENERATOR_VERSION,
    STABLE_PILOT_SCENARIOS,
    build_training_manifest,
    sha256_file,
    split_for_scenario,
    utc_now,
    write_json,
)


def load_records(output: Path) -> list[dict[str, Any]]:
    records = []
    for path in (output / "scenarios").glob("*/scenario.json"):
        value = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(value, dict) and value.get("scenario_id"):
            value["split"] = split_for_scenario(str(value["scenario_id"]))
            write_json(path, value)
            records.append(value)
    records.sort(key=lambda item: str(item.get("scenario_id") or ""))
    return records


def expected_ids(seed: int, count: int) -> list[str]:
    return [
        f"carla-{seed + index:08d}-{index:04d}-"
        f"{STABLE_PILOT_SCENARIOS[index % len(STABLE_PILOT_SCENARIOS)]}"
        for index in range(count)
    ]


def run(args: argparse.Namespace) -> int:
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    script = Path(__file__).resolve().with_name("mimir_carla_generate.py")
    chunks = []
    requested_ids: list[str] = []
    started = time.perf_counter()
    for offset in range(0, args.total_scenarios, args.chunk_size):
        count = min(args.chunk_size, args.total_scenarios - offset)
        chunk_seed = args.seed + offset
        chunk_number = offset // args.chunk_size
        chunk_ids = expected_ids(chunk_seed, count)
        requested_ids.extend(chunk_ids)
        attempts = []
        present_before = {
            str(item.get("scenario_id") or "")
            for item in load_records(output)
        }
        missing_before = [
            scenario_id for scenario_id in chunk_ids if scenario_id not in present_before
        ]
        if not missing_before:
            attempts.append(
                {
                    "attempt": 0,
                    "exit_code": 0,
                    "missing_after_attempt": [],
                    "cached": True,
                }
            )
        for attempt in range(1, args.retries + 1):
            if not missing_before:
                break
            isolated_port = args.port + ((chunk_number * args.retries + attempt - 1) * 10)
            command = [
                sys.executable,
                str(script),
                "--carla-root",
                str(Path(args.carla_root).resolve()),
                "--output",
                str(output),
                "--scenarios",
                str(count),
                "--seed",
                str(chunk_seed),
                "--map",
                args.map,
                "--port",
                str(isolated_port),
                "--scenario-kinds",
                ",".join(STABLE_PILOT_SCENARIOS),
                "--launch-server",
            ]
            completed = subprocess.run(command, check=False)
            time.sleep(args.restart_delay_sec)
            present = {
                str(item.get("scenario_id") or "")
                for item in load_records(output)
            }
            missing = [scenario_id for scenario_id in chunk_ids if scenario_id not in present]
            attempts.append(
                {
                    "attempt": attempt,
                    "port": isolated_port,
                    "exit_code": completed.returncode,
                    "missing_after_attempt": missing,
                }
            )
            if not missing:
                break
            print(
                f"CARLA chunk {offset // args.chunk_size + 1}: "
                f"{len(missing)} missing; restarting clean server"
            )
        chunks.append(
            {
                "offset": offset,
                "seed": chunk_seed,
                "requested": count,
                "attempts": attempts,
                "passed": not attempts[-1]["missing_after_attempt"],
            }
        )
        if not chunks[-1]["passed"]:
            break

    records = load_records(output)
    requested_set = set(requested_ids)
    selected = [
        item for item in records if str(item.get("scenario_id") or "") in requested_set
    ]
    selected.sort(key=lambda item: str(item.get("scenario_id") or ""))
    manifest = build_training_manifest(output, selected)
    missing = sorted(requested_set - {str(item["scenario_id"]) for item in selected})
    report = {
        "schema_version": "mimir_carla_batch_report_v1",
        "generated_at": utc_now(),
        "generator_version": GENERATOR_VERSION,
        "requested_scenarios": args.total_scenarios,
        "completed_scenarios": len(selected),
        "missing_scenarios": missing,
        "actual_collisions": sum(1 for item in selected if item.get("actual_collision")),
        "no_collision": sum(1 for item in selected if not item.get("actual_collision")),
        "intended_label_mismatches": sum(
            1
            for item in selected
            if bool(item.get("intended_positive")) != bool(item.get("actual_collision"))
        ),
        "chunks": chunks,
        "runtime_sec": round(time.perf_counter() - started, 3),
        "training_manifest": str((output / "training_manifest.json").resolve()),
        "training_manifest_sha256": sha256_file(output / "training_manifest.json"),
        "source_audit": manifest["source_audit"],
        "source_role": "synthetic_auxiliary",
        "promotion_eligible": False,
        "passed": not missing and all(item["passed"] for item in chunks),
    }
    write_json(output / "batch_report.json", report)
    print("Mimir CARLA synthetic batch")
    print("===========================")
    print(f"completed: {len(selected)} / {args.total_scenarios}")
    print(
        f"collisions / no collision: "
        f"{report['actual_collisions']} / {report['no_collision']}"
    )
    print(f"label mismatches: {report['intended_label_mismatches']}")
    print(f"report: {output / 'batch_report.json'}")
    return 0 if report["passed"] else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--carla-root", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--total-scenarios", type=int, default=60)
    parser.add_argument("--chunk-size", type=int, default=3)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--seed", type=int, default=20260723)
    parser.add_argument("--map", default="Town05")
    parser.add_argument("--port", type=int, default=2000)
    parser.add_argument("--restart-delay-sec", type=float, default=3.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if (
        args.total_scenarios <= 0
        or args.chunk_size <= 0
        or args.retries <= 0
        or args.restart_delay_sec < 0
    ):
        print("CARLA batch sizes and retries must be positive", file=sys.stderr)
        return 2
    try:
        return run(args)
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(f"CARLA batch error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
