"""Lightweight local evidence extraction for grouped Core v2 events."""

from __future__ import annotations

from pathlib import Path
from typing import Any


PERSON_PASSBY_MAX_SEC = 2.0
PERSON_LINGER_MIN_SEC = 4.0
VEHICLE_PASSBY_MAX_SEC = 2.0
VEHICLE_LINGER_MIN_SEC = 4.0

MOTION_LOW_THRESHOLD = 0.18
MOTION_MEDIUM_THRESHOLD = 0.35
MOTION_HIGH_THRESHOLD = 0.65

PERSON_CLASS_IDS = {0}
VEHICLE_CLASS_IDS = {2, 3, 5, 7}

_YOLO_MODEL: Any = None
_YOLO_LOAD_ATTEMPTED = False


def _load_cv2() -> Any:
    try:
        import cv2  # type: ignore

        return cv2
    except Exception:
        return None


def _load_yolo() -> Any:
    global _YOLO_MODEL, _YOLO_LOAD_ATTEMPTED

    if _YOLO_LOAD_ATTEMPTED:
        return _YOLO_MODEL

    _YOLO_LOAD_ATTEMPTED = True
    try:
        from ultralytics import YOLO  # type: ignore
    except Exception:
        return None

    backend_root = Path(__file__).resolve().parents[1]
    for weights in (backend_root / "yolo11n.pt", backend_root / "yolov8n.pt"):
        if weights.exists():
            try:
                _YOLO_MODEL = YOLO(str(weights))
                return _YOLO_MODEL
            except Exception:
                return None

    return None


def _safe_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


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


def _motion_for_samples(samples: list[dict]) -> dict:
    cv2 = _load_cv2()
    if cv2 is None or len(samples) < 2:
        return {
            "motion_score": 0.0,
            "max_motion_score": 0.0,
            "motion_spike_time_sec": 0.0,
            "scene_change_score": 0.0,
        }

    previous_gray = None
    motion_scores: list[tuple[float, float]] = []
    for sample in samples:
        frame = sample.get("frame")
        if frame is None:
            continue
        try:
            resized = cv2.resize(frame, (160, 90))
            gray = cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY)
        except Exception:
            continue
        if previous_gray is not None:
            diff = cv2.absdiff(previous_gray, gray)
            score = min(float(diff.mean()) / 60.0, 1.0)
            motion_scores.append((_safe_float(sample.get("time_sec")), round(score, 4)))
        previous_gray = gray

    if not motion_scores:
        return {
            "motion_score": 0.0,
            "max_motion_score": 0.0,
            "motion_spike_time_sec": 0.0,
            "scene_change_score": 0.0,
        }

    average = sum(score for _, score in motion_scores) / len(motion_scores)
    spike_time, max_score = max(motion_scores, key=lambda item: item[1])
    return {
        "motion_score": round(average, 4),
        "max_motion_score": round(max_score, 4),
        "motion_spike_time_sec": round(spike_time, 3),
        "scene_change_score": round(max_score, 4),
    }


def _detect_objects(samples: list[dict]) -> list[dict]:
    model = _load_yolo()
    if model is None:
        return []

    detections: list[dict] = []
    for sample in samples:
        frame = sample.get("frame")
        if frame is None:
            continue
        try:
            results = model.predict(frame, verbose=False, imgsz=320, conf=0.35)
        except Exception:
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
                    }
                )

    return detections


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
        impact_level = _motion_level(max_motion)
        possible_impact = impact_level in {"MEDIUM", "HIGH"}
        possible_contact = bool(possible_impact and (person_detected or vehicle_detected))
        contact_level = impact_level if possible_contact else "NONE"
        normal_traffic = bool(vehicle_detected and not person_detected and not possible_impact and max_motion < MOTION_MEDIUM_THRESHOLD)

        camera_evidence[camera] = {
            **motion,
            "possible_impact": possible_impact,
            "impact_level": impact_level,
            "possible_contact": possible_contact,
            "contact_level": contact_level,
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
    impact_level = _motion_level(max_motion_score)
    possible_impact = any(item.get("possible_impact") for item in camera_evidence.values())
    possible_contact = any(item.get("possible_contact") for item in camera_evidence.values())
    contact_level = impact_level if possible_contact else "NONE"
    person_detected = any(item.get("person_detected") for item in camera_evidence.values())
    vehicle_detected = any(item.get("vehicle_detected") for item in camera_evidence.values())
    person_passby = any(item.get("person_passby_detected") for item in camera_evidence.values())
    person_lingering = any(item.get("person_lingering_detected") for item in camera_evidence.values())
    vehicle_passby = any(item.get("vehicle_passby_detected") for item in camera_evidence.values())
    vehicle_lingering = any(item.get("vehicle_lingering_detected") for item in camera_evidence.values())
    normal_traffic = bool(
        any(item.get("normal_traffic_evidence") for item in camera_evidence.values())
        and not possible_impact
        and not possible_contact
        and not person_lingering
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
        "camera_count": len(clips),
        "available_cameras": cameras,
        "total_duration_sec": round(duration, 3),
        "sampled_frames": int(sample_result.get("sampled_frames") or 0),
        "multi_camera": len(clips) > 1,
        "has_video": any(bool(clip.get("exists")) for clip in clips),
        "motion_score": round(motion_score, 4),
        "max_motion_score": round(max_motion_score, 4),
        "motion_spike_time_sec": next((item.get("motion_spike_time_sec", 0.0) for item in camera_evidence.values() if _safe_float(item.get("max_motion_score")) == max_motion_score), 0.0),
        "scene_change_score": round(max_motion_score, 4),
        "possible_impact": possible_impact,
        "impact_level": impact_level,
        "impact_score": round(max_motion_score, 4),
        "possible_contact": possible_contact,
        "contact_level": contact_level,
        "contact_score": round(max_motion_score if possible_contact else 0.0, 4),
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
        "strong_motion_spike": max_motion_score >= MOTION_HIGH_THRESHOLD,
        "camera_evidence": camera_evidence,
        "object_tracks": all_tracks,
        "primary_camera_candidate": best_camera,
        "timeline_markers": timeline_markers,
        "hero_thumbnail": "",
        "contact_sheet": "",
    }
