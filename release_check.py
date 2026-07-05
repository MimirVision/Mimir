import csv
import json
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent
LATEST_SESSION = ROOT / "MimirOutput" / "latest_session.json"
GROUPING_CONTRACT_REPORT = ROOT / "MimirOutput" / "grouping_contract_report.json"
BENCHMARK_LABELS = ROOT / "benchmark_labels.csv"
TEST_INPUT = r"C:\mimir\test"


class CriticalRegressionDetected(Exception):
    pass


class GroupingContractFailed(Exception):
    pass


class ReleaseCheckFailed(Exception):
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


def run_required_script(filename, args=None):
    path = ROOT / filename

    if not path.exists():
        raise ReleaseCheckFailed(f"Required release check script is missing: {filename}")

    run_required([sys.executable, filename, *(args or [])])
    return "passed"


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


def normalize_text(value):
    return str(value or "").strip()


def normalize_lookup_text(value):
    return normalize_text(value).lower().replace("\\", "/")


def normalize_severity(value):
    severity = normalize_text(value).upper()
    return severity if severity in {"IGNORE", "REVIEW", "IMPORTANT"} else ""


def get_incidents(session):
    incidents = session.get("incidents")
    return incidents if isinstance(incidents, list) else []


def get_final_severity(incident):
    if not isinstance(incident, dict):
        return ""

    return normalize_severity(
        incident.get("final_severity")
        or incident.get("severity")
        or incident.get("user_status")
    )


def incident_bool(incident, field):
    if not isinstance(incident, dict):
        return False

    debug = incident.get("classification_debug")
    if not isinstance(debug, dict):
        debug = {}

    return bool(incident.get(field) or debug.get(field))


def incident_reference_blob(incident):
    if not isinstance(incident, dict):
        return ""

    values = []
    for field in (
        "id",
        "incident_id",
        "event_group_id",
        "event_timestamp",
        "event_folder",
        "source_video",
        "source_filename",
        "filename",
        "video_path",
        "original_source_video",
        "library_video_path",
        "source_category",
    ):
        value = incident.get(field)
        if value:
            values.append(str(value))

    camera_clips = incident.get("camera_clips")
    if isinstance(camera_clips, list):
        for clip in camera_clips:
            if not isinstance(clip, dict):
                continue
            for field in ("path", "filename", "library_path", "original_source_video"):
                value = clip.get(field)
                if value:
                    values.append(str(value))

    return normalize_lookup_text(" ".join(values))


def read_benchmark_labels():
    if not BENCHMARK_LABELS.exists():
        return []

    try:
        with BENCHMARK_LABELS.open("r", encoding="utf-8", newline="") as file:
            return list(csv.DictReader(file))
    except OSError as exc:
        raise ReleaseCheckFailed(f"Could not read benchmark_labels.csv: {exc}") from exc


def find_matching_incident(label_key, incidents):
    key = normalize_lookup_text(label_key)
    if not key:
        return None

    for incident in incidents:
        if key in incident_reference_blob(incident):
            return incident

    return None


def has_hard_important_evidence(incident):
    if not isinstance(incident, dict):
        return False

    debug = incident.get("classification_debug")
    if not isinstance(debug, dict):
        debug = {}

    if incident.get("important_evidence_found") or debug.get("important_evidence_found"):
        return True

    for field in (
        "crash_safety_triggered",
        "visible_contact",
        "visible_impact",
        "person_interaction_evidence",
        "tampering_evidence",
        "door_handle_attempt",
    ):
        if incident_bool(incident, field):
            return True

    for field in ("impact_level", "contact_level", "impact_evidence_level", "contact_evidence_level"):
        value = normalize_text(incident.get(field) or debug.get(field)).upper()
        if value == "HIGH":
            return True

    return False


def enforce_release_policies(session):
    incidents = get_incidents(session)
    failures = []

    for incident in incidents:
        severity = get_final_severity(incident)
        incident_id = normalize_text(incident.get("id") or incident.get("incident_id") or "unknown")
        person_near_only = incident_bool(incident, "person_near_only")
        passby = (
            incident_bool(incident, "person_passby_evidence")
            or incident_bool(incident, "person_passby_detected")
            or incident_bool(incident, "vehicle_passby_detected")
        )

        if severity == "IMPORTANT" and person_near_only:
            failures.append(f"{incident_id}: person-near-only incident is IMPORTANT")
        elif severity == "IMPORTANT" and passby and not has_hard_important_evidence(incident):
            failures.append(f"{incident_id}: pass-by incident is IMPORTANT without hard evidence")

    for label in read_benchmark_labels():
        label_key = label.get("filename_or_group")
        category = normalize_text(label.get("category")).lower()
        notes = normalize_text(label.get("notes")).lower()
        matched = find_matching_incident(label_key, incidents)
        if not matched:
            continue

        severity = get_final_severity(matched)
        incident_id = normalize_text(matched.get("id") or matched.get("incident_id") or "unknown")

        if category in {"person_near", "person_passby"} and "never important" in notes and severity == "IMPORTANT":
            failures.append(f"{label_key}: benchmark person/pass-by case matched {incident_id} as IMPORTANT")

        rear_impact_label = category == "rear_impact" or "rear impact" in notes or "rear-end" in notes
        if rear_impact_label and severity == "IGNORE":
            failures.append(f"{label_key}: rear-impact benchmark case matched {incident_id} as IGNORE")

    if failures:
        print()
        print("Release policy failures:")
        for failure in failures:
            print(f"- {failure}")
        raise ReleaseCheckFailed("release policy validation failed")


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

    incidents = get_incidents(session)
    incident_count = len(incidents)
    performance = session.get("performance") if isinstance(session.get("performance"), dict) else {}

    print(f"- total runtime: {performance.get('total_runtime_sec', session.get('total_runtime_sec', 'not available'))}")
    print(f"- event groups: {session.get('event_groups_found', performance.get('total_event_groups', 'not available'))}")
    print(f"- incidents: {incident_count}")
    print(f"- important: {session.get('important', 'not available')}")
    print(f"- review: {session.get('review', 'not available')}")
    print(f"- ignore: {session.get('ignore', 'not available')}")
    print(f"- AI calls: {performance.get('ai_calls', session.get('ai_calls', 'not available'))}")


def main():
    print("Mimir backend release check")
    print(f"Backend root: {ROOT}")
    started_at = time.perf_counter()
    grouping_contract_status = "not run"
    benchmark_status = "not run"

    try:
        run_required([sys.executable, "-m", "py_compile", "tesla_ai_sorter.py"])
        compile_if_exists("benchmark_mimir.py")
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

        if not LATEST_SESSION.exists():
            raise ReleaseCheckFailed(f"latest_session.json missing: {LATEST_SESSION}")

        run_required([sys.executable, "validate_mimir_output.py"])
        grouping_contract_status = run_grouping_contract_check()
        benchmark_status = run_required_script("benchmark_mimir.py")

        session = safe_read_json(LATEST_SESSION)
        if not isinstance(session, dict):
            raise ReleaseCheckFailed(f"Could not read latest_session.json: {LATEST_SESSION}")
        enforce_release_policies(session)

        print_session_summary()
        print_grouping_summary()
        print()
        print(f"Grouping contract: {grouping_contract_status}")
        print(f"Benchmark: {benchmark_status}")
        print(f"Release check runtime sec: {time.perf_counter() - started_at:.1f}")
        print()
        print("RELEASE CHECK PASSED")
        return 0
    except GroupingContractFailed:
        print_session_summary()
        print_grouping_summary()
        print()
        print("Grouping contract: failed")
        print(f"Benchmark: {benchmark_status}")
        print(f"Release check runtime sec: {time.perf_counter() - started_at:.1f}")
        print()
        print("RELEASE CHECK FAILED - camera angle grouping is broken")
        print("RELEASE CHECK FAILED")
        return 1
    except ReleaseCheckFailed as exc:
        print()
        print(f"Release check error: {exc}")
        print_session_summary()
        print_grouping_summary()
        print()
        print(f"Grouping contract: {grouping_contract_status}")
        print(f"Benchmark: {benchmark_status}")
        print(f"Release check runtime sec: {time.perf_counter() - started_at:.1f}")
        print()
        print("RELEASE CHECK FAILED")
        return 1
    except SystemExit:
        print_session_summary()
        print_grouping_summary()
        print()
        print(f"Grouping contract: {grouping_contract_status}")
        print(f"Benchmark: {benchmark_status}")
        print(f"Release check runtime sec: {time.perf_counter() - started_at:.1f}")
        print()
        print("RELEASE CHECK FAILED")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
