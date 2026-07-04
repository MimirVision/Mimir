import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
SORTER = BASE_DIR / "tesla_ai_sorter.py"
OUTPUT_DIR = BASE_DIR / "MimirOutput"
LATEST_SESSION = OUTPUT_DIR / "latest_session.json"
PERFORMANCE_REPORT = OUTPUT_DIR / "performance_report.json"
BENCHMARK_REPORT = OUTPUT_DIR / "large_scan_benchmark.json"
REGRESSION_CHECK = BASE_DIR / "regression_check.py"


def read_json(path):
    with open(path, "r", encoding="utf-8") as file:
        return json.load(file)


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as file:
        json.dump(value, file, indent=2)
        file.write("\n")


def to_float(value, default=0.0):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number


def to_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def nested(mapping, *keys, default=None):
    current = mapping
    for key in keys:
        if not isinstance(current, dict):
            return default
        current = current.get(key)
    return default if current is None else current


def slowest_stage(performance_report, session):
    stage = performance_report.get("slowest_stage")
    if isinstance(stage, dict):
        return stage

    timings = (
        performance_report.get("stage_timings")
        or nested(performance_report, "performance", "stage_timings", default={})
        or nested(session, "performance", "stage_timings", default={})
        or {}
    )
    if not isinstance(timings, dict) or not timings:
        return {"stage": "", "runtime_sec": 0.0}

    name, runtime = max(timings.items(), key=lambda item: to_float(item[1]))
    return {"stage": name, "runtime_sec": round(to_float(runtime), 3)}


def slowest_groups(performance_report, session, limit=10):
    groups = (
        performance_report.get("slowest_groups")
        or nested(performance_report, "performance", "slowest_groups", default=[])
        or nested(session, "performance", "slowest_groups", default=[])
        or performance_report.get("groups")
        or []
    )
    if not isinstance(groups, list):
        return []

    return sorted(
        [group for group in groups if isinstance(group, dict)],
        key=lambda group: to_float(group.get("runtime_sec")),
        reverse=True,
    )[:limit]


def severity_counts(session):
    incidents = session.get("incidents", [])
    if not isinstance(incidents, list):
        incidents = []

    counts = {
        "IMPORTANT": to_int(session.get("important")),
        "REVIEW": to_int(session.get("review")),
        "IGNORE": to_int(session.get("ignore")),
    }

    if any(counts.values()):
        return counts

    for incident in incidents:
        if not isinstance(incident, dict):
            continue
        severity = str(incident.get("final_severity") or incident.get("severity") or "IGNORE").upper()
        if severity not in counts:
            severity = "IGNORE"
        counts[severity] += 1

    return counts


def cache_stats(session, performance_report):
    performance = session.get("performance", {}) if isinstance(session.get("performance"), dict) else {}
    report_performance = (
        performance_report.get("performance")
        if isinstance(performance_report.get("performance"), dict)
        else {}
    )
    return {
        "hits": to_int(
            performance.get("cache_hits")
            or report_performance.get("cache_hits")
            or performance_report.get("cache_hits")
        ),
        "misses": to_int(
            performance.get("cache_misses")
            or report_performance.get("cache_misses")
            or performance_report.get("cache_misses")
        ),
    }


def run_command(command, cwd):
    started = time.perf_counter()
    completed = subprocess.run(
        command,
        cwd=str(cwd),
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return {
        "command": command,
        "returncode": completed.returncode,
        "stdout": completed.stdout or "",
        "stderr": completed.stderr or "",
        "runtime_sec": round(time.perf_counter() - started, 3),
    }


def run_scanner(input_folder, mode):
    return run_command(
        [
            sys.executable,
            str(SORTER),
            "--input",
            str(input_folder),
            "--mode",
            str(mode),
        ],
        BASE_DIR,
    )


def run_regression_check_if_present():
    if not REGRESSION_CHECK.exists():
        return {
            "present": False,
            "returncode": 0,
            "stdout": "",
            "stderr": "",
            "runtime_sec": 0.0,
        }

    result = run_command([sys.executable, str(REGRESSION_CHECK)], BASE_DIR)
    result["present"] = True
    return result


def build_summary(scanner_result, regression_result, session, performance_report, max_runtime_min):
    performance = session.get("performance", {}) if isinstance(session.get("performance"), dict) else {}
    report_performance = (
        performance_report.get("performance")
        if isinstance(performance_report.get("performance"), dict)
        else {}
    )
    counts = severity_counts(session)
    cache = cache_stats(session, performance_report)
    runtime_sec = to_float(
        performance.get("total_runtime_sec")
        or report_performance.get("total_runtime_sec")
        or performance_report.get("total_runtime_sec")
        or scanner_result.get("runtime_sec")
    )
    slow_stage = slowest_stage(performance_report, session)
    slow_groups = slowest_groups(performance_report, session)

    return {
        "ok": True,
        "input": "",
        "mode": session.get("scan_mode"),
        "max_runtime_min": max_runtime_min,
        "scanner_returncode": scanner_result.get("returncode"),
        "scanner_runtime_sec": scanner_result.get("runtime_sec"),
        "total_runtime_sec": runtime_sec,
        "event_groups": to_int(
            performance.get("total_event_groups")
            or report_performance.get("total_event_groups")
            or session.get("event_groups_found")
        ),
        "videos": to_int(
            performance.get("videos_processed")
            or report_performance.get("videos_processed")
            or session.get("clips_processed")
        ),
        "total_video_duration_sec": to_float(
            performance.get("total_video_duration_sec")
            or report_performance.get("total_video_duration_sec")
        ),
        "prepass_groups": to_int(session.get("prepass_groups_processed") or performance.get("prepass_groups_processed")),
        "deep_analysis_groups": to_int(session.get("deep_analysis_groups") or performance.get("deep_analysis_groups")),
        "skipped_groups": to_int(session.get("skipped_low_interest_groups") or performance.get("skipped_low_interest_groups")),
        "ai_calls": to_int(
            performance.get("ai_calls")
            or report_performance.get("ai_calls")
            or performance.get("total_ai_calls")
        ),
        "cache_hits": cache["hits"],
        "cache_misses": cache["misses"],
        "incidents_created": to_int(
            performance.get("incidents_created")
            or report_performance.get("incidents_created")
            or len(session.get("incidents", []) if isinstance(session.get("incidents"), list) else [])
        ),
        "important": counts["IMPORTANT"],
        "review": counts["REVIEW"],
        "ignore": counts["IGNORE"],
        "slowest_stage": slow_stage,
        "slowest_groups": slow_groups,
        "regression_check": {
            "present": bool(regression_result.get("present")),
            "returncode": regression_result.get("returncode"),
            "runtime_sec": regression_result.get("runtime_sec"),
        },
        "failures": [],
    }


def print_summary(summary):
    print("Large scan benchmark")
    print("====================")
    print(f"Total runtime: {summary['total_runtime_sec']:.1f} sec")
    print(f"Event groups: {summary['event_groups']}")
    print(f"Videos: {summary['videos']}")
    print(f"Total video duration: {summary['total_video_duration_sec']:.1f} sec")
    print(f"Prepass groups: {summary['prepass_groups']}")
    print(f"Deep analysis groups: {summary['deep_analysis_groups']}")
    print(f"Skipped groups: {summary['skipped_groups']}")
    print(f"AI calls: {summary['ai_calls']}")
    print(f"Cache hits/misses: {summary['cache_hits']}/{summary['cache_misses']}")
    print(f"Incidents created: {summary['incidents_created']}")
    print(f"Important/Review/Ignore: {summary['important']}/{summary['review']}/{summary['ignore']}")

    stage = summary["slowest_stage"]
    print(f"Slowest stage: {stage.get('stage') or 'n/a'} ({to_float(stage.get('runtime_sec')):.1f} sec)")
    print("Slowest 10 groups:")
    if not summary["slowest_groups"]:
        print("- n/a")
    for group in summary["slowest_groups"]:
        print(
            f"- {group.get('group_id', 'unknown')} | "
            f"{to_float(group.get('runtime_sec')):.1f} sec | "
            f"ai_calls={to_int(group.get('ai_calls'))} | "
            f"severity={group.get('final_severity') or 'n/a'}"
        )

    if summary["failures"]:
        print("Failures:")
        for failure in summary["failures"]:
            print(f"- {failure}")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Run Project Mimir scanner and summarize beta readiness performance."
    )
    parser.add_argument("--input", required=True, help="Folder to scan.")
    parser.add_argument("--mode", default="balanced", help="Scanner mode. Defaults to balanced.")
    parser.add_argument(
        "--max-runtime-min",
        type=float,
        default=60.0,
        help="Fail if scanner runtime exceeds this many minutes. Defaults to 60.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    scanner_result = run_scanner(args.input, args.mode)
    failures = []

    if scanner_result["returncode"] != 0:
        failures.append("scanner crashed")

    if not LATEST_SESSION.exists():
        failures.append("latest_session.json missing")

    session = {}
    performance_report = {}

    if LATEST_SESSION.exists():
        try:
            session = read_json(LATEST_SESSION)
        except Exception as exc:
            failures.append(f"latest_session.json could not be read: {exc}")

    if PERFORMANCE_REPORT.exists():
        try:
            performance_report = read_json(PERFORMANCE_REPORT)
        except Exception as exc:
            failures.append(f"performance_report.json could not be read: {exc}")

    regression_result = run_regression_check_if_present()
    if regression_result.get("returncode") != 0:
        failures.append("regression_check.py failed")

    summary = build_summary(
        scanner_result,
        regression_result,
        session,
        performance_report,
        args.max_runtime_min,
    )
    summary["input"] = str(args.input)
    summary["failures"] = failures

    if summary["total_runtime_sec"] > args.max_runtime_min * 60.0:
        summary["failures"].append(
            f"runtime exceeded {args.max_runtime_min:g} minutes"
        )

    summary["ok"] = not summary["failures"]
    summary["scanner_stdout_tail"] = scanner_result["stdout"][-8000:]
    summary["scanner_stderr_tail"] = scanner_result["stderr"][-8000:]
    summary["regression_stdout_tail"] = str(regression_result.get("stdout", ""))[-4000:]
    summary["regression_stderr_tail"] = str(regression_result.get("stderr", ""))[-4000:]

    write_json(BENCHMARK_REPORT, summary)
    print_summary(summary)
    print(f"Benchmark report: {BENCHMARK_REPORT}")

    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
