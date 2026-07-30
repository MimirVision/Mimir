"""Frame-to-frame motion signal extraction for Core v2.

Threshold ownership remains with the evidence policy layer. This module only
computes the same deterministic signal series from supplied helpers/config.
"""

from __future__ import annotations

from typing import Any, Callable


# Motion is an absdiff between two frames, so its magnitude depends entirely on
# how much time separates them. Comparing each frame to the immediately
# preceding *sampled* frame therefore made a score mean "change per sampling
# interval" -- a different quantity in every scan mode. A sharp impact filled
# one 1.0s delta at 1 fps and spiked hard, but was split across four small
# 0.25s deltas at 4 fps, flattening into the background until an unrelated
# later event won candidate selection. Denser sampling produced worse impact
# timing than sparse sampling.
#
# Each frame is now differenced against the buffered frame closest to
# BASELINE_SEC earlier, instead of its immediate predecessor. The measured
# interval is then ~1.0s regardless of mode, so:
#   - 1 fps is unchanged (its predecessor already is the 1.0s-old frame),
#   - 2/4 fps measure the same 1.0s change as 1 fps but evaluate it 2x/4x more
#     often, giving strictly finer localisation of the peak at equal magnitude.
# No scaling constant is needed; the signal is made comparable by construction.
MOTION_BASELINE_SEC = 1.0

# A reference frame must be at least this fraction of the baseline old to be
# used, so the opening moments of a clip do not emit weak, short-interval
# deltas that would not be comparable to the rest.
MOTION_BASELINE_MIN_RATIO = 0.75

# How much history to retain, as a multiple of the baseline.
MOTION_HISTORY_SPAN = 2.0


def _selection_grid(
    motion_samples: list[dict],
    safe_float: Callable[[object, float], float],
    interval_sec: float = MOTION_BASELINE_SEC,
) -> list[dict]:
    """Thin motion samples to a fixed ~``interval_sec`` grid.

    Candidate selection is what actually decides *which* event becomes the
    incident, and it is the part the 1 fps `fast` mode gets right: clusters are
    judged on a coarse, evenly spaced grid. Denser modes previously selected on
    every sampled frame, so they surfaced extra short-lived clusters that 1 fps
    never saw and drifted onto different events.

    Selecting on this grid makes every mode agree with `fast` by construction,
    while the finer samples are still used for object detection and for key
    moment refinement -- so denser modes keep their advantages (better recall,
    better sub-second localisation) without changing which event is chosen.
    """

    grid: list[dict] = []
    last_time: float | None = None
    for sample in motion_samples:
        time_sec = safe_float(sample.get("time_sec"), 0.0)
        if last_time is None or time_sec - last_time >= interval_sec - 1e-6:
            grid.append(sample)
            last_time = time_sec
    return grid


def _baseline_reference(history: list[tuple[float, Any]], current_time: float) -> tuple[float, Any] | None:
    """Buffered frame closest to one baseline before ``current_time``."""

    if not history:
        return None
    target = current_time - MOTION_BASELINE_SEC
    reference = min(history, key=lambda item: abs(item[0] - target))
    if current_time - reference[0] < MOTION_BASELINE_SEC * MOTION_BASELINE_MIN_RATIO:
        return None
    return reference


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

    # (time_sec, gray) for recent frames, so each frame can be differenced
    # against one a fixed ~1.0s earlier rather than its immediate predecessor.
    history: list[tuple[float, Any]] = []
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
        current_time = safe_float(sample.get("time_sec"), 0.0)
        reference = _baseline_reference(history, current_time)
        if reference is not None:
            diff = cv2.absdiff(reference[1], gray)
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
        history.append((current_time, gray))
        span = MOTION_BASELINE_SEC * MOTION_HISTORY_SPAN
        history = [entry for entry in history if current_time - entry[0] <= span]

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
    # Choose the candidate on the fixed grid so every mode selects the same
    # event as fast; the dense samples above still drive object detection and
    # downstream key-moment refinement.
    selection_samples = _selection_grid(motion_samples, safe_float)
    selection_max_localized = max(
        (safe_float(sample.get("localized_motion_score"), 0.0) for sample in selection_samples),
        default=max_localized,
    )
    visual_contact = first_visual_contact_sample(selection_samples, selection_max_localized, duration_sec)
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
