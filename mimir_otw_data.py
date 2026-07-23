"""Prepare the CC BY 4.0 Out the Window dataset for Mimir shadow training.

OTW contains fixed-camera vehicle-door activity and hard negatives. It is
useful auxiliary supervision, but it is not physical-contact ground truth and
can never promote a Mimir contact detector by itself.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import shutil
import sys
import tarfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "mimir_otw_auxiliary_manifest_v1"
SOURCE_ID = "otw_stationary_door_activity"
SOURCE_URL = "https://stresearch.github.io/otw/"
ARCHIVE_URL = "http://bit.ly/out_the_window_dataset"
ARCHIVE_MD5 = "9096bad6ff78056b505fafb4cded1734"
LICENSE = "CC-BY-4.0"
LICENSE_URL = "https://creativecommons.org/licenses/by/4.0/"
ATTRIBUTION = (
    "Castanon, Shnidman, Anderson and Byrne, Out the Window: "
    "A Crowd-Sourced Dataset for Activity Classification in Security Video (2019)."
)
SPLIT_VERSION = "otw_video_sha256_mod5_v1"

SIDE_DOOR_LABELS = {"opening door", "closing door"}
VEHICLE_ACCESS_LABELS = {
    "opening trunk",
    "closing trunk",
    "loading vehicle",
    "unloading vehicle",
}
OTHER_ACTIVITY_LABELS = {
    "carrying (large)",
    "carrying (small)",
    "conversation",
    "dismounting bike",
    "entering",
    "exiting",
    "mounting bike",
    "pushing cart",
    "riding bike",
    "talking on phone",
    "texting on phone",
    "vehicle turning left",
    "vehicle turning right",
    "vehicle u-turn",
}
ACTION_LABELS = SIDE_DOOR_LABELS | VEHICLE_ACCESS_LABELS | OTHER_ACTIVITY_LABELS
WITHHELD_HOMES_IDS = {
    f"{value:05d}"
    for value in (16, 19, 21, 30, 40, 52, 63, 66, 67, 83, 96, 168, 214, 227, 262, 289, 377, 403, 440, 510, 540, 565, 600)
}


class OtwDataError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def md5_file(path: Path) -> str:
    digest = hashlib.md5(usedforsecurity=False)
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def deterministic_rank(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def split_for_video(collection: str, video_id: str) -> str:
    bucket = int(deterministic_rank(f"{collection}/{video_id}")[:8], 16) % 5
    return "validation_auxiliary" if bucket == 0 else "train_auxiliary"


def class_for_label(label: str) -> str | None:
    if label in SIDE_DOOR_LABELS:
        return "side_door_activity"
    if label in VEHICLE_ACCESS_LABELS:
        return "other_vehicle_access"
    if label in OTHER_ACTIVITY_LABELS:
        return "other_activity"
    return None


def repository_revision(repository_root: Path) -> str | None:
    head = repository_root / ".git" / "HEAD"
    if not head.is_file():
        return None
    value = head.read_text(encoding="utf-8").strip()
    if value.startswith("ref: "):
        reference = repository_root / ".git" / value[5:]
        return reference.read_text(encoding="utf-8").strip() if reference.is_file() else None
    return value or None


def _append_track_point(state: dict[str, Any], frame: int, box: list[int], labeled: bool) -> None:
    state["start_frame"] = min(int(state["start_frame"]), frame)
    state["end_frame"] = max(int(state["end_frame"]), frame)
    last_frame = int(state.get("last_track_frame", -1000000))
    if labeled or frame - last_frame >= 12 or not state["track"]:
        state["track"].append([frame, *box])
        state["last_track_frame"] = frame


def parse_annotations(path: Path, collection: str) -> list[dict[str, Any]]:
    if not path.is_file():
        raise OtwDataError(f"OTW annotations are missing: {path}")
    activities: dict[tuple[str, str, str], dict[str, Any]] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = csv.reader(line for line in handle if not line.startswith("#"))
        for row in rows:
            if len(row) < 10:
                continue
            label = row[3].strip().lower()
            target_class = class_for_label(label)
            if target_class is None:
                continue
            video_id = row[0].strip()
            if collection == "homes" and video_id in WITHHELD_HOMES_IDS:
                continue
            activity_id = row[1].strip()
            try:
                frame = int(row[4])
                box = [int(float(value)) for value in row[5:9]]
            except ValueError:
                continue
            if box[2] <= box[0] or box[3] <= box[1]:
                continue
            key = (video_id, activity_id, label)
            state = activities.setdefault(
                key,
                {
                    "collection": collection,
                    "video_id": video_id,
                    "activity_id": activity_id,
                    "activity_label": label,
                    "target_class": target_class,
                    "start_frame": frame,
                    "end_frame": frame,
                    "track": [],
                    "last_track_frame": -1000000,
                },
            )
            _append_track_point(state, frame, box, row[9].strip().lower() == "true")
    result: list[dict[str, Any]] = []
    for state in activities.values():
        state.pop("last_track_frame", None)
        if len(state["track"]) < 2 or state["end_frame"] <= state["start_frame"]:
            continue
        state["source_event_group"] = f"otw/{collection}/{state['video_id']}"
        state["split"] = split_for_video(collection, state["video_id"])
        state["source_role"] = "auxiliary"
        state["contact_ground_truth_available"] = False
        result.append(state)
    return result


def _select_videos(
    activities: list[dict[str, Any]],
    max_videos: int,
    positive_fraction: float,
) -> set[tuple[str, str]]:
    all_videos: dict[tuple[str, str], bool] = {}
    for item in activities:
        key = (str(item["collection"]), str(item["video_id"]))
        all_videos[key] = all_videos.get(key, False) or item["target_class"] == "side_door_activity"
    positives = sorted(
        (key for key, positive in all_videos.items() if positive),
        key=lambda key: deterministic_rank("/".join(key)),
    )
    negatives = sorted(
        (key for key, positive in all_videos.items() if not positive),
        key=lambda key: deterministic_rank("/".join(key)),
    )
    if max_videos <= 0 or max_videos >= len(all_videos):
        return set(all_videos)
    positive_limit = min(len(positives), max(1, math.ceil(max_videos * positive_fraction)))
    negative_limit = min(len(negatives), max_videos - positive_limit)
    if positive_limit + negative_limit < max_videos:
        positive_limit = min(len(positives), max_videos - negative_limit)
    return set(positives[:positive_limit] + negatives[:negative_limit])


def _balance_activities(
    activities: list[dict[str, Any]],
    max_instances_per_class: int,
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in activities:
        grouped.setdefault(str(item["target_class"]), []).append(item)
    selected: list[dict[str, Any]] = []
    for class_name, rows in grouped.items():
        rows.sort(
            key=lambda item: deterministic_rank(
                f"{item['collection']}/{item['video_id']}/{item['activity_id']}/{item['activity_label']}"
            )
        )
        selected.extend(rows[:max_instances_per_class] if max_instances_per_class > 0 else rows)
    selected.sort(
        key=lambda item: (
            item["split"],
            item["collection"],
            item["video_id"],
            int(item["start_frame"]),
        )
    )
    return selected


def catalog(args: argparse.Namespace) -> int:
    repository = Path(args.metadata_repo).resolve()
    activities: list[dict[str, Any]] = []
    annotation_files: dict[str, dict[str, str]] = {}
    for collection in ("homes", "lots"):
        path = repository / collection / "annotations.csv"
        activities.extend(parse_annotations(path, collection))
        annotation_files[collection] = {
            "path": str(path),
            "sha256": sha256_file(path),
        }
    selected_videos = _select_videos(activities, args.max_videos, args.positive_video_fraction)
    activities = [
        item
        for item in activities
        if (str(item["collection"]), str(item["video_id"])) in selected_videos
    ]
    activities = _balance_activities(activities, args.max_instances_per_class)
    used_videos = sorted(
        {(str(item["collection"]), str(item["video_id"])) for item in activities}
    )
    videos = [
        {
            "collection": collection,
            "video_id": video_id,
            "source_event_group": f"otw/{collection}/{video_id}",
            "split": split_for_video(collection, video_id),
            "archive_member": None,
            "local_path": None,
            "sha256": None,
            "extracted": False,
        }
        for collection, video_id in used_videos
    ]
    class_counts = Counter(str(item["target_class"]) for item in activities)
    split_counts = Counter(str(item["split"]) for item in activities)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "created_at": utc_now(),
        "source_id": SOURCE_ID,
        "source_url": SOURCE_URL,
        "archive_url": ARCHIVE_URL,
        "archive_md5": ARCHIVE_MD5,
        "capture_domain": "fixed_stationary_security_camera",
        "target_domain": "stationary_vehicle_mounted_camera",
        "source_role": "auxiliary",
        "promotion_eligible": False,
        "contact_ground_truth_available": False,
        "physical_contact_claim_allowed": False,
        "pixel_overlap_is_physical_contact_proof": False,
        "license": LICENSE,
        "license_url": LICENSE_URL,
        "attribution": ATTRIBUTION,
        "metadata_repository": "https://github.com/stresearch/otw",
        "metadata_revision": repository_revision(repository),
        "annotation_files": annotation_files,
        "split_assignment_version": SPLIT_VERSION,
        "selection_policy": {
            "max_videos": int(args.max_videos),
            "positive_video_fraction": float(args.positive_video_fraction),
            "max_instances_per_class": int(args.max_instances_per_class),
            "withheld_video_ids_excluded": sorted(WITHHELD_HOMES_IDS),
            "filename_text_used_as_detection_evidence": False,
        },
        "video_count": len(videos),
        "activity_count": len(activities),
        "class_counts": dict(sorted(class_counts.items())),
        "split_counts": dict(sorted(split_counts.items())),
        "videos": videos,
        "activities": activities,
    }
    output = Path(args.output).resolve()
    write_json(output, manifest)
    write_json(
        output.with_name("OTW_SOURCE_RECEIPT.json"),
        {
            "schema_version": "mimir_external_dataset_receipt_v1",
            "source_id": SOURCE_ID,
            "source_url": SOURCE_URL,
            "archive_url": ARCHIVE_URL,
            "archive_expected_md5": ARCHIVE_MD5,
            "metadata_repository": manifest["metadata_repository"],
            "metadata_revision": manifest["metadata_revision"],
            "retrieved_at": utc_now(),
            "license": LICENSE,
            "license_url": LICENSE_URL,
            "license_reviewed": True,
            "license_acceptance_required": False,
            "acquisition_basis": "Project owner requested legal and free training data",
            "attribution": ATTRIBUTION,
            "source_role": "auxiliary",
            "promotion_eligible": False,
            "contact_ground_truth_available": False,
            "automatic_upload": False,
        },
    )
    print(f"OTW auxiliary catalog written: {output}")
    print(f"videos: {len(videos)}")
    print(f"activities: {len(activities)}")
    for name, count in sorted(class_counts.items()):
        print(f"{name}: {count}")
    return 0


def _safe_member_name(name: str) -> str:
    return name.replace("\\", "/").lstrip("./")


def _member_video_key(name: str) -> tuple[str, str] | None:
    parts = _safe_member_name(name).split("/")
    for index in range(len(parts) - 2):
        collection = parts[index].lower()
        directory = parts[index + 1].lower()
        filename = parts[index + 2]
        if collection in {"homes", "lots"} and directory in {"video", "videos"}:
            if filename.lower().endswith(".mp4"):
                return collection, Path(filename).stem
    return None


def extract(args: argparse.Namespace) -> int:
    manifest_path = Path(args.manifest).resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise OtwDataError(f"Unsupported OTW manifest: {manifest_path}")
    archive = Path(args.archive).resolve()
    if not archive.is_file():
        raise OtwDataError(f"OTW archive is missing: {archive}")
    actual_md5 = md5_file(archive)
    if actual_md5 != ARCHIVE_MD5:
        raise OtwDataError(f"OTW archive MD5 mismatch: {actual_md5} != {ARCHIVE_MD5}")
    output_root = Path(args.output).resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    video_records = {
        (str(item["collection"]), str(item["video_id"])): item
        for item in manifest.get("videos", [])
    }
    pending = {
        key
        for key, item in video_records.items()
        if not (item.get("extracted") and Path(str(item.get("local_path") or "")).is_file())
    }
    processed = 0
    with tarfile.open(archive, "r:gz") as bundle:
        for member in bundle:
            if not member.isfile():
                continue
            key = _member_video_key(member.name)
            if key not in pending:
                continue
            record = video_records[key]
            destination = output_root / key[0] / "videos" / f"{key[1]}.mp4"
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_suffix(".mp4.part")
            source = bundle.extractfile(member)
            if source is None:
                raise OtwDataError(f"Could not read OTW archive member: {member.name}")
            with source, temporary.open("wb") as target:
                shutil.copyfileobj(source, target, length=1024 * 1024)
            temporary.replace(destination)
            record["archive_member"] = member.name
            record["local_path"] = str(destination)
            record["size_bytes"] = destination.stat().st_size
            record["sha256"] = sha256_file(destination)
            record["extracted"] = True
            pending.remove(key)
            processed += 1
            if processed == 1 or processed % max(1, args.checkpoint_every) == 0:
                manifest["videos"] = list(video_records.values())
                manifest["extracted_count"] = sum(
                    1 for item in video_records.values() if item.get("extracted")
                )
                write_json(manifest_path, manifest)
                print(f"extracted {processed}; remaining {len(pending)}")
            if not pending:
                break
    manifest["videos"] = list(video_records.values())
    manifest["archive_path"] = str(archive)
    manifest["archive_verified_md5"] = actual_md5
    manifest["extracted_at"] = utc_now()
    manifest["extracted_count"] = sum(1 for item in video_records.values() if item.get("extracted"))
    manifest["missing_videos"] = [
        {"collection": collection, "video_id": video_id}
        for collection, video_id in sorted(pending)
    ]
    write_json(manifest_path, manifest)
    write_json(output_root / "otw_auxiliary_manifest.json", manifest)
    if pending:
        raise OtwDataError(f"{len(pending)} selected OTW videos were not found in the archive")
    print(f"OTW selected videos extracted: {manifest['extracted_count']}")
    print(f"output: {output_root}")
    return 0


def verify(args: argparse.Namespace) -> int:
    manifest_path = Path(args.manifest).resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    failures: list[str] = []
    verified = 0
    for item in manifest.get("videos", []):
        path = Path(str(item.get("local_path") or ""))
        if not path.is_file():
            failures.append(f"missing: {item.get('collection')}/{item.get('video_id')}")
            continue
        expected = str(item.get("sha256") or "")
        actual = sha256_file(path)
        if expected and actual != expected:
            failures.append(f"checksum: {path}")
            continue
        verified += 1
    report = {
        "schema_version": "mimir_otw_integrity_report_v1",
        "generated_at": utc_now(),
        "manifest": str(manifest_path),
        "verified_videos": verified,
        "failures": failures,
        "passed": not failures,
    }
    output = Path(args.report).resolve()
    write_json(output, report)
    print(f"OTW videos verified: {verified}")
    print(f"integrity failures: {len(failures)}")
    print(f"report: {output}")
    return 0 if not failures else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    catalog_parser = subparsers.add_parser("catalog")
    catalog_parser.add_argument("--metadata-repo", required=True)
    catalog_parser.add_argument("--output", required=True)
    catalog_parser.add_argument("--max-videos", type=int, default=320)
    catalog_parser.add_argument("--positive-video-fraction", type=float, default=0.68)
    catalog_parser.add_argument("--max-instances-per-class", type=int, default=1800)
    catalog_parser.set_defaults(handler=catalog)

    extract_parser = subparsers.add_parser("extract")
    extract_parser.add_argument("--manifest", required=True)
    extract_parser.add_argument("--archive", required=True)
    extract_parser.add_argument("--output", required=True)
    extract_parser.add_argument("--checkpoint-every", type=int, default=10)
    extract_parser.set_defaults(handler=extract)

    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("--manifest", required=True)
    verify_parser.add_argument("--report", required=True)
    verify_parser.set_defaults(handler=verify)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.handler(args))
    except (OtwDataError, OSError, tarfile.TarError, json.JSONDecodeError) as exc:
        print(f"OTW data error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
