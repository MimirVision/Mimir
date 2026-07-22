"""Run configured Mimir Core v2 benchmark folders.

This is regression tooling only. It scans configured folders into isolated
output directories, runs the benchmark against each latest_session.json, and
then writes one combined report.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "mimir_core_v2" / "benchmark_suite.json"
LABELS_PATH = ROOT / "mimir_core_v2" / "benchmark_labels.csv"
OUTPUT_ROOT = ROOT / "MimirOutputV2"
RUNS_ROOT = OUTPUT_ROOT / "regression_runs"
REPORT_PATH = OUTPUT_ROOT / "regression_suite_report.json"


class SuiteError(Exception):
    pass


def safe_name(value: object) -> str:
    text = str(value or "").strip().lower()
    safe = re.sub(r"[^a-z0-9_.-]+", "_", text)
    return safe.strip("._") or "suite"


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def read_config(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise SuiteError(f"Regression suite config not found: {path}")
    data = read_json(path)
    if not isinstance(data, list):
        raise SuiteError(f"Regression suite config must be a list: {path}")
    entries = [entry for entry in data if isinstance(entry, dict)]
    if len(entries) != len(data):
        raise SuiteError("Regression suite config contains a non-object entry.")
    return entries


def run_command(args: list[str], label: str) -> subprocess.CompletedProcess[str]:
    print()
    print(f"> {' '.join(args)}")
    result = subprocess.run(
        args,
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if result.stdout:
        print(result.stdout.rstrip())
    if result.returncode != 0:
        raise SuiteError(f"{label} failed with exit code {result.returncode}")
    return result


def benchmark_summary(report_path: Path) -> dict[str, int]:
    report = read_json(report_path) if report_path.exists() else {}
    if not isinstance(report, dict):
        report = {}
    return {
        "labels_matched": int(report.get("labels_matched") or 0),
        "passed": int(report.get("passed") or 0),
        "failed": int(report.get("failed") or 0),
        "critical_failures": int(report.get("critical_failures") or 0),
        "false_importants": int(report.get("false_importants") or 0),
        "false_ignores": int(report.get("false_ignores") or 0),
        "skipped_unmatched": int(report.get("skipped_unmatched") or 0),
    }


def scan_and_benchmark(entry: dict[str, Any]) -> dict[str, Any]:
    name = safe_name(entry.get("name"))
    input_path = Path(str(entry.get("input") or ""))
    output_dir = RUNS_ROOT / name
    session_path = output_dir / "latest_session.json"
    benchmark_report_path = output_dir / "benchmark_report.json"

    output_dir.mkdir(parents=True, exist_ok=True)

    scan_args = [
        sys.executable,
        "mimir_core_v2_scan.py",
        "--input",
        str(input_path),
        "--mode",
        "balanced",
    ]
    extra_args = entry.get("extra_args") if isinstance(entry.get("extra_args"), list) else []
    scan_args.extend(str(arg) for arg in extra_args)
    scan_args.extend(["--output-dir", str(output_dir)])
    run_command(scan_args, f"scan {name}")

    source_set = str(entry.get("source_set") or name)
    benchmark_args = [
        sys.executable,
        "-m",
        "mimir_core_v2.benchmark",
        "--session",
        str(session_path),
        "--labels",
        str(LABELS_PATH),
        "--report",
        str(benchmark_report_path),
        "--source-set",
        source_set,
    ]
    run_command(benchmark_args, f"benchmark {name}")

    summary = benchmark_summary(benchmark_report_path)
    return {
        "name": name,
        "input": str(input_path),
        "required": bool(entry.get("required")),
        "notes": entry.get("notes", ""),
        "source_set": source_set,
        "extra_args": [str(arg) for arg in extra_args],
        "output_dir": str(output_dir),
        "session_path": str(session_path),
        "benchmark_report_path": str(benchmark_report_path),
        "status": "passed",
        **summary,
    }


def write_report(report: dict[str, Any]) -> None:
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with REPORT_PATH.open("w", encoding="utf-8") as file:
        json.dump(report, file, indent=2)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the Mimir Core v2 regression suite.")
    parser.add_argument("--config", default=str(CONFIG_PATH), help="Path to benchmark_suite.json.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config_path = Path(args.config)
    failed = False
    folders_scanned = 0
    skipped_entries: list[dict[str, Any]] = []
    failed_entries: list[dict[str, Any]] = []
    run_results: list[dict[str, Any]] = []

    totals = {
        "labels_matched": 0,
        "passed": 0,
        "failed": 0,
        "critical_failures": 0,
        "false_importants": 0,
        "false_ignores": 0,
    }

    try:
        entries = read_config(config_path)
    except SuiteError as exc:
        report = {
            "passed": False,
            "error": str(exc),
            "config_path": str(config_path),
            "runs": [],
            "skipped": [],
            "failed": [],
            "totals": totals,
        }
        write_report(report)
        print(f"Regression suite error: {exc}")
        print("MIMIR REGRESSION SUITE FAILED")
        return 2

    print("Mimir Core v2 Regression Suite")
    print("==============================")
    print(f"config: {config_path}")

    for entry in entries:
        name = safe_name(entry.get("name"))
        input_path = Path(str(entry.get("input") or ""))
        required = bool(entry.get("required"))

        if not input_path.exists() or not input_path.is_dir():
            skipped = {
                "name": name,
                "input": str(input_path),
                "required": required,
                "status": "missing_required" if required else "skipped_missing_optional",
                "notes": entry.get("notes", ""),
            }
            skipped_entries.append(skipped)
            print()
            print(f"SKIP {name}: folder not found: {input_path}")
            if required:
                failed = True
                failed_entries.append(skipped)
            continue

        try:
            result = scan_and_benchmark(entry)
            folders_scanned += 1
            run_results.append(result)
            for key in totals:
                totals[key] += int(result.get(key) or 0)
            if (
                int(result.get("failed") or 0)
                or int(result.get("critical_failures") or 0)
                or int(result.get("false_importants") or 0)
                or int(result.get("false_ignores") or 0)
            ):
                failed = True
                failed_entries.append(result)
        except SuiteError as exc:
            failed = True
            failure = {
                "name": name,
                "input": str(input_path),
                "required": required,
                "status": "failed",
                "error": str(exc),
                "notes": entry.get("notes", ""),
            }
            failed_entries.append(failure)
            run_results.append(failure)

    report = {
        "passed": not failed,
        "config_path": str(config_path),
        "labels_path": str(LABELS_PATH),
        "runs_root": str(RUNS_ROOT),
        "folders_scanned": folders_scanned,
        "folders_skipped": len(skipped_entries),
        "totals": totals,
        "runs": run_results,
        "skipped": skipped_entries,
        "failed": failed_entries,
    }
    write_report(report)

    print()
    print("Regression suite summary")
    print("========================")
    print(f"folders scanned: {folders_scanned}")
    print(f"folders skipped: {len(skipped_entries)}")
    print(f"total labels matched: {totals['labels_matched']}")
    print(f"total passed: {totals['passed']}")
    print(f"total failed: {totals['failed']}")
    print(f"total critical_failures: {totals['critical_failures']}")
    print(f"total false_importants: {totals['false_importants']}")
    print(f"total false_ignores: {totals['false_ignores']}")
    print(f"combined report: {REPORT_PATH}")
    print()

    if failed:
        print("MIMIR REGRESSION SUITE FAILED")
        return 2

    print("MIMIR REGRESSION SUITE PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
