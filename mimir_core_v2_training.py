"""Consent, provenance, preparation, and training orchestration for Mimir.

This command never discovers random web video and never infers consent. It can
prepare real annotated footage and launch RF-DETR fine-tuning only after the
dataset passes rights, leakage, and minimum-size checks.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mimir_core_v2.dataset_package import exclusion_hashes


ROOT = Path(__file__).resolve().parent
SOURCE_REGISTRY = ROOT / "mimir_core_v2" / "dataset_sources.json"
TRAINING_SCHEMA = "mimir_training_manifest_v1"
CATEGORIES = {
    "person": 1,
    "vehicle": 2,
    "vehicle_door": 3,
    "ego_vehicle": 4,
}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return value


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def collection_roots(dataset_root: Path) -> list[Path]:
    if (dataset_root / "manifest.json").exists():
        return [dataset_root]
    return sorted({path.parent for path in dataset_root.rglob("manifest.json")}) if dataset_root.exists() else []


def _filled(value: object) -> bool:
    return value is not None and str(value).strip() != ""


def audit_dataset(dataset_root: Path) -> dict[str, Any]:
    collections = collection_roots(dataset_root)
    errors: list[str] = []
    warnings: list[str] = []
    source_splits: dict[str, str] = {}
    media_hashes: dict[str, str] = {}
    severity_counts: Counter[str] = Counter()
    outcome_counts: Counter[str] = Counter()
    split_complete_counts: Counter[str] = Counter()
    complete_items = 0
    object_annotations = 0
    mask_annotations = 0
    total_items = 0
    blind_relabel_items = 0
    blind_severity_matches = 0
    blind_outcome_matches = 0
    blind_timing_comparable = 0
    blind_timing_within_one_sec = 0
    excluded_hashes = exclusion_hashes()

    if not collections:
        errors.append("no consented dataset collections were found")

    for collection in collections:
        try:
            manifest = read_json(collection / "manifest.json")
            consent = read_json(collection / str(manifest.get("consent_record") or "consent.json"))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            errors.append(f"{collection}: {exc}")
            continue
        if not consent.get("rights_confirmed"):
            errors.append(f"{collection}: rights are not explicitly confirmed")
        if consent.get("schema_version") != "mimir_dataset_consent_v2":
            errors.append(f"{collection}: version 2 clip-by-clip consent is required")
        if not str(consent.get("rights_basis") or "").strip():
            errors.append(f"{collection}: rights_basis is missing")
        if not str(consent.get("permission_reference") or "").strip():
            errors.append(f"{collection}: permission_reference is missing")
        consented_ids = {str(item) for item in consent.get("incident_ids", [])}
        consent_hashes = {
            str(item.get("sha256") or "").lower()
            for item in consent.get("clips", [])
            if isinstance(item, dict) and item.get("sha256")
        }

        for item in manifest.get("items", []) if isinstance(manifest.get("items"), list) else []:
            if not isinstance(item, dict):
                continue
            total_items += 1
            incident_id = str(item.get("incident_id") or "")
            if incident_id not in consented_ids:
                errors.append(f"{collection}: {incident_id} has no matching consent record")
            source_hash = str(item.get("source_group_hash") or "")
            split = str(item.get("split") or "")
            if not source_hash:
                errors.append(f"{collection}: {incident_id} has no source group hash")
            if split not in {"train", "validation", "test"}:
                errors.append(f"{collection}: {incident_id} has invalid split {split!r}")
            previous = source_splits.setdefault(source_hash, split)
            if source_hash and previous != split:
                errors.append(f"source leakage: {source_hash} appears in {previous} and {split}")
            annotation_path = collection / str(item.get("annotation") or "")
            if not annotation_path.exists():
                errors.append(f"{collection}: missing annotation for {incident_id}")
                continue
            try:
                record = read_json(annotation_path)
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                errors.append(f"{annotation_path}: {exc}")
                continue
            annotation = record.get("annotation") if isinstance(record.get("annotation"), dict) else {}
            severity = str(annotation.get("human_severity") or "").upper()
            outcome = str(annotation.get("contact_outcome") or "").lower()
            severity_counts[severity or "UNLABELED"] += 1
            outcome_counts[outcome or "unlabeled"] += 1
            complete = severity in {"IGNORE", "REVIEW", "IMPORTANT"} and outcome in {
                "contact",
                "impact",
                "no_contact",
                "uncertain",
            }
            if outcome in {"contact", "impact"} and not (
                _filled(annotation.get("apparent_contact_time_sec")) or _filled(annotation.get("impact_time_sec"))
            ):
                complete = False
                warnings.append(f"{incident_id}: positive item needs a human contact or impact time")
            if complete:
                complete_items += 1
                split_complete_counts[split] += 1

            reannotations = record.get("blind_reannotations") if isinstance(record.get("blind_reannotations"), list) else []
            if reannotations:
                relabel = reannotations[-1] if isinstance(reannotations[-1], dict) else {}
                blind_relabel_items += 1
                if str(relabel.get("human_severity") or "").upper() == severity:
                    blind_severity_matches += 1
                if str(relabel.get("contact_outcome") or "").lower() == outcome:
                    blind_outcome_matches += 1
                original_time = annotation.get("impact_time_sec")
                if original_time is None:
                    original_time = annotation.get("apparent_contact_time_sec")
                relabel_time = relabel.get("impact_time_sec")
                if relabel_time is None:
                    relabel_time = relabel.get("apparent_contact_time_sec")
                if original_time is not None and relabel_time is not None:
                    blind_timing_comparable += 1
                    if abs(float(original_time) - float(relabel_time)) <= 1.0:
                        blind_timing_within_one_sec += 1

            objects = annotation.get("objects") if isinstance(annotation.get("objects"), list) else []
            for obj in objects:
                if not isinstance(obj, dict) or str(obj.get("class_name") or "") not in CATEGORIES:
                    continue
                bbox = obj.get("bbox_xyxy")
                if isinstance(bbox, list) and len(bbox) == 4:
                    object_annotations += 1
                    segmentation = obj.get("segmentation")
                    if isinstance(segmentation, list) and segmentation:
                        mask_annotations += 1

            media_records = record.get("media", []) if isinstance(record.get("media"), list) else []
            if not media_records:
                errors.append(f"{incident_id}: no consented training media was copied")
            for media in media_records:
                if not isinstance(media, dict):
                    continue
                relative = str(media.get("relative_path") or "")
                media_path = collection / relative
                expected = str(media.get("sha256") or "")
                if not media_path.exists():
                    errors.append(f"{incident_id}: copied training media is missing: {relative}")
                    continue
                actual = sha256(media_path)
                if expected and actual != expected:
                    errors.append(f"{incident_id}: media checksum changed: {relative}")
                if actual not in consent_hashes:
                    errors.append(f"{incident_id}: media hash is not covered by clip-by-clip consent: {relative}")
                if actual in excluded_hashes:
                    permission_relative = str(consent.get("independent_permission_record") or "")
                    permission_path = collection / permission_relative if permission_relative else None
                    expected_permission_hash = str(consent.get("independent_permission_record_sha256") or "")
                    if (
                        permission_path is None
                        or not permission_path.is_file()
                        or not expected_permission_hash
                        or sha256(permission_path) != expected_permission_hash
                    ):
                        errors.append(
                            f"{incident_id}: excluded regression footage lacks an independently verified permission record"
                        )
                previous_path = media_hashes.setdefault(actual, str(media_path))
                if previous_path != str(media_path):
                    warnings.append(f"duplicate media: {media_path} and {previous_path}")

    positives = sum(outcome_counts[name] for name in ("contact", "impact"))
    hard_negatives = outcome_counts["no_contact"]
    blind_relabel_required = math.ceil(complete_items * 0.1)
    split_ready = all(split_complete_counts[name] > 0 for name in ("train", "validation", "test"))
    if complete_items and not split_ready:
        warnings.append("complete labels must include source-isolated train, validation, and locked test groups")
    return {
        "schema_version": "mimir_dataset_audit_v1",
        "generated_at": utc_now(),
        "dataset_root": str(dataset_root.resolve()),
        "passed": not errors,
        "training_ready": not errors
        and complete_items >= 100
        and positives >= 25
        and hard_negatives >= 25
        and blind_relabel_items >= blind_relabel_required
        and split_ready,
        "collections": len(collections),
        "items": total_items,
        "complete_items": complete_items,
        "positive_items": positives,
        "hard_negative_items": hard_negatives,
        "blind_relabel_items": blind_relabel_items,
        "blind_relabel_required": blind_relabel_required,
        "intra_annotator_agreement": {
            "severity_exact": blind_severity_matches / blind_relabel_items if blind_relabel_items else None,
            "outcome_exact": blind_outcome_matches / blind_relabel_items if blind_relabel_items else None,
            "timing_within_one_sec": blind_timing_within_one_sec / blind_timing_comparable if blind_timing_comparable else None,
            "timing_comparable_items": blind_timing_comparable,
        },
        "object_annotations": object_annotations,
        "mask_annotations": mask_annotations,
        "severity_counts": dict(severity_counts),
        "outcome_counts": dict(outcome_counts),
        "complete_split_counts": dict(split_complete_counts),
        "errors": errors,
        "warnings": warnings,
        "release_targets": {
            "event_groups": 2500,
            "impact_contact_positives": 400,
            "locked_test_groups": 750,
            "locked_test_positives": 300,
            "locked_test_hard_negatives": 300,
        },
    }


def print_audit(report: dict[str, Any]) -> None:
    status = "READY" if report.get("training_ready") else "NOT READY"
    print(f"Mimir training data: {status}")
    print(f"collections: {report.get('collections', 0)}")
    print(f"items: {report.get('items', 0)}")
    print(f"complete labels: {report.get('complete_items', 0)}")
    print(f"positive / hard negative: {report.get('positive_items', 0)} / {report.get('hard_negative_items', 0)}")
    print(
        "blind re-labels: "
        f"{report.get('blind_relabel_items', 0)} / {report.get('blind_relabel_required', 0)} required"
    )
    for error in report.get("errors", []):
        print(f"ERROR: {error}")
    for warning in report.get("warnings", [])[:20]:
        print(f"WARNING: {warning}")


def show_sources() -> int:
    registry = read_json(SOURCE_REGISTRY)
    print("Mimir dataset source policy")
    print("===========================")
    for source in registry.get("sources", []):
        if isinstance(source, dict):
            print(f"{source.get('id')}: {source.get('status')}")
            print(f"  {source.get('name')}")
            if source.get("notes"):
                print(f"  {source.get('notes')}")
    return 0


def fetch_source(args: argparse.Namespace) -> int:
    registry = read_json(SOURCE_REGISTRY)
    source = next(
        (item for item in registry.get("sources", []) if isinstance(item, dict) and item.get("id") == args.source),
        None,
    )
    if not source:
        raise ValueError(f"unknown dataset source: {args.source}")
    if args.source != "nexar_collision_prediction":
        raise ValueError(f"automatic retrieval is not allowed for source status: {source.get('status')}")
    if args.include_footage and not args.accept_license:
        raise ValueError("--accept-license is required before downloading Nexar footage")
    try:
        from huggingface_hub import snapshot_download
    except ImportError as exc:
        raise ValueError("Install requirements-training.txt to retrieve approved datasets") from exc

    output = Path(args.output)
    patterns = ["README.md", "LICENSE", "*.csv"]
    if args.include_footage:
        patterns.extend(["train/**"])
    snapshot_download(
        repo_id=str(source["repository"]),
        repo_type="dataset",
        local_dir=str(output),
        allow_patterns=patterns,
    )
    receipt = {
        "schema_version": "mimir_external_dataset_receipt_v1",
        "source_id": args.source,
        "retrieved_at": utc_now(),
        "include_footage": bool(args.include_footage),
        "license_accepted": bool(args.accept_license),
        "license_url": source.get("license_url"),
        "attribution_required": True,
        "automatic_training": False,
    }
    write_json(output / "MIMIR_SOURCE_RECEIPT.json", receipt)
    print(f"Source files retrieved to {output}")
    if not args.include_footage:
        print("Only metadata and license files were retrieved; no footage was downloaded.")
    return 0


def audit_command(args: argparse.Namespace) -> int:
    root = Path(args.dataset_root)
    report = audit_dataset(root)
    report_path = Path(args.report) if args.report else root / "dataset_audit_report.json"
    write_json(report_path, report)
    print_audit(report)
    print(f"report: {report_path}")
    return 0 if report.get("passed") else 1


def _media_for_object(collection: Path, record: dict[str, Any], obj: dict[str, Any]) -> Path | None:
    requested = str(obj.get("media_relative_path") or obj.get("media_filename") or "")
    media = record.get("media") if isinstance(record.get("media"), list) else []
    for item in media:
        if not isinstance(item, dict):
            continue
        relative = str(item.get("relative_path") or "")
        if requested and requested not in {relative, str(item.get("filename") or "")}:
            continue
        path = collection / relative
        if path.exists():
            return path
    return None


def prepare_dataset(args: argparse.Namespace) -> int:
    import cv2  # type: ignore

    dataset_root = Path(args.dataset_root)
    output = Path(args.output)
    audit = audit_dataset(dataset_root)
    if audit["errors"]:
        print_audit(audit)
        return 1
    split_names = {"train": "train", "validation": "valid", "test": "test"}
    coco: dict[str, dict[str, Any]] = {
        name: {
            "info": {"description": "Mimir consented contact dataset", "version": TRAINING_SCHEMA},
            "images": [],
            "annotations": [],
            "categories": [{"id": value, "name": key} for key, value in CATEGORIES.items()],
        }
        for name in split_names.values()
    }
    temporal_items: list[dict[str, Any]] = []
    image_id = 1
    annotation_id = 1

    for collection in collection_roots(dataset_root):
        manifest = read_json(collection / "manifest.json")
        for item in manifest.get("items", []) if isinstance(manifest.get("items"), list) else []:
            if not isinstance(item, dict):
                continue
            record = read_json(collection / str(item.get("annotation") or ""))
            annotation = record.get("annotation") if isinstance(record.get("annotation"), dict) else {}
            source_split = str(record.get("split") or item.get("split") or "train")
            coco_split = split_names.get(source_split, "train")
            temporal_items.append(
                {
                    "incident_id": record.get("incident_id"),
                    "source_group_hash": record.get("source_group_hash"),
                    "split": source_split,
                    "media": [str((collection / str(media.get("relative_path") or "")).resolve()) for media in record.get("media", []) if isinstance(media, dict)],
                    "duration_sec": record.get("duration_sec"),
                    "human_severity": annotation.get("human_severity"),
                    "contact_outcome": annotation.get("contact_outcome"),
                    "closest_approach_time_sec": annotation.get("closest_approach_time_sec"),
                    "apparent_contact_time_sec": annotation.get("apparent_contact_time_sec"),
                    "impact_time_sec": annotation.get("impact_time_sec"),
                    "camera": record.get("camera"),
                    "objects": annotation.get("objects") if isinstance(annotation.get("objects"), list) else [],
                    "vehicle_door_involved": any(
                        isinstance(value, dict) and value.get("class_name") == "vehicle_door"
                        for value in annotation.get("objects", [])
                    ) if isinstance(annotation.get("objects"), list) else False,
                    "category": (
                        "door_contact"
                        if isinstance(annotation.get("objects"), list)
                        and any(
                            isinstance(value, dict) and value.get("class_name") == "vehicle_door"
                            for value in annotation.get("objects", [])
                        )
                        and annotation.get("contact_outcome") in {"contact", "impact"}
                        else annotation.get("contact_outcome")
                    ),
                    "ego_vehicle_polygons": annotation.get("ego_vehicle_polygons") if isinstance(annotation.get("ego_vehicle_polygons"), list) else [],
                    "foreign_object_masks": annotation.get("foreign_object_masks") if isinstance(annotation.get("foreign_object_masks"), list) else [],
                }
            )
            grouped: dict[tuple[str, float], list[dict[str, Any]]] = defaultdict(list)
            for obj in annotation.get("objects", []) if isinstance(annotation.get("objects"), list) else []:
                if not isinstance(obj, dict) or str(obj.get("class_name") or "") not in CATEGORIES:
                    continue
                media_path = _media_for_object(collection, record, obj)
                if media_path is None:
                    continue
                grouped[(str(media_path), float(obj.get("time_sec") or 0.0))].append(obj)
            for (media_name, time_sec), objects in grouped.items():
                media_path = Path(media_name)
                capture = cv2.VideoCapture(str(media_path))
                try:
                    capture.set(cv2.CAP_PROP_POS_MSEC, max(0.0, time_sec) * 1000.0)
                    ok, frame = capture.read()
                finally:
                    capture.release()
                if not ok or frame is None:
                    continue
                split_dir = output / coco_split
                split_dir.mkdir(parents=True, exist_ok=True)
                filename = f"{image_id:08d}.jpg"
                image_path = split_dir / filename
                if not cv2.imwrite(str(image_path), frame, [int(cv2.IMWRITE_JPEG_QUALITY), 95]):
                    continue
                height, width = frame.shape[:2]
                coco[coco_split]["images"].append(
                    {
                        "id": image_id,
                        "file_name": filename,
                        "width": int(width),
                        "height": int(height),
                        "source_group_hash": record.get("source_group_hash"),
                        "time_sec": time_sec,
                    }
                )
                for obj in objects:
                    box = obj.get("bbox_xyxy")
                    if not isinstance(box, list) or len(box) != 4:
                        continue
                    x1, y1, x2, y2 = (float(value) for value in box)
                    box_width = max(0.0, x2 - x1)
                    box_height = max(0.0, y2 - y1)
                    if box_width <= 0 or box_height <= 0:
                        continue
                    entry = {
                        "id": annotation_id,
                        "image_id": image_id,
                        "category_id": CATEGORIES[str(obj["class_name"])],
                        "bbox": [x1, y1, box_width, box_height],
                        "area": box_width * box_height,
                        "iscrowd": 0,
                    }
                    segmentation = obj.get("segmentation")
                    if isinstance(segmentation, list) and segmentation:
                        entry["segmentation"] = segmentation
                    coco[coco_split]["annotations"].append(entry)
                    annotation_id += 1
                image_id += 1

    for split, payload in coco.items():
        split_dir = output / split
        split_dir.mkdir(parents=True, exist_ok=True)
        write_json(split_dir / "_annotations.coco.json", payload)
    training_manifest = {
        "schema_version": TRAINING_SCHEMA,
        "created_at": utc_now(),
        "source_dataset_root": str(dataset_root.resolve()),
        "source_audit": audit,
        "object_frames": sum(len(item["images"]) for item in coco.values()),
        "object_annotations": sum(len(item["annotations"]) for item in coco.values()),
        "segmentation_annotations": sum(
            1 for item in coco.values() for annotation in item["annotations"] if annotation.get("segmentation")
        ),
        "temporal_items": temporal_items,
    }
    write_json(output / "training_manifest.json", training_manifest)
    print(f"Prepared {training_manifest['object_frames']} real annotated frames in {output}")
    print(f"Temporal event records: {len(temporal_items)}")
    if not temporal_items:
        print("TRAINING BLOCKED: no consented, human-labeled temporal events are available.")
        return 2
    return 0


def train_model(args: argparse.Namespace) -> int:
    prepared = Path(args.prepared)
    manifest = read_json(prepared / "training_manifest.json")
    audit = manifest.get("source_audit") if isinstance(manifest.get("source_audit"), dict) else {}
    groups = int(audit.get("complete_items") or 0)
    positives = int(audit.get("positive_items") or 0)
    hard_negatives = int(audit.get("hard_negative_items") or 0)
    split_counts = audit.get("complete_split_counts") if isinstance(audit.get("complete_split_counts"), dict) else {}
    frames = int(manifest.get("object_frames") or 0)
    masks = int(manifest.get("segmentation_annotations") or 0)
    blind_relabels = int(audit.get("blind_relabel_items") or 0)
    required_relabels = math.ceil(groups * 0.1)
    blockers: list[str] = []
    if groups < args.min_groups:
        blockers.append(f"{groups} complete groups; pilot minimum is {args.min_groups}")
    if positives < args.min_positives:
        blockers.append(f"{positives} positives; pilot minimum is {args.min_positives}")
    if hard_negatives < args.min_hard_negatives:
        blockers.append(f"{hard_negatives} hard negatives; pilot minimum is {args.min_hard_negatives}")
    if blind_relabels < required_relabels:
        blockers.append(f"{blind_relabels} blind re-labels; at least {required_relabels} (10%) are required")
    for split_name in ("train", "validation", "test"):
        if int(split_counts.get(split_name) or 0) <= 0:
            blockers.append(f"no complete source-isolated {split_name} groups are available")
    if frames < args.min_frames:
        blockers.append(f"{frames} object frames; pilot minimum is {args.min_frames}")
    if args.model == "segmentation" and masks < args.min_frames:
        blockers.append(f"{masks} masks; segmentation training requires one for each pilot frame")
    if blockers:
        print("TRAINING BLOCKED")
        for blocker in blockers:
            print(f"- {blocker}")
        print("Mimir will not overfit a release model to a handful of regression clips.")
        return 2

    try:
        from rfdetr import RFDETRNano, RFDETRSegNano
    except ImportError:
        print("Install requirements-training.txt before training.", file=sys.stderr)
        return 1
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + hashlib.sha256(str(prepared.resolve()).encode()).hexdigest()[:8]
    run_dir = Path(args.output) / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    run_manifest = {
        "schema_version": "mimir_training_run_v1",
        "run_id": run_id,
        "started_at": utc_now(),
        "prepared_dataset": str(prepared.resolve()),
        "dataset_manifest_sha256": sha256(prepared / "training_manifest.json"),
        "candidate_kind": "rfdetr_segmentation" if args.model == "segmentation" else "rfdetr_detection",
        "model": args.model,
        "epochs": args.epochs,
        "batch_size": args.batch_size,
        "status": "running",
    }
    write_json(run_dir / "run_manifest.json", run_manifest)
    model = RFDETRSegNano() if args.model == "segmentation" else RFDETRNano()
    try:
        model.train(
            dataset_dir=str(prepared),
            output_dir=str(run_dir / "output"),
            epochs=args.epochs,
            batch_size=args.batch_size,
        )
        exported = Path(
            model.export(
                output_dir=str(run_dir / "candidate"),
                shape=(384, 384),
                batch_size=1,
                dynamic_batch=False,
                format="onnx",
                notes="Unpromoted Mimir consented-data candidate; locked evaluation required.",
            )
        )
        if not exported.exists():
            raise RuntimeError("RF-DETR export did not produce an ONNX artifact")
    except Exception as exc:
        run_manifest["status"] = "failed"
        run_manifest["error"] = f"{type(exc).__name__}: {exc}"
        write_json(run_dir / "run_manifest.json", run_manifest)
        raise
    run_manifest["status"] = "complete"
    run_manifest["completed_at"] = utc_now()
    run_manifest["artifacts"] = [{"path": str(exported.resolve()), "sha256": sha256(exported)}]
    candidate_manifest = {
        "schema_version": "mimir_candidate_model_manifest_v1",
        "candidate_id": f"mimir-rfdetr-{args.model}-{run_id}",
        "candidate_kind": "rfdetr_segmentation" if args.model == "segmentation" else "rfdetr_detection",
        "created_at": utc_now(),
        "model_path": str(exported.resolve()),
        "sha256": sha256(exported),
        "license": "RF-DETR Apache-2.0 code/weights with Mimir consented fine-tuning data",
        "promoted": False,
        "outputs": ["ego_vehicle", "person", "vehicle", "vehicle_door"],
        "class_names": ["person", "vehicle", "vehicle_door", "ego_vehicle"],
        "inference_policy": {
            "frozen_before_locked_evaluation": True,
            "confidence_threshold": 0.3,
            "max_detections_per_frame": 24,
            "mask_threshold": 0.5,
        },
        "training_run_id": run_id,
    }
    write_json(run_dir / "run_manifest.json", run_manifest)
    write_json(run_dir / "candidate_model_manifest.json", candidate_manifest)
    print(f"Training complete: {run_dir}")
    print("The trained artifact is not a release model until locked evaluation passes.")
    return 0


def extract_temporal_command(args: argparse.Namespace) -> int:
    from mimir_core_v2.temporal_training import extract_feature_dataset

    result = extract_feature_dataset(Path(args.prepared), Path(args.output), args.sample_fps)
    print(f"Temporal sequences: {len(result.get('sequences') or [])}")
    print(f"Temporal feature manifest: {Path(args.output) / 'temporal_features_manifest.json'}")
    if result.get("errors"):
        print(f"Skipped unreadable sequences: {len(result['errors'])}")
    return 0


def infer_perception_command(args: argparse.Namespace) -> int:
    from mimir_core_v2.candidate_perception import infer_candidate_perception

    result = infer_candidate_perception(
        Path(args.prepared), Path(args.model_manifest), Path(args.output), args.sample_fps
    )
    print(json.dumps(result, indent=2))
    print("Candidate perception remains isolated from the production scanner.")
    return 0


def train_temporal_command(args: argparse.Namespace) -> int:
    from mimir_core_v2.temporal_training import train_temporal_candidate

    result = train_temporal_candidate(
        Path(args.features),
        Path(args.prepared),
        Path(args.output),
        epochs=args.epochs,
        learning_rate=args.learning_rate,
        min_groups=args.min_groups,
        min_positives=args.min_positives,
        min_negatives=args.min_hard_negatives,
    )
    print(json.dumps(result, indent=2))
    print("Candidate remains unpromoted until locked evaluation passes.")
    return 0


def predict_temporal_command(args: argparse.Namespace) -> int:
    from mimir_core_v2.temporal_training import predict_temporal_candidate

    result = predict_temporal_candidate(
        Path(args.features),
        Path(args.model_manifest),
        Path(args.output),
        split=args.split,
        training_diagnostics_only=args.training_diagnostics_only,
    )
    print(f"Candidate predictions: {len(result.get('items') or [])}")
    print(f"release eligible: {result.get('release_eligible')}")
    print(f"report: {args.output}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Mimir consented training pipeline.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("sources", help="Show reviewed dataset candidates and license status.")

    fetch = commands.add_parser("fetch-source", help="Retrieve a registry-approved source or metadata.")
    fetch.add_argument("--source", required=True)
    fetch.add_argument("--output", required=True)
    fetch.add_argument("--include-footage", action="store_true")
    fetch.add_argument("--accept-license", action="store_true")

    audit = commands.add_parser("audit", help="Audit consent, rights, labels, duplicates, and split leakage.")
    audit.add_argument("--dataset-root", required=True)
    audit.add_argument("--report", default="")

    prepare = commands.add_parser("prepare", help="Extract annotated real frames and temporal manifests.")
    prepare.add_argument("--dataset-root", required=True)
    prepare.add_argument("--output", required=True)

    train = commands.add_parser("train", help="Fine-tune RF-DETR after pilot data gates pass.")
    train.add_argument("--prepared", required=True)
    train.add_argument("--output", default=str(ROOT / "training_runs"))
    train.add_argument("--model", choices=("detection", "segmentation"), default="segmentation")
    train.add_argument("--epochs", type=int, default=50)
    train.add_argument("--batch-size", type=int, default=4)
    train.add_argument("--min-groups", type=int, default=100)
    train.add_argument("--min-positives", type=int, default=25)
    train.add_argument("--min-hard-negatives", type=int, default=25)
    train.add_argument("--min-frames", type=int, default=250)
    temporal = commands.add_parser("extract-temporal", help="Extract dense candidate-only temporal features at 10-15 FPS.")
    temporal.add_argument("--prepared", required=True)
    temporal.add_argument("--output", required=True)
    temporal.add_argument("--sample-fps", type=float, default=15.0)
    perception = commands.add_parser(
        "infer-perception", help="Run an unpromoted RF-DETR candidate over prepared footage."
    )
    perception.add_argument("--prepared", required=True)
    perception.add_argument("--model-manifest", required=True)
    perception.add_argument("--output", required=True)
    perception.add_argument("--sample-fps", type=float, default=15.0)
    train_temporal = commands.add_parser("train-temporal", help="Train an unpromoted compact temporal ONNX verifier.")
    train_temporal.add_argument("--features", required=True)
    train_temporal.add_argument("--prepared", required=True)
    train_temporal.add_argument("--output", default=str(ROOT / "training_runs" / "temporal"))
    train_temporal.add_argument("--epochs", type=int, default=40)
    train_temporal.add_argument("--learning-rate", type=float, default=0.001)
    train_temporal.add_argument("--min-groups", type=int, default=100)
    train_temporal.add_argument("--min-positives", type=int, default=25)
    train_temporal.add_argument("--min-hard-negatives", type=int, default=25)
    predict_temporal = commands.add_parser(
        "predict-temporal", help="Run an unpromoted temporal ONNX candidate with its frozen policy."
    )
    predict_temporal.add_argument("--features", required=True)
    predict_temporal.add_argument("--model-manifest", required=True)
    predict_temporal.add_argument("--output", required=True)
    predict_temporal.add_argument("--split", choices=("train", "validation", "test", "all"), default="test")
    predict_temporal.add_argument(
        "--training-diagnostics-only",
        action="store_true",
        help="Allow human annotation geometry, while marking output ineligible for locked evaluation.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "sources":
            return show_sources()
        if args.command == "fetch-source":
            return fetch_source(args)
        if args.command == "audit":
            return audit_command(args)
        if args.command == "prepare":
            return prepare_dataset(args)
        if args.command == "extract-temporal":
            return extract_temporal_command(args)
        if args.command == "infer-perception":
            return infer_perception_command(args)
        if args.command == "train-temporal":
            return train_temporal_command(args)
        if args.command == "predict-temporal":
            return predict_temporal_command(args)
        return train_model(args)
    except (OSError, ValueError, json.JSONDecodeError, RuntimeError) as exc:
        print(f"Training pipeline error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
