"""Beta validation wrapper for Mimir Core v2.

This script only runs validation commands and reads their reports. It does not
change scanner behavior, frontend behavior, or source footage.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = ROOT / "MimirOutputV2"
SESSION_PATH = OUTPUT_DIR / "latest_session.json"
RELEASE_REPORT_PATH = OUTPUT_DIR / "release_check_report.json"
BENCHMARK_REPORT_PATH = OUTPUT_DIR / "benchmark_report.json"
BETA_REPORT_PATH = OUTPUT_DIR / "beta_candidate_report.json"

RELEASE_INPUT = r"D:\TeslaCam\SentryClips\2026-04-18_16-04-02"
BENCHMARK_INPUT = r"D:\TeslaCam\SentryClips\2026-04-19_12-44-51"


def command_text(args: list[str]) -> str:
    return " ".join(f'"{arg}"' if " " in str(arg) else str(arg) for arg in args)


def run_command(label: str, args: list[str]) -> dict[str, Any]:
    print()
    print(f"> {command_text(args)}")
    started = time.perf_counter()
    try:
        result = subprocess.run(
            args,
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
    except OSError as exc:
        runtime_sec = time.perf_counter() - started
        print(f"{label} failed to start: {exc}")
        return {
            "label": label,
            "command": args,
            "returncode": 1,
            "runtime_sec": round(runtime_sec, 3),
            "stdout_tail": "",
            "error": str(exc),
        }

    runtime_sec = time.perf_counter() - started
    output = result.stdout or ""
    if output:
        print(output.rstrip())
    return {
        "label": label,
        "command": args,
        "returncode": result.returncode,
        "runtime_sec": round(runtime_sec, 3),
        "stdout_tail": tail_text(output),
        "error": "",
    }


def tail_text(text: str, max_lines: int = 80) -> str:
    lines = text.splitlines()
    return "\n".join(lines[-max_lines:])


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def incident_counts(session: dict[str, Any]) -> dict[str, int]:
    incidents = session.get("incidents")
    if not isinstance(incidents, list):
        incidents = []

    counts = {"important": 0, "review": 0, "ignore": 0}
    for incident in incidents:
        if not isinstance(incident, dict):
            continue
        severity = str(incident.get("final_severity") or incident.get("severity") or "").upper()
        if severity == "IMPORTANT":
            counts["important"] += 1
        elif severity == "REVIEW":
            counts["review"] += 1
        elif severity == "IGNORE":
            counts["ignore"] += 1

    for key in list(counts):
        if counts[key] == 0 and key in session:
            counts[key] = as_int(session.get(key))
    return counts


def build_checks(
    release_command: dict[str, Any],
    scan_command: dict[str, Any],
    benchmark_command: dict[str, Any],
    release_report: dict[str, Any],
    session: dict[str, Any],
    benchmark_report: dict[str, Any],
) -> dict[str, bool]:
    incidents = session.get("incidents") if isinstance(session.get("incidents"), list) else []
    release_passed = release_command.get("returncode") == 0 and bool(release_report.get("passed", True))
    critical_failures = as_int(benchmark_report.get("critical_failures"))
    false_importants = as_int(benchmark_report.get("false_importants"))
    benchmark_passed = benchmark_command.get("returncode") == 0 and critical_failures == 0 and false_importants == 0

    return {
        "release_check_passed": release_passed,
        "benchmark_passed": benchmark_passed,
        "scan_command_passed": scan_command.get("returncode") == 0,
        "latest_session_exists": SESSION_PATH.exists(),
        "schema_version_is_mimir_v2": session.get("schema_version") == "mimir_v2",
        "incidents_exist": bool(incidents),
        "no_critical_benchmark_failures": critical_failures == 0,
        "false_importants_is_zero": false_importants == 0,
        "grouping_ok": release_report.get("grouping_status") == "passed",
        "evidence_ok": release_report.get("evidence_status") == "passed",
        "resolver_passed": release_report.get("resolver_status") == "passed",
    }


def print_summary(summary: dict[str, Any], checks: dict[str, bool]) -> None:
    print()
    print("Mimir beta candidate summary")
    print("============================")
    print(f"release check: {'passed' if checks.get('release_check_passed') else 'failed'}")
    print(f"benchmark: {'passed' if checks.get('benchmark_passed') else 'failed'}")
    print(f"incidents scanned: {summary.get('incidents_scanned')}")
    print(f"important: {summary.get('important')}")
    print(f"review: {summary.get('review')}")
    print(f"ignore: {summary.get('ignore')}")
    print(f"false_importants: {summary.get('false_importants')}")
    print(f"false_ignores: {summary.get('false_ignores')}")
    print(f"critical_failures: {summary.get('critical_failures')}")
    print(f"grouping: {'OK' if checks.get('grouping_ok') else 'FAILED'}")
    print(f"evidence: {'OK' if checks.get('evidence_ok') else 'FAILED'}")
    print(f"resolver: {'passed' if checks.get('resolver_passed') else 'failed'}")
    print(f"report: {BETA_REPORT_PATH}")


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()

    release_command = run_command(
        "release_check",
        [
            sys.executable,
            "mimir_core_v2_release_check.py",
            "--input",
            RELEASE_INPUT,
        ],
    )
    release_report = read_json(RELEASE_REPORT_PATH)

    scan_command = run_command(
        "benchmark_candidate_scan",
        [
            sys.executable,
            "mimir_core_v2_scan.py",
            "--input",
            BENCHMARK_INPUT,
            "--mode",
            "balanced",
        ],
    )
    session = read_json(SESSION_PATH)

    benchmark_command = run_command(
        "benchmark",
        [sys.executable, "-m", "mimir_core_v2.benchmark"],
    )
    benchmark_report = read_json(BENCHMARK_REPORT_PATH)

    counts = incident_counts(session)
    incidents = session.get("incidents") if isinstance(session.get("incidents"), list) else []
    checks = build_checks(
        release_command,
        scan_command,
        benchmark_command,
        release_report,
        session,
        benchmark_report,
    )

    summary = {
        "release_check_passed": checks["release_check_passed"],
        "benchmark_passed": checks["benchmark_passed"],
        "incidents_scanned": len(incidents),
        "important": counts["important"],
        "review": counts["review"],
        "ignore": counts["ignore"],
        "false_importants": as_int(benchmark_report.get("false_importants")),
        "false_ignores": as_int(benchmark_report.get("false_ignores")),
        "critical_failures": as_int(benchmark_report.get("critical_failures")),
    }
    passed = all(checks.values())

    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "ok": passed,
        "total_runtime_sec": round(time.perf_counter() - started, 3),
        "release_input": RELEASE_INPUT,
        "benchmark_input": BENCHMARK_INPUT,
        "summary": summary,
        "checks": checks,
        "commands": {
            "release_check": release_command,
            "benchmark_candidate_scan": scan_command,
            "benchmark": benchmark_command,
        },
        "paths": {
            "latest_session": str(SESSION_PATH),
            "release_report": str(RELEASE_REPORT_PATH),
            "benchmark_report": str(BENCHMARK_REPORT_PATH),
            "beta_candidate_report": str(BETA_REPORT_PATH),
        },
    }
    with BETA_REPORT_PATH.open("w", encoding="utf-8") as file:
        json.dump(report, file, indent=2)

    print_summary(summary, checks)
    print()
    if passed:
        print("MIMIR BETA CANDIDATE PASSED")
        return 0
    print("MIMIR BETA CANDIDATE FAILED")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
