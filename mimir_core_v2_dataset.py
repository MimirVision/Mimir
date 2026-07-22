"""Consent-first dataset export and split validation for Mimir Core v2."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DATASET_SCHEMA = "mimir_contact_dataset_v1"
ANNOTATION_FIELDS = {
    "ego_vehicle_polygons": [],
    "foreign_object_masks": [],
    "objects": [],
    "door_state": None,
    "closest_approach_time_sec": None,
    "apparent_contact_time_sec": None,
    "impact_time_sec": None,
    "human_severity": None,
    "contact_outcome": None,
    "annotator_notes": "",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return data


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def clean_name(value: object) -> str:
    text = str(value or "incident").strip()
    cleaned = "".join(char if char.isalnum() or char in "-_." else "_" for char in text)
    return (cleaned.strip("_.") or "incident")[:120]


def hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_group_key(incident: dict[str, Any], session: dict[str, Any], explicit_group: str = "") -> str:
    if explicit_group.strip():
        return explicit_group.strip()
    session_source = str(session.get("selected_input") or session.get("source_path") or "").strip()
    if session_source:
        # Keep a complete source folder/session in one split. Adjacent clips from
        # one physical event must never be divided merely because group ids differ.
        return session_source
    return str(
        incident.get("event_group_id")
        or incident.get("event_folder")
        or incident.get("source_filename")
        or incident.get("id")
        or "unknown"
    )


def assigned_split(source_hash: str) -> str:
    bucket = int(source_hash[:8], 16) % 100
    if bucket < 80:
        return "train"
    if bucket < 90:
        return "validation"
    return "test"


def incident_media_sources(incident: dict[str, Any]) -> list[dict[str, str]]:
    sources: list[dict[str, str]] = []
    clips = incident.get("camera_clips")
    if isinstance(clips, list):
        for clip in clips:
            if isinstance(clip, dict) and clip.get("path"):
                sources.append({"path": str(clip["path"]), "camera": str(clip.get("camera") or "unknown")})
    if not sources and incident.get("video_path"):
        sources.append({"path": str(incident["video_path"]), "camera": str(incident.get("primary_camera") or "unknown")})
    unique: list[dict[str, str]] = []
    seen: set[str] = set()
    for source in sources:
        key = source["path"].lower()
        if key not in seen:
            unique.append(source)
            seen.add(key)
    return unique


def export_dataset(args: argparse.Namespace) -> int:
    if not args.rights_confirmed:
        raise ValueError("--rights-confirmed is required; Mimir never infers permission to train on footage.")
    session_path = Path(args.session)
    session = read_json(session_path)
    incidents = session.get("incidents") if isinstance(session.get("incidents"), list) else []
    consented_ids = {str(value).strip() for value in args.consent_incident if str(value).strip()}
    if not consented_ids:
        raise ValueError("At least one --consent-incident is required. Consent is never inferred.")

    output = Path(args.output)
    annotations_dir = output / "annotations"
    media_dir = output / "media"
    selected = [
        incident
        for incident in incidents
        if isinstance(incident, dict)
        and str(incident.get("id") or incident.get("event_group_id") or "") in consented_ids
    ]
    missing = sorted(consented_ids - {str(item.get("id") or item.get("event_group_id") or "") for item in selected})
    if missing:
        raise ValueError(f"Consented incident ids were not found: {', '.join(missing)}")

    manifest_items: list[dict[str, Any]] = []
    for incident in selected:
        incident_id = str(incident.get("id") or incident.get("event_group_id"))
        source_hash = hash_text(source_group_key(incident, session, args.source_group))
        media_records: list[dict[str, Any]] = []
        if args.include_video:
            incident_media = media_dir / clean_name(incident_id)
            incident_media.mkdir(parents=True, exist_ok=True)
            for index, media_source in enumerate(incident_media_sources(incident), start=1):
                source = Path(media_source["path"])
                if not source.exists() or not source.is_file():
                    continue
                destination = incident_media / f"{index:02d}_{clean_name(source.name)}"
                shutil.copy2(source, destination)
                media_records.append(
                    {
                        "filename": destination.name,
                        "relative_path": str(destination.relative_to(output)),
                        "size_bytes": destination.stat().st_size,
                        "sha256": sha256_file(destination),
                        "camera": media_source["camera"],
                        "source_filename": source.name,
                    }
                )

        annotation = {
            "schema_version": DATASET_SCHEMA,
            "incident_id": incident_id,
            "source_group_hash": source_hash,
            "split": assigned_split(source_hash),
            "source_filename": Path(str(incident.get("source_filename") or incident.get("video_path") or "")).name,
            "camera": incident.get("primary_camera") or "unknown",
            "duration_sec": incident.get("local_evidence", {}).get("total_duration_sec") if isinstance(incident.get("local_evidence"), dict) else None,
            "mimir_severity": incident.get("final_severity"),
            "mimir_primary_key_moment_sec": incident.get("primary_key_moment_sec"),
            "user_key_moment_sec": incident.get("user_key_moment_sec"),
            "media": media_records,
            "annotation": dict(ANNOTATION_FIELDS),
        }
        annotation_path = annotations_dir / f"{clean_name(incident_id)}.json"
        write_json(annotation_path, annotation)
        manifest_items.append(
            {
                "incident_id": incident_id,
                "source_group_hash": source_hash,
                "split": annotation["split"],
                "annotation": str(annotation_path.relative_to(output)),
                "media_files": len(media_records),
            }
        )

    consent = {
        "schema_version": "mimir_dataset_consent_v1",
        "recorded_at": now_iso(),
        "recorded_by": args.recorded_by,
        "session_id": session.get("session_id"),
        "incident_ids": sorted(consented_ids),
        "video_copy_authorized": bool(args.include_video),
        "rights_confirmed": True,
        "rights_basis": args.rights_basis,
        "license_id": args.license_id,
        "automatic_upload": False,
        "source_group": args.source_group or str(session.get("selected_input") or session.get("source_path") or "session"),
        "statement": "The recorder confirmed rights to use the listed incidents for local model development.",
    }
    manifest = {
        "schema_version": DATASET_SCHEMA,
        "created_at": now_iso(),
        "source_session_id": session.get("session_id"),
        "source_session_basename": session_path.name,
        "consent_record": "consent.json",
        "items": manifest_items,
    }
    write_json(output / "consent.json", consent)
    write_json(output / "manifest.json", manifest)
    print(f"Exported {len(manifest_items)} consented incidents to {output}")
    print("No data was uploaded.")
    return 0


def validate_dataset(args: argparse.Namespace) -> int:
    root = Path(args.dataset)
    manifest = read_json(root / "manifest.json")
    consent = read_json(root / str(manifest.get("consent_record") or "consent.json"))
    consented_ids = set(consent.get("incident_ids") or [])
    errors: list[str] = []
    if not consent.get("rights_confirmed"):
        errors.append("dataset rights were not explicitly confirmed")
    if not str(consent.get("rights_basis") or "").strip():
        errors.append("rights_basis is missing")
    split_by_source: dict[str, str] = {}
    for item in manifest.get("items", []) if isinstance(manifest.get("items"), list) else []:
        if not isinstance(item, dict):
            continue
        incident_id = str(item.get("incident_id") or "")
        if incident_id not in consented_ids:
            errors.append(f"missing consent for {incident_id}")
        annotation_path = root / str(item.get("annotation") or "")
        if not annotation_path.exists():
            errors.append(f"missing annotation for {incident_id}")
        source_hash = str(item.get("source_group_hash") or "")
        split = str(item.get("split") or "")
        previous = split_by_source.setdefault(source_hash, split)
        if previous != split:
            errors.append(f"source leakage: {source_hash} appears in {previous} and {split}")
    if errors:
        print("DATASET INVALID")
        for error in errors:
            print(f"- {error}")
        return 1
    print(f"DATASET OK: {len(manifest.get('items') or [])} consented incidents")
    return 0


def _annotation_path_for_incident(root: Path, incident_id: str) -> Path:
    manifest = read_json(root / "manifest.json")
    for item in manifest.get("items", []) if isinstance(manifest.get("items"), list) else []:
        if isinstance(item, dict) and str(item.get("incident_id") or "") == incident_id:
            path = root / str(item.get("annotation") or "")
            if path.exists():
                return path
            raise ValueError(f"Annotation file is missing for {incident_id}: {path}")
    raise ValueError(f"Incident is not present in the consented collection: {incident_id}")


def _validate_annotation_objects(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ValueError("Object annotation JSON must contain a list")
    allowed_classes = {"person", "vehicle", "vehicle_door", "ego_vehicle"}
    objects: list[dict[str, Any]] = []
    for index, item in enumerate(value, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"Object annotation {index} is not an object")
        class_name = str(item.get("class_name") or "")
        if class_name not in allowed_classes:
            raise ValueError(f"Object annotation {index} has unsupported class: {class_name}")
        try:
            time_sec = float(item.get("time_sec"))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Object annotation {index} needs a numeric time_sec") from exc
        bbox = item.get("bbox_xyxy")
        if not isinstance(bbox, list) or len(bbox) != 4:
            raise ValueError(f"Object annotation {index} needs bbox_xyxy with four values")
        try:
            x1, y1, x2, y2 = (float(part) for part in bbox)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Object annotation {index} has a non-numeric bbox") from exc
        if time_sec < 0 or x2 <= x1 or y2 <= y1:
            raise ValueError(f"Object annotation {index} has an invalid time or bbox")
        normalized = dict(item)
        normalized["class_name"] = class_name
        normalized["time_sec"] = round(time_sec, 4)
        normalized["bbox_xyxy"] = [x1, y1, x2, y2]
        objects.append(normalized)
    return objects


def list_annotations(args: argparse.Namespace) -> int:
    root = Path(args.dataset)
    manifest = read_json(root / "manifest.json")
    print("Mimir consented annotation queue")
    print("=================================")
    for item in manifest.get("items", []) if isinstance(manifest.get("items"), list) else []:
        if not isinstance(item, dict):
            continue
        incident_id = str(item.get("incident_id") or "")
        annotation_path = root / str(item.get("annotation") or "")
        status = "MISSING"
        if annotation_path.exists():
            record = read_json(annotation_path)
            annotation = record.get("annotation") if isinstance(record.get("annotation"), dict) else {}
            severity = str(annotation.get("human_severity") or "")
            outcome = str(annotation.get("contact_outcome") or "")
            status = f"{severity or 'UNLABELED'} / {outcome or 'unlabeled'}"
        print(f"{incident_id}: {status} [{item.get('split') or 'unassigned'}]")
    return 0


def annotate_incident(args: argparse.Namespace) -> int:
    root = Path(args.dataset)
    annotation_path = _annotation_path_for_incident(root, args.incident)
    record = read_json(annotation_path)
    annotation = record.get("annotation") if isinstance(record.get("annotation"), dict) else {}
    annotation = {**ANNOTATION_FIELDS, **annotation}

    timing = {
        "closest_approach_time_sec": args.closest_approach_time_sec,
        "apparent_contact_time_sec": args.apparent_contact_time_sec,
        "impact_time_sec": args.impact_time_sec,
    }
    duration = record.get("duration_sec")
    try:
        duration_sec = float(duration) if duration is not None else None
    except (TypeError, ValueError):
        duration_sec = None
    for name, value in timing.items():
        if value is None:
            continue
        if value < 0 or (duration_sec is not None and value > duration_sec):
            raise ValueError(f"{name} must be within the clip duration")
        annotation[name] = round(float(value), 4)

    if args.contact_outcome in {"contact", "impact"} and not (
        annotation.get("apparent_contact_time_sec") is not None
        or annotation.get("impact_time_sec") is not None
    ):
        raise ValueError("A contact or impact annotation requires a human contact/impact time")

    annotation["human_severity"] = args.human_severity
    annotation["contact_outcome"] = args.contact_outcome
    if args.door_state is not None:
        annotation["door_state"] = args.door_state
    if args.notes is not None:
        annotation["annotator_notes"] = args.notes
    if args.objects:
        object_path = Path(args.objects)
        annotation["objects"] = _validate_annotation_objects(json.loads(object_path.read_text(encoding="utf-8")))

    record["annotation"] = annotation
    record["annotation_updated_at"] = now_iso()
    record["annotated_by"] = args.annotated_by
    write_json(annotation_path, record)
    print(f"Annotated {args.incident}: {args.human_severity} / {args.contact_outcome}")
    print(f"annotation: {annotation_path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Consent-first Mimir dataset tooling.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    export = subparsers.add_parser("export", help="Export explicitly consented incidents locally.")
    export.add_argument("--session", required=True)
    export.add_argument("--output", required=True)
    export.add_argument("--consent-incident", action="append", default=[], help="Incident id explicitly approved for export. Repeat as needed.")
    export.add_argument("--recorded-by", required=True, help="Person or process recording the explicit consent.")
    export.add_argument(
        "--rights-confirmed",
        action="store_true",
        help="Confirm that the recorder owns the footage or has permission/license to use it for model development.",
    )
    export.add_argument(
        "--rights-basis",
        choices=("owned", "explicit_permission", "public_license"),
        required=True,
        help="Legal/provenance basis for model-development use.",
    )
    export.add_argument("--license-id", default="", help="License name or permission record when applicable.")
    export.add_argument("--source-group", default="", help="Physical collection/event id used to keep related clips in one split.")
    export.add_argument("--include-video", action="store_true", help="Copy video only for the explicitly consented incidents.")
    validate = subparsers.add_parser("validate", help="Validate consent and source-isolated splits.")
    validate.add_argument("--dataset", required=True)
    queue = subparsers.add_parser("list", help="List the human annotation state of consented incidents.")
    queue.add_argument("--dataset", required=True)
    annotate = subparsers.add_parser("annotate", help="Record a human temporal/contact label.")
    annotate.add_argument("--dataset", required=True)
    annotate.add_argument("--incident", required=True)
    annotate.add_argument("--annotated-by", required=True)
    annotate.add_argument("--human-severity", choices=("IGNORE", "REVIEW", "IMPORTANT"), required=True)
    annotate.add_argument("--contact-outcome", choices=("contact", "impact", "no_contact", "uncertain"), required=True)
    annotate.add_argument("--closest-approach-time-sec", type=float)
    annotate.add_argument("--apparent-contact-time-sec", type=float)
    annotate.add_argument("--impact-time-sec", type=float)
    annotate.add_argument("--door-state", choices=("closed", "opening", "open", "closing", "not_visible", "not_applicable"))
    annotate.add_argument("--notes")
    annotate.add_argument("--objects", help="JSON list of real frame-level object boxes/masks for this incident.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "export":
            return export_dataset(args)
        if args.command == "validate":
            return validate_dataset(args)
        if args.command == "list":
            return list_annotations(args)
        return annotate_incident(args)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Dataset error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
