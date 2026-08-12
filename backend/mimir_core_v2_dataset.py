"""Consent-first dataset export and split validation for Mimir Core v2."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mimir_core_v2.dataset_package import (
    DatasetPackageError,
    encrypt_collection,
    exclusion_hashes,
    intake_package,
)
from mimir_core_v2.feedback_package import build_feedback_package, encrypt_feedback_collection


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
    package_id = str(getattr(args, "package_id", "") or uuid.uuid4().hex)
    if len(package_id) != 32 or any(char not in "0123456789abcdef" for char in package_id.lower()):
        raise ValueError("package_id must be a 32-character hexadecimal identifier")
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
    consent_clips: list[dict[str, Any]] = []
    excluded = exclusion_hashes()
    permission_record_value = str(getattr(args, "independent_permission_record", "") or "").strip()
    permission_record = Path(permission_record_value).resolve() if permission_record_value else None
    if permission_record is not None and not permission_record.is_file():
        raise ValueError(f"Independent permission record does not exist: {permission_record}")
    for incident in selected:
        source_incident_id = str(incident.get("id") or incident.get("event_group_id"))
        incident_id = f"{package_id}_{clean_name(source_incident_id)}"
        source_hash = hash_text(source_group_key(incident, session, args.source_group))
        media_records: list[dict[str, Any]] = []
        sources = incident_media_sources(incident)
        for media_source in sources:
            source = Path(media_source["path"])
            if not source.exists() or not source.is_file():
                continue
            digest = sha256_file(source)
            if digest in excluded and permission_record is None:
                raise ValueError(
                    "Excluded regression footage cannot enter training without an independently "
                    f"verified permission record: {source.name} ({excluded[digest]})"
                )
            consent_clips.append(
                {
                    "incident_id": incident_id,
                    "source_incident_id": source_incident_id,
                    "source_filename": source.name,
                    "sha256": digest,
                    "size_bytes": source.stat().st_size,
                    "rights_confirmed": True,
                }
            )
        if args.include_video:
            incident_media = media_dir / clean_name(incident_id)
            incident_media.mkdir(parents=True, exist_ok=True)
            for index, media_source in enumerate(sources, start=1):
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
            "source_incident_id": source_incident_id,
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
                "source_incident_id": source_incident_id,
                "source_group_hash": source_hash,
                "split": annotation["split"],
                "annotation": str(annotation_path.relative_to(output)),
                "media_files": len(media_records),
            }
        )

    permission_reference = str(getattr(args, "permission_reference", "") or "").strip()
    if not permission_reference and args.rights_basis != "owned":
        # Only where an external document is what the basis rests on, and only
        # from a licence id the caller actually supplied. The old fallback
        # synthesised "owned:some-session" whenever the field was blank, which
        # put a string that looks like a permission reference into a receipt
        # where no permission had been referenced. An empty field paired with
        # rights_basis "owned" is the honest record: the terms of service carry
        # that warranty, and inventing a citation to fill a column makes the
        # receipt worth less, not more.
        permission_reference = str(args.license_id or "")
    consent = {
        "schema_version": "mimir_dataset_consent_v2",
        "receipt_id": uuid.uuid4().hex,
        "recorded_at": now_iso(),
        "recorded_by": args.recorded_by,
        "session_id": session.get("session_id"),
        "incident_ids": sorted(str(item["incident_id"]) for item in manifest_items),
        "source_incident_ids": sorted(consented_ids),
        "video_copy_authorized": bool(args.include_video),
        "rights_confirmed": True,
        "rights_basis": args.rights_basis,
        "permission_reference": permission_reference,
        "license_id": args.license_id,
        "clips": consent_clips,
        "automatic_upload": False,
        "source_group": args.source_group or str(session.get("selected_input") or session.get("source_path") or "session"),
        "statement": "The recorder confirmed rights to use the listed incidents for local model development.",
    }
    if permission_record is not None:
        provenance_dir = output / "provenance"
        provenance_dir.mkdir(parents=True, exist_ok=True)
        copied_record = provenance_dir / clean_name(permission_record.name)
        shutil.copy2(permission_record, copied_record)
        consent["independent_permission_record"] = copied_record.relative_to(output).as_posix()
        consent["independent_permission_record_sha256"] = sha256_file(copied_record)
    manifest = {
        "schema_version": DATASET_SCHEMA,
        "package_id": package_id,
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


def export_encrypted_dataset(args: argparse.Namespace) -> int:
    recipient = str(args.recipient or "").strip()
    if args.recipient_file:
        recipient = Path(args.recipient_file).read_text(encoding="utf-8").strip()
    if not recipient:
        raise ValueError("An age recipient or --recipient-file is required.")
    output = Path(args.output)
    with tempfile.TemporaryDirectory(prefix="mimir-contribution-") as temporary:
        collection = Path(temporary) / "collection"
        export_args = argparse.Namespace(**vars(args))
        export_args.output = str(collection)
        export_args.include_video = True
        export_args.package_id = uuid.uuid4().hex
        result = export_dataset(export_args)
        if result != 0:
            return result
        package = encrypt_collection(collection, output, recipient, Path(args.session))
    print(f"Encrypted contribution package: {package['output']}")
    print(f"package id: {package['package_id']}")
    print(f"sha256: {package['sha256']}")
    print("No data was uploaded. Transfer the encrypted package manually.")
    # The package id is generated in here, not known to the caller in
    # advance. This line is how the Rust side learns it (to name the Outbox
    # entry and set the upload header) without parsing the prose above.
    print("MIMIR_PACKAGE_JSON: " + json.dumps(package))
    return 0


def export_feedback_package(args: argparse.Namespace) -> int:
    """Package feedback (+ optional video) into an age-encrypted, self-contained
    file. This function only builds the file -- it never sends it anywhere;
    the caller (the Mimir app's Outbox) decides whether and when to upload it.
    """

    recipient = str(args.recipient or "").strip()
    if args.recipient_file:
        recipient = Path(args.recipient_file).read_text(encoding="utf-8").strip()
    if not recipient:
        raise ValueError("An age recipient or --recipient-file is required.")

    feedback = json.loads(Path(args.feedback_json).read_text(encoding="utf-8"))
    video_path = Path(args.video) if args.video else None
    output = Path(args.output)

    with tempfile.TemporaryDirectory(prefix="mimir-feedback-") as temporary:
        collection = build_feedback_package(feedback, video_path, Path(temporary))
        package = encrypt_feedback_collection(collection, output, recipient)

    print(f"Encrypted feedback package: {package['output']}")
    print(f"package id: {package['package_id']}")
    print(f"sha256: {package['sha256']}")
    print("No data was uploaded. Submission is a separate, explicit action.")
    print("MIMIR_PACKAGE_JSON: " + json.dumps(package))
    return 0
    return 0


def intake_encrypted_dataset(args: argparse.Namespace) -> int:
    result = intake_package(
        Path(args.package),
        Path(args.identity),
        Path(args.dataset_root),
        cvat_url=args.cvat_url if args.create_cvat_tasks else "",
        cvat_token=args.cvat_token,
        cvat_token_file=args.cvat_token_file,
    )
    print(json.dumps(result, indent=2))
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


def record_blind_relabel(args: argparse.Namespace) -> int:
    root = Path(args.dataset)
    annotation_path = _annotation_path_for_incident(root, args.incident)
    record = read_json(annotation_path)
    original = record.get("annotation") if isinstance(record.get("annotation"), dict) else {}
    if not original.get("human_severity") or not original.get("contact_outcome"):
        raise ValueError("Blind re-labeling requires a complete original annotation.")
    updated_at = str(record.get("annotation_updated_at") or "")
    if updated_at:
        try:
            original_time = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
            elapsed_hours = (datetime.now(timezone.utc) - original_time).total_seconds() / 3600.0
        except ValueError as exc:
            raise ValueError("Original annotation timestamp is malformed.") from exc
        if elapsed_hours < args.minimum_delay_hours:
            raise ValueError(
                f"Blind re-label delay is {elapsed_hours:.1f} hours; "
                f"minimum is {args.minimum_delay_hours:.1f} hours."
            )
    timing = {
        "closest_approach_time_sec": args.closest_approach_time_sec,
        "apparent_contact_time_sec": args.apparent_contact_time_sec,
        "impact_time_sec": args.impact_time_sec,
    }
    relabel: dict[str, Any] = {
        "schema_version": "mimir_blind_reannotation_v1",
        "recorded_at": now_iso(),
        "annotated_by": args.annotated_by,
        "human_severity": args.human_severity,
        "contact_outcome": args.contact_outcome,
        "door_state": args.door_state,
        "notes": args.notes or "",
    }
    for name, value in timing.items():
        if value is not None:
            if value < 0:
                raise ValueError(f"{name} must not be negative")
            relabel[name] = round(float(value), 4)
    if args.contact_outcome in {"contact", "impact"} and not (
        relabel.get("apparent_contact_time_sec") is not None or relabel.get("impact_time_sec") is not None
    ):
        raise ValueError("A contact or impact re-label requires a human contact/impact time.")
    history = record.get("blind_reannotations") if isinstance(record.get("blind_reannotations"), list) else []
    history.append(relabel)
    record["blind_reannotations"] = history
    write_json(annotation_path, record)
    print(f"Blind re-label recorded for {args.incident} without changing the original annotation.")
    return 0


def default_feedback_folder() -> Path | None:
    home = os.environ.get("USERPROFILE") or os.environ.get("HOME")
    if not home:
        return None
    return Path(home) / "Documents" / "Mimir Feedback"


def feedback_incident_ids(feedback_folder: Path | None) -> set[str]:
    if feedback_folder is None or not feedback_folder.is_dir():
        return set()
    ids: set[str] = set()
    for entry in feedback_folder.iterdir():
        feedback_path = entry / "feedback.json"
        if not feedback_path.is_file():
            continue
        try:
            record = read_json(feedback_path)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        incident_id = str(record.get("incident_id") or "").strip()
        if incident_id:
            ids.add(incident_id)
    return ids


def incident_matches_filter(incident: dict[str, Any], filter_name: str, feedback_ids: set[str]) -> bool:
    incident_id = str(incident.get("id") or incident.get("event_group_id") or "")
    severity = str(incident.get("final_severity") or incident.get("severity") or "").upper()
    if filter_name == "all":
        return True
    if filter_name == "reviewed":
        return bool(incident.get("manual_status_override")) or incident_id in feedback_ids
    if filter_name == "feedback":
        return incident_id in feedback_ids
    if filter_name == "important":
        return severity == "IMPORTANT"
    if filter_name == "review":
        return severity == "REVIEW"
    if filter_name == "important_or_review":
        return severity in {"IMPORTANT", "REVIEW"}
    return False


def select_incidents(args: argparse.Namespace) -> int:
    """Lists incidents matching a review filter, then prints a ready-to-run
    export-encrypted command covering all of them -- so reviewing hundreds of
    incidents doesn't mean typing hundreds of --consent-incident flags by hand.
    Consent is still never inferred: this only prints the command, it never
    runs it, and the rights/consent flags still have to be supplied explicitly.
    """
    session_path = Path(args.session)
    session = read_json(session_path)
    incidents = session.get("incidents") if isinstance(session.get("incidents"), list) else []
    feedback_ids = feedback_incident_ids(Path(args.feedback) if args.feedback else default_feedback_folder())

    matched = [
        incident
        for incident in incidents
        if isinstance(incident, dict) and incident_matches_filter(incident, args.filter, feedback_ids)
    ]
    matched_ids = sorted(str(item.get("id") or item.get("event_group_id") or "") for item in matched)

    print(f"Filter: {args.filter}")
    print(f"Matched {len(matched_ids)} of {len(incidents)} incidents in this session.")
    for incident_id in matched_ids:
        print(f"  {incident_id}")

    if not matched_ids:
        return 0

    if not (args.recorded_by and args.rights_basis and args.permission_reference and args.output):
        print(
            "\nTo get a ready-to-run export command, also pass --recorded-by, "
            "--rights-basis, --permission-reference, and --output."
        )
        return 0

    command_parts = [
        "python", "mimir_core_v2_dataset.py", "export-encrypted",
        "--session", str(session_path),
        "--output", args.output,
        "--recorded-by", args.recorded_by,
        "--rights-confirmed",
        "--rights-basis", args.rights_basis,
        "--permission-reference", args.permission_reference,
    ]
    for incident_id in matched_ids:
        command_parts += ["--consent-incident", incident_id]
    if args.recipient_file:
        command_parts += ["--recipient-file", args.recipient_file]
    elif args.recipient:
        command_parts += ["--recipient", args.recipient]

    print("\nReview the list above, then run this to export all of them as one consented package:\n")
    print(" ".join(f'"{part}"' if " " in part else part for part in command_parts))
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
    export.add_argument("--permission-reference", default="", help="Auditable ownership, permission, or license reference.")
    export.add_argument("--independent-permission-record", default="", help="File proving permission for otherwise excluded footage.")
    export.add_argument("--source-group", default="", help="Physical collection/event id used to keep related clips in one split.")
    export.add_argument("--include-video", action="store_true", help="Copy video only for the explicitly consented incidents.")
    encrypted = subparsers.add_parser("export-encrypted", help="Create an age-encrypted, manually transferable contribution package.")
    encrypted.add_argument("--session", required=True)
    encrypted.add_argument("--output", required=True, help="Destination ending in .mimir-dataset.age")
    encrypted.add_argument("--consent-incident", action="append", default=[], required=True)
    encrypted.add_argument("--recorded-by", required=True)
    encrypted.add_argument("--rights-confirmed", action="store_true", required=True)
    encrypted.add_argument("--rights-basis", choices=("owned", "explicit_permission", "public_license"), required=True)
    encrypted.add_argument("--permission-reference", required=True)
    encrypted.add_argument("--license-id", default="")
    encrypted.add_argument("--independent-permission-record", default="")
    encrypted.add_argument("--source-group", default="")
    encrypted.add_argument("--recipient", default="")
    encrypted.add_argument("--recipient-file", default="")
    feedback = subparsers.add_parser("export-feedback", help="Create an age-encrypted feedback package (no video required).")
    feedback.add_argument("--feedback-json", required=True, help="Path to the feedback JSON to package")
    feedback.add_argument("--video", default="", help="Optional path to a video clip to include")
    feedback.add_argument("--output", required=True, help="Destination ending in .mimir-feedback.age")
    feedback.add_argument("--recipient", default="")
    feedback.add_argument("--recipient-file", default="")
    intake = subparsers.add_parser("intake", help="Decrypt, validate, deduplicate, split, and optionally send a package to local CVAT.")
    intake.add_argument("--package", required=True)
    intake.add_argument("--identity", required=True)
    intake.add_argument("--dataset-root", required=True)
    intake.add_argument("--create-cvat-tasks", action="store_true")
    intake.add_argument("--cvat-url", default="http://localhost:8080")
    intake.add_argument("--cvat-token", default="")
    intake.add_argument("--cvat-token-file", default="")
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
    relabel = subparsers.add_parser("blind-relabel", help="Record a delayed intra-annotator re-label without replacing the original.")
    relabel.add_argument("--dataset", required=True)
    relabel.add_argument("--incident", required=True)
    relabel.add_argument("--annotated-by", required=True)
    relabel.add_argument("--human-severity", choices=("IGNORE", "REVIEW", "IMPORTANT"), required=True)
    relabel.add_argument("--contact-outcome", choices=("contact", "impact", "no_contact", "uncertain"), required=True)
    relabel.add_argument("--closest-approach-time-sec", type=float)
    relabel.add_argument("--apparent-contact-time-sec", type=float)
    relabel.add_argument("--impact-time-sec", type=float)
    relabel.add_argument("--door-state", choices=("closed", "opening", "open", "closing", "not_visible", "not_applicable"))
    relabel.add_argument("--notes")
    relabel.add_argument("--minimum-delay-hours", type=float, default=24.0)
    select = subparsers.add_parser(
        "select",
        help="List incidents matching a review filter and print a ready-to-run export-encrypted command.",
    )
    select.add_argument("--session", required=True)
    select.add_argument(
        "--filter",
        choices=("reviewed", "feedback", "important", "review", "important_or_review", "all"),
        default="reviewed",
        help="reviewed = manually confirmed status or has feedback (default); feedback = has feedback only.",
    )
    select.add_argument("--feedback", default="", help="Override the default Documents\\Mimir Feedback folder.")
    select.add_argument("--recorded-by", default="")
    select.add_argument("--rights-basis", choices=("owned", "explicit_permission", "public_license"), default="")
    select.add_argument("--permission-reference", default="")
    select.add_argument("--output", default="", help="Destination ending in .mimir-dataset.age for the printed command.")
    select.add_argument("--recipient", default="")
    select.add_argument("--recipient-file", default="")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "export":
            return export_dataset(args)
        if args.command == "export-encrypted":
            return export_encrypted_dataset(args)
        if args.command == "export-feedback":
            return export_feedback_package(args)
        if args.command == "intake":
            return intake_encrypted_dataset(args)
        if args.command == "blind-relabel":
            return record_blind_relabel(args)
        if args.command == "validate":
            return validate_dataset(args)
        if args.command == "list":
            return list_annotations(args)
        if args.command == "select":
            return select_incidents(args)
        return annotate_incident(args)
    except (OSError, ValueError, json.JSONDecodeError, DatasetPackageError) as exc:
        print(f"Dataset error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
