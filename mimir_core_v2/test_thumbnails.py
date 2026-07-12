"""Validate Mimir Core v2 thumbnail output.

Run:
    python -m mimir_core_v2.test_thumbnails
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


DEFAULT_SESSION = Path(r"C:\Mimir_Backend\MimirOutputV2\latest_session.json")
THUMBNAIL_FIELDS = ["thumbnail", "hero_thumbnail", "contact_sheet"]


def load_session(path: Path) -> tuple[dict | None, str]:
    if not path.exists():
        return None, f"session not found: {path}"
    try:
        with path.open("r", encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"could not read session: {exc}"
    if not isinstance(data, dict):
        return None, "session JSON is not an object"
    return data, ""


def readable_frames_expected(incident: dict[str, Any]) -> bool:
    evidence = incident.get("local_evidence")
    if not isinstance(evidence, dict):
        evidence = {}
    try:
        sampled_frames = int(evidence.get("sampled_frames") or 0)
    except (TypeError, ValueError):
        sampled_frames = 0
    return sampled_frames > 0


def path_exists(value: Any) -> bool:
    if value is None:
        return False
    text = str(value).strip()
    if not text:
        return False
    try:
        return Path(text).exists()
    except (OSError, ValueError):
        return False


def validate_incident(incident: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    incident_id = str(incident.get("id") or "unknown")

    for field in THUMBNAIL_FIELDS:
        if field not in incident:
            problems.append(f"{incident_id}: missing thumbnail field '{field}'")

    if readable_frames_expected(incident):
        for field in THUMBNAIL_FIELDS:
            if not path_exists(incident.get(field)):
                problems.append(f"{incident_id}: {field} file is missing for readable footage")

    return problems


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate Mimir Core v2 thumbnail output.")
    parser.add_argument("--session", default=str(DEFAULT_SESSION), help="Path to MimirOutputV2/latest_session.json.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    session_path = Path(args.session)
    session, error = load_session(session_path)

    print("Mimir Core v2 Thumbnail Test")
    print("============================")
    print(f"session: {session_path}")

    if error:
        print(error)
        print()
        print("THUMBNAILS FAILED")
        return 1

    incidents = session.get("incidents") if isinstance(session, dict) else []
    if not isinstance(incidents, list) or not incidents:
        print("incidents missing or empty")
        print()
        print("THUMBNAILS FAILED")
        return 1

    problems: list[str] = []
    for incident in incidents:
        if isinstance(incident, dict):
            problems.extend(validate_incident(incident))
        else:
            problems.append("incident entry is not an object")

    print(f"incidents checked: {len(incidents)}")
    print(f"thumbnails_generated: {session.get('thumbnails_generated', 'missing')}")
    print(f"thumbnails_failed: {session.get('thumbnails_failed', 'missing')}")
    print(f"thumbnail_output_dir: {session.get('thumbnail_output_dir', 'missing')}")

    for field in ("thumbnails_generated", "thumbnails_failed", "thumbnail_output_dir"):
        if field not in session:
            problems.append(f"session missing {field}")

    if problems:
        print()
        for problem in problems:
            print(f"- {problem}")
        print()
        print("THUMBNAILS FAILED")
        return 1

    print()
    print("THUMBNAILS OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
