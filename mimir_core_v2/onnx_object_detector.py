"""Permissively licensed RF-DETR inference through ONNX Runtime.

This module deliberately exposes only person and vehicle candidates. Bounding
boxes are supporting proximity evidence; they never prove physical contact.
"""

from __future__ import annotations

from pathlib import Path
from time import perf_counter
from typing import Any

from .model_manifest import active_detector_manifest


_SESSION: Any = None
_LOAD_ATTEMPTED = False
_LAST_ERROR = ""
_INFERENCE_COUNT = 0
_INFERENCE_RUNTIME_SEC = 0.0


def _safe_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _configuration() -> tuple[dict, Path | None]:
    manifest = active_detector_manifest()
    if str(manifest.get("runtime") or "").lower() != "onnxruntime":
        return manifest, None
    if str(manifest.get("architecture") or "").lower() != "rf_detr":
        return manifest, None
    files = manifest.get("resolved_model_files") if isinstance(manifest.get("resolved_model_files"), list) else []
    for item in files:
        if not isinstance(item, dict):
            continue
        if not item.get("exists") or not item.get("checksum_matches"):
            continue
        filename = str(item.get("filename") or "")
        if filename.lower().endswith(".onnx"):
            return manifest, Path(__file__).resolve().parents[1] / filename
    return manifest, None


def _load_session(model_path: Path) -> Any:
    global _SESSION, _LOAD_ATTEMPTED, _LAST_ERROR
    if _LOAD_ATTEMPTED:
        return _SESSION
    _LOAD_ATTEMPTED = True
    try:
        import onnxruntime as ort  # type: ignore

        available = ort.get_available_providers()
        preferred = [name for name in ("CUDAExecutionProvider", "CPUExecutionProvider") if name in available]
        _SESSION = ort.InferenceSession(str(model_path), providers=preferred or ["CPUExecutionProvider"])
        _LAST_ERROR = ""
    except Exception as exc:
        _SESSION = None
        _LAST_ERROR = f"{type(exc).__name__}: {exc}"
    return _SESSION


def _preprocess(frame: Any, input_height: int, input_width: int) -> Any:
    import cv2  # type: ignore
    import numpy as np  # type: ignore

    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    resized = cv2.resize(rgb, (input_width, input_height), interpolation=cv2.INTER_LINEAR)
    tensor = resized.astype(np.float32) / 255.0
    mean = np.asarray([0.485, 0.456, 0.406], dtype=np.float32)
    std = np.asarray([0.229, 0.224, 0.225], dtype=np.float32)
    tensor = (tensor - mean) / std
    return tensor.transpose(2, 0, 1)[None].astype(np.float32)


def _find_outputs(session: Any, outputs: list[Any]) -> tuple[Any, Any]:
    names = [str(item.name).lower() for item in session.get_outputs()]
    boxes_index = next((index for index, name in enumerate(names) if "dets" in name), None)
    logits_index = next((index for index, name in enumerate(names) if "labels" in name), None)
    if boxes_index is None:
        boxes_index = next(
            (index for index, value in enumerate(outputs) if getattr(value, "ndim", 0) == 3 and value.shape[-1] == 4),
            None,
        )
    if logits_index is None:
        logits_index = next(
            (index for index, value in enumerate(outputs) if getattr(value, "ndim", 0) == 3 and value.shape[-1] != 4),
            None,
        )
    if boxes_index is None or logits_index is None:
        raise ValueError("RF-DETR ONNX outputs could not be identified")
    return outputs[boxes_index], outputs[logits_index]


def _decode(
    boxes_output: Any,
    logits_output: Any,
    frame_width: int,
    frame_height: int,
    confidence_threshold: float,
    person_ids: set[int],
    vehicle_ids: set[int],
    max_detections: int,
) -> list[dict]:
    import numpy as np  # type: ignore

    boxes = np.asarray(boxes_output)[0]
    logits = np.asarray(logits_output)[0, :, :-1]
    scores_all = 1.0 / (1.0 + np.exp(-np.clip(logits, -88, 88)))
    scores = scores_all.max(axis=-1)
    class_ids = scores_all.argmax(axis=-1)
    accepted: list[tuple[float, int, Any]] = []
    supported_ids = person_ids | vehicle_ids
    for score, class_id, box in zip(scores, class_ids, boxes):
        class_id = int(class_id)
        score = float(score)
        if score >= confidence_threshold and class_id in supported_ids:
            accepted.append((score, class_id, box))
    accepted.sort(key=lambda item: item[0], reverse=True)

    decoded: list[dict] = []
    for score, class_id, box in accepted[:max_detections]:
        center_x, center_y, box_width, box_height = (float(value) for value in box)
        x1 = max(0.0, min(float(frame_width), (center_x - box_width / 2.0) * frame_width))
        y1 = max(0.0, min(float(frame_height), (center_y - box_height / 2.0) * frame_height))
        x2 = max(0.0, min(float(frame_width), (center_x + box_width / 2.0) * frame_width))
        y2 = max(0.0, min(float(frame_height), (center_y + box_height / 2.0) * frame_height))
        if x2 <= x1 or y2 <= y1:
            continue
        decoded.append(
            {
                "class_id": class_id,
                "class_name": "person" if class_id in person_ids else "vehicle",
                "confidence": round(score, 4),
                "bbox": [round(x1, 2), round(y1, 2), round(x2, 2), round(y2, 2)],
                "frame_width": frame_width,
                "frame_height": frame_height,
                "detector_backend": "onnxruntime_rf_detr",
            }
        )
    return decoded


def detect_samples(samples: list[dict]) -> tuple[list[dict], bool, bool]:
    """Return detections, runtime availability, and whether ONNX is configured."""

    global _INFERENCE_COUNT, _INFERENCE_RUNTIME_SEC, _LAST_ERROR
    manifest, model_path = _configuration()
    configured = (
        str(manifest.get("runtime") or "").lower() == "onnxruntime"
        and str(manifest.get("architecture") or "").lower() == "rf_detr"
    )
    if not configured:
        return [], False, False
    if model_path is None:
        _LAST_ERROR = "configured model is missing or its checksum does not match"
        return [], False, True
    session = _load_session(model_path)
    if session is None:
        return [], False, True

    try:
        input_meta = session.get_inputs()[0]
        input_name = str(input_meta.name)
        _, channels, input_height, input_width = input_meta.shape
        if int(channels) != 3:
            raise ValueError(f"unsupported RF-DETR channel count: {channels}")
        confidence_threshold = _safe_float(manifest.get("confidence_threshold"), 0.30)
        person_ids = {int(value) for value in manifest.get("person_class_ids", [1])}
        vehicle_ids = {int(value) for value in manifest.get("vehicle_class_ids", [3, 4, 6, 8])}
        max_detections = max(1, int(manifest.get("max_detections_per_frame") or 20))
    except Exception as exc:
        _LAST_ERROR = f"{type(exc).__name__}: {exc}"
        return [], False, True

    detections: list[dict] = []
    for sample in samples:
        frame = sample.get("frame")
        if frame is None:
            continue
        started = perf_counter()
        try:
            tensor = _preprocess(frame, int(input_height), int(input_width))
            outputs = session.run(None, {input_name: tensor})
            boxes_output, logits_output = _find_outputs(session, outputs)
            frame_height, frame_width = frame.shape[:2]
            sample_detections = _decode(
                boxes_output,
                logits_output,
                int(frame_width),
                int(frame_height),
                confidence_threshold,
                person_ids,
                vehicle_ids,
                max_detections,
            )
        except Exception as exc:
            _LAST_ERROR = f"{type(exc).__name__}: {exc}"
            return detections, False, True
        finally:
            _INFERENCE_COUNT += 1
            _INFERENCE_RUNTIME_SEC += max(0.0, perf_counter() - started)
        for detection in sample_detections:
            detection["camera"] = sample.get("camera", "unknown")
            detection["time_sec"] = _safe_float(sample.get("time_sec"))
            detections.append(detection)
    _LAST_ERROR = ""
    return detections, True, True


def runtime_diagnostics() -> dict:
    manifest, model_path = _configuration()
    providers = _SESSION.get_providers() if _SESSION is not None else []
    return {
        "object_detector_id": str(manifest.get("detector_id") or "unknown"),
        "object_detector_version": str(manifest.get("detector_version") or "unknown"),
        "object_detector_backend": "onnxruntime_rf_detr",
        "object_detector_model_path": str(model_path) if model_path is not None else "",
        "object_detector_providers": list(providers),
        "object_detector_inference_count": int(_INFERENCE_COUNT),
        "object_detector_runtime_sec": round(_INFERENCE_RUNTIME_SEC, 3),
        "object_detector_last_error": _LAST_ERROR,
    }


def reset_runtime() -> None:
    """Reset process-local state; used when tests toggle object detection."""

    global _SESSION, _LOAD_ATTEMPTED, _LAST_ERROR, _INFERENCE_COUNT, _INFERENCE_RUNTIME_SEC
    _SESSION = None
    _LOAD_ATTEMPTED = False
    _LAST_ERROR = ""
    _INFERENCE_COUNT = 0
    _INFERENCE_RUNTIME_SEC = 0.0
