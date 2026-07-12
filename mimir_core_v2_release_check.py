"""Release validation for Mimir Core v2.

This script only runs validation commands. It does not modify scanner behavior.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = ROOT / "MimirOutputV2"
SESSION_PATH = OUTPUT_DIR / "latest_session.json"
BENCHMARK_REPORT_PATH = OUTPUT_DIR / "benchmark_report.json"
RELEASE_REPORT_PATH = OUTPUT_DIR / "release_check_report.json"

COMPILE_TARGETS = [
    "mimir_core_v2_scan.py",
    "mimir_core_v2\\cli.py",
    "mimir_core_v2\\event_grouping.py",
    "mimir_core_v2\\frame_sampler.py",
    "mimir_core_v2\\evidence_extractor.py",
    "mimir_core_v2\\thumbnailer.py",
    "mimir_core_v2\\severity_resolver.py",
    "mimir_core_v2\\test_grouping.py",
    "mimir_core_v2\\test_evidence.py",
    "mimir_core_v2\\test_thumbnails.py",
    "mimir_core_v2\\test_resolver.py",
    "mimir_core_v2\\test_ai_guardrails.py",
    "mimir_core_v2\\benchmark.py",
    "mimir_core_v2_audit.py",
]


class ReleaseCheckFailed(Exception):
    pass


def command_text(args: list[str]) -> str:
    return " ".join(f'"{arg}"' if " " in str(arg) else str(arg) for arg in args)


def run_required(args: list[str], label: str) -> subprocess.CompletedProcess:
    print()
    print(f"> {command_text(args)}")
    result = subprocess.run(args, cwd=ROOT)
    if result.returncode != 0:
        raise ReleaseCheckFailed(f"{label} failed with exit code {result.returncode}")
    return result


def read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def read_session_required() -> dict:
    session = read_json(SESSION_PATH)
    if not session:
        raise ReleaseCheckFailed(f"latest_session.json missing or unreadable: {SESSION_PATH}")
    return session


def compile_targets() -> None:
    for target in COMPILE_TARGETS:
        if not (ROOT / target).exists():
            raise ReleaseCheckFailed(f"compile target missing: {target}")
        run_required([sys.executable, "-m", "py_compile", target], f"compile {target}")


def benchmark_status_from_report() -> tuple[str, int]:
    report = read_json(BENCHMARK_REPORT_PATH)
    failed = int(report.get("failed") or 0)
    critical = int(report.get("critical_failures") or 0)
    if failed or critical:
        return "failed", critical
    return "passed", critical


def build_summary(input_folder: str, scan_runtime_sec: float, statuses: dict) -> dict:
    session = read_json(SESSION_PATH)
    incidents = session.get("incidents") if isinstance(session.get("incidents"), list) else []
    grouping_debug = session.get("grouping_debug") if isinstance(session.get("grouping_debug"), dict) else {}
    benchmark_report = read_json(BENCHMARK_REPORT_PATH)

    return {
        "input_folder": input_folder,
        "scan_runtime_sec": round(scan_runtime_sec, 3),
        "videos_found": grouping_debug.get("mp4_files_found", "not available"),
        "event_groups": session.get("event_groups_found", "not available"),
        "incidents": len(incidents),
        "important": session.get("important", "not available"),
        "review": session.get("review", "not available"),
        "ignore": session.get("ignore", "not available"),
        "grouping_status": statuses.get("grouping", "not run"),
        "resolver_status": statuses.get("resolver", "not run"),
        "evidence_status": statuses.get("evidence", "not run"),
        "thumbnails_status": statuses.get("thumbnails", "not run"),
        "audit_status": statuses.get("audit", "not run"),
        "thumbnails_generated": session.get("thumbnails_generated", "not available"),
        "thumbnails_failed": session.get("thumbnails_failed", "not available"),
        "benchmark_status": statuses.get("benchmark", "not run"),
        "critical_failures": int(benchmark_report.get("critical_failures") or 0),
        "benchmark": {
            "labels_loaded": benchmark_report.get("labels_loaded", 0),
            "labels_matched": benchmark_report.get("labels_matched", 0),
            "skipped_unmatched": benchmark_report.get("skipped_unmatched", 0),
            "passed": benchmark_report.get("passed", 0),
            "failed": benchmark_report.get("failed", 0),
            "false_importants": benchmark_report.get("false_importants", 0),
            "false_ignores": benchmark_report.get("false_ignores", 0),
        },
    }


def write_release_report(summary: dict, passed: bool, error: str = "") -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    report = dict(summary)
    report["passed"] = passed
    report["error"] = error
    with RELEASE_REPORT_PATH.open("w", encoding="utf-8") as file:
        json.dump(report, file, indent=2)


def print_summary(summary: dict) -> None:
    print()
    print("Mimir Core v2 release summary")
    print("=============================")
    print(f"input folder: {summary.get('input_folder')}")
    print(f"scan runtime: {summary.get('scan_runtime_sec')} sec")
    print(f"videos found: {summary.get('videos_found')}")
    print(f"event groups: {summary.get('event_groups')}")
    print(f"incidents: {summary.get('incidents')}")
    print(f"important: {summary.get('important')}")
    print(f"review: {summary.get('review')}")
    print(f"ignore: {summary.get('ignore')}")
    print(f"grouping status: {summary.get('grouping_status')}")
    print(f"resolver status: {summary.get('resolver_status')}")
    print(f"evidence status: {summary.get('evidence_status')}")
    print(f"thumbnails status: {summary.get('thumbnails_status')}")
    print(f"audit status: {summary.get('audit_status')}")
    print(f"thumbnails generated: {summary.get('thumbnails_generated')}")
    print(f"thumbnails failed: {summary.get('thumbnails_failed')}")
    print(f"benchmark status: {summary.get('benchmark_status')}")
    print(f"critical failures: {summary.get('critical_failures')}")
    print(f"report: {RELEASE_REPORT_PATH}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Mimir Core v2 release checks.")
    parser.add_argument("--input", required=True, help="Folder to scan for the release check.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    statuses = {
        "resolver": "not run",
        "grouping": "not run",
        "evidence": "not run",
        "thumbnails": "not run",
        "audit": "not run",
        "benchmark": "not run",
    }
    scan_runtime_sec = 0.0

    try:
        print("Mimir Core v2 release check")
        print(f"Backend root: {ROOT}")
        print(f"Input: {args.input}")

        compile_targets()
        run_required([sys.executable, "-m", "mimir_core_v2.test_resolver"], "resolver tests")
        statuses["resolver"] = "passed"

        scan_started = time.perf_counter()
        run_required(
            [
                sys.executable,
                "mimir_core_v2_scan.py",
                "--input",
                args.input,
                "--mode",
                "balanced",
            ],
            "v2 scan",
        )
        scan_runtime_sec = time.perf_counter() - scan_started
        read_session_required()

        run_required([sys.executable, "-m", "mimir_core_v2.test_grouping", "--input", args.input], "grouping test")
        statuses["grouping"] = "passed"

        run_required([sys.executable, "-m", "mimir_core_v2.test_evidence", "--input", args.input], "evidence test")
        statuses["evidence"] = "passed"

        run_required([sys.executable, "-m", "mimir_core_v2.test_thumbnails"], "thumbnail test")
        statuses["thumbnails"] = "passed"

        run_required([sys.executable, "mimir_core_v2_audit.py"], "detection audit")
        statuses["audit"] = "passed"

        run_required([sys.executable, "-m", "mimir_core_v2.test_ai_guardrails"], "AI guardrail tests")

        run_required([sys.executable, "-m", "mimir_core_v2.benchmark"], "benchmark")
        statuses["benchmark"], critical_failures = benchmark_status_from_report()
        if statuses["benchmark"] != "passed":
            raise ReleaseCheckFailed(f"benchmark reported failed matched labels or critical failures ({critical_failures})")

        summary = build_summary(args.input, scan_runtime_sec, statuses)
        write_release_report(summary, True)
        print_summary(summary)
        print()
        print("MIMIR CORE V2 RELEASE CHECK PASSED")
        return 0
    except ReleaseCheckFailed as exc:
        summary = build_summary(args.input, scan_runtime_sec, statuses)
        write_release_report(summary, False, str(exc))
        print()
        print(f"Release check error: {exc}")
        print_summary(summary)
        print()
        print("MIMIR CORE V2 RELEASE CHECK FAILED")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
