"""Frame-to-frame motion signal extraction for Core v2.

Threshold ownership remains with the evidence policy layer. This module only
computes the same deterministic signal series from supplied helpers/config.
"""

from __future__ import annotations

from typing import Any, Callable


def analyze_motion(
    samples: list[dict],
    *,
    cv2: Any,
    empty_metrics: Callable[[], dict],
    safe_float: Callable[[object, float], float],
    diff_tile_scores: Callable[[Any], list[float]],
    ego_vehicle_zone_motion: Callable[[Any], tuple[str, float, dict[str, float]]],
    motion_regions_from_diff: Callable[[Any], list[dict]],
    spike_ratio_for_scores: Callable[[list[float], float], float],
    first_visual_contact_sample: Callable[[list[dict], float, float], dict],
    thresholds: dict[str, float],
) -> dict:
    if cv2 is None or len(samples) < 2:
        return empty_metrics()

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
            tile_scores = diff_tile_scores(diff)
            localized_score = max(tile_scores, default=global_score)
            ego_zone, ego_zone_score, ego_zone_scores = ego_vehicle_zone_motion(diff)
            motion_regions = motion_regions_from_diff(diff)
            widespread_threshold = max(0.12, global_score * 0.7)
            widespread_fraction = (
                sum(1 for score in tile_scores if score >= widespread_threshold) / len(tile_scores)
                if tile_scores
                else 0.0
            )
            camera_shake_score = global_score * widespread_fraction
            motion_scores.append(
                (
                    safe_float(sample.get("time_sec"), 0.0),
                    round(global_score, 4),
                    round(localized_score, 4),
                    round(camera_shake_score, 4),
                )
            )
            motion_sample = {
                "camera": sample.get("camera", "unknown"),
                "source_video": sample.get("source_video", ""),
                "frame_index": int(safe_float(sample.get("frame_index"), 0.0)),
                "fps": round(safe_float(sample.get("fps"), 0.0), 3),
                "time_sec": round(safe_float(sample.get("time_sec"), 0.0), 3),
                "duration_sec": round(safe_float(sample.get("duration_sec"), 0.0), 3),
                "motion_score": round(global_score, 4),
                "localized_motion_score": round(localized_score, 4),
                "ego_vehicle_zone": ego_zone,
                "ego_zone_motion_score": round(ego_zone_score, 4),
                "ego_zone_scores": ego_zone_scores,
                "visual_contact_score": round(
                    max(ego_zone_score, localized_score) * 0.65
                    + global_score * 0.25
                    + camera_shake_score * 0.10,
                    4,
                ),
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
        return empty_metrics()

    average = sum(score for _, score, _, _ in motion_scores) / len(motion_scores)
    spike_time, max_score, localized_at_spike, _ = max(motion_scores, key=lambda item: item[1])
    max_localized = max(score for _, _, score, _ in motion_scores)
    max_ego_motion = max(
        (safe_float(sample.get("ego_zone_motion_score"), 0.0) for sample in motion_samples),
        default=0.0,
    )
    max_camera_shake = max(score for _, _, _, score in motion_scores)
    spike_ratio = spike_ratio_for_scores([score for _, score, _, _ in motion_scores], max_score)
    duration_sec = max(
        (safe_float(sample.get("duration_sec"), 0.0) for sample in motion_samples),
        default=0.0,
    )
    visual_contact = first_visual_contact_sample(motion_samples, max_localized, duration_sec)
    abrupt_scene_change = bool(
        max_score >= thresholds["motion_medium"]
        and (
            max_camera_shake >= thresholds["camera_shake_medium"]
            or spike_ratio >= thresholds["motion_spike_medium_ratio"]
        )
    )

    reasons: list[str] = []
    if spike_ratio >= thresholds["motion_spike_medium_ratio"] and max_score >= thresholds["motion_low"]:
        reasons.append(f"motion_spike_ratio={spike_ratio:g}")
    if max_camera_shake >= thresholds["camera_shake_medium"]:
        reasons.append(f"camera_shake_score={max_camera_shake:.4f}")
    if abrupt_scene_change:
        reasons.append("abrupt_scene_change")
    if max_localized >= thresholds["localized_contact"]:
        reasons.append(f"localized_motion_score={max_localized:.4f}")
    visual_reasons: list[str] = []
    visual_contact_score = safe_float(visual_contact.get("visual_contact_score"), 0.0) if visual_contact else 0.0
    visual_contact_candidate = bool(visual_contact) and visual_contact_score >= thresholds["visual_contact_min_score"]
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
        "visual_contact_time_sec": round(safe_float(visual_contact.get("time_sec"), 0.0), 3) if visual_contact else 0.0,
        "visual_contact_score": round(visual_contact_score, 4),
        "visual_contact_candidate": visual_contact_candidate,
        "visual_contact_frame_index": int(safe_float(visual_contact.get("frame_index"), 0.0)) if visual_contact else 0,
        "visual_contact_fps": round(safe_float(visual_contact.get("fps"), 0.0), 3) if visual_contact else 0.0,
        "visual_contact_reasons": visual_reasons,
        "motion_spike_time_sec": round(spike_time, 3),
        "motion_spike_ratio": spike_ratio,
        "camera_shake_score": round(max_camera_shake, 4),
        "abrupt_scene_change": abrupt_scene_change,
        "scene_change_score": round(max_score, 4),
        "impact_evidence_reasons": reasons,
        "motion_samples": motion_samples,
    }
