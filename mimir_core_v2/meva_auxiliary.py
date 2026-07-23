"""Licensed MEVA auxiliary pretraining for stationary activity context.

This model distinguishes annotated vehicle-door activity from selected hard
negatives using only geometry/tracking features. It is a shadow pretrainer,
not a physical-contact detector, and is never eligible for production
promotion on its own.
"""

from __future__ import annotations

import ast
import hashlib
import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


MODEL_VERSION = "meva_stationary_auxiliary_v1"
FEATURE_NAMES = (
    "person_track_count",
    "vehicle_track_count",
    "person_observation_log",
    "vehicle_observation_log",
    "person_max_area_ratio",
    "vehicle_max_area_ratio",
    "person_mean_speed_ratio",
    "vehicle_mean_speed_ratio",
    "person_max_span_ratio",
    "vehicle_max_span_ratio",
    "minimum_person_vehicle_gap",
    "maximum_person_vehicle_overlap",
    "close_pair_fraction",
)


class AuxiliaryTrainingError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _line_payload(lines: Iterable[str], key: str) -> Iterable[dict[str, Any]]:
    for line in lines:
        text = line.strip()
        if text.startswith("- "):
            text = text[2:]
        if not text or f"'{key}'" not in text:
            continue
        try:
            payload = ast.literal_eval(text)
        except (SyntaxError, ValueError):
            continue
        value = payload.get(key) if isinstance(payload, dict) else None
        if isinstance(value, dict):
            yield value


def _read_types(path: Path) -> dict[int, str]:
    result: dict[int, str] = {}
    with path.open("r", encoding="utf-8") as handle:
        for item in _line_payload(handle, "types"):
            classes = item.get("cset3") if isinstance(item.get("cset3"), dict) else {}
            class_name = next((str(name) for name, score in classes.items() if float(score or 0) > 0), "")
            if class_name in {"person", "vehicle"}:
                result[int(item.get("id1") or 0)] = class_name
    return result


def _video_shape(path: Path) -> tuple[int, int, float, int]:
    try:
        import cv2  # type: ignore

        capture = cv2.VideoCapture(str(path))
        try:
            if capture.isOpened():
                return (
                    max(1, int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 1)),
                    max(1, int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 1)),
                    max(0.0, float(capture.get(cv2.CAP_PROP_FPS) or 0.0)),
                    max(0, int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)),
                )
        finally:
            capture.release()
    except Exception:
        pass
    return 1920, 1080, 30.0, 0


def _normalized_box(raw: object, width: int, height: int) -> tuple[float, float, float, float] | None:
    try:
        x1, y1, x2, y2 = (float(value) for value in str(raw).split())
    except (TypeError, ValueError):
        return None
    if x2 <= x1 or y2 <= y1:
        return None
    return (
        max(0.0, min(1.0, x1 / width)),
        max(0.0, min(1.0, y1 / height)),
        max(0.0, min(1.0, x2 / width)),
        max(0.0, min(1.0, y2 / height)),
    )


def _area(box: tuple[float, float, float, float]) -> float:
    return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])


def _center(box: tuple[float, float, float, float]) -> tuple[float, float]:
    return ((box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0)


def _gap_and_overlap(
    left: tuple[float, float, float, float],
    right: tuple[float, float, float, float],
) -> tuple[float, float]:
    dx = max(left[0] - right[2], right[0] - left[2], 0.0)
    dy = max(left[1] - right[3], right[1] - left[3], 0.0)
    overlap_width = max(0.0, min(left[2], right[2]) - max(left[0], right[0]))
    overlap_height = max(0.0, min(left[3], right[3]) - max(left[1], right[1]))
    overlap = overlap_width * overlap_height / max(min(_area(left), _area(right)), 1e-6)
    return math.hypot(dx, dy), min(1.0, overlap)


def extract_record_features(record: dict[str, Any]) -> dict[str, Any]:
    annotation_files = record.get("annotation_files") if isinstance(record.get("annotation_files"), dict) else {}
    types_path = Path(str(annotation_files.get("types") or ""))
    geometry_path = Path(str(annotation_files.get("geometry") or ""))
    video_path = Path(str(record.get("local_path") or ""))
    if not types_path.is_file() or not geometry_path.is_file() or not video_path.is_file():
        raise AuxiliaryTrainingError(f"MEVA record is incomplete: {record.get('video_name')}")

    object_types = _read_types(types_path)
    width, height, fps, frame_count = _video_shape(video_path)
    tracks: dict[int, dict[str, Any]] = {}
    proximity_frames: dict[int, dict[str, list[tuple[float, float, float, float]]]] = {}
    with geometry_path.open("r", encoding="utf-8") as handle:
        for item in _line_payload(handle, "geom"):
            track_id = int(item.get("id1") or 0)
            class_name = object_types.get(track_id)
            if class_name not in {"person", "vehicle"}:
                continue
            box = _normalized_box(item.get("g0"), width, height)
            if box is None:
                continue
            frame_index = int(item.get("ts0") or 0)
            state = tracks.setdefault(
                track_id,
                {
                    "class_name": class_name,
                    "count": 0,
                    "area_max": 0.0,
                    "distance_sum": 0.0,
                    "first_frame": frame_index,
                    "last_frame": frame_index,
                    "last_center": None,
                },
            )
            center = _center(box)
            previous = state["last_center"]
            if previous is not None:
                state["distance_sum"] += math.hypot(center[0] - previous[0], center[1] - previous[1])
            state["last_center"] = center
            state["count"] += 1
            state["area_max"] = max(state["area_max"], _area(box))
            state["first_frame"] = min(state["first_frame"], frame_index)
            state["last_frame"] = max(state["last_frame"], frame_index)
            if bool(item.get("keyframe")) or frame_index % 10 == 0:
                frame = proximity_frames.setdefault(frame_index, {"person": [], "vehicle": []})
                frame[class_name].append(box)

    by_class = {
        class_name: [item for item in tracks.values() if item["class_name"] == class_name]
        for class_name in ("person", "vehicle")
    }
    frame_denominator = max(frame_count, max((item["last_frame"] for item in tracks.values()), default=1), 1)
    pair_count = 0
    close_count = 0
    minimum_gap = 1.0
    maximum_overlap = 0.0
    for frame in proximity_frames.values():
        for person in frame["person"]:
            for vehicle in frame["vehicle"]:
                gap, overlap = _gap_and_overlap(person, vehicle)
                minimum_gap = min(minimum_gap, gap)
                maximum_overlap = max(maximum_overlap, overlap)
                pair_count += 1
                if gap <= 0.05 or overlap > 0:
                    close_count += 1

    def aggregate(class_name: str) -> tuple[float, float, float, float, float]:
        values = by_class[class_name]
        observations = sum(int(item["count"]) for item in values)
        max_area = max((float(item["area_max"]) for item in values), default=0.0)
        speeds = [float(item["distance_sum"]) / max(int(item["count"]) - 1, 1) for item in values]
        max_span = max(
            ((int(item["last_frame"]) - int(item["first_frame"])) / frame_denominator for item in values),
            default=0.0,
        )
        return float(len(values)), math.log1p(observations), max_area, sum(speeds) / max(len(speeds), 1), max_span

    person = aggregate("person")
    vehicle = aggregate("vehicle")
    features = [
        person[0],
        vehicle[0],
        person[1],
        vehicle[1],
        person[2],
        vehicle[2],
        person[3],
        vehicle[3],
        person[4],
        vehicle[4],
        minimum_gap,
        maximum_overlap,
        close_count / max(pair_count, 1),
    ]
    role = str(record.get("role") or "")
    target = 1 if role == "door_activity" else 0 if role == "hard_negative" else None
    return {
        "source_event_group": str(record.get("source_event_group") or ""),
        "video_name": str(record.get("video_name") or ""),
        "source_sha256": str(record.get("sha256") or ""),
        "split": str(record.get("split") or ""),
        "source_role": role,
        "target": target,
        "feature_names": list(FEATURE_NAMES),
        "features": [round(float(value), 8) for value in features],
        "object_tracks": len(tracks),
        "fps": round(fps, 3),
        "frame_count": frame_count,
        "license": "CC-BY-4.0",
        "contact_ground_truth_available": False,
    }


def _metrics(targets: Any, probabilities: Any) -> dict[str, Any]:
    predictions = (probabilities >= 0.5).astype("int64")
    true_positive = int(((predictions == 1) & (targets == 1)).sum())
    true_negative = int(((predictions == 0) & (targets == 0)).sum())
    false_positive = int(((predictions == 1) & (targets == 0)).sum())
    false_negative = int(((predictions == 0) & (targets == 1)).sum())
    return {
        "count": int(len(targets)),
        "accuracy": round((true_positive + true_negative) / max(len(targets), 1), 4),
        "precision": round(true_positive / max(true_positive + false_positive, 1), 4),
        "recall": round(true_positive / max(true_positive + false_negative, 1), 4),
        "confusion_matrix": {
            "true_positive": true_positive,
            "true_negative": true_negative,
            "false_positive": false_positive,
            "false_negative": false_negative,
        },
    }


def train_auxiliary(rows: list[dict[str, Any]], epochs: int = 2500) -> dict[str, Any]:
    try:
        import numpy as np  # type: ignore
    except Exception as exc:
        raise AuxiliaryTrainingError(f"NumPy is required for auxiliary pretraining: {exc}") from exc

    usable = [row for row in rows if row.get("target") in {0, 1}]
    train = [row for row in usable if row.get("split") == "train_auxiliary"]
    validation = [row for row in usable if row.get("split") == "validation_auxiliary"]
    if not train or not validation:
        raise AuxiliaryTrainingError("Both source-isolated train and validation rows are required.")
    train_groups = {row["source_event_group"] for row in train}
    validation_groups = {row["source_event_group"] for row in validation}
    overlap = train_groups & validation_groups
    if overlap:
        raise AuxiliaryTrainingError(f"Physical-event split leakage detected: {sorted(overlap)[:3]}")

    x_train = np.asarray([row["features"] for row in train], dtype=np.float64)
    y_train = np.asarray([row["target"] for row in train], dtype=np.float64)
    mean = x_train.mean(axis=0)
    scale = x_train.std(axis=0)
    scale[scale < 1e-8] = 1.0
    normalized = (x_train - mean) / scale
    weights = np.zeros(normalized.shape[1], dtype=np.float64)
    bias = 0.0
    positives = max(float(y_train.sum()), 1.0)
    negatives = max(float(len(y_train) - y_train.sum()), 1.0)
    sample_weights = np.where(y_train > 0.5, len(y_train) / (2 * positives), len(y_train) / (2 * negatives))
    learning_rate = 0.04
    regularization = 0.01
    for _ in range(max(100, int(epochs))):
        logits = np.clip(normalized @ weights + bias, -30.0, 30.0)
        probabilities = 1.0 / (1.0 + np.exp(-logits))
        error = (probabilities - y_train) * sample_weights
        weights -= learning_rate * ((normalized.T @ error) / len(y_train) + regularization * weights)
        bias -= learning_rate * float(error.mean())

    def predict(rows_for_split: list[dict[str, Any]]) -> Any:
        matrix = np.asarray([row["features"] for row in rows_for_split], dtype=np.float64)
        logits = np.clip(((matrix - mean) / scale) @ weights + bias, -30.0, 30.0)
        return 1.0 / (1.0 + np.exp(-logits))

    train_probabilities = predict(train)
    validation_probabilities = predict(validation)
    return {
        "weights": [round(float(value), 10) for value in weights],
        "bias": round(float(bias), 10),
        "feature_mean": [round(float(value), 10) for value in mean],
        "feature_scale": [round(float(value), 10) for value in scale],
        "train_metrics": _metrics(y_train.astype("int64"), train_probabilities),
        "validation_metrics": _metrics(
            np.asarray([row["target"] for row in validation], dtype="int64"),
            validation_probabilities,
        ),
        "validation_predictions": [
            {
                "video_name": row["video_name"],
                "target": int(row["target"]),
                "door_activity_probability": round(float(probability), 6),
            }
            for row, probability in zip(validation, validation_probabilities)
        ],
    }


def run_training(manifest_path: Path, output_dir: Path, epochs: int = 2500) -> dict[str, Any]:
    started = time.perf_counter()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != "mimir_stationary_auxiliary_manifest_v1":
        raise AuxiliaryTrainingError("Unsupported stationary auxiliary manifest.")
    if manifest.get("promotion_eligible") is not False or manifest.get("contact_ground_truth_available") is not False:
        raise AuxiliaryTrainingError("Auxiliary safety policy is missing from the source manifest.")

    records = [record for record in manifest.get("records", []) if record.get("downloaded")]
    rows = [extract_record_features(record) for record in records if record.get("role") in {"door_activity", "hard_negative"}]
    result = train_auxiliary(rows, epochs=epochs)
    output_dir.mkdir(parents=True, exist_ok=True)
    features_path = output_dir / "meva_auxiliary_features.jsonl"
    features_path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8")
    feature_hash = hashlib.sha256(features_path.read_bytes()).hexdigest()
    model = {
        "schema_version": "mimir_auxiliary_model_manifest_v1",
        "model_version": MODEL_VERSION,
        "created_at": utc_now(),
        "purpose": "stationary door-activity and hard-negative representation pretraining",
        "production_detector": False,
        "contact_detector": False,
        "promotion_eligible": False,
        "contact_ground_truth_available": False,
        "physical_contact_claim_allowed": False,
        "source_id": manifest.get("source_id"),
        "source_manifest": str(manifest_path.resolve()),
        "source_license": manifest.get("license"),
        "source_attribution": manifest.get("attribution"),
        "feature_file": str(features_path.resolve()),
        "feature_sha256": feature_hash,
        "feature_names": list(FEATURE_NAMES),
        "row_count": len(rows),
        "epochs": int(epochs),
        "runtime_sec": round(time.perf_counter() - started, 3),
        **result,
    }
    checksum_payload = json.dumps(model, sort_keys=True, separators=(",", ":")).encode("utf-8")
    model["model_checksum"] = hashlib.sha256(checksum_payload).hexdigest()
    model_path = output_dir / "meva_auxiliary_model.json"
    temporary = model_path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(model, indent=2) + "\n", encoding="utf-8")
    temporary.replace(model_path)
    return model
