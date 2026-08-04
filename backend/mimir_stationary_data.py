"""Acquire legally usable stationary-camera auxiliary video for Mimir training.

This tool deliberately keeps external surveillance data separate from consented
Tesla Sentry footage. MEVA can teach vehicle-door and person/vehicle activity,
but it is never treated as proof of contact with the ego vehicle.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import http.client
import json
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "mimir_stationary_auxiliary_manifest_v1"
SPLIT_ASSIGNMENT_VERSION = "physical_event_sha256_mod5_v1"
SOURCE_ID = "meva_kf1_vehicle_activity"
GIB = 1024 ** 3
MEVA_BUCKET = "https://mevadata-public-01.s3.amazonaws.com"
MEVA_REPOSITORY = "https://gitlab.kitware.com/meva/meva-data-repo.git"
MEVA_LICENSE_URL = "https://mevadata.org/resources/MEVA-data-license.txt"
MEVA_README_URL = "https://mevadata.org/resources/README-meva-kf1-data.html"

DOOR_ACTIVITY_LABELS = {
    "person_opens_vehicle_door",
    "person_closes_vehicle_door",
    "person_enters_vehicle",
    "person_exits_vehicle",
    "person_loads_vehicle",
    "person_unloads_vehicle",
}

HARD_NEGATIVE_LABELS = {
    "person_talks_to_person",
    "person_talks_on_phone",
    "person_texts_on_phone",
    "person_carries_heavy_object",
    "person_picks_up_object",
    "person_puts_down_object",
    "person_rides_bicycle",
    "person_sits_down",
    "person_stands_up",
    "vehicle_makes_u_turn",
    "vehicle_reverses",
    "vehicle_starts",
    "vehicle_stops",
    "vehicle_turns_left",
    "vehicle_turns_right",
}

SUSPICIOUS_ACTIVITY_LABELS = {
    "person_abandons_package",
    "person_steals_object",
}

ACTIVITY_PATTERNS = (
    re.compile(r"!!set\s*\{([A-Za-z0-9_]+)\s*:"),
    re.compile(r"[\"']?act2[\"']?\s*:\s*\{\s*[\"']?([A-Za-z0-9_]+)[\"']?\s*:"),
)
VIDEO_NAME_PATTERN = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})\."
    r"(?P<start>\d{2}-\d{2}-\d{2})\."
    r"(?P<end>\d{2}-\d{2}-\d{2})\."
    r"(?P<site>[^.]+)\."
    r"(?P<camera>[^.]+)\.activities\.yml$"
)


class StationaryDataError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    temporary.replace(path)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def deterministic_rank(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def assign_source_isolated_splits(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Assign every camera from one physical event to the same stable split."""

    assigned: list[dict[str, Any]] = []
    group_splits: dict[str, str] = {}
    for record in records:
        group = str(record.get("source_event_group") or record.get("video_name") or "")
        if group not in group_splits:
            bucket = int(deterministic_rank(group)[:8], 16) % 5
            group_splits[group] = "validation_auxiliary" if bucket == 0 else "train_auxiliary"
        updated = dict(record)
        updated.setdefault("source_split", str(record.get("split") or ""))
        updated["split"] = group_splits[group]
        updated["split_assignment_version"] = SPLIT_ASSIGNMENT_VERSION
        assigned.append(updated)
    return assigned


def repository_revision(repository_root: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(repository_root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return result.stdout.strip() or None


def read_activity_labels(path: Path) -> Counter[str]:
    text = path.read_text(encoding="utf-8", errors="replace")
    labels: list[str] = []
    for pattern in ACTIVITY_PATTERNS:
        labels.extend(pattern.findall(text))
    return Counter(labels)


def source_record(annotation_path: Path, split: str, labels: Counter[str]) -> dict[str, Any]:
    match = VIDEO_NAME_PATTERN.match(annotation_path.name)
    if not match:
        raise StationaryDataError(f"Unsupported MEVA annotation filename: {annotation_path}")
    values = match.groupdict()
    stem = annotation_path.name.removesuffix(".activities.yml")
    video_name = f"{stem}.r13.avi"
    key = f"drops-123-r13/{values['date']}/{values['start'][:2]}/{video_name}"
    selected = sorted(label for label in labels if label in DOOR_ACTIVITY_LABELS)
    suspicious = sorted(label for label in labels if label in SUSPICIOUS_ACTIVITY_LABELS)
    if selected:
        role = "door_activity"
    elif suspicious:
        role = "suspicious_activity"
    else:
        role = "hard_negative"
    event_group = f"{values['date']}.{values['start']}.{values['end']}"
    annotation_root = annotation_path.parent
    return {
        "source_id": SOURCE_ID,
        "source_role": "auxiliary",
        "promotion_eligible": False,
        "contact_ground_truth_available": False,
        "split": split,
        "role": role,
        "source_event_group": event_group,
        "video_name": video_name,
        "camera": values["camera"],
        "site": values["site"],
        "activity_labels": dict(sorted(labels.items())),
        "door_activity_labels": selected,
        "suspicious_activity_labels": suspicious,
        "annotation_files": {
            "activities": str(annotation_path.resolve()),
            "geometry": str((annotation_root / f"{stem}.geom.yml").resolve()),
            "types": str((annotation_root / f"{stem}.types.yml").resolve()),
        },
        "s3_key": key,
        "source_url": f"{MEVA_BUCKET}/{key}",
        "expected_bytes": None,
        "etag": None,
        "local_path": None,
        "sha256": None,
        "downloaded": False,
        "available": None,
    }


def select_pilot_records(records: list[dict[str, Any]], max_bytes: int) -> list[dict[str, Any]]:
    """Choose a deterministic, split-aware door/hard-negative pilot."""

    if max_bytes <= 0:
        return list(records)

    buckets: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for record in records:
        key = (str(record.get("split") or ""), str(record.get("role") or ""))
        buckets.setdefault(key, []).append(record)
    for bucket in buckets.values():
        bucket.sort(key=lambda item: deterministic_rank(str(item.get("source_url") or "")))

    cycle = (
        ("train_auxiliary", "door_activity"),
        ("train_auxiliary", "door_activity"),
        ("train_auxiliary", "hard_negative"),
        ("validation_auxiliary", "door_activity"),
        ("validation_auxiliary", "hard_negative"),
        ("train_auxiliary", "suspicious_activity"),
        ("validation_auxiliary", "suspicious_activity"),
    )
    positions = {key: 0 for key in buckets}
    selected: list[dict[str, Any]] = []
    selected_keys: set[str] = set()
    total = 0
    while True:
        added = False
        for key in cycle:
            bucket = buckets.get(key, [])
            position = positions.get(key, 0)
            if position >= len(bucket):
                continue
            record = bucket[position]
            positions[key] = position + 1
            identity = str(record.get("source_url") or record.get("video_name") or "")
            if identity in selected_keys:
                continue
            size = max(1, int(record.get("expected_bytes") or 256 * 1024 * 1024))
            if selected and total + size > max_bytes:
                continue
            selected.append(record)
            selected_keys.add(identity)
            total += size
            added = True
        if not added:
            break

    return selected


def candidate_records(
    annotations_root: Path,
    subset: str,
    split: str,
    hard_negative_limit: int,
) -> list[dict[str, Any]]:
    subset_root = annotations_root / subset
    if not subset_root.exists():
        raise StationaryDataError(f"MEVA annotation subset is missing: {subset_root}")

    positives: list[dict[str, Any]] = []
    negatives: list[dict[str, Any]] = []
    for annotation_path in subset_root.rglob("*.activities.yml"):
        labels = read_activity_labels(annotation_path)
        if labels.keys() & (DOOR_ACTIVITY_LABELS | SUSPICIOUS_ACTIVITY_LABELS):
            positives.append(source_record(annotation_path, split, labels))
        elif labels.keys() & HARD_NEGATIVE_LABELS:
            negatives.append(source_record(annotation_path, split, labels))

    negatives.sort(key=lambda item: deterministic_rank(item["video_name"]))
    selected = positives + negatives[: max(0, hard_negative_limit)]
    selected.sort(key=lambda item: (item["source_event_group"], item["camera"], item["video_name"]))
    return selected


def probe_record(record: dict[str, Any], timeout: int) -> dict[str, Any]:
    request = urllib.request.Request(record["source_url"], method="HEAD")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            updated = dict(record)
            length = response.headers.get("Content-Length")
            updated["expected_bytes"] = int(length) if length else None
            updated["etag"] = str(response.headers.get("ETag") or "").strip('"') or None
            updated["available"] = True
            return updated
    except (OSError, urllib.error.URLError) as exc:
        updated = dict(record)
        updated["available"] = False
        updated["probe_error"] = str(exc)
        return updated


def catalog_meva(args: argparse.Namespace) -> int:
    annotations_repo = Path(args.annotations_repo).resolve()
    annotations_root = annotations_repo / "annotation" / "DIVA-phase-2" / "MEVA"
    records = candidate_records(
        annotations_root,
        "kitware-meva-training",
        "train_auxiliary",
        args.train_hard_negatives,
    )
    records.extend(
        candidate_records(
            annotations_root,
            "kitware",
            "validation_auxiliary",
            args.validation_hard_negatives,
        )
    )
    records = assign_source_isolated_splits(records)

    unavailable: list[dict[str, Any]] = []
    if args.probe:
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
            records = list(executor.map(lambda record: probe_record(record, args.timeout), records))
        unavailable = [record for record in records if not record.get("available")]
        records = [record for record in records if record.get("available")]

    split_counts = Counter(record["split"] for record in records)
    role_counts = Counter(record["role"] for record in records)
    expected_bytes = sum(int(record.get("expected_bytes") or 0) for record in records)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "created_at": utc_now(),
        "source_id": SOURCE_ID,
        "capture_domain": "stationary_ground_surveillance",
        "target_domain": "stationary_vehicle_mounted_camera",
        "domain_role": "auxiliary_vehicle_door_and_proximity_pretraining",
        "promotion_eligible": False,
        "contact_ground_truth_available": False,
        "pixel_overlap_is_physical_contact_proof": False,
        "license": "CC-BY-4.0",
        "license_url": MEVA_LICENSE_URL,
        "attribution": "MEVA dataset by Kitware Inc. and IARPA DIVA",
        "repository": MEVA_REPOSITORY,
        "repository_revision": repository_revision(annotations_repo),
        "split_assignment_version": SPLIT_ASSIGNMENT_VERSION,
        "selection_policy": {
            "training_annotations": "kitware-meva-training",
            "validation_annotations": "kitware",
            "door_activity_labels": sorted(DOOR_ACTIVITY_LABELS),
            "hard_negative_labels": sorted(HARD_NEGATIVE_LABELS),
            "train_hard_negative_limit": args.train_hard_negatives,
            "validation_hard_negative_limit": args.validation_hard_negatives,
            "filename_text_used_as_detection_evidence": False,
        },
        "record_count": len(records),
        "split_counts": dict(sorted(split_counts.items())),
        "role_counts": dict(sorted(role_counts.items())),
        "expected_bytes": expected_bytes or None,
        "unavailable_count": len(unavailable),
        "unavailable_records": unavailable,
        "records": records,
    }
    output = Path(args.output).resolve()
    write_json(output, manifest)
    print(f"MEVA catalog written: {output}")
    print(f"records: {len(records)}")
    print(f"door activity clips: {role_counts['door_activity']}")
    print(f"suspicious activity clips: {role_counts['suspicious_activity']}")
    print(f"hard-negative clips: {role_counts['hard_negative']}")
    print(f"unavailable source clips skipped: {len(unavailable)}")
    if expected_bytes:
        print(f"expected download: {expected_bytes / (1024 ** 3):.2f} GiB")
    return 0


def repair_splits(args: argparse.Namespace) -> int:
    manifest_path = Path(args.manifest).resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise StationaryDataError(f"Unsupported stationary manifest: {manifest_path}")
    records = assign_source_isolated_splits(list(manifest.get("records") or []))
    split_counts = Counter(record["split"] for record in records)
    group_splits: dict[str, set[str]] = {}
    for record in records:
        group_splits.setdefault(str(record.get("source_event_group") or ""), set()).add(record["split"])
    leaked = [group for group, splits in group_splits.items() if len(splits) > 1]
    if leaked:
        raise StationaryDataError(f"Source-isolated split repair failed for {len(leaked)} groups")
    manifest["records"] = records
    manifest["split_assignment_version"] = SPLIT_ASSIGNMENT_VERSION
    manifest["split_counts"] = dict(sorted(split_counts.items()))
    manifest["splits_repaired_at"] = utc_now()
    write_json(manifest_path, manifest)
    print(f"MEVA source-isolated splits repaired: {manifest_path}")
    print(f"physical event groups: {len(group_splits)}")
    print(f"train records: {split_counts['train_auxiliary']}")
    print(f"validation records: {split_counts['validation_auxiliary']}")
    return 0


def _range_total(headers: Any) -> int:
    content_range = str(headers.get("Content-Range") or "")
    match = re.search(r"/(\d+)$", content_range)
    if match:
        return int(match.group(1))
    return 0


def _record_paths(record: dict[str, Any], output_root: Path) -> tuple[Path, Path]:
    existing_path = str(record.get("local_path") or "").strip()
    destination = (
        Path(existing_path)
        if existing_path
        else output_root / str(record.get("split") or "") / str(record.get("video_name") or "")
    )
    return destination, destination.with_suffix(destination.suffix + ".part")


def download_record(
    record: dict[str, Any],
    output_root: Path,
    timeout: int,
    retries: int,
) -> dict[str, Any]:
    destination, temporary = _record_paths(record, output_root)
    destination.parent.mkdir(parents=True, exist_ok=True)
    expected = int(record.get("expected_bytes") or 0)
    expected_sha256 = str(record.get("expected_sha256") or record.get("sha256") or "").lower()

    if destination.exists() and (not expected or destination.stat().st_size == expected):
        actual_sha256 = sha256_file(destination)
        if expected_sha256 and actual_sha256 != expected_sha256:
            raise StationaryDataError(
                f"Checksum mismatch for completed file {record['video_name']}: "
                f"{actual_sha256} != {expected_sha256}"
            )
        updated = dict(record)
        updated["local_path"] = str(destination.resolve())
        updated["sha256"] = actual_sha256
        updated["downloaded"] = True
        updated["download_error"] = None
        return updated

    errors: list[str] = []
    attempts = max(1, retries + 1)
    for attempt in range(1, attempts + 1):
        offset = temporary.stat().st_size if temporary.exists() else 0
        if expected and offset > expected:
            temporary.write_bytes(b"")
            offset = 0
        headers = {"User-Agent": "Mimir-Dataset-Acquisition/1"}
        if offset:
            headers["Range"] = f"bytes={offset}-"
            if record.get("etag"):
                headers["If-Range"] = f'"{record["etag"]}"'
        request = urllib.request.Request(record["source_url"], headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                status = int(getattr(response, "status", response.getcode()) or 200)
                response_etag = str(response.headers.get("ETag") or "").strip('"')
                if record.get("etag") and response_etag and response_etag != record.get("etag"):
                    raise StationaryDataError(f"ETag changed for {record['video_name']}")
                range_total = _range_total(response.headers)
                if range_total:
                    expected = range_total
                append = bool(offset and status == 206)
                mode = "ab" if append else "wb"
                with temporary.open(mode) as output:
                    shutil.copyfileobj(response, output, length=1024 * 1024)
            current = temporary.stat().st_size
            if expected and current < expected:
                raise http.client.IncompleteRead(b"", expected - current)
            if expected and current > expected:
                raise StationaryDataError(
                    f"Size mismatch for {record['video_name']}: {current} != {expected}"
                )
            break
        except (OSError, urllib.error.URLError, http.client.HTTPException, StationaryDataError) as exc:
            errors.append(f"attempt {attempt}: {type(exc).__name__}: {exc}")
            if isinstance(exc, StationaryDataError) and str(exc).startswith("ETag changed"):
                temporary.unlink(missing_ok=True)
            if attempt >= attempts:
                raise StationaryDataError(
                    f"Could not download {record['source_url']} after {attempts} attempts: {errors[-1]}"
                ) from exc
            time.sleep(min(2 ** (attempt - 1), 15))

    actual_sha256 = sha256_file(temporary)
    if expected_sha256 and actual_sha256 != expected_sha256:
        raise StationaryDataError(
            f"Checksum mismatch for {record['video_name']}: {actual_sha256} != {expected_sha256}"
        )
    temporary.replace(destination)
    updated = dict(record)
    updated["expected_bytes"] = expected or destination.stat().st_size
    updated["local_path"] = str(destination.resolve())
    updated["sha256"] = actual_sha256
    updated["downloaded"] = True
    updated["download_error"] = None
    updated["download_attempts"] = len(errors) + 1
    return updated


def _download_totals(records: list[dict[str, Any]]) -> tuple[int, int]:
    downloaded = 0
    downloaded_bytes = 0
    for record in records:
        local_path = Path(str(record.get("local_path") or ""))
        if record.get("downloaded") and local_path.is_file():
            downloaded += 1
            downloaded_bytes += local_path.stat().st_size
    return downloaded, downloaded_bytes


def download_meva(args: argparse.Namespace) -> int:
    manifest_path = Path(args.manifest).resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise StationaryDataError(f"Unsupported stationary manifest: {manifest_path}")
    output_root = Path(args.output).resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    records = list(manifest.get("records") or [])
    max_bytes = max(0, int(float(args.max_gib) * GIB))
    existing = [record for record in records if any(path.exists() for path in _record_paths(record, output_root))]
    existing_urls = {str(record.get("source_url") or "") for record in existing}
    existing_budget = sum(int(record.get("expected_bytes") or 0) for record in existing)
    remaining_budget = max(0, max_bytes - existing_budget) if max_bytes else 0
    candidates = [record for record in records if str(record.get("source_url") or "") not in existing_urls]
    additional = [] if max_bytes and remaining_budget <= 0 else select_pilot_records(candidates, remaining_budget)
    selected = existing + additional
    selected_urls = {str(record.get("source_url") or "") for record in selected}
    remaining_bytes = sum(
        max(0, int(record.get("expected_bytes") or 0) - (
            _record_paths(record, output_root)[1].stat().st_size
            if _record_paths(record, output_root)[1].exists()
            else _record_paths(record, output_root)[0].stat().st_size
            if _record_paths(record, output_root)[0].exists()
            else 0
        ))
        for record in selected
        if not record.get("downloaded")
    )
    free_bytes = shutil.disk_usage(output_root).free
    reserve_bytes = max(0, int(float(args.reserve_gib) * GIB))
    if remaining_bytes and free_bytes - reserve_bytes < remaining_bytes:
        raise StationaryDataError(
            f"Insufficient disk space: need {remaining_bytes / GIB:.2f} GiB plus "
            f"{reserve_bytes / GIB:.2f} GiB reserve, have {free_bytes / GIB:.2f} GiB free"
        )
    started = time.monotonic()
    record_positions = {str(record.get("source_url") or ""): index for index, record in enumerate(records)}
    pending = [record for record in selected if not record.get("downloaded")]
    failures = 0

    manifest["pilot_selection"] = {
        "selected_at": utc_now(),
        "max_gib": float(args.max_gib),
        "selected_count": len(selected),
        "selected_expected_bytes": sum(int(record.get("expected_bytes") or 0) for record in selected),
        "source_urls": sorted(selected_urls),
    }

    def checkpoint() -> None:
        downloaded_count, downloaded_bytes = _download_totals(records)
        manifest["download_runtime_sec"] = round(time.monotonic() - started, 2)
        manifest["downloaded_count"] = downloaded_count
        manifest["downloaded_bytes"] = downloaded_bytes
        manifest["download_failures"] = failures
        manifest["records"] = records
        write_json(manifest_path, manifest)
        write_json(output_root / "stationary_auxiliary_manifest.json", manifest)

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        future_map = {
            executor.submit(download_record, record, output_root, args.timeout, args.retries): record
            for record in pending
        }
        for index, future in enumerate(concurrent.futures.as_completed(future_map), start=1):
            original = future_map[future]
            position = record_positions[str(original.get("source_url") or "")]
            try:
                records[position] = future.result()
            except Exception as exc:
                failures += 1
                failed = dict(original)
                failed["downloaded"] = False
                failed["download_error"] = f"{type(exc).__name__}: {exc}"
                failed["last_download_attempt_at"] = utc_now()
                records[position] = failed
                print(f"warning: {failed['video_name']}: {failed['download_error']}", file=sys.stderr)
            if index == 1 or index % max(1, args.checkpoint_every) == 0 or index == len(pending):
                checkpoint()
                print(f"processed {index}/{len(pending)} selected clips")

    manifest["downloaded_at"] = utc_now()
    checkpoint()
    print(f"MEVA pilot download complete: {output_root}")
    print(f"selected clips: {len(selected)}")
    print(f"failed clips: {failures}")
    print(f"downloaded: {manifest['downloaded_bytes'] / (1024 ** 3):.2f} GiB")
    return 1 if failures else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Acquire stationary-camera auxiliary datasets for Mimir.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    catalog = subparsers.add_parser("catalog-meva", help="Select licensed MEVA vehicle activity clips.")
    catalog.add_argument("--annotations-repo", required=True)
    catalog.add_argument("--output", required=True)
    catalog.add_argument("--train-hard-negatives", type=int, default=120)
    catalog.add_argument("--validation-hard-negatives", type=int, default=40)
    catalog.add_argument("--probe", action="store_true")
    catalog.add_argument("--workers", type=int, default=12)
    catalog.add_argument("--timeout", type=int, default=60)
    catalog.set_defaults(handler=catalog_meva)

    download = subparsers.add_parser("download-meva", help="Download and hash a MEVA catalog.")
    download.add_argument("--manifest", required=True)
    download.add_argument("--output", required=True)
    download.add_argument("--workers", type=int, default=8)
    download.add_argument("--timeout", type=int, default=300)
    download.add_argument("--retries", type=int, default=5)
    download.add_argument("--max-gib", type=float, default=0.0, help="Deterministic pilot size; 0 downloads the full manifest.")
    download.add_argument("--reserve-gib", type=float, default=10.0, help="Free disk space retained after the download.")
    download.add_argument("--checkpoint-every", type=int, default=1)
    download.set_defaults(handler=download_meva)

    split_repair = subparsers.add_parser(
        "repair-splits",
        help="Assign source-isolated train/validation splits without moving video files.",
    )
    split_repair.add_argument("--manifest", required=True)
    split_repair.set_defaults(handler=repair_splits)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.handler(args))
    except StationaryDataError as exc:
        print(f"Stationary data error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
