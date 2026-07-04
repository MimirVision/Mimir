import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
LATEST_SESSION = ROOT / "MimirOutput" / "latest_session.json"
GROUPING_CONTRACT_REPORT = ROOT / "MimirOutput" / "grouping_contract_report.json"
TEST_INPUT = r"C:\mimir\test"


class CriticalRegressionDetected(Exception):
    pass


class GroupingContractFailed(Exception):
    pass


def command_text(args):
    return " ".join(f'"{arg}"' if " " in str(arg) else str(arg) for arg in args)


def run_command(args):
    print()
    print(f"> {command_text(args)}")
    result = subprocess.run(args, cwd=ROOT)

    if result.returncode != 0:
        print()
        print(f"FAILED: {command_text(args)}")
        print(f"Exit code: {result.returncode}")
        return False

    return True


def run_required(args):
    if not run_command(args):
        raise SystemExit(1)


def compile_if_exists(filename):
    path = ROOT / filename

    if not path.exists():
        print()
        print(f"Skipping compile, file not found: {filename}")
        return

    run_required([sys.executable, "-m", "py_compile", filename])


def run_if_exists(filename):
    path = ROOT / filename

    if not path.exists():
        print()
        print(f"Skipping run, file not found: {filename}")
        return

    run_required([sys.executable, filename])


def run_if_labels_exist(script_name, labels_name):
    script_path = ROOT / script_name
    labels_path = ROOT / labels_name

    if not script_path.exists():
        print()
        print(f"Skipping run, file not found: {script_name}")
        return

    if not labels_path.exists():
        print()
        print(f"Skipping {script_name}, labels file not found: {labels_name}")
        return

    run_required([sys.executable, script_name])


def run_regression_check_if_exists():
    script_path = ROOT / "regression_check.py"

    if not script_path.exists():
        print()
        print("Skipping regression check, file not found: regression_check.py")
        return "not run"

    if not run_command([sys.executable, "regression_check.py"]):
        raise CriticalRegressionDetected()

    return "passed"


def safe_read_json(path):
    try:
        if not path.exists():
            return None

        with path.open("r", encoding="utf-8") as file:
            data = json.load(file)

        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def run_grouping_contract_check():
    path = ROOT / "test_grouping_contract.py"

    if not path.exists():
        print()
        print("RELEASE CHECK FAILED - camera angle grouping is broken")
        print("Missing required grouping contract: test_grouping_contract.py")
        raise GroupingContractFailed()

    if not run_command(
        [
            sys.executable,
            "test_grouping_contract.py",
            "--input",
            TEST_INPUT,
        ]
    ):
        print()
        print("RELEASE CHECK FAILED - camera angle grouping is broken")
        raise GroupingContractFailed()

    return "passed"


def print_grouping_summary():
    session = safe_read_json(LATEST_SESSION) or {}
    report = safe_read_json(GROUPING_CONTRACT_REPORT) or {}
    grouping_debug = session.get("grouping_debug")

    if not isinstance(grouping_debug, dict):
        grouping_debug = {}

    video_files_found = (
        report.get("total_mp4_files")
        if report.get("total_mp4_files") is not None
        else grouping_debug.get("video_files_found", "not available")
    )
    event_groups_built = grouping_debug.get(
        "event_groups_built",
        session.get(
            "event_groups_found",
            report.get("total_timestamp_groups", "not available")
        )
    )
    incidents_created = grouping_debug.get(
        "incidents_created",
        len(session.get("incidents", [])) if isinstance(session.get("incidents"), list) else "not available"
    )
    multi_camera_groups = session.get(
        "multi_camera_groups",
        "not available"
    )

    print()
    print("Grouping summary:")
    print(f"- video files found: {video_files_found}")
    print(f"- event groups built: {event_groups_built}")
    print(f"- incidents created: {incidents_created}")
    print(f"- multi camera groups: {multi_camera_groups}")


def print_session_summary():
    print()
    print("Backend summary:")

    if not LATEST_SESSION.exists():
        print(f"- latest_session.json not found: {LATEST_SESSION}")
        return

    try:
        with LATEST_SESSION.open("r", encoding="utf-8") as file:
            session = json.load(file)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"- Could not read latest_session.json: {exc}")
        return

    incidents = session.get("incidents")
    incident_count = len(incidents) if isinstance(incidents, list) else 0
    performance = session.get("performance") if isinstance(session.get("performance"), dict) else {}

    print(f"- clips_processed: {session.get('clips_processed', 'not available')}")
    print(f"- incident_count: {incident_count}")
    print(f"- important: {session.get('important', 'not available')}")
    print(f"- review: {session.get('review', 'not available')}")
    print(f"- ignore: {session.get('ignore', 'not available')}")

    runtime = performance.get("total_runtime_sec")
    if runtime is not None:
        print(f"- runtime_sec: {runtime}")

    print(f"- progress_supported: {session.get('progress_supported', 'not available')}")
    print(f"- detected_source_type: {session.get('detected_source_type', 'not available')}")


def main():
    print("Mimir backend release check")
    print(f"Backend root: {ROOT}")
    regression_status = "not run"

    try:
        run_required([sys.executable, "-m", "py_compile", "tesla_ai_sorter.py"])
        run_required([sys.executable, "-m", "py_compile", "validate_mimir_output.py"])
        compile_if_exists("inspect_latest_session.py")
        compile_if_exists("benchmark_mimir.py")
        compile_if_exists("benchmark_impact.py")
        compile_if_exists("mimir_clip_actions.py")
        compile_if_exists("discover_footage_source.py")
        compile_if_exists("regression_check.py")
        compile_if_exists("test_grouping_contract.py")

        run_required(
            [
                sys.executable,
                "tesla_ai_sorter.py",
                "--input",
                TEST_INPUT,
                "--mode",
                "balanced",
                "--vlm",
                "qwen2.5vl:7b",
            ]
        )
        run_required([sys.executable, "validate_mimir_output.py"])
        grouping_contract_status = run_grouping_contract_check()
        run_if_exists("inspect_latest_session.py")
        run_if_labels_exist("benchmark_mimir.py", "benchmark_labels.csv")
        run_if_labels_exist("benchmark_impact.py", "impact_labels.csv")
        regression_status = run_regression_check_if_exists()

        print_session_summary()
        print_grouping_summary()
        print()
        print(f"Grouping contract: {grouping_contract_status}")
        print(f"Regression check: {regression_status}")
        print()
        print("RELEASE CHECK PASSED")
        return 0
    except GroupingContractFailed:
        print_session_summary()
        print_grouping_summary()
        print()
        print("Grouping contract: failed")
        print(f"Regression check: {regression_status}")
        print()
        print("RELEASE CHECK FAILED - camera angle grouping is broken")
        return 1
    except CriticalRegressionDetected:
        regression_status = "failed"
        print_session_summary()
        print_grouping_summary()
        print()
        print(f"Regression check: {regression_status}")
        print()
        print("RELEASE CHECK FAILED - critical regression detected")
        return 1
    except SystemExit:
        print_session_summary()
        print_grouping_summary()
        print()
        print(f"Regression check: {regression_status}")
        print()
        print("RELEASE CHECK FAILED")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
