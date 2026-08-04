"""Validate Mimir Core v2 key moment output.

Run:
    python -m mimir_core_v2.test_key_moments
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


DEFAULT_SESSION = Path(r"C:\Mimir_Backend\MimirOutputV2\latest_session.json")
IMPACT_LABELS = {"Impact/contact", "Impact"}


def load_session(path: Path) -> tuple[dict[str, Any] | None, str]:
    if not path.exists():
        return None, f"session not found: {path}"
    try:
        with path.open("r", encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"could not read session: {exc}"
    return (data, "") if isinstance(data, dict) else (None, "session JSON is not an object")


def as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def incident_id(incident: dict[str, Any], index: int) -> str:
    return str(incident.get("id") or f"incident_{index:04d}")


def severity(incident: dict[str, Any]) -> str:
    return str(incident.get("final_severity") or incident.get("severity") or "").upper()


def known_duration(incident: dict[str, Any]) -> float:
    durations: list[float] = []
    for clip in as_list(incident.get("camera_clips")):
        duration = safe_float(as_dict(clip).get("duration_sec"))
        if duration > 0:
            durations.append(duration)
    evidence = as_dict(incident.get("local_evidence")) or as_dict(incident.get("local_evidence_summary"))
    evidence_duration = safe_float(evidence.get("total_duration_sec"))
    if evidence_duration > 0 and not durations:
        durations.append(evidence_duration)
    return max(durations, default=0.0)


def evidence_has_impact_or_contact(incident: dict[str, Any]) -> bool:
    evidence = as_dict(incident.get("local_evidence")) or as_dict(incident.get("local_evidence_summary"))
    return bool(
        evidence.get("strong_impact_like_motion")
        or evidence.get("possible_impact")
        or evidence.get("possible_contact")
        or str(evidence.get("impact_level") or "").upper() in {"MEDIUM", "HIGH"}
        or str(evidence.get("contact_level") or "").upper() in {"MEDIUM", "HIGH"}
        or evidence.get("hard_contact_candidate")
        or evidence.get("rear_impact_candidate")
    )


def has_evidence_time(incident: dict[str, Any]) -> bool:
    evidence = as_dict(incident.get("local_evidence")) or as_dict(incident.get("local_evidence_summary"))
    if safe_float(evidence.get("motion_spike_time_sec")) > 0:
        return True
    for camera_evidence in as_dict(evidence.get("camera_evidence")).values():
        if safe_float(as_dict(camera_evidence).get("motion_spike_time_sec")) > 0:
            return True
    return False


def validate_incident(incident: dict[str, Any], index: int) -> list[str]:
    problems: list[str] = []
    current_id = incident_id(incident, index)
    moments = as_list(incident.get("key_moments"))
    if not moments:
        problems.append(f"{current_id}: key_moments is missing or empty")
        return problems

    times: list[float] = []
    duration = known_duration(incident)
    for moment_index, raw_moment in enumerate(moments):
        moment = as_dict(raw_moment)
        if not moment:
            problems.append(f"{current_id}: key moment {moment_index} is not an object")
            continue
        time_sec = safe_float(moment.get("time_sec"), -1.0)
        times.append(time_sec)
        if time_sec < 0:
            problems.append(f"{current_id}: marker time is negative")
        if duration > 0 and time_sec > duration + 0.05:
            problems.append(f"{current_id}: marker time {time_sec:g}s exceeds duration {duration:g}s")
        for field in ("label", "type", "source", "camera"):
            if not str(moment.get(field) or "").strip():
                problems.append(f"{current_id}: key moment missing {field}")

    if times != sorted(times):
        problems.append(f"{current_id}: key_moments are not sorted")
    for left, right in zip(times, times[1:]):
        if abs(right - left) < 1.0:
            problems.append(f"{current_id}: duplicate markers within 1 second ({left:g}s, {right:g}s)")

    primary_time = safe_float(incident.get("primary_key_moment_sec"), -1.0)
    primary_label = str(incident.get("primary_key_moment_label") or "")
    if primary_time < 0 or not primary_label:
        problems.append(f"{current_id}: primary key moment fields are missing")

    evidence = as_dict(incident.get("local_evidence")) or as_dict(incident.get("local_evidence_summary"))
    refinement = as_dict(evidence.get("key_moment_refinement"))
    refinement_anchor = safe_float(
        refinement.get("candidate_interval_time_sec"),
        safe_float(refinement.get("coarse_time_sec"), -1.0),
    )
    if evidence_has_impact_or_contact(incident) and refinement.get("refined") and refinement_anchor >= 0:
        if abs(primary_time - refinement_anchor) > 0.8:
            problems.append(
                f"{current_id}: refined primary {primary_time:g}s drifted from candidate interval {refinement_anchor:g}s"
            )

    labels = {str(as_dict(moment).get("label") or "") for moment in moments}
    if severity(incident) == "IMPORTANT" and evidence_has_impact_or_contact(incident):
        if not labels.intersection(IMPACT_LABELS):
            problems.append(f"{current_id}: important impact/contact incident lacks an Impact/contact or Impact marker")

    if not evidence_has_impact_or_contact(incident):
        bad_labels = labels.intersection(IMPACT_LABELS)
        if bad_labels:
            problems.append(f"{current_id}: non-impact/pass-by incident has impact marker labels: {sorted(bad_labels)}")

    if not has_evidence_time(incident):
        if not any(as_dict(moment).get("type") == "review_point" for moment in moments):
            problems.append(f"{current_id}: fallback review_point missing when no evidence time is available")

    return problems


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate Core v2 key moments.")
    parser.add_argument("--session", default=str(DEFAULT_SESSION), help="Path to latest_session.json.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    session, error = load_session(Path(args.session))

    print("Mimir Core v2 Key Moments Test")
    print("==============================")
    print(f"session: {args.session}")

    if error or session is None:
        print(error)
        print()
        print("KEY MOMENTS FAILED")
        return 1

    incidents = as_list(session.get("incidents"))
    if not incidents:
        print("incidents missing or empty")
        print()
        print("KEY MOMENTS FAILED")
        return 1

    problems: list[str] = []
    for index, incident in enumerate(incidents, start=1):
        if isinstance(incident, dict):
            problems.extend(validate_incident(incident, index))
        else:
            problems.append(f"incident_{index:04d}: incident entry is not an object")

    print(f"incidents checked: {len(incidents)}")
    print(f"key_moment_version: {session.get('key_moment_version', 'missing')}")
    print(f"key_moments_generated_count: {session.get('key_moments_generated_count', 'missing')}")

    if problems:
        print()
        for problem in problems:
            print(f"- {problem}")
        print()
        print("KEY MOMENTS FAILED")
        return 1

    print()
    print("KEY MOMENTS OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
