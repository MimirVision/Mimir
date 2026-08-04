"""Run an unpromoted RF-DETR candidate over consented prepared footage.

Outputs are isolated training artifacts. This module is never imported by the
production scanner and cannot change release detection or severity behavior.
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class CandidatePerceptionError(RuntimeError):
    """Raised when candidate perception cannot produce auditable output."""


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise CandidatePerceptionError(f"Expected a JSON object: {path}")
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


def _preprocess(frame: Any, height: int, width: int) -> Any:
    import cv2  # type: ignore
    import numpy as np  # type: ignore

    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    resized = cv2.resize(rgb, (width, height), interpolation=cv2.INTER_LINEAR)
    tensor = resized.astype(np.float32) / 255.0
    tensor = (tensor - np.asarray([0.485, 0.456, 0.406], dtype=np.float32)) / np.asarray(
        [0.229, 0.224, 0.225], dtype=np.float32
    )
    return tensor.transpose(2, 0, 1)[None].astype(np.float32)


def _find_outputs(session: Any, outputs: list[Any]) -> tuple[Any, Any, Any | None]:
    names = [str(value.name).lower() for value in session.get_outputs()]
    boxes_index = next((index for index, name in enumerate(names) if "dets" in name or "box" in name), None)
    logits_index = next((index for index, name in enumerate(names) if "label" in name or "logit" in name), None)
    masks_index = next((index for index, name in enumerate(names) if "mask" in name), None)
    if boxes_index is None:
        boxes_index = next(
            (index for index, value in enumerate(outputs) if getattr(value, "ndim", 0) == 3 and value.shape[-1] == 4),
            None,
        )
    if logits_index is None:
        logits_index = next(
            (
                index
                for index, value in enumerate(outputs)
                if getattr(value, "ndim", 0) == 3 and value.shape[-1] != 4
            ),
            None,
        )
    if masks_index is None:
        masks_index = next(
            (index for index, value in enumerate(outputs) if getattr(value, "ndim", 0) == 4),
            None,
        )
    if boxes_index is None or logits_index is None:
        raise CandidatePerceptionError("RF-DETR candidate ONNX outputs could not be identified.")
    return outputs[boxes_index], outputs[logits_index], outputs[masks_index] if masks_index is not None else None


def _mask_polygon(mask: Any, width: int, height: int) -> list[list[float]]:
    import cv2  # type: ignore
    import numpy as np  # type: ignore

    values = np.asarray(mask, dtype=np.float32)
    if values.size == 0:
        return []
    if float(values.min()) < 0.0 or float(values.max()) > 1.0:
        values = 1.0 / (1.0 + np.exp(-np.clip(values, -40.0, 40.0)))
    resized = cv2.resize(values, (width, height), interpolation=cv2.INTER_LINEAR)
    binary = (resized >= 0.5).astype(np.uint8)
    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return []
    contour = max(contours, key=cv2.contourArea)
    if cv2.contourArea(contour) < 16:
        return []
    epsilon = max(1.0, 0.004 * cv2.arcLength(contour, True))
    simplified = cv2.approxPolyDP(contour, epsilon, True).reshape(-1, 2)
    if len(simplified) < 3:
        return []
    return [[float(value) for point in simplified for value in point]]


def _decode(
    boxes_output: Any,
    logits_output: Any,
    masks_output: Any | None,
    frame_width: int,
    frame_height: int,
    class_names: list[str],
    confidence_threshold: float,
    max_detections: int,
    time_sec: float,
    camera: str,
) -> list[dict[str, Any]]:
    import numpy as np  # type: ignore

    boxes = np.asarray(boxes_output)[0]
    logits = np.asarray(logits_output)[0]
    if logits.shape[-1] == len(class_names) + 1:
        logits = logits[:, :-1]
    if logits.shape[-1] != len(class_names):
        raise CandidatePerceptionError(
            f"Candidate class count mismatch: ONNX has {logits.shape[-1]}, manifest has {len(class_names)}."
        )
    probabilities = 1.0 / (1.0 + np.exp(-np.clip(logits, -40.0, 40.0)))
    scores = probabilities.max(axis=-1)
    class_ids = probabilities.argmax(axis=-1)
    masks = np.asarray(masks_output)[0] if masks_output is not None else None
    accepted = sorted(
        (
            (float(score), int(class_id), index, box)
            for index, (score, class_id, box) in enumerate(zip(scores, class_ids, boxes))
            if float(score) >= confidence_threshold
        ),
        reverse=True,
    )[:max_detections]
    values: list[dict[str, Any]] = []
    for score, class_id, index, box in accepted:
        center_x, center_y, box_width, box_height = (float(part) for part in box)
        x1 = max(0.0, min(float(frame_width), (center_x - box_width / 2) * frame_width))
        y1 = max(0.0, min(float(frame_height), (center_y - box_height / 2) * frame_height))
        x2 = max(0.0, min(float(frame_width), (center_x + box_width / 2) * frame_width))
        y2 = max(0.0, min(float(frame_height), (center_y + box_height / 2) * frame_height))
        if x2 <= x1 or y2 <= y1:
            continue
        class_name = class_names[class_id]
        item: dict[str, Any] = {
            "class_name": class_name,
            "time_sec": round(time_sec, 4),
            "camera": camera,
            "confidence": round(score, 6),
            "bbox_xyxy": [round(x1, 3), round(y1, 3), round(x2, 3), round(y2, 3)],
        }
        if masks is not None and index < len(masks):
            polygon = _mask_polygon(masks[index], frame_width, frame_height)
            if polygon:
                item["segmentation"] = polygon
        values.append(item)
    return values


def _assign_tracks(
    detections: list[dict[str, Any]],
    active: dict[str, dict[str, Any]],
    next_track: int,
    frame_width: int,
    frame_height: int,
) -> int:
    """Greedy short-horizon association for temporal candidate features."""

    def similarity(a: list[float], b: list[float]) -> tuple[float, float]:
        left, top = max(a[0], b[0]), max(a[1], b[1])
        right, bottom = min(a[2], b[2]), min(a[3], b[3])
        intersection = max(0.0, right - left) * max(0.0, bottom - top)
        area_a = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
        area_b = max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])
        union = max(1.0, area_a + area_b - intersection)
        center_a = ((a[0] + a[2]) / 2, (a[1] + a[3]) / 2)
        center_b = ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)
        center_distance = math.hypot(center_a[0] - center_b[0], center_a[1] - center_b[1]) / max(
            1.0, math.hypot(frame_width, frame_height)
        )
        return intersection / union, center_distance

    now = max((float(value.get("time_sec") or 0.0) for value in detections), default=0.0)
    for track_id in list(active):
        if now - float(active[track_id].get("time_sec") or 0.0) > 1.0:
            del active[track_id]
    used: set[str] = set()
    for detection in sorted(detections, key=lambda value: float(value.get("confidence") or 0.0), reverse=True):
        box = [float(value) for value in detection["bbox_xyxy"]]
        candidates: list[tuple[float, float, str]] = []
        for track_id, previous in active.items():
            if track_id in used or previous.get("class_name") != detection.get("class_name"):
                continue
            iou, distance = similarity(box, [float(value) for value in previous["bbox_xyxy"]])
            if iou >= 0.15 or distance <= 0.08:
                candidates.append((iou, -distance, track_id))
        if candidates:
            track_id = max(candidates)[2]
        else:
            track_id = f"candidate-track-{next_track:06d}"
            next_track += 1
        detection["track_id"] = track_id
        active[track_id] = {
            "class_name": detection.get("class_name"),
            "bbox_xyxy": box,
            "time_sec": detection.get("time_sec"),
        }
        used.add(track_id)
    return next_track


def infer_candidate_perception(
    prepared: Path,
    model_manifest_path: Path,
    output: Path,
    sample_fps: float = 15.0,
) -> dict[str, Any]:
    try:
        import cv2  # type: ignore
        import numpy as np  # type: ignore
        import onnxruntime as ort  # type: ignore
    except ImportError as exc:
        raise CandidatePerceptionError("OpenCV, NumPy, and ONNX Runtime are required.") from exc
    candidate = read_json(model_manifest_path)
    if candidate.get("candidate_kind") not in {"rfdetr_segmentation", "rfdetr_detection"}:
        raise CandidatePerceptionError("An RF-DETR candidate manifest is required.")
    if candidate.get("promoted") is not False:
        raise CandidatePerceptionError("Perception candidates must remain explicitly unpromoted.")
    model_path = Path(str(candidate.get("model_path") or ""))
    if not model_path.is_absolute():
        model_path = model_manifest_path.resolve().parent / model_path
    if not model_path.is_file() or sha256(model_path) != str(candidate.get("sha256") or ""):
        raise CandidatePerceptionError("Candidate ONNX file is missing or has the wrong checksum.")
    policy = candidate.get("inference_policy") if isinstance(candidate.get("inference_policy"), dict) else {}
    if policy.get("frozen_before_locked_evaluation") is not True:
        raise CandidatePerceptionError("RF-DETR inference policy was not frozen before locked evaluation.")
    class_names = candidate.get("class_names") if isinstance(candidate.get("class_names"), list) else []
    class_names = [str(value) for value in class_names]
    if class_names != ["person", "vehicle", "vehicle_door", "ego_vehicle"]:
        raise CandidatePerceptionError("Candidate class manifest is missing the fixed Mimir class order.")
    available = ort.get_available_providers()
    providers = [name for name in ("CUDAExecutionProvider", "CPUExecutionProvider") if name in available]
    session = ort.InferenceSession(str(model_path), providers=providers or ["CPUExecutionProvider"])
    input_meta = session.get_inputs()[0]
    try:
        _, channels, input_height, input_width = input_meta.shape
        if int(channels) != 3:
            raise ValueError
        input_height, input_width = int(input_height), int(input_width)
    except (TypeError, ValueError) as exc:
        raise CandidatePerceptionError(f"Unsupported candidate input shape: {input_meta.shape}") from exc
    source_manifest_path = prepared / "training_manifest.json"
    prepared_manifest = read_json(source_manifest_path)
    enriched_items: list[dict[str, Any]] = []
    errors: list[str] = []
    total_detections = 0
    for item in prepared_manifest.get("temporal_items", []):
        if not isinstance(item, dict):
            continue
        enriched = dict(item)
        media = item.get("media") if isinstance(item.get("media"), list) else []
        media_path = next((Path(str(value)) for value in media if Path(str(value)).is_file()), None)
        if media_path is None:
            errors.append(f"{item.get('incident_id')}: no readable media")
            enriched["perception_objects"] = []
            enriched_items.append(enriched)
            continue
        capture = cv2.VideoCapture(str(media_path))
        native_fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
        frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        duration = float(item.get("duration_sec") or 0.0)
        if duration <= 0 and native_fps > 0 and frame_count > 0:
            duration = frame_count / native_fps
        if duration <= 0:
            capture.release()
            errors.append(f"{item.get('incident_id')}: duration unavailable")
            enriched["perception_objects"] = []
            enriched_items.append(enriched)
            continue
        actual_fps = min(max(1.0, sample_fps), native_fps if native_fps > 0 else sample_fps)
        detections: list[dict[str, Any]] = []
        active_tracks: dict[str, dict[str, Any]] = {}
        next_track = 1
        try:
            for time_sec in np.arange(0.0, duration, 1.0 / actual_fps):
                capture.set(cv2.CAP_PROP_POS_MSEC, float(time_sec) * 1000.0)
                ok, frame = capture.read()
                if not ok or frame is None:
                    continue
                outputs = session.run(None, {input_meta.name: _preprocess(frame, input_height, input_width)})
                boxes, logits, masks = _find_outputs(session, outputs)
                height, width = frame.shape[:2]
                frame_detections = _decode(
                    boxes,
                    logits,
                    masks,
                    int(width),
                    int(height),
                    class_names,
                    float(policy.get("confidence_threshold") or 0.3),
                    int(policy.get("max_detections_per_frame") or 24),
                    float(time_sec),
                    str(item.get("camera") or "unknown"),
                )
                next_track = _assign_tracks(
                    frame_detections, active_tracks, next_track, int(width), int(height)
                )
                detections.extend(frame_detections)
        finally:
            capture.release()
        enriched["perception_objects"] = detections
        enriched["perception_provenance"] = {
            "candidate_id": candidate.get("candidate_id"),
            "model_sha256": candidate.get("sha256"),
            "sample_fps": actual_fps,
        }
        total_detections += len(detections)
        enriched_items.append(enriched)
    enriched_manifest = dict(prepared_manifest)
    enriched_manifest["temporal_items"] = enriched_items
    enriched_manifest["candidate_perception"] = {
        "candidate_id": candidate.get("candidate_id"),
        "candidate_manifest_sha256": sha256(model_manifest_path),
        "model_sha256": candidate.get("sha256"),
        "created_at": utc_now(),
    }
    output.mkdir(parents=True, exist_ok=True)
    write_json(output / "training_manifest.json", enriched_manifest)
    report = {
        "schema_version": "mimir_candidate_perception_v1",
        "created_at": utc_now(),
        "source_manifest": str(source_manifest_path.resolve()),
        "source_manifest_sha256": sha256(source_manifest_path),
        "candidate_id": candidate.get("candidate_id"),
        "candidate_manifest_sha256": sha256(model_manifest_path),
        "model_sha256": candidate.get("sha256"),
        "sample_fps": sample_fps,
        "event_groups": len(enriched_items),
        "detections": total_detections,
        "errors": errors,
        "output_manifest": str((output / "training_manifest.json").resolve()),
    }
    write_json(output / "candidate_perception_report.json", report)
    if errors:
        raise CandidatePerceptionError(
            f"Candidate perception left {len(errors)} incomplete groups; report: {output / 'candidate_perception_report.json'}"
        )
    return report
