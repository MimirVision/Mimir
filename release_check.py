import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
LATEST_SESSION = ROOT / "MimirOutput" / "latest_session.json"
TEST_INPUT = r"C:\mimir\test"


class CriticalRegressionDetected(Exception):
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
        run_if_exists("inspect_latest_session.py")
        run_if_labels_exist("benchmark_mimir.py", "benchmark_labels.csv")
        run_if_labels_exist("benchmark_impact.py", "impact_labels.csv")
        regression_status = run_regression_check_if_exists()

        print_session_summary()
        print()
        print(f"Regression check: {regression_status}")
        print()
        print("RELEASE CHECK PASSED")
        return 0
    except CriticalRegressionDetected:
        regression_status = "failed"
        print_session_summary()
        print()
        print(f"Regression check: {regression_status}")
        print()
        print("RELEASE CHECK FAILED - critical regression detected")
        return 1
    except SystemExit:
        print_session_summary()
        print()
        print(f"Regression check: {regression_status}")
        print()
        print("RELEASE CHECK FAILED")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
