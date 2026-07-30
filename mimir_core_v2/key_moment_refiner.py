"""Dense, stabilized second-pass refinement for incident key moments."""

from __future__ import annotations

import hashlib
import json
import statistics
from pathlib import Path
from typing import Any

from .ego_vehicle import EGO_MASK_VERSION, load_calibration, mask_for_frame
from .detector_cache import shared_cache
from .video_decode import read_frames_at_indexes


REFINEMENT_VERSION = "two_pass_contact_timing_v4_time_normalised"
REFINEMENT_SETTINGS = {
    "fast": {"fps": 6.0, "window": 2.5, "max_frames": 24, "coarse_fps": 4.0, "coarse_window": 5.0, "coarse_max_frames": 30},
    "balanced": {"fps": 12.0, "window": 3.0, "max_frames": 42, "coarse_fps": 6.0, "coarse_window": 6.0, "coarse_max_frames": 42},
    "thorough": {"fps": 15.0, "window": 3.5, "max_frames": 60, "coarse_fps": 8.0, "coarse_window": 7.0, "coarse_max_frames": 64},
}
_ANALYSIS_CACHE = shared_cache()

# This pass differences neighbouring frames inside a short window, so unlike the
# coarse pass it *wants* fine intervals -- sub-second localisation is its whole
# job. But an absdiff's magnitude scales with the gap between the two frames,
# and that gap is mode-dependent: at 36 fps native the effective interval is
# ~0.167s at fast, ~0.083s at balanced, ~0.056s at thorough. The /55.0 divisors
# and the absolute floors below (threshold 12.0, confidence 0.18/0.42) were
# calibrated for the fast interval, so at thorough every signal came out ~3x
# smaller, the 12.0 floor bound, and contact detection was suppressed.
#
# Scores are therefore normalised to change-per-REFERENCE_INTERVAL using each
# frame pair's *actual* elapsed time. Two benefits: modes become comparable, and
# the uneven intervals left by frame decimation stop producing phantom spikes,
# because a double-length gap is divided by a double-length delta.
MOTION_REFERENCE_INTERVAL_SEC = 1.0 / 6.0
MOTION_SCALE_MAX = 4.0


def signal_time_scale(delta_sec: float) -> float:
    """Factor bringing a per-interval diff onto the reference interval."""

    if delta_sec <= 0.0:
        return 1.0
    return min(MOTION_REFERENCE_INTERVAL_SEC / delta_sec, MOTION_SCALE_MAX)


def _calibration_checksum(calibration_path: str | Path | None) -> str:
    if not calibration_path:
        return ""
    path = Path(calibration_path)
    if not path.is_file():
        return "missing"
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return "unreadable"


def _refinement_cache_key(
    source_video: str,
    camera: str,
    coarse_time: float,
    coarse_source: str,
    mode: str,
    contact_expected: bool,
    articulated_contact_expected: bool,
    calibration_path: str | Path | None,
) -> str:
    source_sha256 = _ANALYSIS_CACHE.source_sha256(source_video)
    if not source_sha256:
        return ""
    payload = {
        "kind": "key_moment_refinement",
        "version": REFINEMENT_VERSION,
        "ego_mask_version": EGO_MASK_VERSION,
        "source_sha256": source_sha256,
        "camera": camera,
        "coarse_time_sec": round(coarse_time, 3),
        "coarse_source": coarse_source,
        "mode": mode,
        "contact_expected": contact_expected,
        "articulated_contact_expected": articulated_contact_expected,
        "calibration_sha256": _calibration_checksum(calibration_path),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _safe_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _load_cv2() -> Any:
    try:
        import cv2  # type: ignore

        return cv2
    except Exception:
        return None


def _primary_camera_evidence(evidence: dict) -> tuple[str, dict]:
    camera = str(evidence.get("primary_camera") or "unknown")
    camera_evidence = evidence.get("camera_evidence")
    if isinstance(camera_evidence, dict):
        selected = camera_evidence.get(camera)
        if isinstance(selected, dict):
            return camera, selected
        for key, value in camera_evidence.items():
            if isinstance(value, dict):
                return str(key), value
    return camera, evidence


def _source_video(event_group: dict, camera: str, evidence: dict) -> str:
    for clip in event_group.get("clips", []):
        if isinstance(clip, dict) and str(clip.get("camera") or "unknown") == camera:
            return str(clip.get("path") or "")
    for clip in event_group.get("clips", []):
        if isinstance(clip, dict) and clip.get("path"):
            return str(clip["path"])
    return str(evidence.get("source_video") or "")


def _candidate_time(evidence: dict, primary_camera_evidence: dict) -> tuple[float, str]:
    primary_time = _safe_float(evidence.get("primary_key_moment_sec"), -1.0)
    if primary_time > 0.0:
        return primary_time, "primary_key_moment"
    candidates = (
        (primary_camera_evidence.get("object_contact_time_sec"), "object_contact"),
        (primary_camera_evidence.get("contact_track_time_sec"), "contact_track"),
        (primary_camera_evidence.get("visual_contact_time_sec"), "ego_zone_motion"),
        (primary_camera_evidence.get("motion_spike_time_sec"), "motion_spike"),
    )
    for value, source in candidates:
        time_sec = _safe_float(value, -1.0)
        if time_sec > 0.0:
            return time_sec, source
    return 0.0, "fallback"


def _read_frames(path: Path, center_sec: float, settings: dict, coarse_pass: bool = False) -> tuple[list[dict], dict]:
    cv2 = _load_cv2()
    if cv2 is None or not path.exists():
        return [], {}
    capture = cv2.VideoCapture(str(path))
    try:
        if not capture.isOpened():
            return [], {}
        native_fps = _safe_float(capture.get(cv2.CAP_PROP_FPS))
        frame_count = int(_safe_float(capture.get(cv2.CAP_PROP_FRAME_COUNT)))
        if native_fps <= 0 or frame_count <= 0:
            return [], {}
        duration = frame_count / native_fps
        window = _safe_float(settings.get("coarse_window") if coarse_pass else settings.get("window"), 4.0)
        start_sec = max(0.0, center_sec - window / 2.0)
        end_sec = min(duration, center_sec + window / 2.0)
        requested_fps = settings.get("coarse_fps") if coarse_pass else settings.get("fps")
        target_fps = min(native_fps, _safe_float(requested_fps, 12.0))
        step = max(1, int(round(native_fps / max(target_fps, 1.0))))
        first_index = max(0, int(round(start_sec * native_fps)))
        last_index = min(frame_count - 1, int(round(end_sec * native_fps)))
        indexes = list(range(first_index, last_index + 1, step))
        max_frames = int(settings.get("coarse_max_frames") if coarse_pass else settings.get("max_frames") or 54)
        if len(indexes) > max_frames:
            # Evenly spaced pick. `int(index * stride)` truncation used to drop
            # scattered indexes, leaving occasional double-length gaps; since an
            # absdiff scales with the gap, those seams produced motion spikes
            # unrelated to the scene and could win the impulse argmax.
            if max_frames == 1:
                indexes = [indexes[0]]
            else:
                last = len(indexes) - 1
                indexes = [
                    indexes[round(position * last / (max_frames - 1))]
                    for position in range(max_frames)
                ]
                indexes = sorted(dict.fromkeys(indexes))

        frames: list[dict] = []
        for frame_index, frame in read_frames_at_indexes(capture, indexes, cv2):
            height, width = frame.shape[:2]
            target_width = min(width, 480)
            target_height = max(1, int(height * target_width / max(width, 1)))
            if target_width != width:
                frame = cv2.resize(frame, (target_width, target_height))
            frames.append(
                {
                    "frame": frame,
                    "frame_index": frame_index,
                    "time_sec": frame_index / native_fps,
                }
            )
        return frames, {
            "native_fps": native_fps,
            "duration_sec": duration,
            "start_sec": start_sec,
            "end_sec": end_sec,
            "sample_fps": target_fps,
        }
    finally:
        capture.release()


def _dense_signals(frames: list[dict], camera: str, calibration: dict) -> tuple[list[dict], str]:
    cv2 = _load_cv2()
    if cv2 is None or len(frames) < 3:
        return [], ""
    import numpy as np  # type: ignore

    previous_gray = None
    previous_signal_time: float | None = None
    previous_contact_distance = 1.0
    signals: list[dict] = []
    mask_source = ""
    for item in frames:
        frame = item["frame"]
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (5, 5), 0)
        current_signal_time = _safe_float(item.get("time_sec"))
        if previous_gray is None:
            previous_gray = gray
            previous_signal_time = current_signal_time
            continue
        # Normalise this pair's diff onto the reference interval so absolute
        # thresholds mean the same thing at every sampling rate.
        time_scale = signal_time_scale(
            current_signal_time - previous_signal_time if previous_signal_time is not None else 0.0
        )
        try:
            shift, response = cv2.phaseCorrelate(
                previous_gray.astype("float32"),
                gray.astype("float32"),
            )
        except Exception:
            shift, response = (0.0, 0.0), 0.0
        transform = __import__("numpy").float32([[1, 0, shift[0]], [0, 1, shift[1]]])
        aligned_previous = cv2.warpAffine(
            previous_gray,
            transform,
            (gray.shape[1], gray.shape[0]),
            flags=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_REFLECT,
        )
        diff = cv2.absdiff(aligned_previous, gray)
        ego_mask, mask_source = mask_for_frame(cv2, gray.shape[:2], camera, calibration)
        dilated_mask = cv2.dilate(ego_mask, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (17, 17)))
        boundary_mask = cv2.subtract(dilated_mask, ego_mask)
        diff_mean = float(diff.mean())
        diff_std = float(diff.std())
        threshold_value = max(12.0, min(48.0, diff_mean + diff_std * 1.25))
        _, moving_mask = cv2.threshold(diff, threshold_value, 255, cv2.THRESH_BINARY)
        moving_mask = cv2.morphologyEx(moving_mask, cv2.MORPH_OPEN, np.ones((3, 3), dtype="uint8"))
        moving_mask = cv2.morphologyEx(moving_mask, cv2.MORPH_CLOSE, np.ones((5, 5), dtype="uint8"))
        contact_band = cv2.subtract(
            cv2.dilate(ego_mask, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (21, 21))),
            cv2.erode(ego_mask, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (11, 11))),
        )
        distance_to_ego = cv2.distanceTransform(cv2.bitwise_not(ego_mask), cv2.DIST_L2, 3)
        component_score = 0.0
        component_distance = 1.0
        component_area_ratio = 0.0
        components, component_labels, stats, _ = cv2.connectedComponentsWithStats(moving_mask, 8)
        frame_area = max(1, gray.shape[0] * gray.shape[1])
        for component_index in range(1, components):
            area = int(stats[component_index, cv2.CC_STAT_AREA])
            if area < max(18, int(frame_area * 0.00015)):
                continue
            component = (component_labels == component_index).astype("uint8") * 255
            band_overlap = int(cv2.countNonZero(cv2.bitwise_and(component, contact_band)))
            pixels = distance_to_ego[component > 0]
            if pixels.size == 0:
                continue
            minimum_distance = float(pixels.min()) / max(gray.shape[:2])
            overlap_ratio = band_overlap / max(area, 1)
            area_ratio = area / frame_area
            proximity = max(0.0, 1.0 - minimum_distance / 0.08)
            score = min(area_ratio / 0.02, 1.0) * 0.30 + min(overlap_ratio / 0.25, 1.0) * 0.45 + proximity * 0.25
            if score > component_score:
                component_score = score
                component_distance = minimum_distance
                component_area_ratio = area_ratio
        # time_scale keeps these comparable across scan modes and across the
        # uneven gaps left by frame decimation. Camera shift is a per-interval
        # pixel displacement, so it scales the same way.
        global_motion = min(float(diff.mean()) / 55.0 * time_scale, 1.0)
        ego_motion = min(float(cv2.mean(diff, mask=ego_mask)[0]) / 55.0 * time_scale, 1.0)
        boundary_motion = min(float(cv2.mean(diff, mask=boundary_mask)[0]) / 55.0 * time_scale, 1.0)
        shake = min(((shift[0] ** 2 + shift[1] ** 2) ** 0.5) / 12.0 * time_scale, 1.0)
        approach = max(0.0, previous_contact_distance - component_distance)
        signal = boundary_motion * 0.36 + ego_motion * 0.20 + global_motion * 0.12 + shake * 0.10 + component_score * 0.22
        signals.append(
            {
                "time_sec": _safe_float(item.get("time_sec")),
                "frame_index": int(item.get("frame_index") or 0),
                "global_motion": global_motion,
                "ego_motion": ego_motion,
                "boundary_motion": boundary_motion,
                "camera_shift": round(shake, 5),
                "alignment_response": round(_safe_float(response), 5),
                "apparent_contact_score": round(component_score, 5),
                "contact_distance_ratio": round(component_distance, 5),
                "approach_delta": round(approach, 5),
                "contact_component_area_ratio": round(component_area_ratio, 6),
                "signal": signal,
            }
        )
        previous_contact_distance = component_distance
        previous_gray = gray
        previous_signal_time = current_signal_time
    return signals, mask_source


def _select_impulse(signals: list[dict], coarse_time: float, contact_expected: bool = False) -> dict:
    if not signals:
        return {}
    values = [_safe_float(item.get("signal")) for item in signals]
    baseline = statistics.median(values)
    deviations = [abs(value - baseline) for value in values]
    mad = max(statistics.median(deviations), 0.01)
    best: dict = {}
    previous_values: list[tuple[float, float]] = []
    previous_contacts: list[tuple[float, float]] = []
    previous_shakes: list[tuple[float, float]] = []
    contact_streak_sec = 0.0
    previous_time = 0.0
    # Lookbacks are expressed in seconds, not sample counts. `[-4:]` / `[-6:]`
    # meant 0.67s / 1.0s of history at fast but only 0.22s / 0.33s at thorough,
    # so `impulse` was measured against a different real-world baseline per mode.
    baseline_lookback_sec = 0.67
    shake_lookback_sec = 1.0

    def recent(history: list[tuple[float, float]], now: float, span_sec: float) -> list[float]:
        return [value for stamp, value in history if now - stamp <= span_sec] or [
            value for _, value in history[-1:]
        ]

    for index, item in enumerate(signals):
        value = values[index]
        time_sec = _safe_float(item.get("time_sec"))
        recent_values = recent(previous_values, time_sec, baseline_lookback_sec)
        previous_baseline = statistics.median(recent_values) if recent_values else baseline
        impulse = max(0.0, value - previous_baseline)
        robust_z = max(0.0, (value - baseline) / (1.4826 * mad))
        proximity = max(0.0, 1.0 - abs(_safe_float(item.get("time_sec")) - coarse_time) / 5.0)
        apparent_contact = _safe_float(item.get("apparent_contact_score"))
        approach = min(_safe_float(item.get("approach_delta")) / 0.04, 1.0)
        camera_shift = _safe_float(item.get("camera_shift"))
        recent_contacts = recent(previous_contacts, time_sec, baseline_lookback_sec)
        previous_contact = statistics.median(recent_contacts) if recent_contacts else 0.0
        recent_shakes = recent(previous_shakes, time_sec, shake_lookback_sec)
        previous_shake = statistics.median(recent_shakes) if recent_shakes else 0.0
        delta_sec = max(0.0, time_sec - previous_time) if previous_time > 0.0 else 0.0
        mature_contact = min(contact_streak_sec / 0.6, 1.0)
        contact_drop = max(0.0, previous_contact - apparent_contact)
        motion_drop = max(0.0, previous_baseline - value)
        shake_jump = max(0.0, camera_shift - previous_shake)
        terminal_contact = mature_contact * min(
            1.0,
            contact_drop * 0.85 + motion_drop * 2.5 + min(shake_jump / 0.05, 1.0) * 0.50,
        )
        if contact_expected:
            score = (
                impulse * 0.24
                + min(robust_z / 8.0, 1.0) * 0.10
                + value * 0.08
                + apparent_contact * 0.21
                + approach * 0.03
                + proximity * 0.12
                + terminal_contact * 0.42
            )
        else:
            score = impulse * 0.55 + min(robust_z / 8.0, 1.0) * 0.25 + value * 0.15 + proximity * 0.05
        candidate = dict(item)
        candidate["impulse"] = round(impulse, 5)
        candidate["robust_z"] = round(robust_z, 3)
        candidate["selection_score"] = round(score, 5)
        candidate["terminal_contact_score"] = round(terminal_contact, 5)
        if not best or score > _safe_float(best.get("selection_score")):
            best = candidate
        previous_values.append((time_sec, value))
        previous_contacts.append((time_sec, apparent_contact))
        previous_shakes.append((time_sec, camera_shift))
        if apparent_contact >= 0.55:
            contact_streak_sec += delta_sec
        else:
            contact_streak_sec = 0.0
        previous_time = time_sec
    best["baseline_signal"] = round(baseline, 5)
    confidence = min(
        1.0,
        _safe_float(best.get("robust_z")) / 6.0 * 0.55
        + _safe_float(best.get("impulse")) / 0.25 * 0.35
        + _safe_float(best.get("alignment_response")) * 0.10,
    )
    if contact_expected:
        confidence = max(confidence, _safe_float(best.get("terminal_contact_score")) * 0.75)
    best["confidence"] = round(max(0.0, confidence), 3)
    return best


def refine_key_moment(
    event_group: dict,
    evidence: dict,
    mode: str = "balanced",
    calibration_path: str | Path | None = None,
) -> dict:
    camera, primary = _primary_camera_evidence(evidence)
    coarse_time, coarse_source = _candidate_time(evidence, primary)
    source_video = _source_video(event_group, camera, primary)
    result = {
        "version": REFINEMENT_VERSION,
        "attempted": False,
        "refined": False,
        "camera": camera,
        "source_video": source_video,
        "coarse_time_sec": round(coarse_time, 3),
        "coarse_source": coarse_source,
        "refined_time_sec": round(coarse_time, 3),
        "confidence": 0.0,
        "timing_uncertain": True,
        "ego_mask_version": EGO_MASK_VERSION,
        "ego_mask_source": "",
        "reason": "No readable dense refinement window.",
    }
    if not source_video:
        return result
    settings = REFINEMENT_SETTINGS.get(mode, REFINEMENT_SETTINGS["balanced"])
    contact_expected = bool(
        evidence.get("possible_contact")
        or evidence.get("possible_impact")
        or evidence.get("strong_impact_like_motion")
        or str(evidence.get("contact_level") or "").upper() in {"MEDIUM", "HIGH"}
        or str(evidence.get("impact_level") or "").upper() in {"MEDIUM", "HIGH"}
    )
    articulated_contact_expected = str(
        primary.get("contact_object_type") or evidence.get("contact_object_type") or ""
    ) == "vehicle_door_or_body"
    cache_key = _refinement_cache_key(
        source_video,
        camera,
        coarse_time,
        coarse_source,
        mode,
        contact_expected,
        articulated_contact_expected,
        calibration_path,
    )
    cached = _ANALYSIS_CACHE.get_metric(cache_key) if cache_key else None
    if cached is not None:
        cached["source_video"] = source_video
        return cached

    def finish(value: dict) -> dict:
        if cache_key:
            payload = dict(value)
            payload.pop("source_video", None)
            _ANALYSIS_CACHE.put_metric(cache_key, payload)
        return value

    coarse_metadata: dict = {}
    coarse_selected: dict = {}
    if contact_expected:
        coarse_frames, coarse_metadata = _read_frames(Path(source_video), coarse_time, settings, coarse_pass=True)
        if len(coarse_frames) >= 3:
            coarse_signals, _ = _dense_signals(coarse_frames, camera, load_calibration(calibration_path))
            coarse_selected = _select_impulse(
                coarse_signals,
                coarse_time,
                contact_expected=articulated_contact_expected,
            )
            if coarse_selected:
                coarse_time = _safe_float(coarse_selected.get("time_sec"), coarse_time)
                result["candidate_interval_time_sec"] = round(coarse_time, 3)
                result["candidate_interval_score"] = coarse_selected.get("selection_score", 0.0)
                result["candidate_interval_sample_fps"] = coarse_metadata.get("sample_fps", 0.0)
    frames, metadata = _read_frames(Path(source_video), coarse_time, settings)
    if len(frames) < 3:
        return finish(result)
    result["attempted"] = True
    signals, mask_source = _dense_signals(frames, camera, load_calibration(calibration_path))
    selected = _select_impulse(
        signals,
        coarse_time,
        contact_expected=articulated_contact_expected,
    )
    if not selected:
        return finish(result)
    anchor_guard_applied = False
    if (
        articulated_contact_expected
        and coarse_selected
        and abs(_safe_float(selected.get("time_sec")) - coarse_time) > 0.75
        and _safe_float(selected.get("terminal_contact_score")) < 0.35
    ):
        nearest = min(signals, key=lambda item: abs(_safe_float(item.get("time_sec")) - coarse_time))
        anchored = dict(nearest)
        anchored["confidence"] = round(
            max(0.18, min(_safe_float(selected.get("confidence")), _safe_float(coarse_selected.get("confidence"), 0.55))),
            3,
        )
        anchored["selection_score"] = coarse_selected.get("selection_score", 0.0)
        anchored["impulse"] = coarse_selected.get("impulse", 0.0)
        anchored["robust_z"] = coarse_selected.get("robust_z", 0.0)
        selected = anchored
        anchor_guard_applied = True
    confidence = _safe_float(selected.get("confidence"))
    refined_time = _safe_float(selected.get("time_sec"), coarse_time)
    result.update(
        {
            "refined": confidence >= 0.18,
            "refined_time_sec": round(refined_time, 3),
            "frame_index": int(selected.get("frame_index") or 0),
            "native_fps": round(_safe_float(metadata.get("native_fps")), 3),
            "sample_fps": round(_safe_float(metadata.get("sample_fps")), 3),
            "duration_sec": round(_safe_float(metadata.get("duration_sec")), 3),
            "window_start_sec": round(_safe_float(metadata.get("start_sec")), 3),
            "window_end_sec": round(_safe_float(metadata.get("end_sec")), 3),
            "confidence": round(confidence, 3),
            "timing_uncertain": confidence < 0.42,
            "ego_mask_source": mask_source,
            "global_motion": round(_safe_float(selected.get("global_motion")), 5),
            "ego_motion": round(_safe_float(selected.get("ego_motion")), 5),
            "boundary_motion": round(_safe_float(selected.get("boundary_motion")), 5),
            "camera_shift": round(_safe_float(selected.get("camera_shift")), 5),
            "impulse": round(_safe_float(selected.get("impulse")), 5),
            "apparent_contact_score": round(_safe_float(selected.get("apparent_contact_score")), 5),
            "terminal_contact_score": round(_safe_float(selected.get("terminal_contact_score")), 5),
            "contact_distance_ratio": round(_safe_float(selected.get("contact_distance_ratio")), 5),
            "contact_onset_anchor_guard": anchor_guard_applied,
            "reason": "Dense stabilized apparent-contact candidate." if contact_expected else "Dense stabilized ego-boundary motion impulse.",
        }
    )
    return finish(result)


def apply_refined_key_moment(evidence: dict, refinement: dict) -> None:
    evidence["key_moment_refinement"] = refinement
    evidence["key_moment_refinement_version"] = REFINEMENT_VERSION
    evidence["contact_timing_confidence"] = refinement.get("confidence", 0.0)
    evidence["contact_timing_uncertain"] = bool(refinement.get("timing_uncertain", True))
    evidence["ego_vehicle_mask_source"] = refinement.get("ego_mask_source", "")
    coarse_time = _safe_float(refinement.get("coarse_time_sec"), 0.0)
    context_time = _safe_float(refinement.get("refined_time_sec"), coarse_time)
    duration = _safe_float(refinement.get("duration_sec"), 0.0)
    evidence["primary_key_moment_context"] = {
        "before_sec": round(max(0.0, context_time - 2.0), 3),
        "moment_sec": round(max(0.0, context_time), 3),
        "after_sec": round(min(duration, context_time + 2.0), 3) if duration > 0 else round(context_time + 2.0, 3),
        "timing_uncertain": bool(refinement.get("timing_uncertain", True)),
    }
    if not refinement.get("refined"):
        return
    refined_time = _safe_float(refinement.get("refined_time_sec"), -1.0)
    if refined_time < 0.0:
        return
    moments = evidence.get("key_moments")
    if not isinstance(moments, list):
        return
    primary_label = str(evidence.get("primary_key_moment_label") or "")
    primary_time = _safe_float(evidence.get("primary_key_moment_sec"), -1.0)
    target: dict | None = None
    for moment in moments:
        if not isinstance(moment, dict):
            continue
        if str(moment.get("label") or "") == primary_label and abs(_safe_float(moment.get("time_sec")) - primary_time) < 0.01:
            target = moment
            break
    if target is None:
        for moment in moments:
            if isinstance(moment, dict) and str(moment.get("type") or "") in {"impact_contact", "motion_spike"}:
                target = moment
                break
    if target is None:
        return
    target["coarse_time_sec"] = target.get("time_sec")
    target["time_sec"] = round(refined_time, 3)
    target["frame_index"] = refinement.get("frame_index")
    target["fps"] = refinement.get("native_fps")
    target["confidence"] = refinement.get("confidence")
    target["source"] = "local_motion_refined"
    target["reason"] = refinement.get("reason")
    target["timing_uncertain"] = bool(refinement.get("timing_uncertain"))
    evidence["primary_key_moment_sec"] = round(refined_time, 3)
    moments.sort(key=lambda item: _safe_float(item.get("time_sec")) if isinstance(item, dict) else 0.0)
    deduplicated: list[dict] = []
    for moment in moments:
        if not isinstance(moment, dict):
            continue
        if deduplicated and abs(_safe_float(moment.get("time_sec")) - _safe_float(deduplicated[-1].get("time_sec"))) < 1.0:
            if moment is target:
                deduplicated[-1] = moment
            continue
        deduplicated.append(moment)
    evidence["key_moments"] = deduplicated
