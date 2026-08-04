"""Compare Mimir Core v2 no-AI and local vision model runs.

This is regression/diagnostic tooling only. It runs the existing scanner and
benchmark in isolated output directories and does not change detection,
resolver, labels, frontend, or source footage.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
OUTPUT_ROOT = ROOT / "MimirOutputV2"
COMPARE_ROOT = OUTPUT_ROOT / "model_compare"
DEFAULT_MODELS = ["qwen2.5vl:7b", "llava:7b"]
CSV_FIELDS = [
    "model_name",
    "available",
    "scan_completed",
    "ai_enabled",
    "ai_reviewed_groups",
    "ai_skipped_groups",
    "ai_failed_groups",
    "ai_review_runtime_sec",
    "benchmark_passed",
    "labels_matched",
    "failed",
    "critical_failures",
    "false_importants",
    "false_ignores",
    "ai_disagreement_count",
    "ai_downgraded_hard_local_important_count",
    "ai_false_ignore_candidate_count",
    "runtime_sec",
    "output_dir",
    "notes",
]


def safe_name(value: str) -> str:
    text = str(value or "").strip().lower()
    text = text.replace(":", "_").replace(".", "_")
    safe = re.sub(r"[^a-z0-9_.-]+", "_", text)
    return safe.strip("._") or "model"


def timestamp_name() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def read_json(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as file:
            data = json.load(file)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(data, file, indent=2)


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in CSV_FIELDS})


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


def normalize_models(value: str) -> list[str]:
    models = [item.strip() for item in str(value or "").split(",") if item.strip()]
    return models or list(DEFAULT_MODELS)


def ollama_model_available(model: str) -> tuple[bool, str]:
    request = urllib.request.Request("http://127.0.0.1:11434/api/tags", method="GET")
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            data = json.loads(response.read().decode("utf-8", errors="replace"))
    except (OSError, TimeoutError, urllib.error.URLError, json.JSONDecodeError) as exc:
        return False, f"Ollama unavailable or not responding: {exc}"

    installed: list[str] = []
    for item in data.get("models", []):
        if isinstance(item, dict) and item.get("name"):
            installed.append(str(item.get("name")))

    if model in installed:
        return True, ""

    base = model.split(":", 1)[0]
    if any(name == base or name.startswith(base + ":") for name in installed):
        return True, ""

    return False, f"model not installed in Ollama: {model}"


def blank_result(model_name: str, run_dir: Path, notes: str = "") -> dict[str, Any]:
    return {
        "model_name": model_name,
        "available": False,
        "scan_completed": False,
        "ai_enabled": model_name != "no_ai",
        "ai_reviewed_groups": 0,
        "ai_skipped_groups": 0,
        "ai_failed_groups": 0,
        "ai_review_runtime_sec": 0.0,
        "benchmark_passed": False,
        "labels_matched": 0,
        "failed": 0,
        "critical_failures": 0,
        "false_importants": 0,
        "false_ignores": 0,
        "ai_disagreement_count": 0,
        "ai_downgraded_hard_local_important_count": 0,
        "ai_false_ignore_candidate_count": 0,
        "runtime_sec": 0.0,
        "output_dir": str(run_dir),
        "notes": notes,
    }


def scan_timeout_seconds(model_name: str, ai_review_budget: int, ai_timeout_sec: int) -> int:
    if model_name == "no_ai":
        return 240
    return max(300, int(ai_review_budget) * int(ai_timeout_sec) + 240)


def run_scan(
    input_folder: str,
    model_name: str,
    run_dir: Path,
    ai_review_budget: int,
    ai_timeout_sec: int,
) -> tuple[int, float, str]:
    command = [
        sys.executable,
        "mimir_core_v2_scan.py",
        "--input",
        input_folder,
        "--mode",
        "balanced",
        "--output-dir",
        str(run_dir),
    ]
    if model_name != "no_ai":
        command.extend(
            [
                "--vlm",
                model_name,
                "--ai-review-budget",
                str(ai_review_budget),
                "--ai-timeout-sec",
                str(ai_timeout_sec),
            ]
        )

    started = time.perf_counter()
    code, output = run_command(command, timeout_sec=scan_timeout_seconds(model_name, ai_review_budget, ai_timeout_sec))
    return code, round(time.perf_counter() - started, 3), output


def run_benchmark(run_dir: Path, source_set: str) -> tuple[int, str, dict[str, Any]]:
    report_path = run_dir / "benchmark_report.json"
    command = [
        sys.executable,
        "-m",
        "mimir_core_v2.benchmark",
        "--session",
        str(run_dir / "latest_session.json"),
        "--source-set",
        source_set,
        "--report",
        str(report_path),
    ]
    code, output = run_command(command, timeout_sec=180)
    return code, output, read_json(report_path)


def quality_metrics(run_dir: Path) -> dict[str, int]:
    report = read_json(run_dir / "ai_quality_report.json")
    return {
        "ai_disagreement_count": int(report.get("ai_disagreement_count") or 0),
        "ai_downgraded_hard_local_important_count": int(report.get("ai_downgraded_hard_local_important_count") or 0),
        "ai_false_ignore_candidate_count": int(report.get("ai_false_ignore_candidate_count") or 0),
    }


def session_metrics(run_dir: Path) -> dict[str, Any]:
    session = read_json(run_dir / "latest_session.json")
    return {
        "ai_enabled": bool(session.get("ai_enabled")),
        "ai_reviewed_groups": int(session.get("ai_reviewed_groups") or 0),
        "ai_skipped_groups": int(session.get("ai_skipped_groups") or 0),
        "ai_failed_groups": int(session.get("ai_failed_groups") or 0),
        "ai_review_runtime_sec": float(session.get("ai_review_runtime_sec") or 0.0),
    }


def compare_one(
    input_folder: str,
    source_set: str,
    model_name: str,
    run_dir: Path,
    ai_review_budget: int,
    ai_timeout_sec: int,
) -> dict[str, Any]:
    run_dir.mkdir(parents=True, exist_ok=True)

    if model_name != "no_ai":
        available, reason = ollama_model_available(model_name)
        if not available:
            return blank_result(model_name, run_dir, reason)

    scan_code, runtime_sec, scan_output = run_scan(input_folder, model_name, run_dir, ai_review_budget, ai_timeout_sec)
    result = blank_result(model_name, run_dir)
    result["available"] = True
    result["runtime_sec"] = runtime_sec
    result["scan_output_tail"] = scan_output[-4000:]

    if scan_code != 0:
        result["notes"] = f"scan failed with exit code {scan_code}"
        return result

    result["scan_completed"] = True
    result.update(session_metrics(run_dir))
    if result.get("ai_enabled"):
        result.update(quality_metrics(run_dir))
    if int(result.get("ai_failed_groups") or 0) > 0:
        result["notes"] = f"{result.get('ai_failed_groups')} AI review attempt(s) failed safely"

    benchmark_code, benchmark_output, benchmark = run_benchmark(run_dir, source_set)
    result["benchmark_output_tail"] = benchmark_output[-4000:]
    result["labels_matched"] = int(benchmark.get("labels_matched") or 0)
    result["failed"] = int(benchmark.get("failed") or 0)
    result["critical_failures"] = int(benchmark.get("critical_failures") or 0)
    result["false_importants"] = int(benchmark.get("false_importants") or 0)
    result["false_ignores"] = int(benchmark.get("false_ignores") or 0)
    result["benchmark_passed"] = bool(
        benchmark_code == 0
        and result["failed"] == 0
        and result["critical_failures"] == 0
        and result["false_importants"] == 0
        and result["false_ignores"] == 0
        and result["labels_matched"] > 0
    )
    if not result["benchmark_passed"]:
        result["notes"] = f"benchmark failed or had no matched labels; exit code {benchmark_code}"

    return result


def metric_tuple(result: dict[str, Any]) -> tuple[float, float, float, float, float, float, float]:
    unavailable_penalty = 1_000_000 if not result.get("available") or not result.get("scan_completed") else 0
    return (
        unavailable_penalty + float(result.get("critical_failures") or 0),
        float(result.get("false_importants") or 0),
        float(result.get("false_ignores") or 0),
        float(result.get("ai_downgraded_hard_local_important_count") or 0),
        float(result.get("ai_false_ignore_candidate_count") or 0),
        float(result.get("runtime_sec") or 0.0),
        float(result.get("ai_failed_groups") or 0),
    )


def recommendation(results: list[dict[str, Any]]) -> dict[str, str]:
    no_ai = next((result for result in results if result.get("model_name") == "no_ai"), None)
    ai_results = [result for result in results if result.get("model_name") != "no_ai" and result.get("available") and result.get("scan_completed")]

    if not no_ai or not no_ai.get("benchmark_passed"):
        return {
            "recommended_mode": "needs_manual_review",
            "recommended_model": "",
            "reason": "No-AI baseline did not pass benchmark, so no AI recommendation is safe.",
        }

    good_ai = [
        result
        for result in ai_results
        if result.get("benchmark_passed")
        and int(result.get("critical_failures") or 0) == 0
        and int(result.get("false_importants") or 0) == 0
        and int(result.get("false_ignores") or 0) == 0
    ]

    if not good_ai:
        return {
            "recommended_mode": "no_ai_local_only",
            "recommended_model": "no_ai",
            "reason": "No AI model completed with benchmark quality better than the local-only baseline.",
        }

    best_ai = sorted(good_ai, key=metric_tuple)[0]
    no_ai_quality = metric_tuple(no_ai)
    best_ai_quality = metric_tuple(best_ai)
    ai_quality_worse_than_no_ai = best_ai_quality[3] > no_ai_quality[3] or best_ai_quality[4] > no_ai_quality[4]

    if ai_quality_worse_than_no_ai:
        return {
            "recommended_mode": "no_ai_local_only",
            "recommended_model": "no_ai",
            "reason": "AI matched benchmark outcomes but produced worse hard-impact downgrade or false-ignore quality flags than no AI.",
        }

    if int(best_ai.get("ai_downgraded_hard_local_important_count") or 0) > 0 or int(best_ai.get("ai_false_ignore_candidate_count") or 0) > 0:
        return {
            "recommended_mode": "ai_debug_only",
            "recommended_model": str(best_ai.get("model_name") or ""),
            "reason": "The model is useful for diagnostics but still misjudges hard local evidence, so do not expose AI reasoning in beta UI.",
        }

    return {
        "recommended_mode": "ai_second_opinion_hidden",
        "recommended_model": str(best_ai.get("model_name") or ""),
        "reason": "The model passed benchmark and did not produce hard-impact downgrade or false-ignore quality flags; keep it hidden as a second opinion.",
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Compare Mimir Core v2 local AI model options.")
    parser.add_argument("--input", required=True, help="Folder to scan.")
    parser.add_argument("--source-set", required=True, help="Benchmark source_set to evaluate.")
    parser.add_argument("--models", default=",".join(DEFAULT_MODELS), help="Comma-separated Ollama model names.")
    parser.add_argument("--ai-review-budget", type=int, default=5, help="AI review budget per model run.")
    parser.add_argument("--ai-timeout-sec", type=int, default=60, help="AI timeout per reviewed group.")
    return parser


def print_summary(results: list[dict[str, Any]], rec: dict[str, str], report_dir: Path) -> None:
    print("Mimir AI Model Comparison")
    print("=========================")
    for result in results:
        model = result.get("model_name", "")
        if not result.get("available"):
            status = "unavailable"
        elif not result.get("scan_completed"):
            status = "scan failed"
        elif result.get("benchmark_passed"):
            status = "PASS"
        else:
            status = "FAIL"
        print(
            f"{model}: {status} "
            f"matched={result.get('labels_matched')} "
            f"failed={result.get('failed')} "
            f"critical={result.get('critical_failures')} "
            f"false_importants={result.get('false_importants')} "
            f"false_ignores={result.get('false_ignores')} "
            f"ai_failed={result.get('ai_failed_groups')} "
            f"ai_downgrades={result.get('ai_downgraded_hard_local_important_count')} "
            f"ai_false_ignores={result.get('ai_false_ignore_candidate_count')} "
            f"runtime={result.get('runtime_sec')}s"
        )
        if result.get("notes"):
            print(f"  notes: {result.get('notes')}")

    print()
    print("Recommendation:")
    print(f"{rec.get('recommended_mode')}: {rec.get('recommended_model')}")
    print(rec.get("reason", ""))
    print()
    print(f"report dir: {report_dir}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    compare_dir = COMPARE_ROOT / timestamp_name()
    models = ["no_ai", *normalize_models(args.models)]
    results: list[dict[str, Any]] = []

    for model in models:
        run_dir = compare_dir / safe_name(model)
        print(f"Running comparison for {model}...")
        result = compare_one(
            input_folder=args.input,
            source_set=args.source_set,
            model_name=model,
            run_dir=run_dir,
            ai_review_budget=max(0, args.ai_review_budget),
            ai_timeout_sec=max(1, args.ai_timeout_sec),
        )
        results.append(result)

    rec = recommendation(results)
    report = {
        "input": args.input,
        "source_set": args.source_set,
        "models": models,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "report_dir": str(compare_dir),
        "recommendation": rec,
        "results": results,
    }
    write_json(compare_dir / "model_compare_report.json", report)
    write_csv(compare_dir / "model_compare_report.csv", results)
    print_summary(results, rec, compare_dir)

    no_ai = next((result for result in results if result.get("model_name") == "no_ai"), {})
    return 0 if no_ai.get("benchmark_passed") else 2


if __name__ == "__main__":
    raise SystemExit(main())
