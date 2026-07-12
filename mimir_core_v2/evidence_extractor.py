"""Lightweight local evidence extraction for grouped Core v2 events."""

from __future__ import annotations

from pathlib import Path
from typing import Any


PERSON_PASSBY_MAX_SEC = 2.0
PERSON_LINGER_MIN_SEC = 4.0
VEHICLE_PASSBY_MAX_SEC = 2.0
VEHICLE_LINGER_MIN_SEC = 4.0

EVIDENCE_VERSION = "local_evidence_v2_0_1"
IMPACT_DETECTION_VERSION = "impact_motion_v2_0_3"

MOTION_LOW_THRESHOLD = 0.28
MOTION_MEDIUM_THRESHOLD = 0.55
MOTION_HIGH_THRESHOLD = 0.82
MOTION_SPIKE_MEDIUM_RATIO = 2.2
MOTION_SPIKE_HIGH_RATIO = 3.0
LOCALIZED_CONTACT_THRESHOLD = 0.35
LOCALIZED_CONTACT_HIGH_THRESHOLD = 0.75
CAMERA_SHAKE_MEDIUM_THRESHOLD = 0.38
CAMERA_SHAKE_HIGH_THRESHOLD = 0.55
HARD_CLOSE_OBJECT_AREA_THRESHOLD = 0.70
HARD_CLOSE_OBJECT_BOTTOM_THRESHOLD = 0.95
HARD_CLOSE_LOCALIZED_MOTION_THRESHOLD = 0.50
HARD_CLOSE_GLOBAL_MOTION_THRESHOLD = 0.18
HARD_CLOSE_SPIKE_RATIO_THRESHOLD = 3.0
IMPACT_CANDIDATE_SPIKE_RATIO_THRESHOLD = 3.5
IMPACT_CANDIDATE_SHAKE_THRESHOLD = 0.12
IMPACT_CANDIDATE_MAX_MOTION_THRESHOLD = 0.22

PERSON_CLASS_IDS = {0}
VEHICLE_CLASS_IDS = {2, 3, 5, 7}

_YOLO_MODEL: Any = None
_YOLO_LOAD_ATTEMPTED = False
_YOLO_AVAILABLE = False
_YOLO_FAILURES = 0


def _load_cv2() -> Any:
    try:
        import cv2  # type: ignore

        return cv2
    except Exception:
        return None


def _load_yolo() -> Any:
    global _YOLO_AVAILABLE, _YOLO_MODEL, _YOLO_LOAD_ATTEMPTED, _YOLO_FAILURES

    if _YOLO_LOAD_ATTEMPTED:
        return _YOLO_MODEL

    _YOLO_LOAD_ATTEMPTED = True
    try:
        from ultralytics import YOLO  # type: ignore
    except Exception:
        _YOLO_AVAILABLE = False
        return None

    backend_root = Path(__file__).resolve().parents[1]
    for weights in (backend_root / "yolo11n.pt", backend_root / "yolov8n.pt"):
        if weights.exists():
            try:
                _YOLO_MODEL = YOLO(str(weights))
                _YOLO_AVAILABLE = True
                return _YOLO_MODEL
            except Exception:
                _YOLO_FAILURES += 1
                _YOLO_AVAILABLE = False
                return None

    _YOLO_AVAILABLE = False
    return None


def get_evidence_runtime_diagnostics() -> dict:
    return {
        "yolo_available": bool(_YOLO_AVAILABLE),
        "yolo_failures": int(_YOLO_FAILURES),
    }


def _safe_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _level_rank(level: str) -> int:
    return {"NONE": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3}.get(str(level or "NONE").upper(), 0)


def _max_level(left: str, right: str) -> str:
    return left if _level_rank(left) >= _level_rank(right) else right


def _motion_level(score: float) -> str:
    if score >= MOTION_HIGH_THRESHOLD:
        return "HIGH"
    if score >= MOTION_MEDIUM_THRESHOLD:
        return "MEDIUM"
    if score >= MOTION_LOW_THRESHOLD:
        return "LOW"
    return "NONE"


def _clip_samples_by_camera(sample_result: dict) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for sample in sample_result.get("samples", []):
        if not isinstance(sample, dict):
            continue
        camera = str(sample.get("camera") or "unknown")
        grouped.setdefault(camera, []).append(sample)

    for samples in grouped.values():
        samples.sort(key=lambda item: _safe_float(item.get("time_sec")))

    return grouped


def _empty_motion_metrics() -> dict:
    return {
        "motion_score": 0.0,
        "max_motion_score": 0.0,
        "localized_motion_score": 0.0,
        "motion_spike_time_sec": 0.0,
        "motion_spike_ratio": 0.0,
        "camera_shake_score": 0.0,
        "abrupt_scene_change": False,
        "scene_change_score": 0.0,
        "impact_evidence_reasons": [],
    }


def _spike_ratio(scores: list[float], max_score: float) -> float:
    if len(scores) < 2:
        return 0.0
    baseline_scores = sorted(scores)[:-1] or scores
    baseline = sum(baseline_scores) / len(baseline_scores)
    if baseline <= 0.01:
        baseline = 0.01
    return round(min(max_score / baseline, 99.0), 3)


def _diff_tile_scores(diff: Any) -> list[float]:
    height, width = diff.shape[:2]
    scores: list[float] = []
    for row in range(3):
        for column in range(3):
            y1 = row * height // 3
            y2 = (row + 1) * height // 3
            x1 = column * width // 3
            x2 = (column + 1) * width // 3
            tile = diff[y1:y2, x1:x2]
            if tile.size:
                scores.append(min(float(tile.mean()) / 55.0, 1.0))
    return scores


def _impact_level_from_motion(motion: dict) -> str:
    max_motion = _safe_float(motion.get("max_motion_score"))
    local_motion = _safe_float(motion.get("localized_motion_score"))
    spike_ratio = _safe_float(motion.get("motion_spike_ratio"))
    camera_shake = _safe_float(motion.get("camera_shake_score"))
    abrupt_scene_change = bool(motion.get("abrupt_scene_change"))

    if (
        max_motion >= MOTION_HIGH_THRESHOLD
        or camera_shake >= CAMERA_SHAKE_HIGH_THRESHOLD
        or (abrupt_scene_change and max_motion >= MOTION_MEDIUM_THRESHOLD and spike_ratio >= MOTION_SPIKE_MEDIUM_RATIO)
        or (max_motion >= 0.62 and spike_ratio >= MOTION_SPIKE_HIGH_RATIO)
    ):
        return "HIGH"
    if (
        max_motion >= MOTION_MEDIUM_THRESHOLD
        or camera_shake >= CAMERA_SHAKE_MEDIUM_THRESHOLD
        or local_motion >= LOCALIZED_CONTACT_HIGH_THRESHOLD
        or (max_motion >= MOTION_LOW_THRESHOLD and spike_ratio >= MOTION_SPIKE_MEDIUM_RATIO)
    ):
        return "MEDIUM"
    if max_motion >= 0.14 or local_motion >= 0.22:
        return "LOW"
    return "NONE"


def _motion_for_samples(samples: list[dict]) -> dict:
    cv2 = _load_cv2()
    if cv2 is None or len(samples) < 2:
        return _empty_motion_metrics()

    previous_gray = None
    motion_scores: list[tuple[float, float, float, float]] = []
    for sample in samples:
        frame = sample.get("frame")
        if frame is None:
            continue
        try:
            resized = cv2.resize(frame, (240, 135))
            gray = cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY)
            gray = cv2.GaussianBlur(gray, (5, 5), 0)
        except Exception:
            continue
        if previous_gray is not None:
            diff = cv2.absdiff(previous_gray, gray)
            global_score = min(float(diff.mean()) / 55.0, 1.0)
            tile_scores = _diff_tile_scores(diff)
            localized_score = max(tile_scores, default=global_score)
            widespread_threshold = max(0.12, global_score * 0.7)
            widespread_fraction = (
                sum(1 for score in tile_scores if score >= widespread_threshold) / len(tile_scores)
                if tile_scores
                else 0.0
            )
            camera_shake_score = global_score * widespread_fraction
            motion_scores.append(
                (
                    _safe_float(sample.get("time_sec")),
                    round(global_score, 4),
                    round(localized_score, 4),
                    round(camera_shake_score, 4),
                )
            )
        previous_gray = gray

    if not motion_scores:
        return _empty_motion_metrics()

    average = sum(score for _, score, _, _ in motion_scores) / len(motion_scores)
    spike_time, max_score, localized_at_spike, _ = max(motion_scores, key=lambda item: item[1])
    max_localized = max(score for _, _, score, _ in motion_scores)
    max_camera_shake = max(score for _, _, _, score in motion_scores)
    spike_ratio = _spike_ratio([score for _, score, _, _ in motion_scores], max_score)
    abrupt_scene_change = bool(
        max_score >= MOTION_MEDIUM_THRESHOLD
        and (max_camera_shake >= CAMERA_SHAKE_MEDIUM_THRESHOLD or spike_ratio >= MOTION_SPIKE_MEDIUM_RATIO)
    )

    reasons: list[str] = []
    if spike_ratio >= MOTION_SPIKE_MEDIUM_RATIO and max_score >= MOTION_LOW_THRESHOLD:
        reasons.append(f"motion_spike_ratio={spike_ratio:g}")
    if max_camera_shake >= CAMERA_SHAKE_MEDIUM_THRESHOLD:
        reasons.append(f"camera_shake_score={max_camera_shake:.4f}")
    if abrupt_scene_change:
        reasons.append("abrupt_scene_change")
    if max_localized >= LOCALIZED_CONTACT_THRESHOLD:
        reasons.append(f"localized_motion_score={max_localized:.4f}")

    return {
        "motion_score": round(average, 4),
        "max_motion_score": round(max_score, 4),
        "localized_motion_score": round(max(max_localized, localized_at_spike), 4),
        "motion_spike_time_sec": round(spike_time, 3),
        "motion_spike_ratio": spike_ratio,
        "camera_shake_score": round(max_camera_shake, 4),
        "abrupt_scene_change": abrupt_scene_change,
        "scene_change_score": round(max_score, 4),
        "impact_evidence_reasons": reasons,
    }


def _detect_objects(samples: list[dict]) -> list[dict]:
    global _YOLO_FAILURES

    model = _load_yolo()
    if model is None:
        return []

    detections: list[dict] = []
    for sample in samples:
        frame = sample.get("frame")
        if frame is None:
            continue
        frame_height = int(frame.shape[0]) if hasattr(frame, "shape") and len(frame.shape) >= 2 else 0
        frame_width = int(frame.shape[1]) if hasattr(frame, "shape") and len(frame.shape) >= 2 else 0
        try:
            results = model.predict(frame, verbose=False, imgsz=320, conf=0.35)
        except Exception:
            _YOLO_FAILURES += 1
            return detections
        for result in results:
            boxes = getattr(result, "boxes", None)
            if boxes is None:
                continue
            for box in boxes:
                class_id = int(box.cls[0]) if getattr(box, "cls", None) is not None else -1
                if class_id not in PERSON_CLASS_IDS and class_id not in VEHICLE_CLASS_IDS:
                    continue
                xyxy = box.xyxy[0].tolist() if getattr(box, "xyxy", None) is not None else [0, 0, 0, 0]
                confidence = float(box.conf[0]) if getattr(box, "conf", None) is not None else 0.0
                detections.append(
                    {
                        "camera": sample.get("camera", "unknown"),
                        "time_sec": _safe_float(sample.get("time_sec")),
                        "class_id": class_id,
                        "class_name": "person" if class_id in PERSON_CLASS_IDS else "vehicle",
                        "confidence": round(confidence, 4),
                        "bbox": [round(float(value), 2) for value in xyxy],
                        "frame_width": frame_width,
                        "frame_height": frame_height,
                    }
                )

    return detections


def _close_object_evidence(detections: list[dict]) -> dict:
    reasons: list[str] = []
    max_area_ratio = 0.0
    max_bottom_ratio = 0.0
    max_width_ratio = 0.0
    max_height_ratio = 0.0
    classes: set[str] = set()
    close_classes: set[str] = set()

    for detection in detections:
        bbox = detection.get("bbox") if isinstance(detection.get("bbox"), list) else []
        if len(bbox) != 4:
            continue
        frame_width = _safe_float(detection.get("frame_width"))
        frame_height = _safe_float(detection.get("frame_height"))
        if frame_width <= 0 or frame_height <= 0:
            continue
        x1, y1, x2, y2 = (_safe_float(value) for value in bbox)
        width_ratio = max(0.0, x2 - x1) / frame_width
        height_ratio = max(0.0, y2 - y1) / frame_height
        area_ratio = width_ratio * height_ratio
        bottom_ratio = y2 / frame_height
        class_name = str(detection.get("class_name") or "object")
        classes.add(class_name)
        max_area_ratio = max(max_area_ratio, area_ratio)
        max_bottom_ratio = max(max_bottom_ratio, bottom_ratio)
        max_width_ratio = max(max_width_ratio, width_ratio)
        max_height_ratio = max(max_height_ratio, height_ratio)

        close = (
            area_ratio >= 0.12
            or width_ratio >= 0.45
            or height_ratio >= 0.55
            or bottom_ratio >= 0.86
        )
        if close:
            close_classes.add(class_name)

    for class_name in sorted(close_classes):
        matching = []
        for detection in detections:
            if str(detection.get("class_name") or "object") != class_name:
                continue
            bbox = detection.get("bbox") if isinstance(detection.get("bbox"), list) else []
            if len(bbox) != 4:
                continue
            frame_width = _safe_float(detection.get("frame_width"))
            frame_height = _safe_float(detection.get("frame_height"))
            if frame_width <= 0 or frame_height <= 0:
                continue
            x1, y1, x2, y2 = (_safe_float(value) for value in bbox)
            width_ratio = max(0.0, x2 - x1) / frame_width
            height_ratio = max(0.0, y2 - y1) / frame_height
            area_ratio = width_ratio * height_ratio
            bottom_ratio = y2 / frame_height
            matching.append((area_ratio, bottom_ratio))
        if matching:
            class_area = max(item[0] for item in matching)
            class_bottom = max(item[1] for item in matching)
            reasons.append(f"{class_name}_close_to_camera(max_area={class_area:.3f}, max_bottom={class_bottom:.3f})")

    hard_close_vehicle = bool(
        "vehicle" in close_classes
        and max_area_ratio >= HARD_CLOSE_OBJECT_AREA_THRESHOLD
        and max_bottom_ratio >= HARD_CLOSE_OBJECT_BOTTOM_THRESHOLD
    )

    return {
        "reasons": reasons,
        "classes": sorted(classes),
        "close_classes": sorted(close_classes),
        "max_area_ratio": round(max_area_ratio, 4),
        "max_bottom_ratio": round(max_bottom_ratio, 4),
        "max_width_ratio": round(max_width_ratio, 4),
        "max_height_ratio": round(max_height_ratio, 4),
        "hard_close_vehicle": hard_close_vehicle,
    }


def _hard_close_contact_like_motion(motion: dict, close_evidence: dict) -> bool:
    return bool(
        close_evidence.get("hard_close_vehicle")
        and _safe_float(motion.get("localized_motion_score")) >= HARD_CLOSE_LOCALIZED_MOTION_THRESHOLD
        and _safe_float(motion.get("max_motion_score")) >= HARD_CLOSE_GLOBAL_MOTION_THRESHOLD
        and _safe_float(motion.get("motion_spike_ratio")) >= HARD_CLOSE_SPIKE_RATIO_THRESHOLD
    )


def _contact_level_from_motion(motion: dict, close_evidence: dict, impact_level: str) -> tuple[str, list[str]]:
    local_motion = _safe_float(motion.get("localized_motion_score"))
    max_motion = _safe_float(motion.get("max_motion_score"))
    spike_ratio = _safe_float(motion.get("motion_spike_ratio"))
    close_reasons = list(close_evidence.get("reasons") or [])
    strong_local_spike = bool(
        local_motion >= LOCALIZED_CONTACT_HIGH_THRESHOLD
        or (local_motion >= LOCALIZED_CONTACT_THRESHOLD and spike_ratio >= 1.8)
        or (max_motion >= MOTION_LOW_THRESHOLD and spike_ratio >= MOTION_SPIKE_MEDIUM_RATIO)
    )

    if not close_reasons or not strong_local_spike:
        return "NONE", []

    reasons = list(close_reasons)
    reasons.append(f"localized_motion_score={local_motion:.4f}")
    if spike_ratio:
        reasons.append(f"motion_spike_ratio={spike_ratio:g}")

    if _hard_close_contact_like_motion(motion, close_evidence):
        reasons.append("hard_close_vehicle_motion")
        return "HIGH", reasons
    if local_motion >= LOCALIZED_CONTACT_HIGH_THRESHOLD or impact_level == "HIGH":
        return "HIGH", reasons
    return "MEDIUM", reasons


def _impact_candidate_details(
    motion: dict,
    vehicle_detected: bool,
    possible_contact: bool,
    contact_level: str,
) -> dict:
    max_motion = _safe_float(motion.get("max_motion_score"))
    localized_motion = _safe_float(motion.get("localized_motion_score"))
    spike_ratio = _safe_float(motion.get("motion_spike_ratio"))
    camera_shake = _safe_float(motion.get("camera_shake_score"))
    contact_rank = _level_rank(contact_level)

    score = 0.0
    reasons: list[str] = []
    if vehicle_detected:
        score += 0.2
        reasons.append("vehicle_detected")
    if possible_contact:
        score += 0.25
        reasons.append("possible_contact")
    if contact_rank >= _level_rank("MEDIUM"):
        score += 0.2 if contact_level != "HIGH" else 0.3
        reasons.append(f"contact_level={contact_level}")
    if spike_ratio >= IMPACT_CANDIDATE_SPIKE_RATIO_THRESHOLD:
        score += 0.2
        reasons.append(f"motion_spike_ratio={spike_ratio:g}")
    if camera_shake >= IMPACT_CANDIDATE_SHAKE_THRESHOLD:
        score += 0.1
        reasons.append(f"camera_shake_score={camera_shake:.4f}")
    if max_motion >= IMPACT_CANDIDATE_MAX_MOTION_THRESHOLD:
        score += 0.1
        reasons.append(f"max_motion_score={max_motion:.4f}")
    if localized_motion >= HARD_CLOSE_LOCALIZED_MOTION_THRESHOLD:
        score += 0.1
        reasons.append(f"localized_motion_score={localized_motion:.4f}")

    hard_contact_candidate = bool(
        vehicle_detected
        and possible_contact
        and contact_rank >= _level_rank("MEDIUM")
        and spike_ratio >= IMPACT_CANDIDATE_SPIKE_RATIO_THRESHOLD
        and (
            camera_shake >= IMPACT_CANDIDATE_SHAKE_THRESHOLD
            or max_motion >= IMPACT_CANDIDATE_MAX_MOTION_THRESHOLD
        )
    )

    if hard_contact_candidate:
        reasons.append("hard_contact_candidate")

    return {
        "impact_candidate_score": round(min(score, 1.0), 3),
        "hard_contact_candidate": hard_contact_candidate,
        "rear_impact_candidate": hard_contact_candidate and vehicle_detected,
        "impact_candidate_reasons": reasons,
    }


def _center(bbox: list[float]) -> tuple[float, float]:
    return ((bbox[0] + bbox[2]) / 2.0, (bbox[1] + bbox[3]) / 2.0)


def _track_detections(detections: list[dict]) -> list[dict]:
    tracks: list[dict] = []
    next_id = 1
    for detection in sorted(detections, key=lambda item: (item.get("camera"), item.get("class_name"), item.get("time_sec"))):
        bbox = detection.get("bbox") if isinstance(detection.get("bbox"), list) else [0, 0, 0, 0]
        cx, cy = _center(bbox)
        matched = None
        for track in tracks:
            if track["camera"] != detection.get("camera") or track["class_name"] != detection.get("class_name"):
                continue
            if _safe_float(detection.get("time_sec")) - track["last_seen_time_sec"] > 3.0:
                continue
            tcx, tcy = track["last_center"]
            distance = ((cx - tcx) ** 2 + (cy - tcy) ** 2) ** 0.5
            if distance <= 180:
                matched = track
                break
        if matched is None:
            matched = {
                "track_id": next_id,
                "camera": detection.get("camera", "unknown"),
                "class_name": detection.get("class_name", "unknown"),
                "first_seen_time_sec": _safe_float(detection.get("time_sec")),
                "last_seen_time_sec": _safe_float(detection.get("time_sec")),
                "frame_count": 0,
                "confidence_values": [],
                "bbox_first": bbox,
                "bbox_last": bbox,
                "last_center": (cx, cy),
            }
            next_id += 1
            tracks.append(matched)
        matched["last_seen_time_sec"] = _safe_float(detection.get("time_sec"))
        matched["frame_count"] += 1
        matched["confidence_values"].append(_safe_float(detection.get("confidence")))
        matched["bbox_last"] = bbox
        matched["last_center"] = (cx, cy)

    finalized: list[dict] = []
    for track in tracks:
        dwell = max(0.0, track["last_seen_time_sec"] - track["first_seen_time_sec"])
        confidence_values = track.pop("confidence_values")
        track.pop("last_center", None)
        track["dwell_time_sec"] = round(dwell, 3)
        track["confidence_avg"] = round(sum(confidence_values) / len(confidence_values), 4) if confidence_values else 0.0
        track["confidence_max"] = round(max(confidence_values), 4) if confidence_values else 0.0
        finalized.append(track)
    return finalized


def _dwell_flags(tracks: list[dict], class_name: str, passby_sec: float, linger_sec: float) -> tuple[bool, bool]:
    matching = [track for track in tracks if track.get("class_name") == class_name]
    passby = any(track.get("dwell_time_sec", 0.0) <= passby_sec or track.get("frame_count", 0) <= 2 for track in matching)
    lingering = any(track.get("dwell_time_sec", 0.0) >= linger_sec or track.get("frame_count", 0) >= 5 for track in matching)
    return passby, lingering


def _camera_score(camera_evidence: dict) -> float:
    score = _safe_float(camera_evidence.get("max_motion_score"))
    if camera_evidence.get("possible_impact"):
        score += 2.0
    if camera_evidence.get("possible_contact"):
        score += 1.5
    if camera_evidence.get("person_detected"):
        score += 0.5
    if camera_evidence.get("vehicle_detected"):
        score += 0.25
    return round(score, 4)


def fallback_evidence(event_group: dict, warning: str) -> dict:
    clips = event_group.get("clips") if isinstance(event_group.get("clips"), list) else []
    cameras = event_group.get("available_cameras") if isinstance(event_group.get("available_cameras"), list) else []
    return {
        "evidence_version": EVIDENCE_VERSION,
        "impact_detection_version": IMPACT_DETECTION_VERSION,
        "camera_count": len(clips),
        "available_cameras": cameras,
        "total_duration_sec": 0.0,
        "sampled_frames": 0,
        "multi_camera": len(clips) > 1,
        "has_video": any(bool(clip.get("exists")) for clip in clips),
        "motion_score": 0.0,
        "max_motion_score": 0.0,
        "motion_spike_time_sec": 0.0,
        "motion_spike_ratio": 0.0,
        "camera_shake_score": 0.0,
        "abrupt_scene_change": False,
        "localized_motion_score": 0.0,
        "scene_change_score": 0.0,
        "strong_impact_like_motion": False,
        "possible_impact": False,
        "impact_level": "NONE",
        "impact_score": 0.0,
        "impact_candidate_score": 0.0,
        "hard_contact_candidate": False,
        "rear_impact_candidate": False,
        "impact_evidence_reasons": [],
        "possible_contact": False,
        "contact_level": "NONE",
        "contact_score": 0.0,
        "contact_evidence_reasons": [],
        "person_detected": False,
        "vehicle_detected": False,
        "person_near_only": False,
        "person_passby_detected": False,
        "person_passby": False,
        "person_lingering_detected": False,
        "vehicle_passby_detected": False,
        "vehicle_lingering_detected": False,
        "normal_traffic": False,
        "normal_traffic_evidence": False,
        "visible_contact": False,
        "visible_impact": False,
        "person_interaction_evidence": False,
        "tampering_evidence": False,
        "door_handle_attempt": False,
        "crash_safety_triggered": False,
        "camera_evidence": {},
        "object_tracks": [],
        "primary_camera_candidate": "",
        "timeline_markers": [],
        "hero_thumbnail": "",
        "contact_sheet": "",
        "evidence_warnings": [warning],
        **get_evidence_runtime_diagnostics(),
    }


def extract_evidence(event_group: dict, sample_result: dict) -> dict:
    clips = event_group.get("clips") if isinstance(event_group.get("clips"), list) else []
    cameras = event_group.get("available_cameras") if isinstance(event_group.get("available_cameras"), list) else []
    duration = 0.0
    for clip in clips:
        try:
            duration += float(clip.get("duration_sec") or 0.0)
        except (TypeError, ValueError):
            continue

    samples_by_camera = _clip_samples_by_camera(sample_result)
    camera_evidence: dict[str, dict] = {}
    all_tracks: list[dict] = []

    for camera in cameras or ["unknown"]:
        samples = samples_by_camera.get(camera, [])
        motion = _motion_for_samples(samples)
        detections = _detect_objects(samples)
        tracks = _track_detections(detections)
        all_tracks.extend(tracks)

        person_detected = any(track.get("class_name") == "person" for track in tracks)
        vehicle_detected = any(track.get("class_name") == "vehicle" for track in tracks)
        person_passby, person_lingering = _dwell_flags(tracks, "person", PERSON_PASSBY_MAX_SEC, PERSON_LINGER_MIN_SEC)
        vehicle_passby, vehicle_lingering = _dwell_flags(tracks, "vehicle", VEHICLE_PASSBY_MAX_SEC, VEHICLE_LINGER_MIN_SEC)

        max_motion = _safe_float(motion.get("max_motion_score"))
        impact_level = _impact_level_from_motion(motion)
        possible_impact = impact_level in {"MEDIUM", "HIGH"}
        strong_impact_like_motion = impact_level == "HIGH"
        close_evidence = _close_object_evidence(detections)
        hard_close_contact = _hard_close_contact_like_motion(motion, close_evidence)
        if hard_close_contact:
            impact_level = _max_level(impact_level, "HIGH")
            possible_impact = True
            strong_impact_like_motion = True
            motion.setdefault("impact_evidence_reasons", [])
            motion["impact_evidence_reasons"] = list(motion.get("impact_evidence_reasons") or [])
            motion["impact_evidence_reasons"].append("hard_close_vehicle_motion")
        contact_level, contact_reasons = _contact_level_from_motion(motion, close_evidence, impact_level)
        possible_contact = contact_level in {"MEDIUM", "HIGH"}
        candidate_details = _impact_candidate_details(
            motion,
            vehicle_detected,
            possible_contact,
            contact_level,
        )
        if candidate_details.get("hard_contact_candidate"):
            contact_level = _max_level(contact_level, "HIGH")
            possible_contact = True
            contact_reasons = list(contact_reasons)
            contact_reasons.append("hard_contact_candidate")
        if candidate_details.get("rear_impact_candidate"):
            motion.setdefault("impact_evidence_reasons", [])
            motion["impact_evidence_reasons"] = list(motion.get("impact_evidence_reasons") or [])
            motion["impact_evidence_reasons"].append("rear_impact_candidate")
        possible_impact = possible_impact or bool(candidate_details.get("rear_impact_candidate"))
        normal_traffic = bool((vehicle_detected or person_detected) and not possible_impact and max_motion < MOTION_MEDIUM_THRESHOLD)

        camera_evidence[camera] = {
            **motion,
            "close_object_evidence": close_evidence,
            **candidate_details,
            "possible_impact": possible_impact,
            "impact_level": impact_level,
            "strong_impact_like_motion": strong_impact_like_motion,
            "possible_contact": possible_contact,
            "contact_level": contact_level,
            "contact_evidence_reasons": contact_reasons,
            "person_detected": person_detected,
            "vehicle_detected": vehicle_detected,
            "person_passby_detected": person_passby,
            "person_lingering_detected": person_lingering,
            "vehicle_passby_detected": vehicle_passby,
            "vehicle_lingering_detected": vehicle_lingering,
            "normal_traffic_evidence": normal_traffic,
            "sampled_frames": len(samples),
        }
        camera_evidence[camera]["evidence_score"] = _camera_score(camera_evidence[camera])

    best_camera = ""
    if camera_evidence:
        best_camera = max(camera_evidence.items(), key=lambda item: item[1].get("evidence_score", 0.0))[0]

    max_motion_score = max((_safe_float(item.get("max_motion_score")) for item in camera_evidence.values()), default=0.0)
    motion_score = max((_safe_float(item.get("motion_score")) for item in camera_evidence.values()), default=0.0)
    localized_motion_score = max((_safe_float(item.get("localized_motion_score")) for item in camera_evidence.values()), default=0.0)
    motion_spike_ratio = max((_safe_float(item.get("motion_spike_ratio")) for item in camera_evidence.values()), default=0.0)
    camera_shake_score = max((_safe_float(item.get("camera_shake_score")) for item in camera_evidence.values()), default=0.0)
    abrupt_scene_change = any(item.get("abrupt_scene_change") for item in camera_evidence.values())
    impact_level = "NONE"
    contact_level = "NONE"
    for item in camera_evidence.values():
        impact_level = _max_level(impact_level, str(item.get("impact_level") or "NONE"))
        contact_level = _max_level(contact_level, str(item.get("contact_level") or "NONE"))
    possible_impact = any(item.get("possible_impact") for item in camera_evidence.values())
    possible_contact = any(item.get("possible_contact") for item in camera_evidence.values())
    strong_impact_like_motion = any(item.get("strong_impact_like_motion") for item in camera_evidence.values()) or impact_level == "HIGH"
    impact_candidate_score = max((_safe_float(item.get("impact_candidate_score")) for item in camera_evidence.values()), default=0.0)
    hard_contact_candidate = any(item.get("hard_contact_candidate") for item in camera_evidence.values())
    rear_impact_candidate = any(item.get("rear_impact_candidate") for item in camera_evidence.values())
    impact_evidence_reasons = sorted(
        {
            reason
            for item in camera_evidence.values()
            for reason in item.get("impact_evidence_reasons", [])
            if reason
        }
    )
    contact_evidence_reasons = sorted(
        {
            reason
            for item in camera_evidence.values()
            for reason in item.get("contact_evidence_reasons", [])
            if reason
        }
    )
    impact_candidate_reasons = sorted(
        {
            reason
            for item in camera_evidence.values()
            for reason in item.get("impact_candidate_reasons", [])
            if reason
        }
    )
    if hard_contact_candidate:
        impact_evidence_reasons.append("hard_contact_candidate")
        contact_evidence_reasons.append("hard_contact_candidate")
    if rear_impact_candidate:
        impact_evidence_reasons.append("rear_impact_candidate")
    impact_evidence_reasons = sorted(dict.fromkeys(impact_evidence_reasons))
    contact_evidence_reasons = sorted(dict.fromkeys(contact_evidence_reasons))
    person_detected = any(item.get("person_detected") for item in camera_evidence.values())
    vehicle_detected = any(item.get("vehicle_detected") for item in camera_evidence.values())
    person_passby = any(item.get("person_passby_detected") for item in camera_evidence.values())
    person_lingering = any(item.get("person_lingering_detected") for item in camera_evidence.values())
    vehicle_passby = any(item.get("vehicle_passby_detected") for item in camera_evidence.values())
    vehicle_lingering = any(item.get("vehicle_lingering_detected") for item in camera_evidence.values())
    normal_traffic = bool(
        (
            any(item.get("normal_traffic_evidence") for item in camera_evidence.values())
            or person_passby
            or vehicle_passby
            or (person_detected or vehicle_detected)
        )
        and not possible_impact
        and not possible_contact
        and not person_lingering
        and not vehicle_lingering
    )

    timeline_markers = []
    if possible_impact:
        spike_time = 0.0
        for item in camera_evidence.values():
            if _safe_float(item.get("max_motion_score")) == max_motion_score:
                spike_time = _safe_float(item.get("motion_spike_time_sec"))
                break
        timeline_markers.append(
            {
                "time_sec": round(spike_time, 3),
                "label": "Possible impact",
                "type": "possible_impact",
                "description": "Local motion evidence suggests a possible impact.",
            }
        )
    elif person_detected:
        timeline_markers.append(
            {
                "time_sec": 0.0,
                "label": "Person nearby",
                "type": "person_nearby",
                "description": "A person was detected in sampled frames.",
            }
        )

    return {
        "evidence_version": EVIDENCE_VERSION,
        "impact_detection_version": IMPACT_DETECTION_VERSION,
        "camera_count": len(clips),
        "available_cameras": cameras,
        "total_duration_sec": round(duration, 3),
        "sampled_frames": int(sample_result.get("sampled_frames") or 0),
        "multi_camera": len(clips) > 1,
        "has_video": any(bool(clip.get("exists")) for clip in clips),
        "motion_score": round(motion_score, 4),
        "max_motion_score": round(max_motion_score, 4),
        "localized_motion_score": round(localized_motion_score, 4),
        "motion_spike_time_sec": next((item.get("motion_spike_time_sec", 0.0) for item in camera_evidence.values() if _safe_float(item.get("max_motion_score")) == max_motion_score), 0.0),
        "motion_spike_ratio": round(motion_spike_ratio, 3),
        "camera_shake_score": round(camera_shake_score, 4),
        "abrupt_scene_change": abrupt_scene_change,
        "scene_change_score": round(max_motion_score, 4),
        "possible_impact": possible_impact,
        "impact_level": impact_level,
        "impact_score": round(max_motion_score, 4),
        "impact_candidate_score": round(impact_candidate_score, 3),
        "hard_contact_candidate": hard_contact_candidate,
        "rear_impact_candidate": rear_impact_candidate,
        "impact_evidence_reasons": impact_evidence_reasons,
        "impact_candidate_reasons": impact_candidate_reasons,
        "strong_impact_like_motion": strong_impact_like_motion,
        "possible_contact": possible_contact,
        "contact_level": contact_level if possible_contact else "NONE",
        "contact_score": round(localized_motion_score if possible_contact else 0.0, 4),
        "contact_evidence_reasons": contact_evidence_reasons,
        "person_detected": person_detected,
        "vehicle_detected": vehicle_detected,
        "person_passby_detected": person_passby,
        "person_passby": person_passby,
        "person_lingering_detected": person_lingering,
        "vehicle_passby_detected": vehicle_passby,
        "vehicle_lingering_detected": vehicle_lingering,
        "normal_traffic_evidence": normal_traffic,
        "normal_traffic": normal_traffic,
        "person_near_only": bool(person_detected and not possible_contact and not possible_impact),
        "visible_contact": False,
        "visible_impact": False,
        "person_interaction_evidence": False,
        "tampering_evidence": False,
        "door_handle_attempt": False,
        "crash_safety_triggered": False,
        "camera_evidence": camera_evidence,
        "object_tracks": all_tracks,
        "primary_camera_candidate": best_camera,
        "timeline_markers": timeline_markers,
        "hero_thumbnail": "",
        "contact_sheet": "",
        "evidence_warnings": [],
        **get_evidence_runtime_diagnostics(),
    }
