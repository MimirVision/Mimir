"""Lightweight local evidence extraction for grouped Core v2 events."""

from __future__ import annotations

from typing import Any

from .onnx_object_detector import detect_samples as detect_samples_onnx
from .onnx_object_detector import reset_runtime as reset_object_detector_runtime
from .onnx_object_detector import runtime_diagnostics as object_detector_runtime_diagnostics


PERSON_PASSBY_MAX_SEC = 2.0
PERSON_LINGER_MIN_SEC = 4.0
VEHICLE_PASSBY_MAX_SEC = 2.0
VEHICLE_LINGER_MIN_SEC = 4.0

EVIDENCE_VERSION = "local_evidence_v2_0_2"
IMPACT_DETECTION_VERSION = "impact_motion_v2_0_3"
KEY_MOMENT_VERSION = "key_moments_v3_two_pass_contact_timing"

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
NO_YOLO_LOCALIZED_MOTION_THRESHOLD = 0.50
NO_YOLO_SPIKE_RATIO_THRESHOLD = 4.0
NO_YOLO_CAMERA_SHAKE_THRESHOLD = 0.10
NO_YOLO_MAX_MOTION_THRESHOLD = 0.18
KEY_MOMENT_MAX_COUNT = 5
KEY_MOMENT_DEDUP_SEC = 1.0
VISUAL_CONTACT_IGNORE_START_SEC = 2.0
VISUAL_CONTACT_MIN_LOCALIZED = 0.48
VISUAL_CONTACT_MIN_GLOBAL = 0.10
VISUAL_CONTACT_LOCALIZED_RATIO = 0.55
VISUAL_CONTACT_MIN_CLUSTER_SEC = 1.25
VISUAL_CONTACT_MIN_CLUSTER_SAMPLES = 3
VISUAL_CONTACT_BODY_ZONE_BONUS = 0.08
VISUAL_CONTACT_MIN_SCORE = 0.32
CONTACT_TRACK_WINDOW_BEFORE_SEC = 0.25
CONTACT_TRACK_WINDOW_AFTER_SEC = 1.5
CONTACT_TRACK_MIN_VISUAL_SCORE = 0.22
CONTACT_TRACK_MIN_LOCALIZED_SCORE = 0.30
CONTACT_TRACK_MIN_EGO_ZONE_SCORE = 0.22
MOTION_REGION_MIN_AREA_RATIO = 0.0012
MOTION_REGION_TOUCH_MARGIN_RATIO = 0.035
OBJECT_CONTACT_TIME_TOLERANCE_SEC = 0.35
OBJECT_CONTACT_MIN_SCORE = 0.30
OBJECT_CONTACT_HIGH_SCORE = 0.48
OBJECT_CONTACT_VEHICLE_ONLY_MIN_SCORE = 0.60
OBJECT_CONTACT_PERSON_ONLY_MIN_SCORE = 0.62
OBJECT_CONTACT_VEHICLE_ONLY_IGNORE_START_SEC = 4.0
EGO_ZONE_MIN_MOTION = 0.16
EGO_ZONE_MOTION_RATIO = 0.50
OBJECT_DETECTION_UNIFORM_INTERVAL_SEC = 1.0
OBJECT_DETECTION_CANDIDATE_RADIUS_SEC = 2.0

_OBJECT_DETECTOR_AVAILABLE = False
_OBJECT_DETECTOR_FAILURES = 0
_OBJECT_DETECTOR_FORCED_DISABLED = False


def set_yolo_disabled(disabled: bool) -> None:
    """Backward-compatible flag that disables all object detection."""

    global _OBJECT_DETECTOR_FORCED_DISABLED, _OBJECT_DETECTOR_AVAILABLE
    changed = _OBJECT_DETECTOR_FORCED_DISABLED != bool(disabled)
    _OBJECT_DETECTOR_FORCED_DISABLED = bool(disabled)
    if _OBJECT_DETECTOR_FORCED_DISABLED:
        _OBJECT_DETECTOR_AVAILABLE = False
    if changed:
        reset_object_detector_runtime()


def _load_cv2() -> Any:
    try:
        import cv2  # type: ignore

        return cv2
    except Exception:
        return None


def get_evidence_runtime_diagnostics() -> dict:
    detector = object_detector_runtime_diagnostics()
    return {
        "object_detection_available": bool(_OBJECT_DETECTOR_AVAILABLE),
        "object_detection_failures": int(_OBJECT_DETECTOR_FAILURES),
        "object_detection_forced_disabled": bool(_OBJECT_DETECTOR_FORCED_DISABLED),
        # Kept for backward-compatible diagnostics and --disable-yolo clients.
        "yolo_available": bool(_OBJECT_DETECTOR_AVAILABLE),
        "yolo_failures": int(_OBJECT_DETECTOR_FAILURES),
        "yolo_forced_disabled": bool(_OBJECT_DETECTOR_FORCED_DISABLED),
        **detector,
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
        "ego_vehicle_zone": "",
        "ego_zone_motion_score": 0.0,
        "visual_contact_time_sec": 0.0,
        "visual_contact_score": 0.0,
        "visual_contact_candidate": False,
        "visual_contact_reasons": [],
        "object_contact_candidate": False,
        "object_contact_used_for_contact": False,
        "object_contact_time_sec": 0.0,
        "object_contact_score": 0.0,
        "object_touching_ego_vehicle": False,
        "contact_object_class": "",
        "contact_object_type": "",
        "object_contact_reasons": [],
        "impact_evidence_reasons": [],
        "motion_samples": [],
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


def _ego_zone_rects() -> dict[str, tuple[float, float, float, float]]:
    return {
        "left_edge": (0.00, 0.15, 0.18, 1.00),
        "right_edge": (0.82, 0.15, 1.00, 1.00),
        "lower_band": (0.00, 0.62, 1.00, 1.00),
        "lower_left_body": (0.00, 0.45, 0.35, 1.00),
        "lower_right_body": (0.65, 0.45, 1.00, 1.00),
    }


def _rect_intersection_area(left: tuple[float, float, float, float], right: tuple[float, float, float, float]) -> float:
    x1 = max(left[0], right[0])
    y1 = max(left[1], right[1])
    x2 = min(left[2], right[2])
    y2 = min(left[3], right[3])
    if x2 <= x1 or y2 <= y1:
        return 0.0
    return (x2 - x1) * (y2 - y1)


def _rect_area(rect: tuple[float, float, float, float]) -> float:
    return max(0.0, rect[2] - rect[0]) * max(0.0, rect[3] - rect[1])


def _rect_distance(left: tuple[float, float, float, float], right: tuple[float, float, float, float]) -> float:
    dx = max(left[0] - right[2], right[0] - left[2], 0.0)
    dy = max(left[1] - right[3], right[1] - left[3], 0.0)
    return (dx * dx + dy * dy) ** 0.5


def _rect_touches_ego(rect: tuple[float, float, float, float], margin: float = MOTION_REGION_TOUCH_MARGIN_RATIO) -> tuple[bool, str, float]:
    best_zone = ""
    best_distance = 99.0
    for zone_name, zone_rect in _ego_zone_rects().items():
        overlap = _rect_intersection_area(rect, zone_rect)
        distance = 0.0 if overlap > 0 else _rect_distance(rect, zone_rect)
        if distance < best_distance:
            best_distance = distance
            best_zone = zone_name
        if overlap > 0 or distance <= margin:
            return True, zone_name, distance
    return False, best_zone, best_distance


def _zone_score(diff: Any, x1_ratio: float, y1_ratio: float, x2_ratio: float, y2_ratio: float) -> float:
    height, width = diff.shape[:2]
    x1 = max(0, min(width, int(width * x1_ratio)))
    x2 = max(0, min(width, int(width * x2_ratio)))
    y1 = max(0, min(height, int(height * y1_ratio)))
    y2 = max(0, min(height, int(height * y2_ratio)))
    if x2 <= x1 or y2 <= y1:
        return 0.0
    zone = diff[y1:y2, x1:x2]
    if not getattr(zone, "size", 0):
        return 0.0
    return min(float(zone.mean()) / 55.0, 1.0)


def _ego_vehicle_zone_motion(diff: Any) -> tuple[str, float, dict[str, float]]:
    """Estimate near-camera/ego-car motion without needing a calibrated car mask.

    Parked-car cameras often include the host car at a side edge, lower edge, or
    bumper/body-adjacent region. These zones are intentionally broad; they are
    used for timestamping key moments, not for changing severity by themselves.
    """

    zones = {name: _zone_score(diff, *rect) for name, rect in _ego_zone_rects().items()}
    zone_name, score = max(zones.items(), key=lambda item: item[1])
    return zone_name, round(score, 4), {key: round(value, 4) for key, value in zones.items()}


def _motion_regions_from_diff(diff: Any) -> list[dict]:
    try:
        cv2 = _load_cv2()
        if cv2 is None:
            return []
        height, width = diff.shape[:2]
        if height <= 0 or width <= 0:
            return []
        threshold_value = max(14.0, float(diff.mean()) + float(diff.std()) * 1.25)
        _, mask = cv2.threshold(diff, threshold_value, 255, cv2.THRESH_BINARY)
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
        mask = cv2.dilate(mask, kernel, iterations=1)
        count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    except Exception:
        return []

    regions: list[dict] = []
    frame_area = float(width * height)
    min_area = max(12.0, frame_area * MOTION_REGION_MIN_AREA_RATIO)
    for label in range(1, count):
        x = int(stats[label, cv2.CC_STAT_LEFT])
        y = int(stats[label, cv2.CC_STAT_TOP])
        w = int(stats[label, cv2.CC_STAT_WIDTH])
        h = int(stats[label, cv2.CC_STAT_HEIGHT])
        area = float(stats[label, cv2.CC_STAT_AREA])
        if area < min_area or w <= 1 or h <= 1:
            continue
        rect = (
            max(0.0, x / width),
            max(0.0, y / height),
            min(1.0, (x + w) / width),
            min(1.0, (y + h) / height),
        )
        touches_ego, zone_name, distance = _rect_touches_ego(rect)
        region_pixels = diff[y:y + h, x:x + w]
        intensity = min(float(region_pixels.mean()) / 55.0, 1.0) if getattr(region_pixels, "size", 0) else 0.0
        regions.append(
            {
                "bbox_ratio": [round(value, 4) for value in rect],
                "area_ratio": round(area / frame_area, 5),
                "motion_score": round(intensity, 4),
                "touches_ego_vehicle": touches_ego,
                "ego_vehicle_zone": zone_name,
                "ego_distance_ratio": round(distance, 4),
            }
        )

    regions.sort(key=lambda item: (_safe_float(item.get("touches_ego_vehicle")), _safe_float(item.get("motion_score")), _safe_float(item.get("area_ratio"))), reverse=True)
    return regions[:8]


def _first_visual_contact_sample(motion_samples: list[dict], max_localized: float, duration_sec: float) -> dict:
    if not motion_samples:
        return {}

    max_ego_motion = max((_safe_float(sample.get("ego_zone_motion_score")) for sample in motion_samples), default=0.0)
    localized_threshold = max(VISUAL_CONTACT_MIN_LOCALIZED, max_localized * VISUAL_CONTACT_LOCALIZED_RATIO)
    ego_threshold = max(EGO_ZONE_MIN_MOTION, max_ego_motion * EGO_ZONE_MOTION_RATIO)
    ignore_start = VISUAL_CONTACT_IGNORE_START_SEC if duration_sec >= 6.0 else 0.0
    fallback_candidate: dict = {}
    segments: list[list[dict]] = []
    current_segment: list[dict] = []

    def is_candidate(sample: dict) -> bool:
        global_motion = _safe_float(sample.get("motion_score"))
        localized = _safe_float(sample.get("localized_motion_score"))
        ego_motion = _safe_float(sample.get("ego_zone_motion_score"))
        if global_motion < VISUAL_CONTACT_MIN_GLOBAL:
            return False
        return localized >= localized_threshold or ego_motion >= ego_threshold

    def close_segment() -> None:
        nonlocal current_segment
        if current_segment:
            segments.append(current_segment)
            current_segment = []

    for sample in motion_samples:
        if not isinstance(sample, dict):
            continue
        time_sec = _safe_float(sample.get("time_sec"))
        if not is_candidate(sample):
            close_segment()
            continue
        if not fallback_candidate and time_sec >= ignore_start:
            fallback_candidate = sample
        if time_sec >= ignore_start:
            if current_segment:
                previous_time = _safe_float(current_segment[-1].get("time_sec"))
                if time_sec - previous_time > 1.1:
                    close_segment()
            current_segment.append(sample)
        elif not fallback_candidate:
            fallback_candidate = sample

    close_segment()

    for segment in segments:
        start_time = _safe_float(segment[0].get("time_sec"))
        end_time = _safe_float(segment[-1].get("time_sec"))
        body_zone_hits = sum(1 for item in segment if "body" in str(item.get("ego_vehicle_zone") or "") or item.get("ego_vehicle_zone") == "lower_band")
        sustained = (
            len(segment) >= VISUAL_CONTACT_MIN_CLUSTER_SAMPLES
            and end_time - start_time >= VISUAL_CONTACT_MIN_CLUSTER_SEC
        )
        if sustained and body_zone_hits >= max(1, len(segment) // 2):
            return segment[0]

    if segments:
        def segment_score(segment: list[dict]) -> tuple[float, float]:
            start_time = _safe_float(segment[0].get("time_sec"))
            peak_score = max((_safe_float(item.get("visual_contact_score")) for item in segment), default=0.0)
            body_bonus = VISUAL_CONTACT_BODY_ZONE_BONUS if any("body" in str(item.get("ego_vehicle_zone") or "") for item in segment) else 0.0
            duration_bonus = min(max(_safe_float(segment[-1].get("time_sec")) - start_time, 0.0) / 4.0, 0.25)
            return peak_score + body_bonus + duration_bonus, -start_time

        best_segment = max(segments, key=segment_score)
        return best_segment[0]

    return fallback_candidate


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
    motion_samples: list[dict] = []
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
            ego_zone, ego_zone_score, ego_zone_scores = _ego_vehicle_zone_motion(diff)
            motion_regions = _motion_regions_from_diff(diff)
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
            motion_sample = {
                "camera": sample.get("camera", "unknown"),
                "source_video": sample.get("source_video", ""),
                "frame_index": int(_safe_float(sample.get("frame_index"))),
                "fps": round(_safe_float(sample.get("fps")), 3),
                "time_sec": round(_safe_float(sample.get("time_sec")), 3),
                "duration_sec": round(_safe_float(sample.get("duration_sec")), 3),
                "motion_score": round(global_score, 4),
                "localized_motion_score": round(localized_score, 4),
                "ego_vehicle_zone": ego_zone,
                "ego_zone_motion_score": round(ego_zone_score, 4),
                "ego_zone_scores": ego_zone_scores,
                "visual_contact_score": round(max(ego_zone_score, localized_score) * 0.65 + global_score * 0.25 + camera_shake_score * 0.10, 4),
                "motion_regions": motion_regions,
                "camera_shake_score": round(camera_shake_score, 4),
            }
            motion_samples.append(motion_sample)
            sample["motion_score"] = motion_sample["motion_score"]
            sample["localized_motion_score"] = motion_sample["localized_motion_score"]
            sample["ego_zone_motion_score"] = motion_sample["ego_zone_motion_score"]
            sample["visual_contact_score"] = motion_sample["visual_contact_score"]
            sample["camera_shake_score"] = motion_sample["camera_shake_score"]
        previous_gray = gray

    if not motion_scores:
        return _empty_motion_metrics()

    average = sum(score for _, score, _, _ in motion_scores) / len(motion_scores)
    spike_time, max_score, localized_at_spike, _ = max(motion_scores, key=lambda item: item[1])
    max_localized = max(score for _, _, score, _ in motion_scores)
    max_ego_motion = max((_safe_float(sample.get("ego_zone_motion_score")) for sample in motion_samples), default=0.0)
    max_camera_shake = max(score for _, _, _, score in motion_scores)
    spike_ratio = _spike_ratio([score for _, score, _, _ in motion_scores], max_score)
    duration_sec = max((_safe_float(sample.get("duration_sec")) for sample in motion_samples), default=0.0)
    visual_contact = _first_visual_contact_sample(motion_samples, max_localized, duration_sec)
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
    visual_reasons: list[str] = []
    visual_contact_score = _safe_float(visual_contact.get("visual_contact_score")) if visual_contact else 0.0
    visual_contact_candidate = bool(visual_contact) and visual_contact_score >= VISUAL_CONTACT_MIN_SCORE
    if visual_contact_candidate:
        visual_reasons.append("first strong localized/ego-zone motion")
        if visual_contact.get("ego_vehicle_zone"):
            visual_reasons.append(f"ego_vehicle_zone={visual_contact.get('ego_vehicle_zone')}")

    return {
        "motion_score": round(average, 4),
        "max_motion_score": round(max_score, 4),
        "localized_motion_score": round(max(max_localized, localized_at_spike), 4),
        "ego_vehicle_zone": str(visual_contact.get("ego_vehicle_zone") or ""),
        "ego_zone_motion_score": round(max_ego_motion, 4),
        "visual_contact_time_sec": round(_safe_float(visual_contact.get("time_sec")), 3) if visual_contact else 0.0,
        "visual_contact_score": round(visual_contact_score, 4),
        "visual_contact_candidate": visual_contact_candidate,
        "visual_contact_frame_index": int(_safe_float(visual_contact.get("frame_index"))) if visual_contact else 0,
        "visual_contact_fps": round(_safe_float(visual_contact.get("fps")), 3) if visual_contact else 0.0,
        "visual_contact_reasons": visual_reasons,
        "motion_spike_time_sec": round(spike_time, 3),
        "motion_spike_ratio": spike_ratio,
        "camera_shake_score": round(max_camera_shake, 4),
        "abrupt_scene_change": abrupt_scene_change,
        "scene_change_score": round(max_score, 4),
        "impact_evidence_reasons": reasons,
        "motion_samples": motion_samples,
    }


def _detect_objects(samples: list[dict], object_detection_enabled: bool = True) -> tuple[list[dict], bool]:
    global _OBJECT_DETECTOR_AVAILABLE, _OBJECT_DETECTOR_FAILURES

    if not object_detection_enabled or _OBJECT_DETECTOR_FORCED_DISABLED:
        _OBJECT_DETECTOR_AVAILABLE = False
        return [], False

    onnx_detections, onnx_available, onnx_configured = detect_samples_onnx(samples)
    if onnx_configured:
        _OBJECT_DETECTOR_AVAILABLE = bool(onnx_available)
        if not onnx_available:
            _OBJECT_DETECTOR_FAILURES += 1
        return onnx_detections, onnx_available
    _OBJECT_DETECTOR_AVAILABLE = False
    _OBJECT_DETECTOR_FAILURES += 1
    return [], False


def _object_detection_samples(samples: list[dict], motion: dict) -> list[dict]:
    """Keep uniform context plus dense frames around local motion candidates."""

    if not samples:
        return []
    candidate_times = [
        _safe_float(motion.get("motion_spike_time_sec"), -1.0),
        _safe_float(motion.get("visual_contact_time_sec"), -1.0),
    ]
    motion_samples = motion.get("motion_samples") if isinstance(motion.get("motion_samples"), list) else []
    if motion_samples:
        peak = max(
            (item for item in motion_samples if isinstance(item, dict)),
            key=lambda item: max(
                _safe_float(item.get("motion_score")),
                _safe_float(item.get("localized_motion_score")),
                _safe_float(item.get("ego_zone_motion_score")),
            ),
            default={},
        )
        if peak:
            candidate_times.append(_safe_float(peak.get("time_sec"), -1.0))
    candidate_times = [value for value in candidate_times if value >= 0.0]

    selected: list[dict] = []
    last_uniform = -OBJECT_DETECTION_UNIFORM_INTERVAL_SEC
    for sample in samples:
        time_sec = _safe_float(sample.get("time_sec"))
        dense_candidate = any(
            abs(time_sec - candidate_time) <= OBJECT_DETECTION_CANDIDATE_RADIUS_SEC
            for candidate_time in candidate_times
        )
        uniform_candidate = time_sec - last_uniform >= OBJECT_DETECTION_UNIFORM_INTERVAL_SEC - 0.05
        if dense_candidate or uniform_candidate:
            selected.append(sample)
            if uniform_candidate:
                last_uniform = time_sec
    if not selected or selected[-1] is not samples[-1]:
        selected.append(samples[-1])
    return selected


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


def _detection_bbox_ratio(detection: dict) -> tuple[float, float, float, float] | None:
    bbox = detection.get("bbox") if isinstance(detection.get("bbox"), list) else []
    if len(bbox) != 4:
        return None
    frame_width = _safe_float(detection.get("frame_width"))
    frame_height = _safe_float(detection.get("frame_height"))
    if frame_width <= 0 or frame_height <= 0:
        return None
    x1, y1, x2, y2 = (_safe_float(value) for value in bbox)
    return (
        max(0.0, min(1.0, x1 / frame_width)),
        max(0.0, min(1.0, y1 / frame_height)),
        max(0.0, min(1.0, x2 / frame_width)),
        max(0.0, min(1.0, y2 / frame_height)),
    )


def _related_detections_for_region(region_rect: tuple[float, float, float, float], detections: list[dict]) -> list[tuple[dict, float]]:
    related: list[tuple[dict, float]] = []
    for detection in detections:
        detection_rect = _detection_bbox_ratio(detection)
        if detection_rect is None:
            continue
        overlap = _rect_intersection_area(region_rect, detection_rect)
        min_area = max(min(_rect_area(region_rect), _rect_area(detection_rect)), 0.0001)
        overlap_ratio = overlap / min_area
        distance = _rect_distance(region_rect, detection_rect)
        ego_touching, _, ego_distance = _rect_touches_ego(detection_rect, margin=0.055)
        if overlap_ratio >= 0.03 or distance <= 0.07 or ego_touching:
            relation_score = max(overlap_ratio, max(0.0, 0.08 - distance), max(0.0, 0.06 - ego_distance))
            related.append((detection, relation_score))
    return related


def _visual_object_contact_details(motion: dict, detections: list[dict]) -> dict:
    motion_samples = motion.get("motion_samples") if isinstance(motion.get("motion_samples"), list) else []
    if not motion_samples or not detections:
        return {
            "object_contact_candidate": False,
            "object_contact_time_sec": 0.0,
            "object_contact_score": 0.0,
            "object_touching_ego_vehicle": False,
            "contact_object_class": "",
            "contact_object_type": "",
            "object_contact_reasons": [],
        }

    best_candidate: dict = {}
    first_candidate: dict = {}

    for sample in motion_samples:
        if not isinstance(sample, dict):
            continue
        sample_time = _safe_float(sample.get("time_sec"))
        if sample_time < VISUAL_CONTACT_IGNORE_START_SEC:
            continue
        sample_detections = [
            detection
            for detection in detections
            if abs(_safe_float(detection.get("time_sec")) - sample_time) <= OBJECT_CONTACT_TIME_TOLERANCE_SEC
        ]
        if not sample_detections:
            continue

        for region in sample.get("motion_regions", []) if isinstance(sample.get("motion_regions"), list) else []:
            if not isinstance(region, dict) or not region.get("touches_ego_vehicle"):
                continue
            raw_rect = region.get("bbox_ratio") if isinstance(region.get("bbox_ratio"), list) else []
            if len(raw_rect) != 4:
                continue
            region_rect = tuple(_safe_float(value) for value in raw_rect)
            related = _related_detections_for_region(region_rect, sample_detections)
            if not related:
                continue

            classes = sorted({str(detection.get("class_name") or "object") for detection, _ in related})
            has_person = "person" in classes
            has_vehicle = "vehicle" in classes
            if has_person and has_vehicle:
                object_type = "vehicle_door_or_body"
            elif has_vehicle:
                object_type = "vehicle_body"
            elif has_person:
                object_type = "person"
            else:
                object_type = "object"

            relation_score = max(score for _, score in related)
            region_motion = _safe_float(region.get("motion_score"))
            score = max(
                _safe_float(sample.get("visual_contact_score")),
                _safe_float(sample.get("localized_motion_score")) * 0.75,
                _safe_float(sample.get("ego_zone_motion_score")) * 0.75,
                region_motion,
            )
            if has_person and has_vehicle:
                score += 0.08
            if object_type in {"vehicle_door_or_body", "vehicle_body"}:
                score += 0.05
            if relation_score > 0:
                score += min(relation_score, 0.12)
            score = min(score, 1.0)

            contact_impulse = bool(
                _safe_float(sample.get("visual_contact_score")) >= CONTACT_TRACK_MIN_VISUAL_SCORE
                or _safe_float(sample.get("localized_motion_score")) >= CONTACT_TRACK_MIN_LOCALIZED_SCORE
                or _safe_float(sample.get("ego_zone_motion_score")) >= CONTACT_TRACK_MIN_EGO_ZONE_SCORE
            )

            if has_vehicle and not has_person and sample_time < OBJECT_CONTACT_VEHICLE_ONLY_IGNORE_START_SEC:
                accepted = False
            elif has_person and has_vehicle:
                accepted = score >= OBJECT_CONTACT_MIN_SCORE and contact_impulse
            elif has_vehicle:
                accepted = score >= OBJECT_CONTACT_VEHICLE_ONLY_MIN_SCORE
            elif has_person:
                accepted = score >= OBJECT_CONTACT_PERSON_ONLY_MIN_SCORE
            else:
                accepted = False

            if not accepted:
                continue

            reasons = [
                "moving pixels reach the estimated ego-vehicle region (apparent visual contact only)",
                f"ego_vehicle_zone={region.get('ego_vehicle_zone')}",
                f"contact_object_type={object_type}",
            ]
            if has_person and has_vehicle:
                reasons.append("person and vehicle overlap contact window")
            if contact_impulse:
                reasons.append("localized ego-zone motion impulse")
            reasons.append(f"object_contact_score={score:.3f}")

            candidate = {
                "object_contact_candidate": True,
                "object_contact_time_sec": round(sample_time, 3),
                "object_contact_score": round(score, 4),
                "object_touching_ego_vehicle": True,
                "contact_object_class": "+".join(classes),
                "contact_object_type": object_type,
                "object_contact_frame_index": int(_safe_float(sample.get("frame_index"))),
                "object_contact_fps": round(_safe_float(sample.get("fps")), 3),
                "object_contact_reasons": reasons,
            }
            if not first_candidate:
                first_candidate = candidate
            if not best_candidate or score > _safe_float(best_candidate.get("object_contact_score")):
                best_candidate = candidate

    if str(best_candidate.get("contact_object_type") or "") == "vehicle_door_or_body":
        return best_candidate

    return first_candidate or best_candidate or {
        "object_contact_candidate": False,
        "object_contact_used_for_contact": False,
        "object_contact_time_sec": 0.0,
        "object_contact_score": 0.0,
        "object_touching_ego_vehicle": False,
        "contact_object_class": "",
        "contact_object_type": "",
        "object_contact_reasons": [],
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


def _no_yolo_motion_impact_details(motion: dict, object_detection_available: bool) -> dict:
    localized_motion = _safe_float(motion.get("localized_motion_score"))
    spike_ratio = _safe_float(motion.get("motion_spike_ratio"))
    camera_shake = _safe_float(motion.get("camera_shake_score"))
    max_motion = _safe_float(motion.get("max_motion_score"))
    candidate = bool(
        not object_detection_available
        and localized_motion >= NO_YOLO_LOCALIZED_MOTION_THRESHOLD
        and spike_ratio >= NO_YOLO_SPIKE_RATIO_THRESHOLD
        and (
            camera_shake >= NO_YOLO_CAMERA_SHAKE_THRESHOLD
            or max_motion >= NO_YOLO_MAX_MOTION_THRESHOLD
        )
    )
    reasons: list[str] = []
    if candidate:
        reasons.extend(
            [
                "object_detection_unavailable",
                f"localized_motion_score={localized_motion:.4f}",
                f"motion_spike_ratio={spike_ratio:.3f}",
            ]
        )
        if camera_shake >= NO_YOLO_CAMERA_SHAKE_THRESHOLD:
            reasons.append(f"camera_shake_score={camera_shake:.4f}")
        if max_motion >= NO_YOLO_MAX_MOTION_THRESHOLD:
            reasons.append(f"max_motion_score={max_motion:.4f}")

    return {
        "no_yolo_motion_impact_candidate": candidate,
        "crash_safety_triggered": candidate,
        "crash_safety_reasons": reasons,
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


def _clip_duration_by_camera(event_group: dict, sample_result: dict | None = None) -> dict[str, float]:
    durations: dict[str, float] = {}
    for clip in event_group.get("clips", []):
        if not isinstance(clip, dict):
            continue
        camera = str(clip.get("camera") or "unknown")
        durations[camera] = max(durations.get(camera, 0.0), _safe_float(clip.get("duration_sec")))

    if isinstance(sample_result, dict):
        for metric in sample_result.get("clip_metrics", []):
            if not isinstance(metric, dict):
                continue
            camera = str(metric.get("camera") or "unknown")
            durations[camera] = max(durations.get(camera, 0.0), _safe_float(metric.get("duration_sec")))

    return durations


def _event_duration(event_group: dict, sample_result: dict | None = None) -> float:
    durations = _clip_duration_by_camera(event_group, sample_result)
    return round(max(durations.values(), default=0.0), 3)


def _clamp_time(time_sec: float, duration_sec: float) -> float:
    time_sec = max(0.0, _safe_float(time_sec))
    if duration_sec > 0:
        time_sec = min(time_sec, duration_sec)
    return round(time_sec, 3)


def _key_moment_priority(moment: dict) -> tuple[int, float]:
    priority = {
        "impact_contact": 80,
        "motion_spike": 60,
        "vehicle_near": 45,
        "person_near": 40,
        "start": 30,
        "review_point": 10,
    }
    return (
        priority.get(str(moment.get("type") or ""), 0),
        _safe_float(moment.get("confidence")),
    )


def _make_key_moment(
    time_sec: float,
    label: str,
    moment_type: str,
    confidence: float,
    source: str,
    reason: str,
    camera: str = "unknown",
    duration_sec: float = 0.0,
    frame_index: object = None,
    fps: object = None,
    primary: bool = False,
) -> dict:
    moment = {
        "time_sec": _clamp_time(time_sec, duration_sec),
        "label": str(label or "Key moment"),
        "type": str(moment_type or "review_point"),
        "confidence": round(max(0.0, min(_safe_float(confidence), 1.0)), 3),
        "source": str(source or "fallback"),
        "reason": str(reason or ""),
        "camera": str(camera or "unknown"),
    }
    if frame_index is not None:
        moment["frame_index"] = int(_safe_float(frame_index))
    if fps is not None:
        moment["fps"] = round(_safe_float(fps), 3)
    if primary:
        moment["primary"] = True
    return moment


def _dedupe_key_moments(moments: list[dict]) -> list[dict]:
    cleaned: list[dict] = []
    for moment in sorted(moments, key=lambda item: (_safe_float(item.get("time_sec")), -_key_moment_priority(item)[0])):
        moment_time = _safe_float(moment.get("time_sec"))
        duplicate_index = None
        for index, existing in enumerate(cleaned):
            if abs(_safe_float(existing.get("time_sec")) - moment_time) < KEY_MOMENT_DEDUP_SEC:
                duplicate_index = index
                break
        if duplicate_index is None:
            cleaned.append(moment)
            continue
        if _key_moment_priority(moment) > _key_moment_priority(cleaned[duplicate_index]):
            cleaned[duplicate_index] = moment

    cleaned.sort(key=lambda item: _safe_float(item.get("time_sec")))
    if len(cleaned) <= KEY_MOMENT_MAX_COUNT:
        return cleaned
    protected = [moment for moment in cleaned if moment.get("type") == "impact_contact"]
    remaining = [moment for moment in cleaned if moment.get("type") != "impact_contact"]
    selected = (protected + remaining)[:KEY_MOMENT_MAX_COUNT]
    return sorted(selected, key=lambda item: _safe_float(item.get("time_sec")))


def _motion_peak(camera: str, evidence: dict) -> dict:
    motion_samples = evidence.get("motion_samples") if isinstance(evidence.get("motion_samples"), list) else []
    if motion_samples:
        peak = max(motion_samples, key=lambda item: _safe_float(item.get("motion_score")) if isinstance(item, dict) else 0.0)
        return peak if isinstance(peak, dict) else {}
    return {
        "camera": camera,
        "time_sec": evidence.get("motion_spike_time_sec", 0.0),
        "frame_index": None,
        "fps": None,
        "motion_score": evidence.get("max_motion_score", 0.0),
    }


def _first_activity_time(evidence: dict) -> dict:
    motion_samples = evidence.get("motion_samples") if isinstance(evidence.get("motion_samples"), list) else []
    max_motion = _safe_float(evidence.get("max_motion_score"))
    threshold = max(0.04, min(MOTION_LOW_THRESHOLD, max_motion * 0.35))
    for sample in motion_samples:
        if not isinstance(sample, dict):
            continue
        if (
            _safe_float(sample.get("motion_score")) >= threshold
            or _safe_float(sample.get("localized_motion_score")) >= threshold
        ):
            return sample
    return {}


def _first_track(tracks: list[dict], class_name: str) -> dict:
    matching = [track for track in tracks if isinstance(track, dict) and track.get("class_name") == class_name]
    if not matching:
        return {}
    return min(matching, key=lambda item: _safe_float(item.get("first_seen_time_sec")))


def _contact_track_sample(evidence: dict, tracks: list[dict]) -> dict:
    """Find the strongest ego-zone motion while a person is close to the car.

    This is used only for timestamping key moments after contact/impact evidence
    already exists. It helps door-ding clips point at the visible door/person
    interaction instead of a later shake or exit motion.
    """

    motion_samples = evidence.get("motion_samples") if isinstance(evidence.get("motion_samples"), list) else []
    if not motion_samples:
        return {}

    person_tracks = [track for track in tracks if isinstance(track, dict) and track.get("class_name") == "person"]
    if not person_tracks:
        return {}

    best_sample: dict = {}
    best_score = 0.0
    for track in person_tracks:
        window_start = max(0.0, _safe_float(track.get("first_seen_time_sec")) - CONTACT_TRACK_WINDOW_BEFORE_SEC)
        window_end = _safe_float(track.get("last_seen_time_sec")) + CONTACT_TRACK_WINDOW_AFTER_SEC
        candidates = [
            sample
            for sample in motion_samples
            if isinstance(sample, dict)
            and window_start <= _safe_float(sample.get("time_sec")) <= window_end
        ]
        if not candidates:
            continue
        sample = max(
            candidates,
            key=lambda item: (
                _safe_float(item.get("visual_contact_score")),
                _safe_float(item.get("localized_motion_score")),
                _safe_float(item.get("ego_zone_motion_score")),
            ),
        )
        score = max(
            _safe_float(sample.get("visual_contact_score")),
            _safe_float(sample.get("localized_motion_score")) * 0.75,
            _safe_float(sample.get("ego_zone_motion_score")) * 0.75,
        )
        if score >= CONTACT_TRACK_MIN_VISUAL_SCORE and score > best_score:
            best_sample = sample
            best_score = score

    return best_sample


def _primary_key_moment(moments: list[dict]) -> dict:
    primary_moments = [moment for moment in moments if moment.get("primary")]
    if primary_moments:
        return max(primary_moments, key=_key_moment_priority)
    for moment_type in ("impact_contact", "motion_spike", "vehicle_near", "person_near", "review_point"):
        matching = [moment for moment in moments if moment.get("type") == moment_type]
        if matching:
            return max(matching, key=_key_moment_priority)
    return moments[0] if moments else {}


def _timeline_markers_from_key_moments(key_moments: list[dict], final_severity: str = "") -> list[dict]:
    severity = str(final_severity or "").upper()
    markers: list[dict] = []
    for moment in key_moments:
        moment_type = str(moment.get("type") or "review_point")
        marker_severity = "NEUTRAL"
        if moment_type == "impact_contact":
            marker_severity = "IMPORTANT" if severity == "IMPORTANT" else "REVIEW"
        elif moment_type in {"motion_spike", "person_near", "vehicle_near"}:
            marker_severity = "REVIEW"
        markers.append(
            {
                "time_sec": moment.get("time_sec", 0.0),
                "type": moment_type,
                "severity": marker_severity,
                "label": moment.get("label", "Key moment"),
                "description": moment.get("reason", ""),
                "source": moment.get("source", ""),
                "camera": moment.get("camera", "unknown"),
            }
        )
    return markers


def _build_key_moments(
    event_group: dict,
    sample_result: dict,
    camera_evidence: dict[str, dict],
    best_camera: str,
    all_tracks: list[dict],
    possible_impact: bool,
    possible_contact: bool,
    strong_impact_like_motion: bool,
    normal_traffic: bool,
    crash_safety_triggered: bool = False,
    no_yolo_motion_impact_candidate: bool = False,
) -> dict:
    duration_sec = _event_duration(event_group, sample_result)
    durations_by_camera = _clip_duration_by_camera(event_group, sample_result)
    moments: list[dict] = []
    primary_camera = best_camera or next(iter(camera_evidence.keys()), "unknown")
    primary_evidence = camera_evidence.get(primary_camera, {})
    peak = _motion_peak(primary_camera, primary_evidence)
    peak_time = _safe_float(peak.get("time_sec"), _safe_float(primary_evidence.get("motion_spike_time_sec")))
    object_contact_time = _safe_float(primary_evidence.get("object_contact_time_sec"))
    object_contact_candidate = bool(primary_evidence.get("object_contact_candidate")) and object_contact_time > 0.0
    object_contact_type = str(primary_evidence.get("contact_object_type") or "")
    contact_track = _contact_track_sample(primary_evidence, all_tracks)
    contact_track_time = _safe_float(contact_track.get("time_sec"))
    contact_track_candidate = bool(contact_track) and contact_track_time > 0.0
    visual_contact_time = _safe_float(primary_evidence.get("visual_contact_time_sec"))
    visual_contact_candidate = bool(primary_evidence.get("visual_contact_candidate")) and visual_contact_time > 0.0
    max_motion = _safe_float(primary_evidence.get("max_motion_score"))
    spike_ratio = _safe_float(primary_evidence.get("motion_spike_ratio"))
    confidence = max(max_motion, min(spike_ratio / 5.0, 1.0), _safe_float(primary_evidence.get("impact_candidate_score")))
    contact_evidence_exists = possible_contact or possible_impact or strong_impact_like_motion
    prefer_object_contact = bool(
        object_contact_candidate
        and contact_evidence_exists
        and object_contact_type == "vehicle_door_or_body"
    )
    if prefer_object_contact:
        primary_time = object_contact_time
        primary_frame_index = primary_evidence.get("object_contact_frame_index")
        primary_fps = primary_evidence.get("object_contact_fps")
        primary_source = "local_visual_object_contact"
        primary_reason_source = "object_touching_ego"
    elif contact_track_candidate and contact_evidence_exists and (not visual_contact_candidate or contact_track_time <= visual_contact_time):
        primary_time = contact_track_time
        primary_frame_index = contact_track.get("frame_index")
        primary_fps = contact_track.get("fps")
        primary_source = "local_visual_motion"
        primary_reason_source = "close_person_motion"
    elif visual_contact_candidate and contact_evidence_exists:
        primary_time = visual_contact_time
        primary_frame_index = primary_evidence.get("visual_contact_frame_index")
        primary_fps = primary_evidence.get("visual_contact_fps")
        primary_source = "local_visual_motion"
        primary_reason_source = "ego_zone_motion"
    else:
        primary_time = peak_time
        primary_frame_index = peak.get("frame_index")
        primary_fps = peak.get("fps")
        primary_source = "local_motion"
        primary_reason_source = "motion_peak"

    if crash_safety_triggered or no_yolo_motion_impact_candidate:
        primary_label = "Impact"
        primary_type = "impact_contact"
        primary_reason = (
            "moving pixels reach the estimated ego-vehicle area while object detection was unavailable"
            if primary_reason_source == "object_touching_ego"
            else "motion spike near a close person/object while object detection was unavailable"
            if primary_reason_source == "close_person_motion"
            else "motion spike near ego vehicle area while object detection was unavailable"
            if primary_reason_source == "ego_zone_motion"
            else "motion spike detected while object detection was unavailable"
        )
    elif strong_impact_like_motion:
        primary_label = "Impact"
        primary_type = "impact_contact"
        primary_reason = (
            "moving pixels reach the estimated ego-vehicle area (apparent contact only)"
            if primary_reason_source == "object_touching_ego"
            else "close person/object motion near ego vehicle area"
            if primary_reason_source == "close_person_motion"
            else "first contact-like motion near ego vehicle area"
            if primary_reason_source == "ego_zone_motion"
            else "strong impact-like motion"
        )
    elif possible_contact or possible_impact:
        primary_label = "Impact/contact"
        primary_type = "impact_contact"
        primary_reason = (
            "moving pixels reach the estimated ego-vehicle area (apparent contact only)"
            if primary_reason_source == "object_touching_ego"
            else "close person/object motion near ego vehicle area"
            if primary_reason_source == "close_person_motion"
            else "first contact-like motion near ego vehicle area"
            if primary_reason_source == "ego_zone_motion"
            else "motion spike + contact/impact candidate"
        )
    elif max_motion > 0.0 or peak_time > 0.0:
        primary_label = "Key moment"
        primary_type = "motion_spike"
        primary_reason = "largest local motion spike"
    else:
        primary_label = ""
        primary_type = ""
        primary_reason = ""

    if primary_label:
        moments.append(
            _make_key_moment(
                primary_time,
                primary_label,
                primary_type,
                max(
                    confidence,
                    _safe_float(primary_evidence.get("visual_contact_score")),
                    _safe_float(primary_evidence.get("object_contact_score")),
                ),
                primary_source,
                primary_reason,
                primary_camera,
                durations_by_camera.get(primary_camera, duration_sec),
                primary_frame_index,
                primary_fps,
                True,
            )
        )

    first_activity = _first_activity_time(primary_evidence)
    if first_activity:
        moments.append(
            _make_key_moment(
                _safe_float(first_activity.get("time_sec")),
                "Activity starts",
                "start",
                max(0.25, _safe_float(first_activity.get("motion_score"))),
                "local_motion",
                "first meaningful local motion",
                str(first_activity.get("camera") or primary_camera),
                durations_by_camera.get(primary_camera, duration_sec),
                first_activity.get("frame_index"),
                first_activity.get("fps"),
            )
        )

    if peak and max_motion > 0.0:
        peak_is_primary = abs(peak_time - primary_time) < 0.75
        peak_is_contact = (possible_contact or possible_impact) and peak_is_primary
        peak_label = "Impact/contact" if peak_is_contact else "Peak motion"
        moments.append(
            _make_key_moment(
                peak_time,
                peak_label,
                "impact_contact" if peak_is_contact else "motion_spike",
                confidence,
                "local_motion",
                "highest sampled motion score",
                primary_camera,
                durations_by_camera.get(primary_camera, duration_sec),
                peak.get("frame_index"),
                peak.get("fps"),
            )
        )

    if not possible_contact and not possible_impact:
        person_track = _first_track(all_tracks, "person")
        if person_track:
            moments.append(
                _make_key_moment(
                    _safe_float(person_track.get("first_seen_time_sec")),
                    "Person nearby",
                    "person_near",
                    _safe_float(person_track.get("confidence_max"), 0.35),
                    "local_yolo",
                    "first person detection",
                    str(person_track.get("camera") or "unknown"),
                    durations_by_camera.get(str(person_track.get("camera") or "unknown"), duration_sec),
                )
            )
        vehicle_track = _first_track(all_tracks, "vehicle")
        if vehicle_track and not normal_traffic:
            moments.append(
                _make_key_moment(
                    _safe_float(vehicle_track.get("first_seen_time_sec")),
                    "Vehicle nearby",
                    "vehicle_near",
                    _safe_float(vehicle_track.get("confidence_max"), 0.35),
                    "local_yolo",
                    "first vehicle detection",
                    str(vehicle_track.get("camera") or "unknown"),
                    durations_by_camera.get(str(vehicle_track.get("camera") or "unknown"), duration_sec),
                )
            )

    if not moments:
        fallback_time = duration_sec / 2.0 if duration_sec > 0 else 0.0
        moments.append(
            _make_key_moment(
                fallback_time,
                "Review point",
                "review_point",
                0.1,
                "fallback",
                "no timed local evidence; using clip midpoint",
                primary_camera,
                duration_sec,
            )
        )

    key_moments = _dedupe_key_moments(moments)
    primary = _primary_key_moment(key_moments)
    return {
        "key_moments": key_moments,
        "primary_key_moment_sec": primary.get("time_sec", 0.0),
        "primary_key_moment_label": primary.get("label", "Review point"),
        "key_moment_version": KEY_MOMENT_VERSION,
        "timeline_markers": _timeline_markers_from_key_moments(key_moments),
    }


def _fallback_key_moments(event_group: dict) -> dict:
    duration_sec = _event_duration(event_group)
    fallback_time = duration_sec / 2.0 if duration_sec > 0 else 0.0
    key_moments = [
        _make_key_moment(
            fallback_time,
            "Review point",
            "review_point",
            0.1,
            "fallback",
            "frame evidence unavailable; using clip midpoint",
            "unknown",
            duration_sec,
        )
    ]
    return {
        "key_moments": key_moments,
        "primary_key_moment_sec": key_moments[0]["time_sec"],
        "primary_key_moment_label": key_moments[0]["label"],
        "key_moment_version": KEY_MOMENT_VERSION,
        "timeline_markers": _timeline_markers_from_key_moments(key_moments),
    }


def fallback_evidence(event_group: dict, warning: str) -> dict:
    clips = event_group.get("clips") if isinstance(event_group.get("clips"), list) else []
    cameras = event_group.get("available_cameras") if isinstance(event_group.get("available_cameras"), list) else []
    fallback_moments = _fallback_key_moments(event_group)
    return {
        "evidence_version": EVIDENCE_VERSION,
        "impact_detection_version": IMPACT_DETECTION_VERSION,
        "key_moment_version": KEY_MOMENT_VERSION,
        "camera_count": len(clips),
        "available_cameras": cameras,
        "total_duration_sec": 0.0,
        "sampled_frames": 0,
        "multi_camera": len(clips) > 1,
        "has_video": any(bool(clip.get("exists")) for clip in clips),
        "object_detection_available": False,
        "motion_score": 0.0,
        "max_motion_score": 0.0,
        "motion_spike_time_sec": 0.0,
        "motion_spike_ratio": 0.0,
        "camera_shake_score": 0.0,
        "abrupt_scene_change": False,
        "localized_motion_score": 0.0,
        "scene_change_score": 0.0,
        "ego_vehicle_zone": "",
        "ego_zone_motion_score": 0.0,
        "visual_contact_time_sec": 0.0,
        "visual_contact_score": 0.0,
        "visual_contact_candidate": False,
        "visual_contact_reasons": [],
        "object_contact_candidate": False,
        "object_contact_time_sec": 0.0,
        "object_contact_score": 0.0,
        "object_touching_ego_vehicle": False,
        "contact_object_class": "",
        "contact_object_type": "",
        "object_contact_reasons": [],
        "strong_impact_like_motion": False,
        "possible_impact": False,
        "impact_level": "NONE",
        "impact_score": 0.0,
        "impact_candidate_score": 0.0,
        "hard_contact_candidate": False,
        "rear_impact_candidate": False,
        "no_yolo_motion_impact_candidate": False,
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
        "crash_safety_reasons": [],
        "camera_evidence": {},
        "object_tracks": [],
        "primary_camera_candidate": "",
        **fallback_moments,
        "hero_thumbnail": "",
        "contact_sheet": "",
        "evidence_warnings": [warning],
        **get_evidence_runtime_diagnostics(),
    }


def extract_evidence(event_group: dict, sample_result: dict, object_detection_enabled: bool = True) -> dict:
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
        detection_samples = _object_detection_samples(samples, motion)
        detections, object_detection_available = _detect_objects(
            detection_samples,
            object_detection_enabled=object_detection_enabled,
        )
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
        object_contact_details = _visual_object_contact_details(motion, detections)
        hard_close_contact = _hard_close_contact_like_motion(motion, close_evidence)
        if hard_close_contact:
            impact_level = _max_level(impact_level, "HIGH")
            possible_impact = True
            strong_impact_like_motion = True
            motion.setdefault("impact_evidence_reasons", [])
            motion["impact_evidence_reasons"] = list(motion.get("impact_evidence_reasons") or [])
            motion["impact_evidence_reasons"].append("hard_close_vehicle_motion")
        contact_level, contact_reasons = _contact_level_from_motion(motion, close_evidence, impact_level)
        early_vehicle_only_contact = bool(
            object_contact_details.get("contact_object_type") == "vehicle_body"
            and motion.get("visual_contact_candidate")
            and _safe_float(object_contact_details.get("object_contact_time_sec")) + 2.0 < _safe_float(motion.get("visual_contact_time_sec"))
        )
        object_contact_can_escalate = bool(
            object_contact_details.get("object_contact_candidate")
            and not early_vehicle_only_contact
            and (
                impact_level in {"MEDIUM", "HIGH"}
                or hard_close_contact
            )
        )
        object_contact_details["object_contact_used_for_contact"] = object_contact_can_escalate
        if early_vehicle_only_contact:
            object_contact_details["object_contact_reasons"] = list(object_contact_details.get("object_contact_reasons", []))
            object_contact_details["object_contact_reasons"].append("diagnostic_only_early_vehicle_body_motion")
        if object_contact_can_escalate:
            contact_reasons = list(contact_reasons)
            contact_reasons.extend(object_contact_details.get("object_contact_reasons", []))
            contact_level = _max_level(contact_level, "MEDIUM")
            if (
                object_contact_details.get("contact_object_type") in {"vehicle_door_or_body", "vehicle_body"}
                and _safe_float(object_contact_details.get("object_contact_score")) >= OBJECT_CONTACT_HIGH_SCORE
            ):
                contact_level = _max_level(contact_level, "HIGH")
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
        crash_safety_details = _no_yolo_motion_impact_details(motion, object_detection_available)
        if crash_safety_details.get("no_yolo_motion_impact_candidate"):
            impact_level = _max_level(impact_level, "HIGH")
            possible_impact = True
            strong_impact_like_motion = True
            motion.setdefault("impact_evidence_reasons", [])
            motion["impact_evidence_reasons"] = list(motion.get("impact_evidence_reasons") or [])
            motion["impact_evidence_reasons"].append("no_yolo_motion_impact_candidate")
        possible_impact = possible_impact or bool(candidate_details.get("rear_impact_candidate"))
        normal_traffic = bool((vehicle_detected or person_detected) and not possible_impact and max_motion < MOTION_MEDIUM_THRESHOLD)

        camera_evidence[camera] = {
            **motion,
            "object_detection_available": object_detection_available,
            "close_object_evidence": close_evidence,
            **object_contact_details,
            **candidate_details,
            **crash_safety_details,
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
            "object_detection_sampled_frames": len(detection_samples),
        }
        camera_evidence[camera]["evidence_score"] = _camera_score(camera_evidence[camera])

    best_camera = ""
    if camera_evidence:
        best_camera = max(camera_evidence.items(), key=lambda item: item[1].get("evidence_score", 0.0))[0]
    primary_camera_evidence = camera_evidence.get(best_camera, {}) if best_camera else {}

    max_motion_score = max((_safe_float(item.get("max_motion_score")) for item in camera_evidence.values()), default=0.0)
    motion_score = max((_safe_float(item.get("motion_score")) for item in camera_evidence.values()), default=0.0)
    localized_motion_score = max((_safe_float(item.get("localized_motion_score")) for item in camera_evidence.values()), default=0.0)
    ego_zone_motion_score = max((_safe_float(item.get("ego_zone_motion_score")) for item in camera_evidence.values()), default=0.0)
    visual_contact_score = max((_safe_float(item.get("visual_contact_score")) for item in camera_evidence.values()), default=0.0)
    object_contact_score = max((_safe_float(item.get("object_contact_score")) for item in camera_evidence.values()), default=0.0)
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
    object_contact_candidate = any(item.get("object_contact_candidate") for item in camera_evidence.values())
    object_touching_ego_vehicle = any(item.get("object_touching_ego_vehicle") for item in camera_evidence.values())
    strong_impact_like_motion = any(item.get("strong_impact_like_motion") for item in camera_evidence.values()) or impact_level == "HIGH"
    impact_candidate_score = max((_safe_float(item.get("impact_candidate_score")) for item in camera_evidence.values()), default=0.0)
    hard_contact_candidate = any(item.get("hard_contact_candidate") for item in camera_evidence.values())
    rear_impact_candidate = any(item.get("rear_impact_candidate") for item in camera_evidence.values())
    object_detection_available = any(item.get("object_detection_available") for item in camera_evidence.values())
    no_yolo_motion_impact_candidate = any(item.get("no_yolo_motion_impact_candidate") for item in camera_evidence.values())
    crash_safety_triggered = any(item.get("crash_safety_triggered") for item in camera_evidence.values())
    crash_safety_reasons = sorted(
        {
            reason
            for item in camera_evidence.values()
            for reason in item.get("crash_safety_reasons", [])
            if reason
        }
    )
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
    object_contact_reasons = sorted(
        {
            reason
            for item in camera_evidence.values()
            for reason in item.get("object_contact_reasons", [])
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
    if no_yolo_motion_impact_candidate:
        impact_evidence_reasons.append("no_yolo_motion_impact_candidate")
    if crash_safety_triggered:
        impact_evidence_reasons.append("crash_safety_triggered")
    if object_contact_candidate:
        contact_evidence_reasons.append("object_contact_candidate")
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

    key_moment_result = _build_key_moments(
        event_group,
        sample_result,
        camera_evidence,
        best_camera,
        all_tracks,
        possible_impact,
        possible_contact,
        strong_impact_like_motion,
        normal_traffic,
        crash_safety_triggered,
        no_yolo_motion_impact_candidate,
    )
    apparent_visual_contact = bool(
        object_contact_candidate
        and primary_camera_evidence.get("object_contact_used_for_contact")
        and possible_contact
        and contact_level == "HIGH"
        and primary_camera_evidence.get("contact_object_type") in {"vehicle_door_or_body", "vehicle_body"}
        and object_contact_score >= OBJECT_CONTACT_HIGH_SCORE
    )
    contact_candidate = bool(
        possible_contact
        or object_contact_candidate
        or any(item.get("visual_contact_candidate") for item in camera_evidence.values())
    )

    return {
        "evidence_version": EVIDENCE_VERSION,
        "impact_detection_version": IMPACT_DETECTION_VERSION,
        "key_moment_version": KEY_MOMENT_VERSION,
        "camera_count": len(clips),
        "available_cameras": cameras,
        "total_duration_sec": round(duration, 3),
        "sampled_frames": int(sample_result.get("sampled_frames") or 0),
        "multi_camera": len(clips) > 1,
        "has_video": any(bool(clip.get("exists")) for clip in clips),
        "object_detection_available": object_detection_available,
        "motion_score": round(motion_score, 4),
        "max_motion_score": round(max_motion_score, 4),
        "localized_motion_score": round(localized_motion_score, 4),
        "ego_vehicle_zone": primary_camera_evidence.get("ego_vehicle_zone", ""),
        "ego_zone_motion_score": round(ego_zone_motion_score, 4),
        "visual_contact_time_sec": primary_camera_evidence.get("visual_contact_time_sec", 0.0),
        "visual_contact_score": round(visual_contact_score, 4),
        "visual_contact_candidate": any(item.get("visual_contact_candidate") for item in camera_evidence.values()),
        "visual_contact_reasons": sorted(
            {
                reason
                for item in camera_evidence.values()
                for reason in item.get("visual_contact_reasons", [])
                if reason
            }
        ),
        "object_contact_candidate": object_contact_candidate,
        "object_contact_used_for_contact": any(item.get("object_contact_used_for_contact") for item in camera_evidence.values()),
        "object_contact_time_sec": primary_camera_evidence.get("object_contact_time_sec", 0.0),
        "object_contact_score": round(object_contact_score, 4),
        "object_touching_ego_vehicle": object_touching_ego_vehicle,
        "contact_object_class": primary_camera_evidence.get("contact_object_class", ""),
        "contact_object_type": primary_camera_evidence.get("contact_object_type", ""),
        "object_contact_reasons": object_contact_reasons,
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
        "no_yolo_motion_impact_candidate": no_yolo_motion_impact_candidate,
        "impact_evidence_reasons": impact_evidence_reasons,
        "impact_candidate_reasons": impact_candidate_reasons,
        "strong_impact_like_motion": strong_impact_like_motion,
        "possible_contact": possible_contact,
        "contact_candidate": contact_candidate,
        "contact_verified": False,
        "apparent_visual_contact": apparent_visual_contact,
        "uncertain_close_activity": bool(contact_candidate and not apparent_visual_contact),
        "confidence_provenance": {
            "motion": "local_frame_difference",
            "objects": "local_object_detector" if object_detection_available else "unavailable",
            "contact": "apparent_image_space_evidence",
            "physical_contact_proven": False,
        },
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
        "visible_contact": apparent_visual_contact,
        "visible_impact": False,
        "person_interaction_evidence": False,
        "tampering_evidence": False,
        "door_handle_attempt": False,
        "crash_safety_triggered": crash_safety_triggered,
        "crash_safety_reasons": crash_safety_reasons,
        "camera_evidence": camera_evidence,
        "object_tracks": all_tracks,
        "primary_camera_candidate": best_camera,
        **key_moment_result,
        "hero_thumbnail": "",
        "contact_sheet": "",
        "evidence_warnings": [],
        **get_evidence_runtime_diagnostics(),
    }
