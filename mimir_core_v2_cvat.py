"""Manage Mimir's pinned, self-hosted CVAT annotation project."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from mimir_core_v2.cvat_client import CvatClient, CvatError, PROJECT_DEFINITION, configured_token


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Mimir local CVAT integration.")
    parser.add_argument("--url", default="http://localhost:8080")
    parser.add_argument("--token", default="")
    parser.add_argument("--token-file", default="")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("health")
    project = commands.add_parser("ensure-project")
    project.add_argument("--definition", default=str(PROJECT_DEFINITION))
    task = commands.add_parser("create-task")
    task.add_argument("--project-id", type=int, required=True)
    task.add_argument("--name", required=True)
    task.add_argument("--split", choices=("train", "validation", "test"), required=True)
    task.add_argument("--media", action="append", default=[], required=True)
    sync = commands.add_parser("sync-annotations")
    sync.add_argument("--task-id", type=int, required=True)
    sync.add_argument("--dataset-root", required=True)
    return parser


def _find_task_record(dataset_root: Path, task_id: int) -> tuple[Path, dict[str, object], dict[str, object]]:
    for intake_path in dataset_root.glob("collections/*/intake_record.json"):
        intake = json.loads(intake_path.read_text(encoding="utf-8"))
        for task in intake.get("cvat_tasks", []) if isinstance(intake.get("cvat_tasks"), list) else []:
            if isinstance(task, dict) and int(task.get("task_id") or -1) == task_id:
                collection = intake_path.parent
                manifest = json.loads((collection / "manifest.json").read_text(encoding="utf-8"))
                task_name = str(task.get("name") or "")
                incident_id = task_name.rsplit(" | ", 1)[-1]
                item = next(
                    (
                        value
                        for value in manifest.get("items", [])
                        if isinstance(value, dict) and str(value.get("incident_id") or "") == incident_id
                    ),
                    None,
                )
                if isinstance(item, dict):
                    return collection, intake, item
    raise CvatError(f"Task {task_id} is not registered in this Mimir dataset root.")


def _attribute_values(raw: object, specs: dict[int, dict[str, object]]) -> dict[str, object]:
    values: dict[str, object] = {}
    if not isinstance(raw, list):
        return values
    for item in raw:
        if not isinstance(item, dict):
            continue
        spec = specs.get(int(item.get("spec_id") or -1), {})
        name = str(spec.get("name") or "")
        if name:
            values[name] = item.get("value")
    return values


def _frame_time(frame: int, fps: float, duration: float, task_size: int) -> float:
    if fps > 0:
        return max(0.0, frame / fps)
    if duration > 0 and task_size > 1:
        return max(0.0, frame * duration / (task_size - 1))
    return float(frame)


def sync_annotations(client: CvatClient, task_id: int, dataset_root: Path) -> dict[str, object]:
    collection, intake, item = _find_task_record(dataset_root, task_id)
    annotation_path = collection / str(item.get("annotation") or "")
    record = json.loads(annotation_path.read_text(encoding="utf-8"))
    raw = client.task_annotations(task_id)
    task = client.task(task_id)
    project_id = int(intake.get("cvat_project_id") or task.get("project_id") or 0)
    labels = client.project_labels(project_id)
    label_by_id = {int(label["id"]): label for label in labels if isinstance(label.get("id"), int)}
    specs: dict[int, dict[str, object]] = {}
    for label in labels:
        for spec in label.get("attributes", []) if isinstance(label.get("attributes"), list) else []:
            if isinstance(spec, dict) and isinstance(spec.get("id"), int):
                specs[int(spec["id"])] = spec

    duration = float(record.get("duration_sec") or 0.0)
    fps = 0.0
    media = record.get("media") if isinstance(record.get("media"), list) else []
    first_media = collection / str(media[0].get("relative_path") or "") if media and isinstance(media[0], dict) else None
    if first_media and first_media.is_file():
        try:
            import cv2  # type: ignore

            capture = cv2.VideoCapture(str(first_media))
            try:
                fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
            finally:
                capture.release()
        except ImportError:
            fps = 0.0
    task_size = int(task.get("size") or 0)
    objects: list[dict[str, object]] = []

    def add_shape(shape: dict[str, object], track_id: object = None) -> None:
        label = label_by_id.get(int(shape.get("label_id") or -1), {})
        name = str(label.get("name") or "")
        if name not in {"ego_vehicle", "person", "vehicle", "vehicle_door"} or shape.get("outside") is True:
            return
        frame = int(shape.get("frame") or 0)
        points = shape.get("points") if isinstance(shape.get("points"), list) else []
        shape_type = str(shape.get("type") or "rectangle")
        attributes = _attribute_values(shape.get("attributes"), specs)
        value: dict[str, object] = {
            "class_name": name,
            "track_id": str(track_id) if track_id is not None else f"shape-{len(objects) + 1}",
            "frame_index": frame,
            "fps": fps if fps > 0 else None,
            "time_sec": round(_frame_time(frame, fps, duration, task_size), 4),
            "camera": attributes.get("camera") or record.get("camera") or "unknown",
        }
        if shape_type == "rectangle" and len(points) == 4:
            value["bbox_xyxy"] = [float(part) for part in points]
        elif shape_type in {"polygon", "polyline"} and len(points) >= 6:
            coordinates = [float(part) for part in points]
            xs, ys = coordinates[::2], coordinates[1::2]
            value["bbox_xyxy"] = [min(xs), min(ys), max(xs), max(ys)]
            value["segmentation"] = [coordinates]
        else:
            return
        value.update(attributes)
        objects.append(value)

    for shape in raw.get("shapes", []) if isinstance(raw.get("shapes"), list) else []:
        if isinstance(shape, dict):
            add_shape(shape)
    for track in raw.get("tracks", []) if isinstance(raw.get("tracks"), list) else []:
        if not isinstance(track, dict):
            continue
        track_attributes = track.get("attributes") if isinstance(track.get("attributes"), list) else []
        for shape in track.get("shapes", []) if isinstance(track.get("shapes"), list) else []:
            if isinstance(shape, dict):
                merged = dict(shape)
                merged["label_id"] = track.get("label_id")
                merged["attributes"] = [*track_attributes, *(shape.get("attributes") or [])]
                add_shape(merged, track.get("id"))

    event_values: dict[str, object] = {}
    for tag in raw.get("tags", []) if isinstance(raw.get("tags"), list) else []:
        if not isinstance(tag, dict):
            continue
        label = label_by_id.get(int(tag.get("label_id") or -1), {})
        if label.get("name") == "event_outcome":
            event_values.update(_attribute_values(tag.get("attributes"), specs))
    annotation = record.get("annotation") if isinstance(record.get("annotation"), dict) else {}
    annotation["objects"] = sorted(objects, key=lambda value: (float(value["time_sec"]), str(value["class_name"])))
    for name in ("contact_outcome", "human_severity", "door_state"):
        if event_values.get(name) not in (None, ""):
            annotation[name] = event_values[name]
    timing_confirmed = str(event_values.get("timing_confirmed") or "").lower() == "true"
    if timing_confirmed:
        for frame_name, time_name in (
            ("closest_approach_frame", "closest_approach_time_sec"),
            ("apparent_contact_frame", "apparent_contact_time_sec"),
            ("impact_frame", "impact_time_sec"),
        ):
            if event_values.get(frame_name) not in (None, ""):
                annotation[time_name] = round(
                    _frame_time(int(float(str(event_values[frame_name]))), fps, duration, task_size), 4
                )
    record["annotation"] = annotation
    record["annotation_updated_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    record["annotated_by"] = "cvat"
    record["cvat_task_id"] = task_id
    annotation_path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    export_dir = collection / "cvat_exports"
    export_dir.mkdir(parents=True, exist_ok=True)
    raw_path = export_dir / f"task_{task_id}_annotations.json"
    raw_path.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
    return {
        "task_id": task_id,
        "incident_id": item.get("incident_id"),
        "objects": len(objects),
        "annotation_path": str(annotation_path),
        "raw_export": str(raw_path),
        "fps": fps or None,
    }


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        token = "" if args.command == "health" else configured_token(args.token, args.token_file)
        client = CvatClient(args.url, token)
        if args.command == "health":
            result = client.health()
        elif args.command == "ensure-project":
            result = client.ensure_project(Path(args.definition))
        elif args.command == "create-task":
            result = client.create_video_task(
                args.project_id,
                args.name,
                [Path(value) for value in args.media],
                {"split": args.split},
            )
        else:
            result = sync_annotations(client, args.task_id, Path(args.dataset_root))
        print(json.dumps(result, indent=2))
        return 0
    except (CvatError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"CVAT error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
