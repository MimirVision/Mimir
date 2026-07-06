"""Compare Mimir Core v2 results across local AI model options.

This script is comparison tooling only. It runs the existing v2 scanner and
benchmark without changing scanner behavior, labels, or source files.
"""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = ROOT / "MimirOutputV2"
SESSION_PATH = OUTPUT_DIR / "latest_session.json"
BENCHMARK_REPORT_PATH = OUTPUT_DIR / "benchmark_report.json"
MODEL_COMPARE_JSON = OUTPUT_DIR / "model_compare_report.json"
MODEL_COMPARE_CSV = OUTPUT_DIR / "model_compare_report.csv"
DEFAULT_MODELS = ["none", "qwen2.5vl:7b", "llava:7b"]


def read_json(path: Path) -> dict:
    try:
        with path.open("r", encoding="utf-8") as file:
            data = json.load(file)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError) as exc:
        return {"_read_error": str(exc)}


def normalize_models(value: str) -> list[str]:
    models = [item.strip() for item in str(value or "").split(",") if item.strip()]
    return models or list(DEFAULT_MODELS)


def ollama_model_available(model: str) -> tuple[bool, str]:
    if model == "none":
        return True, ""

    request = urllib.request.Request("http://127.0.0.1:11434/api/tags", method="GET")
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            data = json.loads(response.read().decode("utf-8", errors="replace"))
    except (OSError, TimeoutError, urllib.error.URLError, json.JSONDecodeError) as exc:
        return False, f"AI runtime unavailable or not responding: {exc}"

    installed = []
    for item in data.get("models", []):
        if isinstance(item, dict):
            name = str(item.get("name") or "")
            if name:
                installed.append(name)

    if model in installed:
        return True, ""

    short_model = model.split(":", 1)[0]
    if any(name == short_model or name.startswith(short_model + ":") for name in installed):
        return True, ""

    return False, f"AI model not installed: {model}"


def run_command(command: list[str], timeout_sec: int | None = None) -> tuple[int, str]:
    try:
        completed = subprocess.run(
            command,
            cwd=str(ROOT),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout_sec,
        )
        return completed.returncode, completed.stdout or ""
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout if isinstance(exc.stdout, str) else ""
        return 124, f"Command timed out after {timeout_sec} seconds.\n{output}"
    except OSError as exc:
        return 127, str(exc)


def run_scan(input_folder: str, model: str, mode: str) -> tuple[int, float, str]:
    command = [sys.executable, str(ROOT / "mimir_core_v2_scan.py"), "--input", input_folder, "--mode", mode]
    if model != "none":
        command.extend(["--vlm", model])

    started = time.perf_counter()
    return_code, output = run_command(command)
    runtime = round(time.perf_counter() - started, 3)
    return return_code, runtime, output


def run_benchmark() -> tuple[int, str, dict]:
    return_code, output = run_command([sys.executable, "-m", "mimir_core_v2.benchmark"])
    report = read_json(BENCHMARK_REPORT_PATH) if BENCHMARK_REPORT_PATH.exists() else {}
    return return_code, output, report


def counts_from_session(session: dict) -> dict:
    incidents = session.get("incidents") if isinstance(session.get("incidents"), list) else []
    return {
        "videos_found": session.get("grouping_debug", {}).get("mp4_files_found", ""),
        "event_groups": session.get("event_groups_found", len(incidents)),
        "incidents": session.get("incident_count", len(incidents)),
        "important": session.get("important", 0),
        "review": session.get("review", 0),
        "ignore": session.get("ignore", 0),
        "ai_reviewed_groups": session.get("ai_reviewed_groups", 0),
        "ai_skipped_groups": session.get("ai_skipped_groups", 0),
    }


def result_for_unavailable(model: str, reason: str) -> dict:
    return {
        "model": model,
        "status": "unavailable",
        "reason": reason,
        "runtime_sec": "",
        "videos_found": "",
        "event_groups": "",
        "incidents": "",
        "important": "",
        "review": "",
        "ignore": "",
        "ai_reviewed_groups": "",
        "ai_skipped_groups": "",
        "labels_matched": "",
        "passed": "",
        "failed": "",
        "critical_failures": "",
        "false_importants": "",
        "false_ignores": "",
    }


def compare_one_model(input_folder: str, model: str, mode: str) -> dict:
    available, reason = ollama_model_available(model)
    if not available:
        return result_for_unavailable(model, reason)

    scan_code, runtime, scan_output = run_scan(input_folder, model, mode)
    if scan_code != 0:
        return {
            **result_for_unavailable(model, f"scan failed with exit code {scan_code}"),
            "status": "failed",
            "runtime_sec": runtime,
            "scan_output": scan_output[-4000:],
        }

    session = read_json(SESSION_PATH)
    if session.get("_read_error"):
        return {
            **result_for_unavailable(model, f"latest_session.json could not be read: {session['_read_error']}"),
            "status": "failed",
            "runtime_sec": runtime,
            "scan_output": scan_output[-4000:],
        }

    benchmark_code, benchmark_output, benchmark = run_benchmark()
    status = "ok"
    reason = ""
    if benchmark_code not in {0, 2}:
        status = "benchmark_failed"
        reason = f"benchmark exited {benchmark_code}"

    counts = counts_from_session(session)
    return {
        "model": model,
        "status": status,
        "reason": reason,
        "runtime_sec": runtime,
        **counts,
        "labels_matched": benchmark.get("labels_matched", 0),
        "passed": benchmark.get("passed", 0),
        "failed": benchmark.get("failed", 0),
        "critical_failures": benchmark.get("critical_failures", 0),
        "false_importants": benchmark.get("false_importants", 0),
        "false_ignores": benchmark.get("false_ignores", 0),
        "scan_output": scan_output[-4000:],
        "benchmark_output": benchmark_output[-4000:],
    }


def sortable_number(value: object, default: float = 1_000_000_000.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def recommend_default(results: list[dict]) -> dict:
    if not any(int(result.get("labels_matched") or 0) > 0 for result in results if result.get("status") == "ok"):
        return {
            "model": "",
            "reason": "No benchmark labels matched, so no model recommendation was made.",
        }

    eligible = [
        result
        for result in results
        if result.get("status") == "ok" and int(result.get("critical_failures") or 0) == 0
    ]
    if not eligible:
        return {"model": "", "reason": "No available model completed without critical failures."}

    eligible.sort(
        key=lambda item: (
            sortable_number(item.get("false_ignores")),
            sortable_number(item.get("false_importants")),
            sortable_number(item.get("runtime_sec")),
        )
    )
    best = eligible[0]
    reason = (
        "Recommended by lowest false ignores, then lowest false importants, then fastest runtime."
    )
    if best.get("model") == "none":
        reason = "No AI performed best by benchmark metrics, so no AI is recommended for beta."
    return {"model": best.get("model", ""), "reason": reason}


def write_reports(results: list[dict], recommendation: dict, input_folder: str, mode: str) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    report = {
        "input": input_folder,
        "mode": mode,
        "results": results,
        "recommendation": recommendation,
    }
    with MODEL_COMPARE_JSON.open("w", encoding="utf-8") as file:
        json.dump(report, file, indent=2)

    fields = [
        "model",
        "status",
        "reason",
        "runtime_sec",
        "videos_found",
        "event_groups",
        "incidents",
        "important",
        "review",
        "ignore",
        "ai_reviewed_groups",
        "ai_skipped_groups",
        "labels_matched",
        "passed",
        "failed",
        "critical_failures",
        "false_importants",
        "false_ignores",
    ]
    with MODEL_COMPARE_CSV.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        for result in results:
            writer.writerow({field: result.get(field, "") for field in fields})


def print_table(results: list[dict], recommendation: dict) -> None:
    fields = [
        ("model", 16),
        ("status", 14),
        ("runtime_sec", 11),
        ("event_groups", 12),
        ("incidents", 9),
        ("important", 9),
        ("review", 7),
        ("ignore", 7),
        ("ai_reviewed_groups", 11),
        ("failed", 7),
        ("critical_failures", 8),
        ("false_importants", 10),
        ("false_ignores", 9),
    ]
    header = " ".join(name[:width].ljust(width) for name, width in fields)
    print(header)
    print("-" * len(header))
    for result in results:
        print(" ".join(str(result.get(name, ""))[:width].ljust(width) for name, width in fields))
        if result.get("status") not in {"ok", ""} and result.get("reason"):
            print(f"  reason: {result['reason']}")

    print()
    if recommendation.get("model"):
        print(f"Recommended default: {recommendation['model']}")
    else:
        print("Recommended default: none")
    print(f"Recommendation reason: {recommendation.get('reason', '')}")
    print(f"JSON report: {MODEL_COMPARE_JSON}")
    print(f"CSV report: {MODEL_COMPARE_CSV}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Compare Mimir Core v2 model options on one input folder.")
    parser.add_argument("--input", required=True, help="Benchmark footage folder.")
    parser.add_argument("--models", default=",".join(DEFAULT_MODELS), help="Comma-separated models, use 'none' for no AI.")
    parser.add_argument("--mode", default="balanced", choices=["fast", "balanced", "thorough"], help="Scan mode.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    models = normalize_models(args.models)

    print("Mimir Core v2 Model Compare")
    print("===========================")
    print(f"input: {args.input}")
    print(f"mode: {args.mode}")
    print(f"models: {', '.join(models)}")
    print()

    results = []
    for model in models:
        print(f"Running: {model}")
        result = compare_one_model(args.input, model, args.mode)
        results.append(result)
        print(f"  status: {result.get('status')}")
        if result.get("reason"):
            print(f"  reason: {result['reason']}")

    recommendation = recommend_default(results)
    write_reports(results, recommendation, args.input, args.mode)
    print()
    print_table(results, recommendation)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
