"""Safe grouped storage actions for Mimir Core v2.

This script only operates after review.

Until 2026-08-05 it also promised never to permanently delete anything, and
"trash" meant moving clips into a folder inside the Mimir Library. That was
safe and it was not what people mean by trash: nothing ever emptied the folder,
so clips stopped taking up space on the USB stick and started taking it up on
the system drive instead, permanently. The trash on this developer's machine
had reached 2.5 GB against 40 GB of free space.

So deletion is now real, and the care went into making it recoverable and
specific rather than into refusing to do it:

- The Recycle Bin is the default, so a mistake is undone the way Windows users
  already know how. A failure there is reported, never quietly downgraded to
  an unlink.
- A caller can ask for a true delete when they want the space back now.
- deletion_targets() reports exactly what would go, and how many bytes, so a
  confirmation can state facts instead of asking "are you sure?".
- Deleting reaches the clips, the source event folder, *and* the thumbnails
  Mimir generated into its own session directory -- which the old move-to-trash
  left behind.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mimir_core_v2 import footage_import, recycle_bin
from mimir_core_v2.runtime_paths import default_output_dir


BACKEND_ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = default_output_dir()
DEFAULT_SESSION_PATH = OUTPUT_DIR / "latest_session.json"
ACTION_REPORT_PATH = OUTPUT_DIR / "last_action_report.json"
ACTION_JOURNAL_PATH = OUTPUT_DIR / "storage_action_journal.json"

LIBRARY_ROOT = Path.home() / "Videos" / "Mimir Library"
TRASH_ROOT = LIBRARY_ROOT / "_Mimir Trash"
SEVERITY_FOLDERS = {
    "IMPORTANT": "Important",
    "REVIEW": "Review",
    "IGNORE": "Ignore",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def clean_text(value: Any) -> str:
    return value.strip() if isinstance(value, str) and value.strip() else ""


def normalize_severity(value: Any) -> str:
    text = clean_text(value).upper()
    return text if text in SEVERITY_FOLDERS else "REVIEW"


def normalize_id(value: Any) -> str:
    return clean_text(value)


def path_key(value: Any) -> str:
    text = clean_text(value)
    if text.startswith("\\\\?\\"):
        text = text[4:]
    return os.path.normcase(os.path.normpath(text))


def _same_folder(candidate: Path, resolved: Path) -> bool:
    """Whether two paths name the same folder, comparing them in the same form.

    The incident's own folder is resolved before this comparison; the other
    incident's is whatever the session recorded. Comparing a resolved path
    against an unresolved one means two spellings of one folder do not match:
    an 8.3 short name (`RUNNER~1` for `runneradmin`), a junction, a mapped
    drive, a symlinked temp directory.

    That mattered. When it fails to match, the sibling holding clips in the
    folder is skipped, and the folder is removed while that incident still
    needs it. It is the check that stops Mimir deleting footage a user has not
    finished with, so both sides are resolved before comparing, and an
    unresolvable path is treated as a match rather than risk clearing a folder
    that something else may still be using.
    """

    if path_key(str(candidate)) == path_key(str(resolved)):
        return True
    try:
        return path_key(str(candidate.resolve())) == path_key(str(resolved))
    except OSError:
        return True


def as_path(value: Any) -> Path | None:
    text = clean_text(value)
    if not text:
        return None
    return Path(text)


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    return data if isinstance(data, dict) else {}


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    with temp_path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)
        handle.write("\n")
    os.replace(temp_path, path)


def incident_matches(incident: dict[str, Any], requested_id: str) -> bool:
    return requested_id in {
        normalize_id(incident.get("id")),
        normalize_id(incident.get("event_group_id")),
    }


def selected_incidents(session: dict[str, Any], args: argparse.Namespace) -> list[dict[str, Any]]:
    incidents = session.get("incidents")
    if not isinstance(incidents, list):
        return []

    requested_ids: list[str] = []
    if args.incident_id:
        requested_ids.append(args.incident_id)
    if args.incident_ids:
        requested_ids.extend(item.strip() for item in args.incident_ids.split(",") if item.strip())

    if requested_ids:
        matches: list[dict[str, Any]] = []
        seen_incident_ids: set[str] = set()
        for requested_id in requested_ids:
            for incident in incidents:
                if isinstance(incident, dict) and incident_matches(incident, requested_id):
                    stable_id = normalize_id(incident.get("id")) or normalize_id(incident.get("event_group_id"))
                    if stable_id not in seen_incident_ids:
                        matches.append(incident)
                        seen_incident_ids.add(stable_id)
                    break
        return matches

    if args.status:
        requested_status = args.status.upper()
        return [
            incident
            for incident in incidents
            if isinstance(incident, dict)
            and normalize_severity(incident.get("final_severity") or incident.get("severity")) == requested_status
        ]

    return []


def camera_clips_for_incident(incident: dict[str, Any]) -> list[dict[str, Any]]:
    clips = incident.get("camera_clips")
    if isinstance(clips, list):
        return [clip for clip in clips if isinstance(clip, dict)]

    if isinstance(clips, dict):
        normalized: list[dict[str, Any]] = []
        for camera, value in clips.items():
            if isinstance(value, dict):
                clip = dict(value)
                clip.setdefault("camera", camera)
                normalized.append(clip)
            elif isinstance(value, str):
                normalized.append(
                    {
                        "camera": camera,
                        "path": value,
                        "filename": Path(value).name,
                        "exists": None,
                    }
                )
        incident["camera_clips"] = normalized
        return normalized

    incident["camera_clips"] = []
    return incident["camera_clips"]


def collect_incident_files(incident: dict[str, Any]) -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    seen_paths: set[str] = set()

    for index, clip in enumerate(camera_clips_for_incident(incident)):
        path = (
            clean_text(clip.get("path"))
            or clean_text(clip.get("library_path"))
            or clean_text(clip.get("trash_path"))
            or clean_text(clip.get("original_path"))
        )
        if not path:
            files.append(
                {
                    "kind": "camera_clip",
                    "clip_index": index,
                    "path": "",
                    "filename": clean_text(clip.get("filename")),
                    "camera": clean_text(clip.get("camera")) or "unknown",
                    "skip_reason": "missing path",
                }
            )
            continue
        key = path_key(path)
        if key in seen_paths:
            files.append(
                {
                    "kind": "camera_clip",
                    "clip_index": index,
                    "path": path,
                    "filename": clean_text(clip.get("filename")) or Path(path).name,
                    "camera": clean_text(clip.get("camera")) or "unknown",
                    "skip_reason": "duplicate path already included",
                }
            )
            continue
        seen_paths.add(key)
        files.append(
            {
                "kind": "camera_clip",
                "clip_index": index,
                "path": path,
                "filename": clean_text(clip.get("filename")) or Path(path).name,
                "camera": clean_text(clip.get("camera")) or "unknown",
            }
        )

    video_path = clean_text(incident.get("video_path"))
    if video_path and path_key(video_path) not in seen_paths:
        files.append(
            {
                "kind": "video_path",
                "clip_index": None,
                "path": video_path,
                "filename": Path(video_path).name,
                "camera": "primary",
            }
        )

    return files


def unique_destination(destination_folder: Path, filename: str, create_folder: bool) -> Path:
    if create_folder:
        destination_folder.mkdir(parents=True, exist_ok=True)
    safe_name = filename or "clip.mp4"
    candidate = destination_folder / safe_name
    if not candidate.exists():
        return candidate

    stem = candidate.stem
    suffix = candidate.suffix
    counter = 2
    while True:
        candidate = destination_folder / f"{stem}_{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def same_file_path(source: Path, destination: Path) -> bool:
    return path_key(str(source)) == path_key(str(destination))


def verified_nonzero(path: Path, expected_size: int | None = None) -> tuple[bool, str]:
    try:
        if not path.exists():
            return False, "destination does not exist"
        size = path.stat().st_size
        if size <= 0:
            return False, "destination file is empty"
        if expected_size is not None and size != expected_size:
            return False, f"destination size mismatch: expected {expected_size}, got {size}"
        return True, ""
    except OSError as exc:
        return False, str(exc)


def safe_move_file(source: Path, destination: Path, dry_run: bool) -> dict[str, Any]:
    record = {
        "source": str(source),
        "destination": str(destination),
        "ok": False,
        "dry_run": dry_run,
        "error": "",
    }

    try:
        if not source.exists():
            record["error"] = "source file does not exist"
            return record
        source_size = source.stat().st_size
        if source_size <= 0:
            record["error"] = "source file is empty"
            return record

        if same_file_path(source, destination):
            record["ok"] = True
            record["skipped"] = True
            record["reason"] = "source already at destination"
            record["size"] = source_size
            return record

        if dry_run:
            record["ok"] = True
            record["would_move"] = True
            record["size"] = source_size
            return record

        destination.parent.mkdir(parents=True, exist_ok=True)
        temp_destination = destination.with_name(f"{destination.name}.mimir_tmp")
        suffix = 2
        while temp_destination.exists():
            temp_destination = destination.with_name(f"{destination.name}.mimir_tmp_{suffix}")
            suffix += 1

        shutil.copy2(source, temp_destination)
        verified, error = verified_nonzero(temp_destination, source_size)
        if not verified:
            try:
                temp_destination.unlink(missing_ok=True)
            except OSError:
                pass
            record["error"] = f"copy verification failed: {error}"
            return record

        os.replace(temp_destination, destination)
        verified, error = verified_nonzero(destination, source_size)
        if not verified:
            record["error"] = f"final verification failed: {error}"
            return record

        source.unlink()
        record["ok"] = True
        record["size"] = source_size
        return record
    except OSError as exc:
        record["error"] = str(exc)
        return record


# TeslaCam gives each Sentry/Saved event its own uniquely-timestamped folder
# holding only that event's camera clips -- safe to remove entirely once its
# clips are in Trash. RecentClips is a flat, continuously-recorded folder
# shared by many unrelated moments, and any other category is unrecognized
# structure -- removing the whole folder there could destroy footage that has
# nothing to do with this incident, so those are deliberately excluded.
FOLDER_PER_EVENT_CATEGORIES = {"SentryClips", "SavedClips"}


def source_folder_removal_eligibility(session: dict[str, Any], incident: dict[str, Any]) -> tuple[Path | None, str]:
    """Decides whether it's safe to delete an incident's whole source event
    folder from removable media after its clips are safely in Trash.

    Returns (folder, "") if it's safe to remove, or (None, reason) if not --
    callers should skip removal (not fail the action) when reason is set.
    """
    category = clean_text(incident.get("source_category"))
    if category not in FOLDER_PER_EVENT_CATEGORIES:
        return None, f"source category '{category or 'unknown'}' is not a folder-per-event layout"

    folder = as_path(clean_text(incident.get("event_folder")))
    if folder is None:
        return None, "incident has no recorded source folder"
    try:
        resolved = folder.resolve()
    except OSError as exc:
        return None, f"could not resolve source folder: {exc}"
    if not resolved.is_dir():
        return None, "source folder no longer exists"
    if resolved.parent == resolved:
        return None, "refusing to remove a drive root"

    library_root = LIBRARY_ROOT.resolve()
    if resolved == library_root or library_root in resolved.parents or resolved in library_root.parents:
        return None, "refusing to remove a folder inside Mimir's own library"

    this_id = normalize_id(incident.get("id")) or normalize_id(incident.get("event_group_id"))
    for other in session.get("incidents") or []:
        if not isinstance(other, dict):
            continue
        other_id = normalize_id(other.get("id")) or normalize_id(other.get("event_group_id"))
        if other_id == this_id:
            continue
        other_folder = as_path(clean_text(other.get("event_folder")))
        if other_folder is None or not _same_folder(other_folder, resolved):
            continue
        # An incident whose clips have already been trashed or deleted no
        # longer has anything in this folder to protect.
        #
        # Without this the folder could never go. A Tesla event folder holds
        # several minute-long clips, and Mimir groups on (folder, timestamp),
        # so one folder routinely becomes two or three incidents -- on this
        # developer's drive the very first one checked was shared. Each of them
        # would forever point at a sibling that "still needs" the folder, so
        # the user empties every incident out of it and the folder, its
        # event.json and its thumbnail stay on the card regardless.
        if _incident_files_already_removed(other):
            continue
        return None, "another incident's clips share this source folder"

    return resolved, ""


def _incident_files_already_removed(incident: dict[str, Any]) -> bool:
    """True when this incident no longer has footage in its source folder."""

    if incident.get("user_deleted"):
        return True
    return clean_text(incident.get("storage_state")).lower() in {"trash", "library", "deleted"}


def remove_incident_source_folder(session: dict[str, Any], incident: dict[str, Any], dry_run: bool) -> dict[str, Any]:
    folder, reason = source_folder_removal_eligibility(session, incident)
    result = {
        "incident_id": incident.get("id"),
        "event_group_id": incident.get("event_group_id"),
        "folder": str(folder) if folder else clean_text(incident.get("event_folder")),
        "removed": False,
        "reason": reason,
    }
    if folder is None:
        return result
    if dry_run:
        result["would_remove"] = True
        return result
    try:
        shutil.rmtree(folder)
        result["removed"] = True
    except OSError as exc:
        result["reason"] = f"could not remove folder: {exc}"
    return result


def destination_folder_for_incident(incident: dict[str, Any], action: str) -> Path:
    if action == "restore_from_trash":
        original = as_path(incident.get("original_source_video"))
        if original is not None and original.parent:
            return original.parent
        return LIBRARY_ROOT / "Restored"
    if action == "move_to_trash":
        return TRASH_ROOT

    severity = normalize_severity(incident.get("final_severity") or incident.get("severity"))
    return LIBRARY_ROOT / SEVERITY_FOLDERS[severity]


def destination_for_file(
    incident: dict[str, Any],
    file_record: dict[str, Any],
    action: str,
    create_folder: bool,
) -> Path:
    if action != "restore_from_trash":
        folder = destination_folder_for_incident(incident, action)
        return unique_destination(folder, file_record["filename"], create_folder=create_folder)

    original_path = ""
    if file_record.get("kind") == "camera_clip" and file_record.get("clip_index") is not None:
        clips = camera_clips_for_incident(incident)
        index = int(file_record["clip_index"])
        if 0 <= index < len(clips):
            original_path = clean_text(clips[index].get("original_path"))
    if not original_path and file_record.get("kind") == "video_path":
        original_path = clean_text(incident.get("original_source_video"))
    original = as_path(original_path)
    if original is not None:
        return unique_destination(original.parent, original.name, create_folder=create_folder)
    folder = LIBRARY_ROOT / "Restored"
    return unique_destination(folder, file_record["filename"], create_folder=create_folder)


def apply_file_update(
    incident: dict[str, Any],
    file_record: dict[str, Any],
    moved_path: str,
    action: str,
) -> None:
    original_path = clean_text(file_record.get("path") or file_record.get("source"))
    if file_record["kind"] == "camera_clip" and file_record["clip_index"] is not None:
        clips = camera_clips_for_incident(incident)
        index = int(file_record["clip_index"])
        if 0 <= index < len(clips):
            clip = clips[index]
            clip.setdefault("original_path", original_path)
            clip["path"] = moved_path
            clip["exists"] = True
            clip["storage_state"] = (
                "trash" if action == "move_to_trash" else "source" if action == "restore_from_trash" else "library"
            )
            if action == "move_to_trash":
                clip["trash_path"] = moved_path
            elif action == "restore_from_trash":
                clip["trash_path"] = None
            else:
                clip["library_path"] = moved_path


def choose_primary_moved_path(
    incident: dict[str, Any],
    move_results: list[dict[str, Any]],
    action: str,
) -> str:
    original_video_key = path_key(incident.get("video_path"))
    for result in move_results:
        if result.get("ok") and path_key(result.get("source")) == original_video_key:
            return clean_text(result.get("destination"))

    primary_camera = clean_text(incident.get("primary_camera")).lower()
    for result in move_results:
        if result.get("ok") and clean_text(result.get("camera")).lower() == primary_camera:
            return clean_text(result.get("destination"))

    for result in move_results:
        if result.get("ok"):
            return clean_text(result.get("destination"))

    if action == "move_to_trash":
        return clean_text(incident.get("trash_video_path")) or clean_text(incident.get("video_path"))
    return clean_text(incident.get("library_video_path")) or clean_text(incident.get("video_path"))


def update_incident_storage(
    incident: dict[str, Any],
    action: str,
    destination_folder: Path,
    move_results: list[dict[str, Any]],
    failures: list[dict[str, Any]],
) -> None:
    successful_results = [
        result
        for result in move_results
        if result.get("ok")
        and not result.get("dry_run")
        and (not result.get("skipped") or result.get("reason") == "source already at destination")
        and clean_text(result.get("destination"))
    ]
    failed_for_incident = [
        failure
        for failure in failures
        if failure.get("incident_id") == incident.get("id")
        or failure.get("event_group_id") == incident.get("event_group_id")
    ]
    complete = bool(successful_results) and not failed_for_incident
    partial = bool(successful_results) and bool(failed_for_incident)
    target_state = "trash" if action == "move_to_trash" else "source" if action == "restore_from_trash" else "library"

    for result in successful_results:
        apply_file_update(incident, result, clean_text(result.get("destination")), action)

    primary_moved_path = choose_primary_moved_path(incident, successful_results, action)
    original_video = clean_text(incident.get("original_source_video")) or clean_text(incident.get("video_path"))
    if original_video:
        incident["original_source_video"] = original_video
    if primary_moved_path:
        incident["video_path"] = primary_moved_path

    incident["storage_action_applied"] = bool(successful_results)
    incident["video_exists"] = bool(primary_moved_path and Path(primary_moved_path).exists())
    if action == "move_to_trash":
        incident["trash_folder"] = str(destination_folder)
        incident["trash_video_path"] = primary_moved_path
        incident["user_deleted"] = complete
        incident["moved_to_library"] = False
    elif action == "restore_from_trash":
        incident["trash_video_path"] = None
        incident["user_deleted"] = False
        incident["moved_to_library"] = False
        incident["library_video_path"] = None
    else:
        incident["library_folder"] = str(destination_folder)
        incident["library_video_path"] = primary_moved_path
        incident["moved_to_library"] = complete
        incident["user_deleted"] = False

    if complete:
        incident["storage_state"] = target_state
    elif partial:
        incident["storage_state"] = f"partial_{target_state}"
    elif failed_for_incident:
        incident["storage_state"] = clean_text(incident.get("storage_state")) or "source"


def build_report(action: str, dry_run: bool, selected: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "ok": False,
        "action": f"{action}_dry_run" if dry_run else action,
        "dry_run": dry_run,
        "timestamp": now_iso(),
        "selected_incidents": [
            {
                "id": incident.get("id"),
                "event_group_id": incident.get("event_group_id"),
                "final_severity": incident.get("final_severity") or incident.get("severity"),
                "camera_count": incident.get("camera_count"),
                "camera_clip_count": len(camera_clips_for_incident(incident)),
            }
            for incident in selected
        ],
        "moved_files": [],
        "failed_files": [],
        "skipped_files": [],
        "incident_file_accounting": [],
        "updated_session_path": str(DEFAULT_SESSION_PATH),
        "library_folder": str(LIBRARY_ROOT),
        "trash_folder": str(TRASH_ROOT),
        "failures": [],
        "source_folders_removed": [],
    }


def append_incident_file_accounting(
    report: dict[str, Any],
    incident: dict[str, Any],
    file_records: list[dict[str, Any]],
) -> None:
    incident_id = incident.get("id")
    event_group_id = incident.get("event_group_id")
    expected_camera_clip_count = len(camera_clips_for_incident(incident))
    selected_records = [
        record
        for record in (
            report["moved_files"] + report["failed_files"] + report["skipped_files"]
        )
        if record.get("incident_id") == incident_id
        or record.get("event_group_id") == event_group_id
    ]
    accounted_camera_clip_count = sum(
        1 for record in selected_records if record.get("kind") == "camera_clip"
    )
    accounted_total = len(selected_records)
    extra_video_path_count = sum(
        1 for record in selected_records if record.get("kind") == "video_path"
    )
    validation_ok = accounted_camera_clip_count == expected_camera_clip_count

    accounting = {
        "incident_id": incident_id,
        "event_group_id": event_group_id,
        "camera_count": incident.get("camera_count"),
        "expected_camera_clip_count": expected_camera_clip_count,
        "planned_unique_file_count": len(file_records),
        "accounted_camera_clip_count": accounted_camera_clip_count,
        "extra_video_path_count": extra_video_path_count,
        "accounted_total": accounted_total,
        "validation_ok": validation_ok,
    }
    report["incident_file_accounting"].append(accounting)

    if not validation_ok:
        report["failures"].append(
            {
                "incident_id": incident_id,
                "event_group_id": event_group_id,
                "error": (
                    f"grouped incident expected {expected_camera_clip_count} camera clip records "
                    f"but action report accounted for {accounted_camera_clip_count}"
                ),
            }
        )


def perform_action(
    session: dict[str, Any],
    selected: list[dict[str, Any]],
    action: str,
    dry_run: bool,
    journal_path: Path | None = None,
) -> dict[str, Any]:
    report = build_report(action, dry_run, selected)
    report["transaction_id"] = f"storage-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    report["transaction_state"] = "planning"
    report["rollback_files"] = []
    incident_results: dict[str, list[dict[str, Any]]] = {}
    selected_keys = {
        normalize_id(incident.get("id")) or normalize_id(incident.get("event_group_id")): incident
        for incident in selected
    }

    if not selected:
        report["failures"].append({"error": "no incidents matched the selection"})
        return report

    if journal_path is not None and not dry_run:
        write_json(journal_path, report)

    for incident_key, incident in selected_keys.items():
        file_records = collect_incident_files(incident)
        incident_results[incident_key] = []

        if not file_records:
            failure = {
                "incident_id": incident.get("id"),
                "event_group_id": incident.get("event_group_id"),
                "error": "incident has no camera clip paths or video_path",
            }
            report["failed_files"].append(failure)
            report["failures"].append(failure)
            append_incident_file_accounting(report, incident, file_records)
            continue

        for file_record in file_records:
            if file_record.get("skip_reason"):
                skipped = {
                    "incident_id": incident.get("id"),
                    "event_group_id": incident.get("event_group_id"),
                    "path": file_record.get("path", ""),
                    "destination": "",
                    "ok": True,
                    "skipped": True,
                    "dry_run": dry_run,
                    "camera": file_record.get("camera"),
                    "kind": file_record.get("kind"),
                    "clip_index": file_record.get("clip_index"),
                    "reason": file_record["skip_reason"],
                }
                report["skipped_files"].append(skipped)
                incident_results[incident_key].append(skipped)
                continue

            source = as_path(file_record["path"])
            if source is None:
                skipped = {
                    "incident_id": incident.get("id"),
                    "event_group_id": incident.get("event_group_id"),
                    "path": file_record["path"],
                    "destination": "",
                    "ok": True,
                    "skipped": True,
                    "dry_run": dry_run,
                    "camera": file_record.get("camera"),
                    "kind": file_record.get("kind"),
                    "clip_index": file_record.get("clip_index"),
                    "reason": "missing path",
                }
                report["skipped_files"].append(skipped)
                incident_results[incident_key].append(skipped)
                continue

            destination = destination_for_file(
                incident,
                file_record,
                action,
                create_folder=not dry_run,
            )
            result = safe_move_file(source, destination, dry_run)
            result.update(
                {
                    "incident_id": incident.get("id"),
                    "event_group_id": incident.get("event_group_id"),
                    "camera": file_record.get("camera"),
                    "kind": file_record.get("kind"),
                    "clip_index": file_record.get("clip_index"),
                }
            )
            incident_results[incident_key].append(result)

            if result.get("ok") and result.get("skipped"):
                report["skipped_files"].append(result)
            elif result.get("ok"):
                report["moved_files"].append(result)
            else:
                report["failed_files"].append(result)
                report["failures"].append(result)
            if journal_path is not None and not dry_run:
                report["transaction_state"] = "moving"
                write_json(journal_path, report)

        append_incident_file_accounting(report, incident, file_records)

    if not dry_run and report["failures"]:
        report["transaction_state"] = "rolling_back"
        for moved in reversed(report["moved_files"]):
            destination = as_path(moved.get("destination"))
            source = as_path(moved.get("source"))
            if destination is None or source is None or not destination.exists():
                continue
            rollback = safe_move_file(destination, source, False)
            rollback["original_move"] = moved
            report["rollback_files"].append(rollback)
        rollback_failed = any(not item.get("ok") for item in report["rollback_files"])
        report["transaction_state"] = "rollback_incomplete" if rollback_failed else "rolled_back"
        if journal_path is not None:
            write_json(journal_path, report)
        report["ok"] = False
        return report

    if not dry_run:
        failures = report["failures"]
        for incident_key, results in incident_results.items():
            incident = selected_keys[incident_key]
            update_incident_storage(incident, action, destination_folder_for_incident(incident, action), results, failures)

        # Only once every clip for every selected incident is confirmed safely
        # in Trash (no failures anywhere in this batch -- perform_action rolls
        # the whole batch back above if any file failed) does it become safe
        # to also clear the now-redundant source folder off the USB, so a
        # trashed incident leaves nothing behind on the card. Clips already
        # live on in Trash regardless; this only ever removes the source
        # location, per source_folder_removal_eligibility's guards.
        if action == "move_to_trash" and not failures:
            for incident_key, results in incident_results.items():
                incident = selected_keys[incident_key]
                if not results or not all(item.get("ok") for item in results):
                    continue
                report["source_folders_removed"].append(remove_incident_source_folder(session, incident, dry_run))

    report["ok"] = len(report["failures"]) == 0
    report["transaction_state"] = "dry_run" if dry_run else "committed"
    if journal_path is not None and not dry_run:
        write_json(journal_path, report)
    return report


def derived_artifacts_for_incident(incident: dict[str, Any]) -> list[Path]:
    """Thumbnails and contact sheets Mimir generated for this incident.

    These live in the session output directory, not beside the footage, so
    removing the event folder leaves them behind. Individually they are a few
    kilobytes; across a 656-incident session they are not, and leaving them
    means a "deleted" incident still has a picture of itself on disk.
    """

    seen: set[str] = set()
    artifacts: list[Path] = []
    for key in ("thumbnail", "hero_thumbnail", "contact_sheet"):
        path = as_path(clean_text(incident.get(key)))
        if path is None:
            continue
        key_text = path_key(str(path))
        if key_text in seen:
            continue
        seen.add(key_text)
        artifacts.append(path)
    return artifacts


def deletion_targets(session: dict[str, Any], incident: dict[str, Any]) -> dict[str, Any]:
    """Everything one incident owns, and what deleting it would actually reach.

    Separated from the deleting so a confirmation dialog can state the real
    numbers. "Delete 12 clips (2.3 GB)" is a decision someone can make; "Are
    you sure?" is not.
    """

    clips: list[Path] = []
    for record in collect_incident_files(incident):
        path = as_path(record.get("path"))
        if path is not None and path.exists():
            clips.append(path)

    folder, folder_reason = source_folder_removal_eligibility(session, incident)
    artifacts = [path for path in derived_artifacts_for_incident(incident) if path.exists()]

    total = 0
    for path in clips + artifacts:
        try:
            total += path.stat().st_size
        except OSError:
            pass
    if folder is not None:
        for child in folder.rglob("*"):
            try:
                if child.is_file():
                    total += child.stat().st_size
            except OSError:
                pass

    return {
        "incident_id": incident.get("id"),
        "event_group_id": incident.get("event_group_id"),
        "clips": [str(path) for path in clips],
        "source_folder": str(folder) if folder is not None else "",
        "source_folder_reason": folder_reason,
        "derived_artifacts": [str(path) for path in artifacts],
        "bytes": total,
    }


def _remove_paths(paths: list[Path], use_recycle_bin: bool) -> list[dict[str, Any]]:
    """Remove paths, either to the Recycle Bin or for real.

    A Recycle Bin failure is reported, never silently downgraded to an unlink.
    Someone who chose the recoverable option has to be able to trust that they
    got it -- quietly hard-deleting on their behalf is the one behaviour that
    would make this feature worse than not having it.
    """

    if use_recycle_bin:
        if not recycle_bin.available():
            return [
                {"path": str(path), "ok": False, "reason": "the Recycle Bin is only available on Windows"}
                for path in paths
            ]
        return [
            {"path": result.path, "ok": result.ok, "reason": result.reason, "recycled": result.ok}
            for result in recycle_bin.send_to_recycle_bin(paths)
        ]

    results: list[dict[str, Any]] = []
    for path in paths:
        if not path.exists():
            results.append({"path": str(path), "ok": True, "reason": "already gone", "recycled": False})
            continue
        try:
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
            results.append({"path": str(path), "ok": True, "reason": "", "recycled": False})
        except OSError as exc:
            results.append({"path": str(path), "ok": False, "reason": str(exc), "recycled": False})
    return results


def delete_incidents(
    session: dict[str, Any],
    incidents: list[dict[str, Any]],
    use_recycle_bin: bool,
    dry_run: bool,
) -> dict[str, Any]:
    """Delete the footage and artifacts for the given incidents.

    Unlike move_to_trash this is not reversible by Mimir. With the Recycle Bin
    it stays reversible by Windows; without it, it is gone.

    Note there is no batch rollback here, unlike perform_action. Rolling back a
    delete would mean recreating files that no longer exist, so a partial
    failure is reported honestly and the incident keeps whatever survived,
    rather than pretending the whole batch failed.
    """

    report = {
        "action": "delete_permanently",
        "generated_at": now_iso(),
        "dry_run": dry_run,
        "recycle_bin": use_recycle_bin,
        "requested": len(incidents),
        "incidents": [],
        "failures": [],
        "bytes_deleted": 0,
        "ok": True,
    }

    for incident in incidents:
        targets = deletion_targets(session, incident)
        entry = dict(targets)
        entry["results"] = []

        if dry_run:
            entry["would_delete"] = True
            report["incidents"].append(entry)
            report["bytes_deleted"] += targets["bytes"]
            continue

        paths = [Path(p) for p in targets["clips"]]
        paths.extend(Path(p) for p in targets["derived_artifacts"])
        if targets["source_folder"]:
            paths.append(Path(targets["source_folder"]))

        results = _remove_paths(paths, use_recycle_bin)
        entry["results"] = results

        failed = [item for item in results if not item.get("ok")]
        for item in failed:
            failure = dict(item)
            failure["incident_id"] = incident.get("id")
            report["failures"].append(failure)

        if not failed:
            report["bytes_deleted"] += targets["bytes"]
            incident["user_deleted"] = True
            incident["storage_state"] = "deleted"
            incident["deleted_at"] = now_iso()
            incident["deleted_to_recycle_bin"] = use_recycle_bin
            incident["video_exists"] = False
            incident["trash_video_path"] = None

        report["incidents"].append(entry)

    report["ok"] = not report["failures"]
    return report


def trash_contents() -> dict[str, Any]:
    """What is sitting in Mimir Trash right now."""

    entries: list[dict[str, Any]] = []
    total = 0
    if TRASH_ROOT.is_dir():
        for path in sorted(TRASH_ROOT.rglob("*")):
            if not path.is_file():
                continue
            try:
                size = path.stat().st_size
            except OSError:
                size = 0
            total += size
            entries.append({"path": str(path), "name": path.name, "bytes": size})
    return {"root": str(TRASH_ROOT), "files": entries, "count": len(entries), "bytes": total}


def empty_trash(use_recycle_bin: bool, dry_run: bool) -> dict[str, Any]:
    """Empty the Mimir Trash folder.

    Trash was a one-way door: clips went in and nothing ever took them out, so
    the folder only grew. On this developer's machine it had reached 2.5 GB on
    a system drive with 40 GB free, which is how a feature meant to free space
    ends up consuming it.
    """

    contents = trash_contents()
    report = {
        "action": "empty_trash",
        "generated_at": now_iso(),
        "dry_run": dry_run,
        "recycle_bin": use_recycle_bin,
        "root": contents["root"],
        "files_found": contents["count"],
        "bytes_found": contents["bytes"],
        "results": [],
        "failures": [],
        "ok": True,
    }

    resolved = TRASH_ROOT.resolve() if TRASH_ROOT.exists() else TRASH_ROOT
    library = LIBRARY_ROOT.resolve() if LIBRARY_ROOT.exists() else LIBRARY_ROOT
    # Emptying a folder is one configuration mistake away from emptying a drive.
    # --library-root is user-supplied, so this is not paranoia.
    if resolved == library or library not in resolved.parents:
        report["ok"] = False
        report["failures"].append(
            {"path": str(resolved), "ok": False, "reason": "refusing to empty a folder that is not inside the Mimir Library"}
        )
        return report
    if resolved.parent == resolved:
        report["ok"] = False
        report["failures"].append({"path": str(resolved), "ok": False, "reason": "refusing to empty a drive root"})
        return report

    if not TRASH_ROOT.is_dir() or not contents["count"]:
        return report

    if dry_run:
        report["would_delete"] = contents["count"]
        return report

    # The entries directly under the trash root, so one call handles a folder
    # and everything inside it.
    top_level = sorted(TRASH_ROOT.iterdir())
    results = _remove_paths(top_level, use_recycle_bin)
    report["results"] = results
    report["failures"] = [item for item in results if not item.get("ok")]
    report["ok"] = not report["failures"]
    report["bytes_deleted"] = contents["bytes"] if report["ok"] else 0
    return report


def print_incidents(session: dict[str, Any]) -> int:
    incidents = session.get("incidents")
    if not isinstance(incidents, list):
        print("No incidents found in latest_session.json.")
        return 1

    print("id | event_timestamp | final_severity | camera_count | storage_state | camera_clip_count")
    print("-" * 98)
    for incident in incidents:
        if not isinstance(incident, dict):
            continue
        clip_count = len(camera_clips_for_incident(incident))
        print(
            f"{incident.get('id', '')} | "
            f"{incident.get('event_timestamp', '')} | "
            f"{incident.get('final_severity') or incident.get('severity') or ''} | "
            f"{incident.get('camera_count', '')} | "
            f"{incident.get('storage_state', 'source')} | "
            f"{clip_count}"
        )
    return 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Safe grouped storage actions for Mimir Core v2.")
    parser.add_argument("--session", default=str(DEFAULT_SESSION_PATH), help="Path to Mimir Core v2 latest_session.json.")
    parser.add_argument("--incident-id", help="Incident id or event_group_id to process.")
    parser.add_argument("--incident-ids", help="Comma-separated incident ids or event_group_ids to process.")
    parser.add_argument("--status", choices=["IMPORTANT", "REVIEW", "IGNORE", "important", "review", "ignore"], help="Process all incidents with this final severity.")
    parser.add_argument("--move-to-library", action="store_true", help="Move selected incidents into Mimir Library.")
    parser.add_argument("--move-to-trash", action="store_true", help="Move selected incidents into Mimir Trash.")
    parser.add_argument("--restore-from-trash", action="store_true", help="Restore selected incidents from Mimir Trash.")
    parser.add_argument("--delete-permanently", action="store_true", help="Delete selected incidents: clips, source event folder, and generated thumbnails.")
    parser.add_argument("--empty-trash", action="store_true", help="Remove everything currently in Mimir Trash.")
    parser.add_argument("--list-trash", action="store_true", help="Report what is in Mimir Trash without changing anything.")
    parser.add_argument("--import-footage", default="", help="Copy footage from this folder into the library before scanning.")
    parser.add_argument("--import-destination", default="", help="Where imported footage lands. Defaults to <library>/Footage.")
    parser.add_argument(
        "--clear-source",
        action="store_true",
        help="After every file is copied and verified byte for byte, remove it from the source drive.",
    )
    # Recoverable by default. Choosing to bypass the Recycle Bin has to be
    # something a caller says out loud, not something it gets by omission.
    parser.add_argument(
        "--no-recycle-bin",
        action="store_true",
        help="Delete for real instead of sending to the Recycle Bin. Frees space immediately; nothing is recoverable.",
    )
    parser.add_argument("--report", default="", help="Optional action report path. Defaults beside the selected session.")
    parser.add_argument("--journal", default="", help="Optional transaction journal path. Defaults beside the selected session.")
    parser.add_argument("--library-root", default="", help="Optional Mimir Library root override.")
    parser.add_argument("--dry-run", action="store_true", help="Report planned moves without moving files or updating latest_session.json.")
    parser.add_argument("--list-incidents", action="store_true", help="List incidents from latest_session.json.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    global LIBRARY_ROOT, TRASH_ROOT
    args = parse_args(argv or sys.argv[1:])
    session_path = Path(args.session)
    if args.library_root:
        LIBRARY_ROOT = Path(args.library_root)
        TRASH_ROOT = LIBRARY_ROOT / "_Mimir Trash"
    report_path = Path(args.report) if args.report else session_path.parent / "last_action_report.json"
    journal_path = Path(args.journal) if args.journal else session_path.parent / "storage_action_journal.json"

    use_recycle_bin = not args.no_recycle_bin

    # Handled before the session is loaded, because they do not use one.
    #
    # These act on a folder, not on selected incidents. Requiring a readable
    # session anyway made "Empty Mimir Trash" fail with "Session file not
    # found" on any machine whose latest_session.json is missing -- a fresh
    # install, or a user whose scans went to a different output directory --
    # and the desktop app does not pass --session for these at all, so it hit
    # the default path every time. Caught by running the packaged binary
    # rather than the source.
    if args.list_trash:
        print(json.dumps(trash_contents(), indent=2))
        return 0

    # Also handled before the session load: an import happens before any scan
    # exists, so there is no session to read.
    if args.import_footage:
        destination = (
            Path(args.import_destination) if args.import_destination else LIBRARY_ROOT / "Footage"
        )
        report = footage_import.import_footage(
            Path(args.import_footage),
            destination,
            remove_source=args.clear_source,
            dry_run=args.dry_run,
            on_progress=footage_import.emit_progress,
        )
        try:
            write_json(report_path, report)
        except OSError as exc:
            print(f"Import finished but report writing failed: {exc}")
            return 1
        print(json.dumps(report, indent=2))
        return 0 if report["ok"] else 1

    if args.empty_trash:
        report = empty_trash(use_recycle_bin, args.dry_run)
        try:
            write_json(report_path, report)
        except OSError as exc:
            print(f"Trash emptied but report writing failed: {exc}")
            return 1
        print(json.dumps(report, indent=2))
        return 0 if report["ok"] else 1

    try:
        session = load_json(session_path)
    except FileNotFoundError:
        print(f"Session file not found: {session_path}")
        return 1
    except json.JSONDecodeError as exc:
        print(f"Session file is not valid JSON: {exc}")
        return 1
    except OSError as exc:
        print(f"Could not read session file: {exc}")
        return 1

    if args.list_incidents:
        return print_incidents(session)

    if args.delete_permanently:
        if not (args.incident_id or args.incident_ids or args.status):
            print("Select incidents with --incident-id, --incident-ids, or --status.")
            return 1
        selected = selected_incidents(session, args)
        report = delete_incidents(session, selected, use_recycle_bin, args.dry_run)
        report["updated_session_path"] = str(session_path)
        try:
            # Written whenever anything was deleted, including a partial batch:
            # the session must not keep claiming a clip exists once its file is
            # gone, or the viewer offers playback of nothing.
            if not args.dry_run and report["incidents"]:
                write_json(session_path, session)
            write_json(report_path, report)
        except OSError as exc:
            print(f"Delete completed but report/session writing failed: {exc}")
            return 1
        print(json.dumps(report, indent=2))
        return 0 if report["ok"] else 1

    requested_actions = [args.move_to_library, args.move_to_trash, args.restore_from_trash]
    if sum(1 for selected in requested_actions if selected) != 1:
        print(
            "Choose exactly one action: --move-to-library, --move-to-trash, --restore-from-trash, "
            "--delete-permanently, or --empty-trash."
        )
        return 1

    if not (args.incident_id or args.incident_ids or args.status):
        print("Select incidents with --incident-id, --incident-ids, or --status.")
        return 1

    action = "restore_from_trash" if args.restore_from_trash else "move_to_trash" if args.move_to_trash else "move_to_library"
    selected = selected_incidents(session, args)
    session_before = copy.deepcopy(session)
    report = perform_action(session, selected, action, args.dry_run, journal_path=journal_path)
    report["updated_session_path"] = str(session_path)

    try:
        if not args.dry_run and report.get("transaction_state") == "committed" and report["moved_files"]:
            write_json(session_path, session)
        elif report.get("transaction_state") in {"rolled_back", "rollback_incomplete"}:
            session = session_before
        write_json(report_path, report)
    except OSError as exc:
        print(f"Action completed but report/session writing failed: {exc}")
        return 1

    print(json.dumps(report, indent=2))
    print(f"Action report saved: {report_path}")
    if args.dry_run:
        print("Dry-run only. No files were moved and latest_session.json was not updated.")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
