"""Run repeated real-fixture reliability checks without modifying source footage."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG = ROOT / "mimir_core_v2" / "reliability_suite.json"
DEFAULT_SCANNER = ROOT / "mimir_core_v2_scan.py"
VIDEO_SUFFIXES = {".mp4", ".mov", ".mkv", ".avi"}


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_inventory(root: Path) -> dict[str, tuple[int, str]]:
    return {
        path.relative_to(root).as_posix(): (path.stat().st_size, file_hash(path))
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.suffix.lower() in VIDEO_SUFFIXES
    }


def weighted_scenarios(config: dict[str, Any]) -> list[dict[str, Any]]:
    scenarios = [value for value in config.get("scenarios", []) if isinstance(value, dict)]
    values: list[dict[str, Any]] = []
    maximum_weight = max((max(1, int(value.get("weight") or 1)) for value in scenarios), default=0)
    # Interleave weighted scenarios so even a short smoke run exercises every
    # failure mode once before the high-frequency normal case repeats.
    for repetition in range(maximum_weight):
        for scenario in scenarios:
            if max(1, int(scenario.get("weight") or 1)) > repetition:
                values.append(scenario)
    return values


def copy_fixture(source: Path, destination: Path, mode: str) -> list[Path]:
    shutil.copytree(source, destination)
    media = [path for path in destination.rglob("*") if path.is_file() and path.suffix.lower() in VIDEO_SUFFIXES]
    if mode == "corrupt" and media:
        target = media[0]
        with target.open("r+b") as handle:
            handle.truncate(max(64, target.stat().st_size // 3))
    return media


def scanner_command(scanner: Path, scan_input: Path, run_output: Path, extra_args: list[Any]) -> list[str]:
    prefix = [str(scanner)] if scanner.suffix.lower() == ".exe" else [sys.executable, str(scanner)]
    return [
        *prefix,
        "--input",
        str(scan_input),
        "--mode",
        "balanced",
        "--output",
        str(run_output),
        *(str(value) for value in extra_args),
    ]


def run_once(
    fixture: dict[str, Any],
    scenario: dict[str, Any],
    output: Path,
    index: int,
    scanner: Path,
    timeout_sec: float,
) -> dict[str, Any]:
    source = Path(str(fixture["input"])).resolve()
    run_output = output / "runs" / f"{index:04d}_{fixture['name']}_{scenario['name']}"
    run_output.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    temporary: tempfile.TemporaryDirectory[str] | None = None
    scan_input = source
    copied_media: list[Path] = []
    try:
        copy_mode = str(scenario.get("temporary_copy") or "")
        if copy_mode:
            temporary = tempfile.TemporaryDirectory(prefix="mimir-reliability-")
            scan_input = Path(temporary.name) / "fixture"
            copied_media = copy_fixture(source, scan_input, copy_mode)
        command = scanner_command(scanner, scan_input, run_output, list(scenario.get("extra_args", [])))
        process = subprocess.Popen(command, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        cancel_after = float(scenario.get("cancel_after_sec") or 0.0)
        if cancel_after > 0:
            time.sleep(cancel_after)
            if process.poll() is None:
                process.terminate()
        elif str(scenario.get("temporary_copy") or "") == "remove_during_scan" and copied_media:
            time.sleep(0.25)
            copied_media[-1].unlink(missing_ok=True)
        timed_out = False
        try:
            stdout, stderr = process.communicate(timeout=max(1.0, timeout_sec))
        except subprocess.TimeoutExpired:
            timed_out = True
            process.kill()
            stdout, stderr = process.communicate()
        cancelled = cancel_after > 0
        scan_ok = not timed_out and (process.returncode == 0 if not cancelled else process.returncode != 0)
        benchmark_ok: bool | None = None
        benchmark: dict[str, Any] = {}
        session_path = run_output / "latest_session.json"
        if process.returncode == 0 and session_path.exists() and not copy_mode:
            report_path = run_output / "benchmark_report.json"
            benchmark_command = [
                sys.executable,
                "-m",
                "mimir_core_v2.benchmark",
                "--session",
                str(session_path),
                "--source-set",
                str(fixture.get("source_set") or fixture["name"]),
                "--report",
                str(report_path),
                "--strict",
            ]
            benchmark_result = subprocess.run(
                benchmark_command, cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
            benchmark_ok = benchmark_result.returncode == 0
            if report_path.exists():
                benchmark = read_json(report_path)
            scan_ok = scan_ok and benchmark_ok
        session = read_json(session_path) if session_path.exists() else {}
        incidents = session.get("incidents") if isinstance(session.get("incidents"), list) else []
        media_available = sum(
            1
            for item in incidents
            if isinstance(item, dict)
            and item.get("thumbnail")
            and Path(str(item.get("thumbnail"))).is_file()
            and item.get("video_path")
            and Path(str(item.get("video_path"))).is_file()
        )
        return {
            "index": index,
            "fixture": fixture["name"],
            "scenario": scenario["name"],
            "command": command,
            "returncode": process.returncode,
            "passed": bool(scan_ok),
            "timed_out": timed_out,
            "cancel_requested": cancelled,
            "source_byte_identical": None,
            "runtime_sec": round(time.perf_counter() - started, 3),
            "incidents": len(incidents),
            "media_available": media_available,
            "media_availability_eligible": not cancelled and copy_mode != "remove_during_scan",
            "benchmark_passed": benchmark_ok,
            "critical_failures": int(benchmark.get("critical_failures") or 0),
            "false_importants": int(benchmark.get("false_importants") or 0),
            "false_ignores": int(benchmark.get("false_ignores") or 0),
            "stdout_tail": stdout[-2000:],
            "stderr_tail": stderr[-2000:],
        }
    finally:
        if temporary is not None:
            temporary.cleanup()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Mimir 500-run real-fixture reliability harness.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--iterations", type=int, default=500)
    parser.add_argument("--output", default=str(ROOT / "MimirOutputV2" / "reliability"))
    parser.add_argument("--report", default="", help="Optional canonical report path, such as frontend release_assets.")
    parser.add_argument("--scanner", default=str(DEFAULT_SCANNER), help="Python scanner entrypoint or packaged scanner exe.")
    parser.add_argument("--timeout-sec", type=float, default=900.0)
    parser.add_argument(
        "--allow-missing-required",
        action="store_true",
        help="Developer smoke mode only; the resulting report can never pass the release gate.",
    )
    parser.add_argument("--include-optional-ai", action="store_true")
    args = parser.parse_args(argv)
    try:
        config = read_json(Path(args.config))
        fixtures = []
        missing_required = []
        for fixture in config.get("fixtures", []):
            if not isinstance(fixture, dict):
                continue
            path = Path(str(fixture.get("input") or ""))
            if path.is_dir():
                fixtures.append(fixture)
            elif fixture.get("required"):
                missing_required.append(str(path))
        if missing_required and not args.allow_missing_required:
            raise ValueError("Required reliability fixtures are missing: " + ", ".join(missing_required))
        if missing_required:
            print("WARNING: required reliability fixtures missing; release gate is disabled for this run:")
            for item in missing_required:
                print(f"- {item}")
        if not fixtures:
            raise ValueError("No real reliability fixtures are available.")
        scanner = Path(args.scanner).resolve()
        if not scanner.is_file():
            raise ValueError(f"Scanner is missing: {scanner}")
        scenarios = [
            value
            for value in weighted_scenarios(config)
            if args.include_optional_ai or not value.get("optional")
        ]
        if not scenarios:
            raise ValueError("No reliability scenarios are enabled.")
        output = Path(args.output) / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        source_baselines = {
            str(fixture["name"]): source_inventory(Path(str(fixture["input"])).resolve())
            for fixture in fixtures
        }
        results = []
        for index in range(1, max(1, args.iterations) + 1):
            fixture = fixtures[(index - 1) % len(fixtures)]
            scenario = scenarios[(index - 1) % len(scenarios)]
            print(f"[{index}/{args.iterations}] {fixture['name']} / {scenario['name']}")
            result = run_once(fixture, scenario, output, index, scanner, args.timeout_sec)
            results.append(result)
            if not result["passed"]:
                print("  FAILED")
        source_integrity = {
            str(fixture["name"]): source_baselines[str(fixture["name"])]
            == source_inventory(Path(str(fixture["input"])).resolve())
            for fixture in fixtures
        }
        for result in results:
            result["source_byte_identical"] = source_integrity.get(str(result["fixture"]), False)
            result["passed"] = bool(result["passed"] and result["source_byte_identical"])
        availability_runs = [item for item in results if item.get("media_availability_eligible")]
        total_incidents = sum(int(item["incidents"]) for item in availability_runs)
        total_media = sum(int(item["media_available"]) for item in availability_runs)
        required_fixture_names = {
            str(item.get("name"))
            for item in config.get("fixtures", [])
            if isinstance(item, dict) and item.get("required")
        }
        exercised_fixtures = {str(item["fixture"]) for item in results}
        required_scenario_names = {
            str(item.get("name"))
            for item in config.get("scenarios", [])
            if isinstance(item, dict) and not item.get("optional")
        }
        exercised_scenarios = {str(item["scenario"]) for item in results}
        required_fixtures_complete = not missing_required and required_fixture_names <= exercised_fixtures
        required_scenarios_complete = required_scenario_names <= exercised_scenarios
        source_byte_identical = bool(source_integrity) and all(source_integrity.values())
        availability = total_media / total_incidents if total_incidents else None
        classification_failures = {
            "critical_failures": sum(int(item["critical_failures"]) for item in results),
            "false_importants": sum(int(item["false_importants"]) for item in results),
            "false_ignores": sum(int(item["false_ignores"]) for item in results),
        }
        target_runs = int(config.get("target_runs") or 500)
        release_gate_passed = (
            len(results) >= target_runs
            and not any(not item["passed"] for item in results)
            and required_fixtures_complete
            and required_scenarios_complete
            and source_byte_identical
            and total_incidents > 0
            and availability is not None
            and availability >= 0.995
            and not any(classification_failures.values())
        )
        report = {
            "schema_version": "mimir_reliability_report_v1",
            "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "target_runs": target_runs,
            "completed_runs": len(results),
            "passed_runs": sum(1 for item in results if item["passed"]),
            "failed_runs": sum(1 for item in results if not item["passed"]),
            "scanner": str(scanner),
            "scanner_sha256": file_hash(scanner),
            "required_fixtures": sorted(required_fixture_names),
            "missing_required_fixtures": missing_required,
            "exercised_fixtures": sorted(exercised_fixtures),
            "required_fixtures_complete": required_fixtures_complete,
            "required_scenarios": sorted(required_scenario_names),
            "exercised_scenarios": sorted(exercised_scenarios),
            "required_scenarios_complete": required_scenarios_complete,
            "source_integrity_by_fixture": source_integrity,
            "source_byte_identical": source_byte_identical,
            "thumbnail_video_availability": availability,
            "thumbnail_video_availability_eligible_incidents": total_incidents,
            "intentionally_removed_media_runs": sum(
                1 for item in results if not item.get("media_availability_eligible") and item.get("incidents")
            ),
            **classification_failures,
            "release_gate_passed": release_gate_passed,
            "runs": results,
        }
        report_path = output / "reliability_report.json"
        write_json(report_path, report)
        if args.report:
            write_json(Path(args.report), report)
        print(f"Reliability report: {report_path}")
        if args.report:
            print(f"Canonical report: {Path(args.report).resolve()}")
        print("RELIABILITY GATE PASSED" if report["release_gate_passed"] else "RELIABILITY GATE INCOMPLETE OR FAILED")
        return 0 if not report["failed_runs"] else 1
    except (OSError, ValueError, json.JSONDecodeError, subprocess.SubprocessError) as exc:
        print(f"Reliability suite error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
