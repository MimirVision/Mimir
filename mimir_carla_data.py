"""Acquire and verify CARLA for Mimir synthetic parked-impact generation.

CARLA code is MIT licensed and CARLA-specific assets are CC BY licensed. The
simulator is a development dependency only: it is never bundled with Mimir and
synthetic output is never sufficient for production model promotion.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import time
import urllib.error
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


CARLA_VERSION = "0.9.16"
ARCHIVE_NAME = f"CARLA_{CARLA_VERSION}.zip"
ARCHIVE_URL = "https://carla-releases.b-cdn.net/Windows/CARLA_0.9.16.zip"
EXPECTED_SIZE_BYTES = 7_812_252_810
RELEASE_URL = "https://github.com/carla-simulator/carla/releases/tag/0.9.16"
SOURCE_REVISION = "294096e"
CODE_LICENSE = "MIT"
ASSET_LICENSE = "CC-BY-4.0"
LICENSE_URL = "https://github.com/carla-simulator/carla/blob/0.9.16/LICENSE"
ATTRIBUTION = (
    "CARLA Simulator, Computer Vision Center (CVC), Universitat Autonoma "
    "de Barcelona (UAB), version 0.9.16."
)


class CarlaDataError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def source_receipt(root: Path, archive: Path | None = None) -> dict[str, Any]:
    archive_record = None
    if archive is not None and archive.is_file():
        archive_record = {
            "path": str(archive.resolve()),
            "bytes": archive.stat().st_size,
            "sha256": sha256_file(archive),
        }
    return {
        "schema_version": "mimir_external_dataset_receipt_v1",
        "source_id": "carla_stationary_collision_synthetic",
        "source_url": RELEASE_URL,
        "source_revision": SOURCE_REVISION,
        "simulator_version": CARLA_VERSION,
        "retrieved_at": utc_now(),
        "archive_url": ARCHIVE_URL,
        "archive": archive_record,
        "code_license": CODE_LICENSE,
        "asset_license": ASSET_LICENSE,
        "license_url": LICENSE_URL,
        "license_reviewed": True,
        "license_acceptance_required": False,
        "attribution": ATTRIBUTION,
        "source_role": "synthetic_auxiliary",
        "promotion_eligible": False,
        "real_world_contact_ground_truth": False,
        "simulated_collision_ground_truth": True,
        "automatic_upload": False,
        "distribution_policy": (
            "The CARLA runtime is a local development dependency and is not "
            "redistributed with Mimir."
        ),
        "output_root": str(root.resolve()),
    }


def _download_once(url: str, partial: Path, expected_size: int) -> None:
    existing = partial.stat().st_size if partial.is_file() else 0
    headers = {"User-Agent": "Mimir-Training-Data/1.0"}
    if existing:
        headers["Range"] = f"bytes={existing}-"
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=120) as response:
        status = int(getattr(response, "status", 200))
        resumed = existing > 0 and status == 206
        mode = "ab" if resumed else "wb"
        if existing and not resumed:
            existing = 0
        last_report = existing
        with partial.open(mode) as handle:
            while True:
                block = response.read(8 * 1024 * 1024)
                if not block:
                    break
                handle.write(block)
                existing += len(block)
                if existing - last_report >= 512 * 1024 * 1024:
                    percent = 100.0 * existing / max(expected_size, 1)
                    print(f"CARLA download: {existing / (1024 ** 3):.2f} GiB ({percent:.1f}%)")
                    last_report = existing


def download(args: argparse.Namespace) -> int:
    root = Path(args.output).resolve()
    root.mkdir(parents=True, exist_ok=True)
    archive = root / ARCHIVE_NAME
    partial = archive.with_suffix(archive.suffix + ".part")
    if archive.is_file() and archive.stat().st_size == EXPECTED_SIZE_BYTES:
        print(f"CARLA archive already present: {archive}")
    else:
        if archive.exists():
            archive.unlink()
        for attempt in range(1, max(1, args.retries) + 1):
            try:
                _download_once(ARCHIVE_URL, partial, EXPECTED_SIZE_BYTES)
                if partial.stat().st_size != EXPECTED_SIZE_BYTES:
                    raise CarlaDataError(
                        f"incomplete CARLA archive: {partial.stat().st_size} "
                        f"of {EXPECTED_SIZE_BYTES} bytes"
                    )
                partial.replace(archive)
                break
            except (OSError, urllib.error.URLError, CarlaDataError) as exc:
                if attempt >= max(1, args.retries):
                    raise CarlaDataError(f"CARLA download failed after {attempt} attempts: {exc}") from exc
                delay = min(30, attempt * 3)
                print(f"CARLA download retry {attempt}/{args.retries}: {exc}")
                time.sleep(delay)
    receipt = source_receipt(root, archive)
    write_json(root / "CARLA_SOURCE_RECEIPT.json", receipt)
    print(f"CARLA archive SHA-256: {receipt['archive']['sha256']}")
    print(f"receipt: {root / 'CARLA_SOURCE_RECEIPT.json'}")
    return 0


def _safe_destination(root: Path, member_name: str) -> Path:
    normalized = member_name.replace("\\", "/")
    member = Path(normalized)
    if member.is_absolute() or ".." in member.parts:
        raise CarlaDataError(f"unsafe CARLA archive member: {member_name}")
    destination = (root / member).resolve()
    if os.path.commonpath((str(root.resolve()), str(destination))) != str(root.resolve()):
        raise CarlaDataError(f"CARLA archive member escapes output root: {member_name}")
    return destination


def extract(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    archive = Path(args.archive).resolve() if args.archive else root / ARCHIVE_NAME
    if not archive.is_file():
        raise CarlaDataError(f"CARLA archive is missing: {archive}")
    if archive.stat().st_size != EXPECTED_SIZE_BYTES:
        raise CarlaDataError(
            f"CARLA archive size mismatch: {archive.stat().st_size} != {EXPECTED_SIZE_BYTES}"
        )
    destination_root = Path(args.output).resolve()
    destination_root.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as bundle:
        members = bundle.infolist()
        for index, member in enumerate(members, start=1):
            destination = _safe_destination(destination_root, member.filename)
            if member.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_suffix(destination.suffix + ".part")
            if destination.is_file() and destination.stat().st_size == member.file_size:
                continue
            with bundle.open(member) as source, temporary.open("wb") as target:
                shutil.copyfileobj(source, target, length=1024 * 1024)
            if temporary.stat().st_size != member.file_size:
                raise CarlaDataError(f"incomplete extracted member: {member.filename}")
            temporary.replace(destination)
            if index == 1 or index % 1000 == 0 or index == len(members):
                print(f"CARLA extract: {index}/{len(members)} members")
    write_json(
        destination_root / "MIMIR_CARLA_INSTALL.json",
        {
            **source_receipt(root, archive),
            "schema_version": "mimir_carla_install_v1",
            "extracted_at": utc_now(),
            "install_root": str(destination_root),
        },
    )
    print(f"CARLA extracted: {destination_root}")
    return 0


def verify(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    archive = Path(args.archive).resolve() if args.archive else root / ARCHIVE_NAME
    failures: list[str] = []
    if not archive.is_file():
        failures.append(f"archive missing: {archive}")
    elif archive.stat().st_size != EXPECTED_SIZE_BYTES:
        failures.append(
            f"archive size: {archive.stat().st_size} != {EXPECTED_SIZE_BYTES}"
        )
    else:
        try:
            with zipfile.ZipFile(archive) as bundle:
                invalid = bundle.testzip()
                if invalid:
                    failures.append(f"invalid ZIP member: {invalid}")
        except (OSError, zipfile.BadZipFile) as exc:
            failures.append(f"ZIP validation failed: {exc}")
    receipt_path = root / "CARLA_SOURCE_RECEIPT.json"
    if not receipt_path.is_file():
        failures.append(f"source receipt missing: {receipt_path}")
    else:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        expected_hash = str((receipt.get("archive") or {}).get("sha256") or "")
        if archive.is_file() and expected_hash and sha256_file(archive) != expected_hash:
            failures.append("archive SHA-256 does not match the source receipt")
    report = {
        "schema_version": "mimir_carla_integrity_report_v1",
        "generated_at": utc_now(),
        "archive": str(archive),
        "archive_bytes": archive.stat().st_size if archive.is_file() else None,
        "archive_sha256": sha256_file(archive) if archive.is_file() else None,
        "passed": not failures,
        "failures": failures,
    }
    report_path = Path(args.report).resolve()
    write_json(report_path, report)
    print(f"CARLA integrity: {'PASS' if report['passed'] else 'FAIL'}")
    print(f"report: {report_path}")
    return 0 if report["passed"] else 1


def verify_prepared(args: argparse.Namespace) -> int:
    try:
        import cv2  # type: ignore
        import numpy as np  # type: ignore
    except ImportError as exc:
        raise CarlaDataError(
            f"OpenCV and NumPy are required to verify prepared CARLA data: {exc}"
        ) from exc

    root = Path(args.root).resolve()
    failures: list[str] = []
    records: list[dict[str, Any]] = []
    scenario_paths = sorted((root / "scenarios").glob("*/scenario.json"))
    if len(scenario_paths) != args.expected_scenarios:
        failures.append(
            f"scenario count: {len(scenario_paths)} != {args.expected_scenarios}"
        )
    split_targets: dict[str, set[bool]] = {
        "train": set(),
        "validation": set(),
        "test": set(),
    }
    for scenario_path in scenario_paths:
        try:
            record = json.loads(scenario_path.read_text(encoding="utf-8"))
            scenario_id = str(record.get("scenario_id") or scenario_path.parent.name)
            records.append(record)
            if record.get("generator_version") != args.generator_version:
                failures.append(
                    f"{scenario_id}: generator version "
                    f"{record.get('generator_version')} != {args.generator_version}"
                )
            if record.get("source_role") != "synthetic_auxiliary":
                failures.append(f"{scenario_id}: source role is not synthetic auxiliary")
            if record.get("promotion_eligible") is not False:
                failures.append(f"{scenario_id}: promotion quarantine is missing")
            if record.get("real_world_contact_ground_truth") is not False:
                failures.append(f"{scenario_id}: claims real-world contact truth")
            if record.get("simulated_collision_ground_truth") is not True:
                failures.append(f"{scenario_id}: simulator collision provenance is missing")
            split = str(record.get("split") or "")
            if split not in split_targets:
                failures.append(f"{scenario_id}: invalid split {split!r}")
            else:
                split_targets[split].add(bool(record.get("actual_collision")))
            duration = float(record.get("duration_sec") or 0.0)
            impact_time = record.get("impact_time_sec")
            actual_collision = bool(record.get("actual_collision"))
            if actual_collision != (impact_time is not None):
                failures.append(f"{scenario_id}: collision label and impact time disagree")
            if impact_time is not None and not 0.0 <= float(impact_time) <= duration:
                failures.append(f"{scenario_id}: impact time is outside the video")
            if bool(record.get("intended_positive")) != actual_collision:
                failures.append(f"{scenario_id}: intended and sensor labels disagree")

            media = [
                item
                for item in record.get("media", [])
                if isinstance(item, dict) and item.get("path")
            ]
            if len(media) != 1:
                failures.append(f"{scenario_id}: expected one primary video")
            for media_item in media:
                media_path = Path(str(media_item["path"]))
                if not media_path.is_file():
                    failures.append(f"{scenario_id}: video missing: {media_path}")
                    continue
                if sha256_file(media_path) != str(media_item.get("sha256") or ""):
                    failures.append(f"{scenario_id}: video SHA-256 mismatch")
                capture = cv2.VideoCapture(str(media_path))
                try:
                    readable, frame = capture.read()
                    frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
                    if not capture.isOpened() or not readable or frame is None or frame_count <= 0:
                        failures.append(f"{scenario_id}: video is unreadable")
                finally:
                    capture.release()

            segmentation = (
                record.get("instance_segmentation")
                if isinstance(record.get("instance_segmentation"), dict)
                else {}
            )
            masks_path = Path(str(segmentation.get("path") or ""))
            if not masks_path.is_file():
                failures.append(f"{scenario_id}: instance masks are missing")
            else:
                if sha256_file(masks_path) != str(segmentation.get("sha256") or ""):
                    failures.append(f"{scenario_id}: mask SHA-256 mismatch")
                with np.load(masks_path, allow_pickle=False) as masks:
                    ego_masks = masks["ego_masks"]
                    target_masks = masks["target_masks"]
                    times = masks["times"]
                    if (
                        len(ego_masks) != len(target_masks)
                        or len(times) != len(ego_masks)
                        or len(times) == 0
                    ):
                        failures.append(f"{scenario_id}: inconsistent mask sequence lengths")
                    elif bool(np.any(np.diff(times) < 0)):
                        failures.append(f"{scenario_id}: mask times are not sorted")
        except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
            failures.append(f"{scenario_path.parent.name}: verification error: {exc}")

    for split, targets in split_targets.items():
        if targets != {False, True}:
            failures.append(
                f"{split} split lacks both collision and no-collision records"
            )
    report = {
        "schema_version": "mimir_carla_prepared_integrity_report_v1",
        "generated_at": utc_now(),
        "prepared_root": str(root),
        "expected_scenarios": args.expected_scenarios,
        "verified_scenarios": len(records),
        "generator_version": args.generator_version,
        "collision_count": sum(
            1 for record in records if record.get("actual_collision")
        ),
        "no_collision_count": sum(
            1 for record in records if not record.get("actual_collision")
        ),
        "split_class_coverage": {
            split: sorted("collision" if target else "no_collision" for target in targets)
            for split, targets in split_targets.items()
        },
        "promotion_eligible": False,
        "real_world_contact_ground_truth": False,
        "passed": not failures,
        "failures": failures,
    }
    report_path = Path(args.report).resolve()
    write_json(report_path, report)
    print(f"CARLA prepared integrity: {'PASS' if report['passed'] else 'FAIL'}")
    print(f"scenarios: {len(records)} / {args.expected_scenarios}")
    print(f"report: {report_path}")
    return 0 if report["passed"] else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    download_parser = subparsers.add_parser("download")
    download_parser.add_argument("--output", required=True)
    download_parser.add_argument("--retries", type=int, default=8)
    download_parser.set_defaults(handler=download)

    extract_parser = subparsers.add_parser("extract")
    extract_parser.add_argument("--root", required=True)
    extract_parser.add_argument("--archive")
    extract_parser.add_argument("--output", required=True)
    extract_parser.set_defaults(handler=extract)

    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("--root", required=True)
    verify_parser.add_argument("--archive")
    verify_parser.add_argument("--report", required=True)
    verify_parser.set_defaults(handler=verify)

    prepared_parser = subparsers.add_parser("verify-prepared")
    prepared_parser.add_argument("--root", required=True)
    prepared_parser.add_argument("--report", required=True)
    prepared_parser.add_argument("--expected-scenarios", type=int, default=60)
    prepared_parser.add_argument(
        "--generator-version",
        default="mimir_carla_stationary_generator_v2",
    )
    prepared_parser.set_defaults(handler=verify_prepared)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.handler(args))
    except (CarlaDataError, OSError, json.JSONDecodeError, zipfile.BadZipFile) as exc:
        print(f"CARLA data error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
