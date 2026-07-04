id="hz27zy"
import os
import cv2
import shutil
import time
import base64
import csv
import json
import requests
import argparse
import math
import re
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from ultralytics import YOLO
from config import (
    FRAMES,
    IGNORE,
    IMPORTANT,
    INCIDENTS_OUTPUT,
    INCOMING,
    LATEST_SESSION_JSON,
    LLM_MODEL,
    MIMIR_OUTPUT,
    REVIEW,
    YOLO_MODEL,
)

from rich.console import Console
from rich.progress import (
    Progress,
    BarColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn
)
from rich.table import Table
from rich.panel import Panel

# =========================================================
# CONSOLE
# =========================================================

console = Console()

# =========================================================
# CONFIG
# =========================================================

# YOLO classes
PERSON = 0
VEHICLES = {2, 5, 7}

# detection tuning
MIN_CONF = 0.40
MIN_AREA_RATIO = 0.012

# sampling
SAMPLE_FPS = 2.0
SCAN_MODE = "balanced"

SCAN_MODE_CONFIGS = {
    "fast": {
        "sample_fps": 1.0,
        "prepass_sample_fps": 0.25,
        "prepass_max_samples_per_camera": 10,
        "prepass_object_max_samples_per_camera": 1,
        "prepass_deep_threshold": 10.0,
        "prepass_uncertain_threshold": 5.0,
        "ai_min_event_score": 28.0,
        "description": "Fast mode prioritizes speed and sends only stronger candidate events to AI review."
    },
    "balanced": {
        "sample_fps": 2.0,
        "prepass_sample_fps": 0.5,
        "prepass_max_samples_per_camera": 18,
        "prepass_object_max_samples_per_camera": 2,
        "prepass_deep_threshold": 7.0,
        "prepass_uncertain_threshold": 3.5,
        "ai_min_event_score": 0.0,
        "description": "Balanced mode uses the current default local review behavior."
    },
    "quality": {
        "sample_fps": 3.0,
        "prepass_sample_fps": 0.75,
        "prepass_max_samples_per_camera": 30,
        "prepass_object_max_samples_per_camera": 4,
        "prepass_deep_threshold": 4.0,
        "prepass_uncertain_threshold": 2.0,
        "ai_min_event_score": 0.0,
        "description": "Quality mode samples more frames and prioritizes catching suspicious events."
    },
    "thorough": {
        "sample_fps": 3.0,
        "prepass_sample_fps": 0.75,
        "prepass_max_samples_per_camera": 30,
        "prepass_object_max_samples_per_camera": 4,
        "prepass_deep_threshold": 4.0,
        "prepass_uncertain_threshold": 2.0,
        "ai_min_event_score": 0.0,
        "description": "Thorough mode samples more frames and prioritizes catching suspicious events."
    }
}

SOURCE_ACTIONS = {
    "analyze_only",
    "copy_all",
    "move_all",
    "copy_review",
    "move_review",
}

# event logic
EVENT_TRIGGER = 14.0
EVENT_END_TIMEOUT = 2.0
MIN_EVENT_FRAMES = 4

# motion signal
MOTION_FRAME_SIZE = (320, 180)
MOTION_SPIKE_THRESHOLD = 24.0
CRASH_GLOBAL_MOTION_TRIGGER = 28.0
CRASH_SCENE_CHANGE_TRIGGER = 18.0
CRASH_FLOW_TRIGGER = 2.6
CRASH_STATIC_BASELINE_THRESHOLD = 7.5
CRASH_IMPACT_SPIKE_TRIGGER = 52.0
CRASH_SAFETY_VERSION = "crash_safety_v1"
CAMERA_SHAKE_SCORE_SCALE = 40.0
OPTICAL_FLOW_SCORE_SCALE = 8.0
PROXIMITY_SCORE_SCALE = 25.0
SCENE_CHANGE_SCORE_SCALE = 35.0
LOCAL_EDGE_MOTION_SCORE_SCALE = 18.0
IMPACT_FOCUSED_CONTACT_SHEET_SCORE = 0.45
AI_CLEAR_IGNORE_CONFIDENCE = 0.75
OBJECT_BRIEF_DWELL_SEC = 1.0
OBJECT_BRIEF_MAX_FRAMES = 2
OBJECT_LINGER_DWELL_SEC = 3.0
OBJECT_LINGER_MIN_FRAMES = 5
OBJECT_VERY_LINGER_DWELL_SEC = 6.0
PERSON_PASSBY_MAX_SEC = 2.0
PERSON_LINGER_MIN_SEC = 4.0
VEHICLE_PASSBY_MAX_SEC = 2.0
VEHICLE_LINGER_MIN_SEC = 4.0
OBJECT_TRACK_IOU_THRESHOLD = 0.20
OBJECT_TRACK_CENTROID_DISTANCE_RATIO = 0.16
OBJECT_TRACK_MAX_MISSED_FRAMES = 2
OBJECT_CLOSE_PROXIMITY_THRESHOLD = 0.45

# TeslaCam camera grouping
EXPECTED_TESLACAM_CAMERAS = [
    "front",
    "back",
    "left_repeater",
    "right_repeater",
    "left_pillar",
    "right_pillar",
]

TESLACAM_SOURCE_FOLDERS = {
    "RecentClips",
    "SavedClips",
    "SentryClips",
}

TESLACAM_CAMERA_ALIASES = {
    "front": "front",
    "back": "back",
    "rear": "back",
    "left_repeater": "left_repeater",
    "right_repeater": "right_repeater",
    "left_pillar": "left_pillar",
    "right_pillar": "right_pillar",
    "left": "left_repeater",
    "right": "right_repeater",
}

TESLACAM_CLIP_PATTERN = re.compile(
    r"^(?P<event_group_id>\d{4}-\d{2}-\d{2}[_-]\d{2}-\d{2}-\d{2})-(?P<camera>.+)\.mp4$",
    re.IGNORECASE
)

# AI
AI_ENABLED = True
AI_REVIEW_AVAILABLE = False
AI_REVIEW_MODEL = None
AI_REVIEW_ERROR = None
AI_REVIEW_BUDGET = 50
AI_AUDIT_ENABLED = True
AI_AUDIT_OUTPUT = os.path.join(
    MIMIR_OUTPUT,
    "AIAudit"
)

DEFAULT_AI_REVIEW_BUDGETS = {
    "fast": 20,
    "balanced": 50,
    "quality": 150,
    "thorough": 150
}
SCAN_ENGINE = "standard"

# crop top area (distant traffic/sky)
IGNORE_TOP_RATIO = 0.20

# =========================================================
# CREATE FOLDERS
# =========================================================

for f in [
    INCOMING,
    IMPORTANT,
    REVIEW,
    IGNORE,
    FRAMES,
    MIMIR_OUTPUT,
    INCIDENTS_OUTPUT,
    AI_AUDIT_OUTPUT,
]:
    os.makedirs(f, exist_ok=True)

# =========================================================
# LOAD YOLO
# =========================================================

console.print("[bold cyan]Loading YOLO...[/bold cyan]")

yolo = YOLO(YOLO_MODEL)

console.print("[bold green]YOLO loaded.[/bold green]")

# =========================================================
# AI
# =========================================================

VALID_AI_SEVERITIES = {
    "IMPORTANT",
    "REVIEW",
    "IGNORE"
}

VALID_AI_SCENE_TYPES = {
    "normal_traffic",
    "person_near_vehicle",
    "possible_contact",
    "possible_impact",
    "unclear"
}


def to_float(value, default=0.0):

    if value is None:
        return default

    try:

        if hasattr(value, "item") and callable(value.item):
            value = value.item()

    except Exception:
        return default

    try:
        number = float(value)

    except (TypeError, ValueError, OverflowError):
        return default

    if not math.isfinite(number):
        return default

    return number


def to_int(value, default=0):

    if value is None:
        return default

    try:

        if hasattr(value, "item") and callable(value.item):
            value = value.item()

    except Exception:
        return default

    try:
        number = float(value)

    except (TypeError, ValueError, OverflowError):
        return default

    if not math.isfinite(number):
        return default

    try:
        return int(number)

    except (TypeError, ValueError, OverflowError):
        return default


def to_json_safe(value):

    if value is None or isinstance(value, (str, bool)):
        return value

    if isinstance(value, int):
        return value

    if isinstance(value, float):
        return to_float(value)

    try:

        if hasattr(value, "item") and callable(value.item):
            return to_json_safe(value.item())

    except Exception:
        return str(value)

    if isinstance(value, dict):
        return {
            str(key): to_json_safe(item)
            for key, item in value.items()
        }

    if isinstance(value, (list, tuple)):
        return [
            to_json_safe(item)
            for item in value
        ]

    return str(value)


# =========================================================
# SCAN MODES
# =========================================================

def scan_mode_config(mode):

    return SCAN_MODE_CONFIGS.get(
        mode,
        SCAN_MODE_CONFIGS["balanced"]
    )


def configure_scan_mode(mode):

    global SAMPLE_FPS
    global SCAN_MODE

    normalized_mode = str(mode or "balanced").strip().lower()

    if normalized_mode not in SCAN_MODE_CONFIGS:
        normalized_mode = "balanced"

    config = scan_mode_config(normalized_mode)

    SAMPLE_FPS = to_float(
        config.get("sample_fps"),
        2.0
    )
    SCAN_MODE = normalized_mode

    return normalized_mode


def configure_vlm_model(model_name):

    global LLM_MODEL
    global AI_ENABLED
    global AI_REVIEW_AVAILABLE
    global AI_REVIEW_MODEL
    global AI_REVIEW_ERROR
    global SCAN_ENGINE

    AI_REVIEW_AVAILABLE = False
    AI_REVIEW_MODEL = None
    AI_REVIEW_ERROR = None
    SCAN_ENGINE = "standard"

    if not model_name:
        AI_ENABLED = False
        AI_REVIEW_ERROR = "No enhanced AI model was requested."
        console.print(
            "[yellow]Enhanced AI review unavailable. Continuing with standard local scan.[/yellow]"
        )
        return None

    selected_model = str(
        model_name
    ).strip()

    if selected_model:
        LLM_MODEL = selected_model

    availability = check_vlm_available(LLM_MODEL)

    if availability["available"]:
        AI_ENABLED = True
        AI_REVIEW_AVAILABLE = True
        AI_REVIEW_MODEL = LLM_MODEL
        SCAN_ENGINE = "enhanced_ai"
        return LLM_MODEL

    AI_ENABLED = False
    AI_REVIEW_ERROR = availability["error"]
    console.print(
        "[yellow]Enhanced AI review unavailable. Continuing with standard local scan.[/yellow]"
    )
    if AI_REVIEW_ERROR:
        console.print(
            f"[yellow]AI setup:[/yellow] {AI_REVIEW_ERROR}"
        )

    return None


def check_vlm_available(model_name):

    model = str(
        model_name or ""
    ).strip()

    if not model:
        return {
            "available": False,
            "error": "No enhanced AI model was requested."
        }

    try:

        response = requests.get(
            "http://localhost:11434/api/tags",
            timeout=4
        )
        response.raise_for_status()
        payload = response.json()
        models = payload.get(
            "models",
            []
        )
        installed_names = {
            str(
                item.get(
                    "name",
                    item.get(
                        "model",
                        ""
                    )
                )
            ).lower()
            for item in models
            if isinstance(item, dict)
        }

        if model.lower() in installed_names:
            return {
                "available": True,
                "error": None
            }

        return {
            "available": False,
            "error": f"Enhanced AI model is not installed: {model}"
        }

    except Exception as exc:

        return {
            "available": False,
            "error": f"Ollama is unavailable: {exc}"
        }


def mark_ai_review_unavailable(error):

    global AI_ENABLED
    global AI_REVIEW_AVAILABLE
    global AI_REVIEW_MODEL
    global AI_REVIEW_ERROR
    global SCAN_ENGINE

    AI_ENABLED = False
    AI_REVIEW_AVAILABLE = False
    AI_REVIEW_MODEL = None
    AI_REVIEW_ERROR = str(
        error or "Enhanced AI review failed."
    )
    SCAN_ENGINE = "standard"


def should_run_ai_for_event(event_score):

    config = scan_mode_config(SCAN_MODE)
    min_event_score = to_float(
        config.get("ai_min_event_score"),
        0.0
    )

    return to_float(event_score) >= min_event_score


def ai_budget_for_mode(scan_mode, explicit_budget=None):

    if explicit_budget is not None:
        return max(0, to_int(explicit_budget, 0))

    return to_int(
        DEFAULT_AI_REVIEW_BUDGETS.get(
            str(scan_mode or "balanced").lower(),
            DEFAULT_AI_REVIEW_BUDGETS["balanced"]
        )
    )


def event_group_id_for_value(value):

    try:
        if isinstance(value, dict):
            files = value.get("files") or []
            first_file = files[0] if files else value.get("path", "")
            return str(
                value.get("event_group_id")
                or value.get("event_id")
                or value.get("timestamp")
                or Path(str(first_file)).stem
            )

        metadata = source_discovery_metadata_for_video(value)
        return str(
            metadata.get("camera_group_id")
            or Path(str(value)).stem
        )

    except Exception:
        return "unknown"


def camera_priority_score(camera):

    camera_text = str(camera or "").lower()

    if camera_text in {"back", "rear"}:
        return 8

    if camera_text == "front":
        return 7

    if "repeater" in camera_text:
        return 5

    if "pillar" in camera_text:
        return 4

    if camera_text and camera_text != "unknown":
        return 3

    return 1


def group_ai_candidate_priority(event_group):

    try:
        clips = camera_clips_for_group(event_group)
        cameras = [
            str(clip.get("camera") or "unknown")
            for clip in clips
            if isinstance(clip, dict)
        ]
        metadata_blob = normalized_text_blob(
            event_group.get("source_event_reason"),
            event_group.get("source_event_raw", {}),
            event_group.get("event_id"),
            cameras
        )
        priority = 0.0
        reasons = []

        if text_has_keyword(metadata_blob, ["crash", "collision", "impact", "sentry"]):
            priority += 1000
            reasons.append("event metadata suggests impact/crash/sentry trigger")

        if text_has_keyword(metadata_blob, ["contact", "ding", "damage", "hit"]):
            priority += 800
            reasons.append("event metadata suggests possible contact")

        if text_has_keyword(metadata_blob, ["person", "entry", "door", "handle", "tamper"]):
            priority += 500
            reasons.append("event metadata suggests person or tampering")

        if to_int(event_group.get("camera_count", 0)) > 1:
            priority += 100
            reasons.append("multi-camera event group")

        prepass_reason = str(
            event_group.get("prepass_candidate_reason", "")
        ).lower()
        prepass_hint = str(
            event_group.get("prepass_severity_hint", "IGNORE")
        ).upper()
        prepass_motion_score = to_float(
            event_group.get("prepass_motion_score", 0.0)
        )
        object_persistence = event_group.get(
            "prepass_object_persistence",
            {}
        )

        if prepass_hint == "IMPORTANT":
            priority += 900
            reasons.append("prepass severity hint IMPORTANT")

        elif prepass_hint == "REVIEW":
            priority += 350
            reasons.append("prepass severity hint REVIEW")

        if text_has_keyword(prepass_reason, ["crash", "impact", "contact", "spike"]):
            priority += 700
            reasons.append("prepass found crash/contact motion candidate")

        if text_has_keyword(prepass_reason, ["person", "door", "tamper"]):
            priority += 500
            reasons.append("prepass found person or tampering candidate")

        if prepass_motion_score >= MOTION_SPIKE_THRESHOLD * 0.70:
            priority += 400
            reasons.append("prepass high motion score")

        if isinstance(object_persistence, dict):
            if object_persistence.get("lingering_person_detected"):
                priority += 650
                reasons.append("prepass lingering person evidence")

            if object_persistence.get("lingering_vehicle_detected"):
                priority += 350
                reasons.append("prepass lingering vehicle evidence")

            if object_persistence.get("normal_passing_traffic_evidence"):
                priority -= 250
                reasons.append("prepass suggests normal passing traffic")

        best_camera = None
        best_camera_score = -1

        for clip in clips:
            camera = clip.get("camera") if isinstance(clip, dict) else None
            score = camera_priority_score(camera)

            if score > best_camera_score:
                best_camera_score = score
                best_camera = camera

        priority += max(0, best_camera_score)

        if not reasons:
            reasons.append("standard local prepass candidate")

        return to_json_safe({
            "event_group_id": event_group_id_for_value(event_group),
            "priority": round(priority, 3),
            "candidate_reasons": reasons,
            "best_camera_hint": best_camera or "unknown"
        })

    except Exception as exc:
        return {
            "event_group_id": event_group_id_for_value(event_group),
            "priority": 0.0,
            "candidate_reasons": [f"candidate priority fallback: {exc}"],
            "best_camera_hint": "unknown"
        }


def build_ai_review_context(event_groups, budget):

    candidates = [
        group_ai_candidate_priority(group)
        for group in event_groups
    ]
    candidates = sorted(
        candidates,
        key=lambda item: to_float(item.get("priority", 0.0)),
        reverse=True
    )
    selected_ids = {
        str(candidate.get("event_group_id"))
        for candidate in candidates[:max(0, to_int(budget))]
    }

    return {
        "budget": max(0, to_int(budget)),
        "candidates": candidates,
        "selected_group_ids": selected_ids,
        "reviewed_group_ids": set(),
        "skipped_group_ids": set(),
        "reviewed_groups": 0,
        "skipped_groups": 0,
        "runtime_sec": 0.0
    }


def refresh_ai_review_context(context, event_groups, budget):

    refreshed = build_ai_review_context(event_groups, budget)

    if not isinstance(context, dict):
        return refreshed

    refreshed["reviewed_group_ids"] = set(
        context.get("reviewed_group_ids", set())
    )
    refreshed["skipped_group_ids"] = set(
        context.get("skipped_group_ids", set())
    )
    refreshed["reviewed_groups"] = len(
        refreshed.get("reviewed_group_ids", set())
    )
    refreshed["skipped_groups"] = len(
        refreshed.get("skipped_group_ids", set())
    )
    refreshed["runtime_sec"] = to_float(
        context.get("runtime_sec", 0.0)
    )

    return refreshed


def set_active_ai_review_context(context):

    global AI_REVIEW_GROUP_CONTEXT
    AI_REVIEW_GROUP_CONTEXT = context


def ai_review_candidate_for_group(context, group_id):

    if not isinstance(context, dict):
        return {}

    for candidate in context.get("candidates", []):
        if str(candidate.get("event_group_id")) == str(group_id):
            return candidate

    return {}


def should_run_group_ai_review(context, group_id, event_score):

    group_id = str(group_id or "unknown")

    if not should_run_ai_for_event(event_score):
        if isinstance(context, dict):
            context.setdefault("skipped_group_ids", set()).add(group_id)
            context["skipped_groups"] = len(context.get("skipped_group_ids", set()))
        return False, "event below AI review threshold"

    if not AI_ENABLED or not AI_REVIEW_AVAILABLE:
        if isinstance(context, dict):
            context.setdefault("skipped_group_ids", set()).add(group_id)
            context["skipped_groups"] = len(context.get("skipped_group_ids", set()))
        return False, "AI review unavailable"

    if not isinstance(context, dict):
        return True, ""

    if group_id in context.setdefault("reviewed_group_ids", set()):
        return False, "AI already reviewed this event group"

    if group_id not in context.setdefault("selected_group_ids", set()):
        context.setdefault("skipped_group_ids", set()).add(group_id)
        context["skipped_groups"] = len(context.get("skipped_group_ids", set()))
        return False, "AI review budget reserved for higher priority candidates"

    return True, ""


def mark_group_ai_reviewed(context, group_id, runtime_sec):

    try:
        if not isinstance(context, dict):
            return

        group_id = str(group_id or "unknown")
        context.setdefault("reviewed_group_ids", set()).add(group_id)
        context["reviewed_groups"] = len(context.get("reviewed_group_ids", set()))
        context["runtime_sec"] = round(
            to_float(context.get("runtime_sec", 0.0)) + to_float(runtime_sec),
            3
        )

    except Exception:
        pass


def update_session_ai_review_budget_fields(session, context):

    try:
        if not isinstance(context, dict):
            return

        candidates = context.get("candidates", [])
        selected_ids = context.get("selected_group_ids", set())
        reviewed_ids = context.get("reviewed_group_ids", set())
        skipped_ids = context.get("skipped_group_ids", set())

        session["ai_review_required"] = True
        session["ai_review_budget"] = to_int(context.get("budget", 0))
        session["ai_review_candidates"] = to_int(len(candidates))
        session["ai_reviewed_groups"] = to_int(len(reviewed_ids))
        session["ai_skipped_groups"] = to_int(
            max(
                len(skipped_ids),
                max(0, len(candidates) - len(selected_ids))
            )
        )
        session["ai_review_runtime_sec"] = round(
            to_float(context.get("runtime_sec", 0.0)),
            3
        )
        session["grouped_camera_review"] = True

        performance = session.setdefault(
            "performance",
            create_performance_metrics()
        )
        performance["ai_review_runtime_sec"] = session["ai_review_runtime_sec"]
        performance["groups_reviewed_by_ai"] = session["ai_reviewed_groups"]
        performance["groups_skipped_by_ai"] = session["ai_skipped_groups"]
        ai_calls = to_int(performance.get("ai_calls", 0))
        performance["avg_ai_call_sec"] = round(
            session["ai_review_runtime_sec"] / ai_calls,
            3
        ) if ai_calls > 0 else 0.0

    except Exception as exc:
        if isinstance(session, dict):
            session.setdefault("storage_warnings", []).append(
                f"AI review budget summary warning: {exc}"
            )


def prepass_config_for_mode(mode):

    config = scan_mode_config(
        str(mode or SCAN_MODE).lower()
    )

    return {
        "sample_fps": to_float(config.get("prepass_sample_fps", 0.5), 0.5),
        "max_samples_per_camera": max(3, to_int(config.get("prepass_max_samples_per_camera", 18), 18)),
        "prepass_object_max_samples_per_camera": max(1, to_int(config.get("prepass_object_max_samples_per_camera", 2), 2)),
        "deep_threshold": to_float(config.get("prepass_deep_threshold", 7.0), 7.0),
        "uncertain_threshold": to_float(config.get("prepass_uncertain_threshold", 3.5), 3.5)
    }


def sample_group_clip_prepass(path, camera, config, track_objects=False):

    started = time.perf_counter()
    cap = None

    try:
        cap = cv2.VideoCapture(path)

        if not cap.isOpened():
            return {
                "camera": camera or "unknown",
                "path": absolute_path_string(path),
                "exists": os.path.exists(path),
                "opened": False,
                "sampled_frames": 0,
                "max_motion_score": 0.0,
                "max_scene_change_score": 0.0,
                "strong_motion_spike": False,
                "mostly_static": True,
                "candidate_windows": [],
                "runtime_sec": round(time.perf_counter() - started, 3),
                "error": "could not open video"
            }

        fps = valid_fps(cap.get(cv2.CAP_PROP_FPS))

        if fps <= 0:
            fps = 30.0

        duration_sec = video_duration_from_capture(cap, fps)
        sample_fps = max(0.1, to_float(config.get("sample_fps", 0.5), 0.5))
        step = max(1, int(fps / sample_fps))
        max_samples = max(3, to_int(config.get("max_samples_per_camera", 18), 18))

        frame_i = 0
        sampled = 0
        previous_frame = None
        max_motion = 0.0
        max_scene = 0.0
        peak_time_sec = None
        candidate_windows = []
        object_tracker = create_object_tracker()
        object_sampled_frames = 0
        max_object_prepass_samples = max(
            1,
            to_int(config.get("prepass_object_max_samples_per_camera", 3), 3)
        )

        while sampled < max_samples:
            ret, frame = cap.read()

            if not ret:
                break

            if frame_i % step != 0:
                frame_i += 1
                continue

            motion_score = frame_motion_score(previous_frame, frame)
            scene_score = scene_change_score(previous_frame, frame)
            now_sec = frame_i / fps
            object_candidate_frame = (
                track_objects
                or motion_score >= MOTION_SPIKE_THRESHOLD * 0.50
                or scene_score >= CRASH_SCENE_CHANGE_TRIGGER * 0.50
            )

            if object_candidate_frame and object_sampled_frames < max_object_prepass_samples:
                try:
                    _score, _persons, _vehicles, detections = analyze(
                        frame,
                        return_detections=True
                    )
                    update_object_tracker(
                        object_tracker,
                        detections,
                        frame_i,
                        now_sec,
                        frame.shape
                    )
                    object_sampled_frames += 1
                except Exception:
                    pass

            if motion_score > max_motion or scene_score > max_scene:
                peak_time_sec = now_sec

            max_motion = max(max_motion, to_float(motion_score))
            max_scene = max(max_scene, to_float(scene_score))

            if (
                motion_score >= MOTION_SPIKE_THRESHOLD * 0.50
                or scene_score >= CRASH_SCENE_CHANGE_TRIGGER * 0.50
            ):
                candidate_windows.append({
                    "start_sec": round(max(0.0, now_sec - 3.0), 2),
                    "peak_sec": round(now_sec, 2),
                    "end_sec": round(min(max(duration_sec, now_sec), now_sec + 3.0), 2),
                    "reason": "motion_or_scene_change_spike"
                })

            previous_frame = frame.copy()
            sampled += 1
            frame_i += 1

        mostly_static = (
            sampled > 0
            and max_motion < config.get("uncertain_threshold", 3.5)
            and max_scene < config.get("uncertain_threshold", 3.5)
        )

        if not candidate_windows and peak_time_sec is not None:
            candidate_windows.append({
                "start_sec": round(max(0.0, peak_time_sec - 3.0), 2),
                "peak_sec": round(peak_time_sec, 2),
                "end_sec": round(min(max(duration_sec, peak_time_sec), peak_time_sec + 3.0), 2),
                "reason": "best_prepass_motion"
            })

        return to_json_safe({
            "camera": camera or "unknown",
            "path": absolute_path_string(path),
            "exists": os.path.exists(path),
            "opened": True,
            "duration_sec": round(to_float(duration_sec), 2),
            "sampled_frames": sampled,
            "max_motion_score": round(max_motion, 2),
            "max_scene_change_score": round(max_scene, 2),
            "strong_motion_spike": (
                max_motion >= MOTION_SPIKE_THRESHOLD * 0.70
                or max_scene >= CRASH_SCENE_CHANGE_TRIGGER * 0.70
            ),
            "mostly_static": mostly_static,
            "prepass_object_sampled_frames": object_sampled_frames,
            "object_persistence": finalize_object_persistence(object_tracker),
            "candidate_windows": candidate_windows[:3],
            "runtime_sec": round(time.perf_counter() - started, 3),
            "error": None
        })

    except Exception as exc:
        return {
            "camera": camera or "unknown",
            "path": absolute_path_string(path),
            "exists": os.path.exists(path),
            "opened": False,
            "sampled_frames": 0,
            "max_motion_score": 0.0,
            "max_scene_change_score": 0.0,
            "strong_motion_spike": False,
            "mostly_static": True,
            "candidate_windows": [],
            "runtime_sec": round(time.perf_counter() - started, 3),
            "error": str(exc)
        }

    finally:
        try:
            if cap is not None:
                cap.release()
        except Exception:
            pass


def merge_prepass_object_persistence(clip_results):

    tracks = []

    for result in clip_results or []:
        if not isinstance(result, dict):
            continue

        camera = result.get("camera") or "unknown"
        persistence = result.get("object_persistence", {})

        if not isinstance(persistence, dict):
            continue

        for track in persistence.get("object_tracks", []):
            if not isinstance(track, dict):
                continue

            copied_track = dict(track)
            copied_track["camera"] = camera
            copied_track["group_track_id"] = (
                f"{camera}-{copied_track.get('track_id', len(tracks) + 1)}"
            )
            tracks.append(copied_track)

    summary = summarize_object_tracks(tracks)
    brief_vehicle_only = (
        summary["vehicles"]["track_count"] > 0
        and summary["vehicles"]["brief_only"]
        and summary["persons"]["track_count"] == 0
    )
    brief_person_only = (
        summary["persons"]["track_count"] > 0
        and summary["persons"]["brief_only"]
        and summary["vehicles"]["track_count"] == 0
    )
    lingering_person_detected = bool(
        summary["persons"]["long_linger_detected"]
    )
    lingering_vehicle_detected = bool(
        summary["vehicles"]["long_linger_detected"]
    )
    person_passby_detected = bool(summary["persons"].get("passby_detected", False))
    person_lingering_detected = bool(summary["persons"].get("lingering_detected", lingering_person_detected))
    vehicle_passby_detected = bool(summary["vehicles"].get("passby_detected", False))
    vehicle_lingering_detected = bool(summary["vehicles"].get("lingering_detected", lingering_vehicle_detected))

    return to_json_safe({
        "object_tracks": tracks,
        "object_persistence_summary": summary,
        "brief_vehicle_only": brief_vehicle_only,
        "brief_person_only": brief_person_only,
        "lingering_person_detected": lingering_person_detected,
        "lingering_vehicle_detected": lingering_vehicle_detected,
        "person_passby_detected": person_passby_detected,
        "person_lingering_detected": person_lingering_detected,
        "vehicle_passby_detected": vehicle_passby_detected,
        "vehicle_lingering_detected": vehicle_lingering_detected,
        "normal_passing_traffic_evidence": bool(
            (brief_vehicle_only or vehicle_passby_detected)
            and not lingering_person_detected
            and not lingering_vehicle_detected
        )
    })


def run_event_group_prepass(event_group, scan_mode):

    started = time.perf_counter()
    config = prepass_config_for_mode(scan_mode)
    clips = camera_clips_for_group(event_group)
    clip_results = []
    metadata_blob = normalized_text_blob(
        event_group.get("source_category"),
        event_group.get("source_event_reason"),
        event_group.get("source_event_raw", {}),
        event_group.get("event_id"),
        event_group.get("files", [])
    )
    useful_metadata = text_has_keyword(
        metadata_blob,
        ["sentry", "savedclips", "sentryclips", "impact", "collision", "contact", "crash", "rear-ended", "rear ended", "door", "person", "damage", "important"]
    )
    metadata_important = text_has_keyword(
        metadata_blob,
        ["impact", "collision", "crash", "rear-ended", "rear ended", "damage", "important"]
    )

    for clip in clips:
        if not isinstance(clip, dict):
            continue

        path = clip.get("path")

        if not path:
            continue

        clip_results.append(
            sample_group_clip_prepass(
                path,
                clip.get("camera"),
                config,
                track_objects=useful_metadata
            )
        )

    best_clip = None
    best_score = -1.0

    for result in clip_results:
        score = (
            to_float(result.get("max_motion_score"))
            + to_float(result.get("max_scene_change_score"))
            + camera_priority_score(result.get("camera")) * 0.25
        )

        if score > best_score:
            best_score = score
            best_clip = result

    max_motion = max(
        [to_float(result.get("max_motion_score")) for result in clip_results]
        or [0.0]
    )
    max_scene = max(
        [to_float(result.get("max_scene_change_score")) for result in clip_results]
        or [0.0]
    )
    prepass_object_persistence = merge_prepass_object_persistence(clip_results)
    lingering_person = bool(
        prepass_object_persistence.get("lingering_person_detected", False)
    )
    lingering_vehicle = bool(
        prepass_object_persistence.get("lingering_vehicle_detected", False)
    )
    object_tracks_found = bool(
        prepass_object_persistence.get("object_tracks", [])
    )
    strong_motion = any(result.get("strong_motion_spike") for result in clip_results)
    mostly_static = bool(clip_results) and all(result.get("mostly_static") for result in clip_results)
    candidate_windows = []

    if best_clip:
        candidate_windows = best_clip.get("candidate_windows", [])

    candidate_score = max(max_motion, max_scene)
    reasons = []

    if strong_motion:
        reasons.append("strong motion or scene-change spike")

    if useful_metadata:
        reasons.append("useful Sentry/Saved event metadata")

    if metadata_important:
        reasons.append("source metadata or filename suggests crash/contact importance")
        if best_clip:
            candidate_windows = [{
                "start_sec": 0.0,
                "peak_sec": round(
                    to_float(best_clip.get("duration_sec", 0.0)) / 2.0,
                    2
                ),
                "end_sec": round(
                    to_float(best_clip.get("duration_sec", 0.0)),
                    2
                ),
                "reason": "metadata_important_full_clip_window"
            }]

    if lingering_person:
        reasons.append("prepass found lingering person")

    if lingering_vehicle:
        reasons.append("prepass found lingering vehicle")

    if object_tracks_found and not lingering_person and not lingering_vehicle:
        reasons.append("prepass found brief object presence")

    if candidate_score >= config["deep_threshold"]:
        reasons.append("prepass score above deep-analysis threshold")

    elif candidate_score >= config["uncertain_threshold"]:
        reasons.append("uncertain motion above review threshold")

    deep_analysis = bool(
        strong_motion
        or useful_metadata
        or lingering_person
        or lingering_vehicle
        or candidate_score >= config["deep_threshold"]
        or (
            str(scan_mode).lower() in {"balanced", "quality", "thorough"}
            and candidate_score >= config["uncertain_threshold"]
        )
    )
    skipped_reason = ""

    if not deep_analysis:
        skipped_reason = "low-interest mostly static group" if mostly_static else "below prepass deep-analysis threshold"

    return to_json_safe({
        "event_group_id": event_group_id_for_value(event_group),
        "deep_analysis": deep_analysis,
        "prepass_motion_score": round(max_motion, 2),
        "prepass_scene_change_score": round(max_scene, 2),
        "prepass_candidate_score": round(candidate_score, 2),
        "prepass_candidate_reason": "; ".join(reasons) if reasons else "no strong local prepass signal",
        "prepass_severity_hint": "IMPORTANT" if metadata_important else ("REVIEW" if useful_metadata else "IGNORE"),
        "primary_camera_candidate": (best_clip or {}).get("camera", "unknown"),
        "primary_video_candidate": (best_clip or {}).get("path"),
        "prepass_object_persistence": prepass_object_persistence,
        "candidate_windows": candidate_windows[:3],
        "mostly_static": mostly_static,
        "skipped_reason": skipped_reason,
        "clip_results": clip_results,
        "runtime_sec": round(time.perf_counter() - started, 3)
    })


def update_event_group_with_prepass(event_group, prepass):

    try:
        event_group["prepass_motion_score"] = prepass.get("prepass_motion_score", 0.0)
        event_group["prepass_scene_change_score"] = prepass.get("prepass_scene_change_score", 0.0)
        event_group["prepass_candidate_reason"] = prepass.get("prepass_candidate_reason", "")
        event_group["prepass_severity_hint"] = prepass.get("prepass_severity_hint", "IGNORE")
        event_group["candidate_windows"] = prepass.get("candidate_windows", [])
        event_group["primary_camera_candidate"] = prepass.get("primary_camera_candidate")
        event_group["prepass_object_persistence"] = prepass.get(
            "prepass_object_persistence",
            finalize_object_persistence(None)
        )
        event_group["deep_analysis_performed"] = bool(prepass.get("deep_analysis", False))
        event_group["skipped_reason"] = prepass.get("skipped_reason", "")
    except Exception:
        pass


def normalized_candidate_windows(candidate_windows, video_duration_sec=0.0):

    windows = []

    if not isinstance(candidate_windows, list):
        return windows

    duration = to_float(video_duration_sec, 0.0)

    for window in candidate_windows:
        if not isinstance(window, dict):
            continue

        start_sec = max(
            0.0,
            to_float(window.get("start_sec"), 0.0)
        )
        end_sec = to_float(
            window.get("end_sec"),
            start_sec
        )

        if duration > 0:
            end_sec = min(duration, end_sec)

        if end_sec < start_sec:
            end_sec = start_sec

        windows.append({
            "start_sec": round(start_sec, 3),
            "end_sec": round(end_sec, 3),
            "peak_sec": round(
                to_float(window.get("peak_sec"), start_sec),
                3
            ),
            "reason": str(window.get("reason", "candidate_window"))
        })

    return windows


def time_in_candidate_windows(time_sec, candidate_windows):

    if not candidate_windows:
        return True

    value = to_float(time_sec, 0.0)

    return any(
        value >= to_float(window.get("start_sec"))
        and value <= to_float(window.get("end_sec"))
        for window in candidate_windows
        if isinstance(window, dict)
    )


# =========================================================
# PERFORMANCE
# =========================================================

ACTIVE_PERFORMANCE = None
ACTIVE_PROFILER = None
PROGRESS_STARTED_AT = None
PROGRESS_LAST_PERCENT = 0.0
PROGRESS_CONTEXT = {
    "total_videos": 0,
    "current_video_index": 0,
    "current_video": None
}
SOURCE_VIDEO_METADATA = {}
AI_REVIEW_GROUP_CONTEXT = None

PROFILE_STAGE_NAMES = [
    "source_discovery",
    "metadata_reading",
    "camera_grouping",
    "group_prepass",
    "video_opening",
    "prepass",
    "frame_sampling",
    "yolo_detection",
    "motion_analysis",
    "impact_analysis",
    "contact_analysis",
    "enhanced_ai_review",
    "thumbnail_generation",
    "timeline_generation",
    "json_writing",
]
PERFORMANCE_REPORT_JSON = os.path.join(MIMIR_OUTPUT, "performance_report.json")
PERFORMANCE_REPORT_CSV = os.path.join(MIMIR_OUTPUT, "performance_report.csv")


def create_performance_metrics():

    return {
        "total_runtime_sec": 0.0,
        "total_video_duration_sec": 0.0,
        "total_ai_calls": 0,
        "total_sampled_frames": 0,
        "total_event_groups": 0,
        "stage_timings": {
            stage_name: 0.0
            for stage_name in PROFILE_STAGE_NAMES
        },
        "slowest_groups": [],
        "videos_processed": 0,
        "avg_sec_per_video": 0.0,
        "yolo_runtime_sec": 0.0,
        "ai_runtime_sec": 0.0,
        "ai_review_runtime_sec": 0.0,
        "avg_ai_call_sec": 0.0,
        "groups_reviewed_by_ai": 0,
        "groups_skipped_by_ai": 0,
        "prepass_runtime_sec": 0.0,
        "deep_analysis_runtime_sec": 0.0,
        "prepass_groups_processed": 0,
        "prepass_candidates_found": 0,
        "deep_analysis_groups": 0,
        "skipped_low_interest_groups": 0,
        "frames_sampled": 0,
        "ai_calls": 0,
        "incidents_created": 0
    }


def set_active_performance(performance):

    global ACTIVE_PERFORMANCE

    ACTIVE_PERFORMANCE = performance


def create_performance_profiler():

    return {
        "stage_timings": {
            stage_name: 0.0
            for stage_name in PROFILE_STAGE_NAMES
        },
        "groups": [],
        "warnings": []
    }


def set_active_profiler(profiler):

    global ACTIVE_PROFILER

    ACTIVE_PROFILER = profiler


def add_profile_warning(session, message):

    try:
        warning = str(message)

        if ACTIVE_PROFILER is not None:
            ACTIVE_PROFILER.setdefault("warnings", []).append(warning)

        if isinstance(session, dict):
            session.setdefault("storage_warnings", []).append(
                f"Performance profiling warning: {warning}"
            )

    except Exception:
        pass


def add_stage_time(stage_name, amount):

    try:
        if stage_name not in PROFILE_STAGE_NAMES:
            return

        if ACTIVE_PROFILER is not None:
            timings = ACTIVE_PROFILER.setdefault("stage_timings", {})
            timings[stage_name] = round(
                to_float(timings.get(stage_name)) + to_float(amount),
                6
            )

        if ACTIVE_PERFORMANCE is not None:
            timings = ACTIVE_PERFORMANCE.setdefault(
                "stage_timings",
                {
                    name: 0.0
                    for name in PROFILE_STAGE_NAMES
                }
            )
            timings[stage_name] = round(
                to_float(timings.get(stage_name)) + to_float(amount),
                6
            )

    except Exception:
        pass


@contextmanager
def profile_stage(stage_name):

    started = time.perf_counter()

    try:
        yield

    finally:

        try:
            add_stage_time(
                stage_name,
                time.perf_counter() - started
            )

        except Exception:
            pass


def profile_metric_for_video(path):

    try:
        metadata = source_discovery_metadata_for_video(path)
        cameras_available = metadata.get("cameras_available", {})

        if not isinstance(cameras_available, dict):
            cameras_available = {}

        cameras_found = sorted(str(camera) for camera in cameras_available.keys())
        camera = metadata.get("camera")

        if not cameras_found and camera:
            cameras_found = [str(camera)]

        group_id = metadata.get("camera_group_id") or Path(path).stem
        source_folder = metadata.get("event_folder") or str(Path(path).parent)
        camera_count = to_int(
            len(cameras_available)
            if cameras_available
            else len(cameras_found)
        )

        return {
            "group_id": str(group_id),
            "source_folder": absolute_path_string(source_folder),
            "camera_count": camera_count,
            "cameras_found": cameras_found,
            "total_duration_sec": 0.0,
            "sampled_frames": 0,
            "yolo_frames": 0,
            "ai_calls": 0,
            "runtime_sec": 0.0,
            "incident_created": False,
            "final_severity": None,
            "error": None,
            "_started_at": time.perf_counter(),
            "_ai_calls_started": to_int((ACTIVE_PERFORMANCE or {}).get("ai_calls", 0))
        }

    except Exception as exc:
        return {
            "group_id": Path(path).stem,
            "source_folder": absolute_path_string(Path(path).parent),
            "camera_count": 0,
            "cameras_found": [],
            "total_duration_sec": 0.0,
            "sampled_frames": 0,
            "yolo_frames": 0,
            "ai_calls": 0,
            "runtime_sec": 0.0,
            "incident_created": False,
            "final_severity": None,
            "error": f"profile metadata failed: {exc}",
            "_started_at": time.perf_counter(),
            "_ai_calls_started": to_int((ACTIVE_PERFORMANCE or {}).get("ai_calls", 0))
        }


def mark_metric_incidents(metric, incidents):

    try:
        if not incidents:
            return

        metric["incident_created"] = True
        severities = [
            str(incident.get("severity", ""))
            for incident in incidents
            if isinstance(incident, dict)
        ]
        severities = [severity for severity in severities if severity]

        if severities:
            metric["final_severity"] = ",".join(severities)

    except Exception:
        pass


def set_metric_error(metric, error):

    try:
        metric["error"] = str(error)

    except Exception:
        pass


def finish_profile_metric(metric, session=None):

    try:
        metric["runtime_sec"] = round(
            time.perf_counter() - to_float(metric.get("_started_at")),
            3
        )
        metric["ai_calls"] = max(
            0,
            to_int((ACTIVE_PERFORMANCE or {}).get("ai_calls", 0))
            - to_int(metric.get("_ai_calls_started", 0))
        )
        metric.pop("_started_at", None)
        metric.pop("_ai_calls_started", None)

        if ACTIVE_PROFILER is not None:
            ACTIVE_PROFILER.setdefault("groups", []).append(to_json_safe(metric))

    except Exception as exc:
        add_profile_warning(
            session,
            f"Could not record performance metric: {exc}"
        )


def slowest_stage(stage_timings):

    try:
        if not isinstance(stage_timings, dict) or not stage_timings:
            return {
                "stage": None,
                "runtime_sec": 0.0
            }

        stage, runtime_sec = max(
            stage_timings.items(),
            key=lambda item: to_float(item[1])
        )
        return {
            "stage": stage,
            "runtime_sec": round(to_float(runtime_sec), 3)
        }

    except Exception:
        return {
            "stage": None,
            "runtime_sec": 0.0
        }


def slowest_groups(groups, limit=10):

    try:
        return [
            to_json_safe(group)
            for group in sorted(
                groups,
                key=lambda group: to_float(group.get("runtime_sec", 0.0)),
                reverse=True
            )[:limit]
        ]

    except Exception:
        return []


def write_performance_reports(session):

    try:
        profiler = ACTIVE_PROFILER or {}
        groups = profiler.get("groups", [])
        stage_timings = profiler.get("stage_timings", {})
        report = {
            "generated_at": timestamp(),
            "session_started_at": session.get("started_at"),
            "session_finished_at": session.get("finished_at"),
            "input_folder": session.get("input_folder"),
            "scan_mode": session.get("scan_mode"),
            "performance": session.get("performance", {}),
            "ai_review_runtime_sec": session.get("ai_review_runtime_sec", 0.0),
            "ai_calls": (session.get("performance", {}) or {}).get("ai_calls", 0),
            "avg_ai_call_sec": (session.get("performance", {}) or {}).get("avg_ai_call_sec", 0.0),
            "groups_reviewed_by_ai": session.get("ai_reviewed_groups", 0),
            "groups_skipped_by_ai": session.get("ai_skipped_groups", 0),
            "scan_pipeline": session.get("scan_pipeline"),
            "prepass_groups_processed": session.get("prepass_groups_processed", 0),
            "prepass_candidates_found": session.get("prepass_candidates_found", 0),
            "deep_analysis_groups": session.get("deep_analysis_groups", 0),
            "skipped_low_interest_groups": session.get("skipped_low_interest_groups", 0),
            "prepass_runtime_sec": session.get("prepass_runtime_sec", 0.0),
            "deep_analysis_runtime_sec": session.get("deep_analysis_runtime_sec", 0.0),
            "stage_timings": stage_timings,
            "slowest_stage": slowest_stage(stage_timings),
            "slowest_groups": slowest_groups(groups),
            "groups": groups,
            "warnings": profiler.get("warnings", [])
        }

        os.makedirs(MIMIR_OUTPUT, exist_ok=True)

        with profile_stage("json_writing"):
            with open(PERFORMANCE_REPORT_JSON, "w", encoding="utf-8") as file:
                json.dump(to_json_safe(report), file, indent=2)

            with open(PERFORMANCE_REPORT_CSV, "w", newline="", encoding="utf-8") as file:
                fieldnames = [
                    "group_id",
                    "source_folder",
                    "camera_count",
                    "cameras_found",
                    "total_duration_sec",
                    "sampled_frames",
                    "yolo_frames",
                    "ai_calls",
                    "runtime_sec",
                    "incident_created",
                    "final_severity",
                    "error"
                ]
                writer = csv.DictWriter(file, fieldnames=fieldnames)
                writer.writeheader()

                for group in groups:
                    row = {field: group.get(field) for field in fieldnames}
                    row["cameras_found"] = ",".join(
                        str(camera)
                        for camera in group.get("cameras_found", [])
                    )
                    writer.writerow(row)

    except Exception as exc:
        add_profile_warning(
            session,
            f"Could not write performance report: {exc}"
        )


def add_performance_value(metric, amount):

    try:

        if ACTIVE_PERFORMANCE is None:
            return

        current = ACTIVE_PERFORMANCE.get(metric, 0)

        if isinstance(current, int) and isinstance(amount, int):
            ACTIVE_PERFORMANCE[metric] = to_int(current) + to_int(amount)
            return

        ACTIVE_PERFORMANCE[metric] = to_float(current) + to_float(amount)

    except Exception:
        pass


def set_performance_value(session, metric, value):

    try:

        performance = session.get("performance")

        if not isinstance(performance, dict):
            performance = create_performance_metrics()
            session["performance"] = performance

        if metric in {
            "videos_processed",
            "frames_sampled",
            "ai_calls",
            "incidents_created",
            "groups_reviewed_by_ai",
            "groups_skipped_by_ai"
        }:
            performance[metric] = to_int(value)
        else:
            performance[metric] = round(
                to_float(value),
                3
            )

    except Exception:
        pass


def finalize_performance_metrics(session, started_perf_counter):

    try:

        elapsed = time.perf_counter() - started_perf_counter
        set_performance_value(
            session,
            "total_runtime_sec",
            elapsed
        )

        performance = session.get("performance", {})
        videos_processed = to_int(
            performance.get(
                "videos_processed",
                0
            )
        )

        avg_sec_per_video = 0.0

        if videos_processed > 0:
            avg_sec_per_video = to_float(elapsed) / videos_processed

        set_performance_value(
            session,
            "avg_sec_per_video",
            avg_sec_per_video
        )

        for metric in [
            "yolo_runtime_sec",
            "ai_runtime_sec",
            "ai_review_runtime_sec",
            "avg_ai_call_sec",
            "prepass_runtime_sec",
            "deep_analysis_runtime_sec"
        ]:
            set_performance_value(
                session,
                metric,
                performance.get(
                    metric,
                    0.0
                )
            )

        for metric in [
            "videos_processed",
            "frames_sampled",
            "ai_calls",
            "incidents_created",
            "groups_reviewed_by_ai",
            "groups_skipped_by_ai",
            "prepass_groups_processed",
            "prepass_candidates_found",
            "deep_analysis_groups",
            "skipped_low_interest_groups"
        ]:
            metric_value = performance.get(
                metric,
                0
            )

            if metric == "incidents_created":
                metric_value = len(
                    session.get(
                        "incidents",
                        []
                    )
                )

            set_performance_value(
                session,
                metric,
                metric_value
            )

        profiler = ACTIVE_PROFILER or {}
        groups = profiler.get("groups", [])
        stage_timings = profiler.get(
            "stage_timings",
            performance.get(
                "stage_timings",
                {}
            )
        )
        performance["stage_timings"] = {
            stage_name: round(
                to_float(stage_timings.get(stage_name)),
                3
            )
            for stage_name in PROFILE_STAGE_NAMES
        }
        performance["total_video_duration_sec"] = round(
            sum(
                to_float(group.get("total_duration_sec"))
                for group in groups
            ),
            3
        )
        performance["total_ai_calls"] = to_int(
            performance.get(
                "ai_calls",
                0
            )
        )
        performance["total_sampled_frames"] = to_int(
            performance.get(
                "frames_sampled",
                0
            )
        )
        performance["total_event_groups"] = to_int(
            session.get(
                "event_groups_found",
                len(groups)
            )
        )
        for metric in [
            "prepass_runtime_sec",
            "deep_analysis_runtime_sec",
            "prepass_groups_processed",
            "prepass_candidates_found",
            "deep_analysis_groups",
            "skipped_low_interest_groups"
        ]:
            performance[metric] = session.get(
                metric,
                performance.get(metric, 0)
            )
        performance["slowest_groups"] = slowest_groups(groups)

    except Exception:
        pass


def print_performance_summary(session):

    performance = session.get("performance", {})

    console.print("\n[bold cyan]Performance:[/bold cyan]")
    console.print(
        f"- Total runtime: {to_float(performance.get('total_runtime_sec')):.1f} sec"
    )
    console.print(
        f"- Videos processed: {to_int(performance.get('videos_processed'))}"
    )
    console.print(
        f"- Event groups processed: {to_int(performance.get('total_event_groups'))}"
    )
    console.print(
        f"- Prepass groups: {to_int(performance.get('prepass_groups_processed'))} processed, {to_int(performance.get('prepass_candidates_found'))} candidates"
    )
    console.print(
        f"- Deep analysis groups: {to_int(performance.get('deep_analysis_groups'))} deep, {to_int(performance.get('skipped_low_interest_groups'))} skipped"
    )
    console.print(
        f"- Avg per video: {to_float(performance.get('avg_sec_per_video')):.1f} sec"
    )
    console.print(
        f"- Frames sampled: {to_int(performance.get('frames_sampled'))}"
    )
    console.print(
        f"- AI calls: {to_int(performance.get('ai_calls'))}"
    )
    slow_stage = slowest_stage(
        performance.get(
            "stage_timings",
            {}
        )
    )
    console.print(
        f"- Slowest stage: {slow_stage.get('stage') or 'n/a'} ({to_float(slow_stage.get('runtime_sec')):.1f} sec)"
    )
    slow_groups = performance.get(
        "slowest_groups",
        []
    )
    slow_group = slow_groups[0] if slow_groups else {}
    console.print(
        f"- Slowest group: {slow_group.get('group_id') or 'n/a'} ({to_float(slow_group.get('runtime_sec')):.1f} sec)"
    )
    console.print(
        f"- YOLO time: {to_float(performance.get('yolo_runtime_sec')):.1f} sec"
    )
    console.print(
        f"- Prepass time: {to_float(performance.get('prepass_runtime_sec')):.1f} sec"
    )
    console.print(
        f"- Deep analysis time: {to_float(performance.get('deep_analysis_runtime_sec')):.1f} sec"
    )
    console.print(
        f"- AI time: {to_float(performance.get('ai_runtime_sec')):.1f} sec"
    )
    console.print(
        f"- AI review groups: {to_int(performance.get('groups_reviewed_by_ai'))} reviewed, {to_int(performance.get('groups_skipped_by_ai'))} skipped"
    )
    console.print(
        f"- Avg AI call: {to_float(performance.get('avg_ai_call_sec')):.1f} sec"
    )


# =========================================================
# MACHINE-READABLE PROGRESS
# =========================================================

def set_progress_context(total=None, current=None, current_video=None):

    try:

        if total is not None:
            PROGRESS_CONTEXT["total_videos"] = to_int(total)

        if current is not None:
            PROGRESS_CONTEXT["current_video_index"] = to_int(current)

        if current_video is not None:
            PROGRESS_CONTEXT["current_video"] = str(current_video)

    except Exception:
        pass


def progress_stage_percent(stage, current=None, total=None):

    stage_value = str(stage or "").lower()

    if stage_value == "complete":
        return 100.0

    if stage_value == "initializing":
        return 0.0

    if stage_value == "reading_clips":
        return 5.0

    if stage_value == "reading_event_metadata":
        return 10.0

    if stage_value == "grouping_camera_angles":
        return 15.0

    if stage_value in {"scanning_video", "detecting_activity"}:
        total_value = to_int(
            total,
            to_int(PROGRESS_CONTEXT.get("total_videos"))
        )
        current_value = to_int(
            current,
            to_int(PROGRESS_CONTEXT.get("current_video_index"))
        )

        if total_value <= 0:
            return 15.0

        ratio = max(
            0.0,
            min(
                1.0,
                to_float(current_value) / to_float(total_value)
            )
        )

        return 15.0 + ratio * 60.0

    if stage_value == "reviewing_suspicious_moments":
        return 75.0

    if stage_value == "building_incident_timeline":
        return 90.0

    if stage_value == "writing_output":
        return 95.0

    if stage_value == "error":
        return None

    return None


def progress_elapsed():

    if PROGRESS_STARTED_AT is None:
        return 0.0

    return max(
        0.0,
        time.perf_counter() - PROGRESS_STARTED_AT
    )


def progress_eta(elapsed_sec, percent):

    percent_value = to_float(
        percent,
        0.0
    )
    elapsed_value = to_float(
        elapsed_sec,
        0.0
    )

    if percent_value <= 3.0 or elapsed_value <= 3.0:
        return None

    estimated_total = elapsed_value / (percent_value / 100.0)
    return max(
        0.0,
        estimated_total - elapsed_value
    )


def emit_progress(stage, message, current=None, total=None, percent=None, extra=None):

    global PROGRESS_STARTED_AT
    global PROGRESS_LAST_PERCENT

    try:

        if PROGRESS_STARTED_AT is None:
            PROGRESS_STARTED_AT = time.perf_counter()

        stage_value = str(stage or "")
        computed_percent = percent

        if computed_percent is None:
            computed_percent = progress_stage_percent(
                stage_value,
                current=current,
                total=total
            )

        if computed_percent is not None:
            computed_percent = max(
                PROGRESS_LAST_PERCENT,
                min(
                    100.0,
                    to_float(computed_percent)
                )
            )
            PROGRESS_LAST_PERCENT = computed_percent

        elapsed = round(
            progress_elapsed(),
            1
        )
        eta = progress_eta(
            elapsed,
            computed_percent
        )

        performance = ACTIVE_PERFORMANCE or {}
        payload = {
            "stage": stage_value,
            "message": str(message or ""),
            "scan_engine": SCAN_ENGINE,
            "ai_review_available": bool(AI_REVIEW_AVAILABLE),
            "enhanced_ai_available": bool(AI_REVIEW_AVAILABLE),
            "elapsed_sec": elapsed,
            "eta_sec": (
                None
                if eta is None
                else round(
                    eta,
                    1
                )
            ),
            "videos_processed": to_int(
                performance.get(
                    "videos_processed",
                    PROGRESS_CONTEXT.get("current_video_index", 0)
                )
            ),
            "clips_processed": to_int(
                performance.get(
                    "videos_processed",
                    PROGRESS_CONTEXT.get("current_video_index", 0)
                )
            ),
            "incidents_created": to_int(
                performance.get(
                    "incidents_created",
                    0
                )
            ),
            "ai_calls": to_int(
                performance.get(
                    "ai_calls",
                    0
                )
            )
        }

        if current is not None:
            payload["current"] = to_int(current)

        if total is not None:
            payload["total"] = to_int(total)

        if computed_percent is not None:
            payload["percent"] = round(
                computed_percent,
                1
            )
        else:
            payload["percent"] = None

        current_video = PROGRESS_CONTEXT.get(
            "current_video"
        )

        if current_video:
            payload["current_video"] = str(current_video)

        if isinstance(extra, dict):
            payload.update(
                to_json_safe(extra)
            )

        print(
            "MIMIR_PROGRESS "
            + json.dumps(
                to_json_safe(payload),
                separators=(",", ":")
            ),
            flush=True
        )

    except Exception:
        pass


def find_event_json(video_path):

    event_json_path = os.path.join(
        os.path.dirname(video_path),
        "event.json"
    )

    if os.path.isfile(event_json_path):
        return event_json_path

    return None


def read_event_json(path):

    if not path:
        return {}

    try:

        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

    except Exception:
        return {}

    if isinstance(data, dict):
        return data

    return {
        "value": data
    }


def get_event_field(raw_event_json, *names):

    if not isinstance(raw_event_json, dict):
        return None

    fields = {
        str(key).lower(): value
        for key, value in raw_event_json.items()
    }

    for name in names:

        value = fields.get(
            name.lower()
        )

        if value is not None:
            return to_json_safe(value)

    return None


def tesla_event_metadata_for_video(path):

    event_json_path = find_event_json(path)
    raw_event_json = to_json_safe(
        read_event_json(event_json_path)
    )

    return {
        "tesla_event_raw": raw_event_json,
        "tesla_event_timestamp": get_event_field(
            raw_event_json,
            "timestamp",
            "event_timestamp",
            "eventTime",
            "event_time"
        ),
        "tesla_event_reason": get_event_field(
            raw_event_json,
            "reason",
            "event_reason",
            "trigger",
            "eventTrigger"
        ),
        "tesla_event_city": get_event_field(
            raw_event_json,
            "city"
        ),
        "tesla_event_est_lat": get_event_field(
            raw_event_json,
            "est_lat",
            "estLat",
            "latitude",
            "lat"
        ),
        "tesla_event_est_lon": get_event_field(
            raw_event_json,
            "est_lon",
            "estLon",
            "longitude",
            "lon",
            "lng"
        )
    }


def count_readable_event_json_files(event_json_paths):

    return sum(
        1
        for event_json_path in event_json_paths
        if read_event_json(event_json_path)
    )


def safe_resolve_path(path):

    try:
        return Path(path).expanduser().resolve()

    except Exception:
        return Path(path).expanduser().absolute()


def path_string_or_none(path):

    if path is None:
        return None

    return absolute_path_string(path)


def is_mp4_file(path):

    try:
        return Path(path).is_file() and Path(path).suffix.lower() == ".mp4"

    except OSError:
        return False


def find_child_dir_case_insensitive(folder, child_name):

    try:

        for child in Path(folder).iterdir():

            if child.is_dir() and child.name.lower() == child_name.lower():
                return child

    except OSError:
        return None

    return None


def nearest_teslacam_root(folder):

    current = Path(folder)

    while True:

        if current.name.lower() == "teslacam":
            return current

        if current.parent == current:
            return None

        current = current.parent


def folder_is_teslacam_source_category(folder):

    return any(
        Path(folder).name.lower() == source_folder.lower()
        for source_folder in TESLACAM_SOURCE_FOLDERS
    )


def folder_is_or_inside_teslacam_source_category(folder):

    current = Path(folder)

    while current.parent != current:

        if folder_is_teslacam_source_category(current):
            return True

        current = current.parent

    return False


def source_category_for_path(path):

    for part in reversed(Path(path).parts):

        for source_folder in TESLACAM_SOURCE_FOLDERS:

            if part.lower() == source_folder.lower():
                return source_folder

    return "Generic"


def teslacam_scan_roots(teslacam_root):

    roots = []

    for source_folder in [
        "RecentClips",
        "SavedClips",
        "SentryClips",
    ]:

        child = find_child_dir_case_insensitive(
            teslacam_root,
            source_folder
        )

        if child:
            roots.append(child)

    return roots or [Path(teslacam_root)]


def direct_mp4_files(folder):

    try:
        return [
            path
            for path in Path(folder).iterdir()
            if is_mp4_file(path)
        ]

    except OSError:
        return []


def classify_input_source(input_folder):

    selected = safe_resolve_path(input_folder)
    warnings = []

    if not selected.exists() or not selected.is_dir():
        warnings.append(
            f"Input folder does not exist: {selected}"
        )
        return {
            "selected_input": selected,
            "detected_source_type": "missing",
            "drive_root": None,
            "teslacam_root": None,
            "scan_roots": [],
            "warnings": warnings
        }

    teslacam_child = find_child_dir_case_insensitive(
        selected,
        "TeslaCam"
    )

    if teslacam_child:
        return {
            "selected_input": selected,
            "detected_source_type": "drive_root",
            "drive_root": selected,
            "teslacam_root": teslacam_child,
            "scan_roots": teslacam_scan_roots(teslacam_child),
            "warnings": warnings
        }

    if selected.name.lower() == "teslacam":
        return {
            "selected_input": selected,
            "detected_source_type": "teslacam_root",
            "drive_root": selected.parent,
            "teslacam_root": selected,
            "scan_roots": teslacam_scan_roots(selected),
            "warnings": warnings
        }

    teslacam_root = nearest_teslacam_root(selected)
    selected_direct_mp4s = direct_mp4_files(selected)
    event_json_path = selected / "event.json"

    if folder_is_teslacam_source_category(selected):
        return {
            "selected_input": selected,
            "detected_source_type": "teslacam_subfolder",
            "drive_root": teslacam_root.parent if teslacam_root else None,
            "teslacam_root": teslacam_root,
            "scan_roots": [selected],
            "warnings": warnings
        }

    if (
        folder_is_or_inside_teslacam_source_category(selected)
        and (
            selected_direct_mp4s
            or event_json_path.exists()
        )
    ):
        return {
            "selected_input": selected,
            "detected_source_type": "event_folder",
            "drive_root": teslacam_root.parent if teslacam_root else None,
            "teslacam_root": teslacam_root,
            "scan_roots": [selected],
            "warnings": warnings
        }

    if folder_is_or_inside_teslacam_source_category(selected):
        return {
            "selected_input": selected,
            "detected_source_type": "teslacam_subfolder",
            "drive_root": teslacam_root.parent if teslacam_root else None,
            "teslacam_root": teslacam_root,
            "scan_roots": [selected],
            "warnings": warnings
        }

    return {
        "selected_input": selected,
        "detected_source_type": (
            "event_folder"
            if event_json_path.exists() and selected_direct_mp4s
            else "generic_folder"
        ),
        "drive_root": teslacam_root.parent if teslacam_root else None,
        "teslacam_root": teslacam_root,
        "scan_roots": [selected],
        "warnings": warnings
    }


def discover_video_files(scan_roots):

    videos = []

    for scan_root in scan_roots:

        try:
            videos.extend(
                path
                for path in Path(scan_root).rglob("*")
                if is_mp4_file(path)
            )

        except OSError:
            continue

    return sorted(
        {
            absolute_path_string(path)
            for path in videos
        },
        key=str.lower
    )


def parse_teslacam_filename(path):

    filename = os.path.basename(path)
    match = TESLACAM_CLIP_PATTERN.match(filename)

    if not match:
        return None

    camera_name = match.group("camera").lower()
    camera = TESLACAM_CAMERA_ALIASES.get(camera_name)

    if not camera:
        return None

    event_group_id = match.group("event_group_id")
    if len(event_group_id) >= 11:
        event_group_id = event_group_id[:10] + "_" + event_group_id[11:]

    return {
        "event_group_id": event_group_id,
        "camera_name": camera
    }


def raw_teslacam_camera_suffix(path):

    filename = os.path.basename(path)
    match = TESLACAM_CLIP_PATTERN.match(filename)

    if not match:
        return None

    return match.group("camera").lower()


def teslacam_timestamp_prefix(path):

    filename = os.path.basename(path)
    match = TESLACAM_CLIP_PATTERN.match(filename)

    if not match:
        return None

    event_group_id = match.group("event_group_id")
    if len(event_group_id) >= 11:
        event_group_id = event_group_id[:10] + "_" + event_group_id[11:]

    return event_group_id


def event_folder_for_video(path, source_category):

    parent = Path(path).parent

    if source_category in TESLACAM_SOURCE_FOLDERS and parent.name == source_category:
        return None

    return parent


def source_event_metadata_from_folder(folder):

    event_json_path = Path(folder) / "event.json"

    if not event_json_path.exists() or not event_json_path.is_file():
        return {
            "event_json_path": None,
            "source_event_timestamp": None,
            "source_event_reason": None,
            "source_event_city": None,
            "source_event_est_lat": None,
            "source_event_est_lon": None,
            "source_event_raw": None
        }

    raw_event_json = to_json_safe(
        read_event_json(str(event_json_path))
    )

    if not raw_event_json:
        return {
            "event_json_path": absolute_path_string(event_json_path),
            "source_event_timestamp": None,
            "source_event_reason": None,
            "source_event_city": None,
            "source_event_est_lat": None,
            "source_event_est_lon": None,
            "source_event_raw": None
        }

    return {
        "event_json_path": absolute_path_string(event_json_path),
        "source_event_timestamp": get_event_field(
            raw_event_json,
            "timestamp",
            "event_timestamp",
            "eventTime",
            "event_time"
        ),
        "source_event_reason": get_event_field(
            raw_event_json,
            "reason",
            "event_reason",
            "trigger",
            "eventTrigger"
        ),
        "source_event_city": get_event_field(
            raw_event_json,
            "city"
        ),
        "source_event_est_lat": get_event_field(
            raw_event_json,
            "est_lat",
            "estLat",
            "latitude",
            "lat"
        ),
        "source_event_est_lon": get_event_field(
            raw_event_json,
            "est_lon",
            "estLon",
            "longitude",
            "lon",
            "lng"
        ),
        "source_event_raw": raw_event_json
    }


def build_source_event_groups(video_files):

    grouped = {}
    video_metadata = {}
    camera_suffixes = set()
    unknown_camera_suffixes = set()

    for video_path in video_files:

        source_category = source_category_for_path(video_path)
        parsed = parse_teslacam_filename(video_path)
        raw_camera = raw_teslacam_camera_suffix(video_path)
        timestamp_prefix = teslacam_timestamp_prefix(video_path)
        camera = None
        camera_group_id = None

        if parsed:
            camera = parsed["camera_name"]
            camera_group_id = parsed["event_group_id"]
            camera_suffixes.add(camera)

        elif raw_camera:
            camera = raw_camera
            unknown_camera_suffixes.add(raw_camera)

        event_folder = event_folder_for_video(
            video_path,
            source_category
        )
        event_folder_key = absolute_path_string(
            event_folder
            if event_folder
            else Path(video_path).parent
        )
        event_id = (
            timestamp_prefix
            if timestamp_prefix
            else Path(video_path).stem
        )
        group_key = (
            event_folder_key,
            event_id
        )

        if group_key not in grouped:
            source_event_metadata = source_event_metadata_from_folder(
                event_folder_key
            )
            grouped[group_key] = {
                "event_id": event_id,
                "source_category": source_category,
                "event_folder": event_folder_key,
                "timestamp": timestamp_prefix,
                "cameras_available": {},
                "camera_count": 0,
                "missing_common_cameras": [],
                "files": [],
                **source_event_metadata
            }

        group = grouped[group_key]
        group["files"].append(
            absolute_path_string(video_path)
        )

        if camera:
            group["cameras_available"][camera] = absolute_path_string(video_path)
        elif timestamp_prefix:
            group["cameras_available"].setdefault("unknown", absolute_path_string(video_path))

    event_groups = []

    for group in grouped.values():

        cameras_available = group["cameras_available"]
        files = [
            absolute_path_string(file_path)
            for file_path in group.get(
                "files",
                []
            )
        ]
        clips = []

        for file_path in files:
            parsed = parse_teslacam_filename(file_path)
            raw_camera = raw_teslacam_camera_suffix(file_path)
            camera = parsed["camera_name"] if parsed else "unknown"
            duration_sec = 0.0

            try:
                cap = cv2.VideoCapture(file_path)
                if cap.isOpened():
                    fps = valid_fps(
                        cap.get(cv2.CAP_PROP_FPS)
                    )
                    if fps <= 0:
                        fps = 30.0
                    duration_sec = video_duration_from_capture(
                        cap,
                        fps
                    )
                cap.release()

            except Exception:
                duration_sec = 0.0

            clips.append(
                {
                    "camera": camera,
                    "path": file_path,
                    "filename": os.path.basename(file_path),
                    "duration_sec": round(
                        to_float(duration_sec),
                        3
                    ),
                    "exists": os.path.exists(file_path)
                }
            )

        group["camera_count"] = to_int(
            len(cameras_available)
            if cameras_available
            else len(files)
        )
        group["clips"] = to_json_safe(clips)
        group["event_group_id"] = group["event_id"]
        group["event_timestamp"] = group["timestamp"]
        group["missing_common_cameras"] = [
            camera
            for camera in EXPECTED_TESLACAM_CAMERAS
            if camera not in cameras_available
        ]
        event_groups.append(group)

        for file_path in group["files"]:

            parsed = parse_teslacam_filename(file_path)
            raw_camera = raw_teslacam_camera_suffix(file_path)
            timestamp_prefix = teslacam_timestamp_prefix(file_path)
            video_metadata[
                absolute_path_string(file_path)
            ] = {
                "source_category": group["source_category"],
                "event_folder": group["event_folder"],
                "camera": parsed["camera_name"] if parsed else "unknown",
                "camera_group_id": timestamp_prefix or group.get("event_group_id") or group.get("event_id"),
                "raw_camera_suffix": raw_camera,
                "cameras_available": to_json_safe(cameras_available),
                "event_json_path": group["event_json_path"],
                "source_event_timestamp": group["source_event_timestamp"],
                "source_event_reason": group["source_event_reason"],
                "source_event_city": group["source_event_city"],
                "source_event_est_lat": group["source_event_est_lat"],
                "source_event_est_lon": group["source_event_est_lon"],
                "source_event_raw": group["source_event_raw"]
            }

    event_groups.sort(
        key=lambda group: (
            group["source_category"],
            group["event_folder"],
            group["timestamp"] or group["event_id"]
        )
    )

    return {
        "event_groups": to_json_safe(event_groups),
        "video_metadata": to_json_safe(video_metadata),
        "camera_suffixes_found": sorted(camera_suffixes),
        "unknown_camera_suffixes": sorted(unknown_camera_suffixes)
    }


def build_event_groups(video_files):

    return build_source_event_groups(video_files)


def discover_input_source(input_folder):

    emit_progress(
        "reading_clips",
        "Discovering footage source."
    )

    classification = classify_input_source(input_folder)
    scan_roots = classification["scan_roots"]
    video_files = discover_video_files(scan_roots)
    warnings = list(
        classification.get(
            "warnings",
            []
        )
    )

    if not video_files:
        warnings.append(
            "No mp4 video files were found in the selected source."
        )

    emit_progress(
        "reading_clips",
        f"Discovered {len(video_files)} video clips.",
        current=0,
        total=len(video_files),
        extra={
            "detected_source_type": classification["detected_source_type"]
        }
    )

    emit_progress(
        "reading_event_metadata",
        "Reading event metadata.",
        current=0,
        total=len(video_files)
    )

    emit_progress(
        "grouping_camera_angles",
        "Grouping camera angles.",
        current=0,
        total=len(video_files)
    )

    grouped = build_event_groups(video_files)
    event_groups = grouped["event_groups"]
    event_json_files = sorted(
        {
            group["event_json_path"]
            for group in event_groups
            if group.get("event_json_path")
        }
    )
    source_categories = sorted(
        {
            source_category_for_path(video_file)
            for video_file in video_files
        }
    )

    return {
        "selected_input": path_string_or_none(classification["selected_input"]),
        "detected_source_type": classification["detected_source_type"],
        "drive_root": path_string_or_none(classification["drive_root"]),
        "teslacam_root": path_string_or_none(classification["teslacam_root"]),
        "scan_roots": [
            absolute_path_string(scan_root)
            for scan_root in scan_roots
        ],
        "video_files": video_files,
        "event_groups": event_groups,
        "video_metadata": grouped["video_metadata"],
        "event_json_files": event_json_files,
        "source_categories_found": source_categories,
        "event_groups_found": to_int(len(event_groups)),
        "camera_suffixes_found": grouped["camera_suffixes_found"],
        "unknown_camera_suffixes": grouped["unknown_camera_suffixes"],
        "warnings": warnings
    }


def source_report_type(detected_source_type):

    mapping = {
        "drive_root": "usb_root",
        "teslacam_root": "teslacam_root",
        "teslacam_subfolder": "teslacam_category",
        "event_folder": "event_folder",
        "generic_folder": "generic_folder",
    }

    return mapping.get(
        str(detected_source_type or ""),
        "unknown"
    )


def source_report_categories(source_discovery):

    categories = set()

    for category in source_discovery.get(
        "source_categories_found",
        []
    ):

        if category != "Generic":
            categories.add(str(category))

    for scan_root in source_discovery.get(
        "scan_roots",
        []
    ):

        scan_root_name = Path(
            str(scan_root)
        ).name

        for source_folder in TESLACAM_SOURCE_FOLDERS:

            if scan_root_name.lower() == source_folder.lower():
                categories.add(source_folder)

    return sorted(categories)


def source_report_user_message(report_type, categories_found, mp4_count, teslacam_root_found):

    if mp4_count <= 0:
        return "No footage was found. Select the USB drive, TeslaCam folder, or a folder containing MP4 clips."

    if report_type == "usb_root" and teslacam_root_found:
        return "Mimir found a TeslaCam folder on this drive."

    if "SentryClips" in categories_found:
        return "Mimir found SentryClips and will scan sentry-style events."

    if categories_found == ["RecentClips"]:
        return "Mimir found RecentClips. These are rolling dashcam clips and may not represent saved incidents."

    if teslacam_root_found:
        return "Mimir found TeslaCam footage and is ready to scan."

    return "Mimir did not find TeslaCam folders, but found video files and will scan them as generic footage."


def build_source_report(source_discovery):

    report_type = source_report_type(
        source_discovery.get(
            "detected_source_type"
        )
    )
    categories_found = source_report_categories(source_discovery)
    mp4_count = to_int(
        len(
            source_discovery.get(
                "video_files",
                []
            )
        )
    )
    event_groups = source_discovery.get(
        "event_groups",
        []
    )
    event_groups_found = to_int(
        source_discovery.get(
            "event_groups_found",
            len(event_groups)
        )
    )
    event_json_files_found = to_int(
        len(
            source_discovery.get(
                "event_json_files",
                []
            )
        )
    )
    teslacam_root_found = bool(
        source_discovery.get(
            "teslacam_root"
        )
    )
    warnings = list(
        source_discovery.get(
            "warnings",
            []
        )
    )
    unknown_camera_suffixes = source_discovery.get(
        "unknown_camera_suffixes",
        []
    )

    if not teslacam_root_found:
        warnings.append(
            "No TeslaCam folder was found."
        )

    if teslacam_root_found and "SentryClips" not in categories_found:
        warnings.append(
            "No SentryClips folder was found."
        )

    if teslacam_root_found and "SavedClips" not in categories_found:
        warnings.append(
            "No SavedClips folder was found."
        )

    if mp4_count <= 0:
        warnings.append(
            "No mp4 files were found."
        )

    if unknown_camera_suffixes:
        warnings.append(
            "Unknown camera suffixes found: "
            + ", ".join(
                str(camera)
                for camera in unknown_camera_suffixes
            )
        )

    incomplete_groups = [
        group
        for group in event_groups
        if (
            group.get("source_category") != "Generic"
            and group.get("missing_common_cameras")
        )
    ]

    if incomplete_groups:
        warnings.append(
            f"{len(incomplete_groups)} camera group(s) are missing common camera angles."
        )

    deduped_warnings = []
    seen_warnings = set()

    for warning in warnings:

        warning_text = str(warning)

        if warning_text in seen_warnings:
            continue

        deduped_warnings.append(warning_text)
        seen_warnings.add(warning_text)

    is_supported = bool(
        mp4_count > 0
        and report_type != "unknown"
    )
    user_message = source_report_user_message(
        report_type,
        categories_found,
        mp4_count,
        teslacam_root_found
    )

    return to_json_safe(
        {
            "selected_input": source_discovery.get("selected_input"),
            "detected_source_type": report_type,
            "is_supported": is_supported,
            "teslacam_root_found": teslacam_root_found,
            "categories_found": categories_found,
            "mp4_files_found": mp4_count,
            "event_groups_found": event_groups_found,
            "camera_suffixes_found": source_discovery.get(
                "camera_suffixes_found",
                []
            ),
            "event_json_files_found": event_json_files_found,
            "warnings": deduped_warnings,
            "user_message": user_message
        }
    )


def set_source_video_metadata(video_metadata):

    global SOURCE_VIDEO_METADATA

    SOURCE_VIDEO_METADATA = {
        absolute_path_string(path): to_json_safe(metadata)
        for path, metadata in (video_metadata or {}).items()
    }


def source_discovery_metadata_for_video(path):

    return to_json_safe(
        SOURCE_VIDEO_METADATA.get(
            absolute_path_string(path),
            {}
        )
    )


def build_teslacam_event_groups(video_paths):

    groups = {}

    for path in video_paths:

        parsed = parse_teslacam_filename(path)

        if not parsed:
            continue

        event_group_id = parsed["event_group_id"]
        camera_name = parsed["camera_name"]

        group = groups.setdefault(
            event_group_id,
            {}
        )

        group[camera_name] = os.path.abspath(path)

    return groups


def teslacam_event_group_metadata_for_video(path, event_groups):

    parsed = parse_teslacam_filename(path)

    if not parsed:
        return {
            "tesla_event_group_id": None,
            "tesla_camera": None,
            "tesla_event_cameras": {},
            "tesla_event_camera_count": 0,
            "tesla_event_missing_cameras": []
        }

    event_group_id = parsed["event_group_id"]
    camera_name = parsed["camera_name"]
    cameras = event_groups.get(
        event_group_id,
        {}
    )
    missing_cameras = [
        camera
        for camera in EXPECTED_TESLACAM_CAMERAS
        if camera not in cameras
    ]

    return {
        "tesla_event_group_id": event_group_id,
        "tesla_camera": camera_name,
        "tesla_event_cameras": to_json_safe(cameras),
        "tesla_event_camera_count": to_int(len(cameras)),
        "tesla_event_missing_cameras": missing_cameras
    }


def normalize_string_list(value, fallback=None, limit=6):

    if isinstance(value, list):
        values = value

    elif value is None:
        values = []

    else:
        values = [value]

    normalized = [
        str(item).strip()
        for item in values
        if str(item).strip()
    ]

    if not normalized and fallback:
        normalized = [str(fallback)]

    return normalized[:limit]


def fallback_ai_review(reason, raw_response="", parse_error=False):

    response = raw_response.strip().upper()
    severity = "IGNORE"
    confidence = 0.25
    scene_type = "unclear"

    if response in VALID_AI_SEVERITIES:
        severity = response
        confidence = 0.5

    else:

        for legacy_severity in VALID_AI_SEVERITIES:

            if legacy_severity in response:
                severity = legacy_severity
                confidence = 0.4
                break

    return normalize_ai_review(
        {
            "scene_type": scene_type,
            "visible_person": False,
            "visible_vehicle_close": False,
            "visible_contact": False,
            "visible_impact": False,
            "normal_passing_traffic": False,
            "recommended_severity": severity,
            "confidence": confidence,
            "evidence": [reason],
            "concerns": []
        },
        raw_response=raw_response,
        parse_error=parse_error
    )


def ensure_ai_review(value):

    if not isinstance(value, dict):
        return fallback_ai_review(
            "AI review was not returned as structured data."
        )

    return normalize_ai_review(value)


def ai_review_json_fields(ai_review):

    normalized = ensure_ai_review(ai_review)

    return {
        "ai_decision": normalized["recommended_severity"],
        "ai_confidence": normalized["confidence"],
        "event_type": normalized["scene_type"],
        "summary": normalized["summary"],
        "evidence": normalized["evidence"],
        "recommended_action": normalized["recommended_action"],
        "ai_recommended_severity": normalized["recommended_severity"],
        "scene_type": normalized["scene_type"],
        "concerns": normalized["concerns"]
    }


def extract_json_response(response):

    text = response.strip()

    if text.startswith("```"):

        text = text.strip("`")

        if text.lower().startswith("json"):
            text = text[4:].strip()

    start = text.find("{")
    end = text.rfind("}")

    if start == -1 or end == -1 or end <= start:
        raise ValueError("AI response did not contain a JSON object")

    return text[start:end + 1]


def normalize_event_type(value):

    text = str(value or "unknown_event").strip().lower()

    cleaned = []

    for char in text:

        if char.isalnum():
            cleaned.append(char)

        else:
            cleaned.append("_")

    event_type = "_".join(
        part
        for part in "".join(cleaned).split("_")
        if part
    )

    return event_type or "unknown_event"


def clamp_confidence(value):

    confidence = to_float(value, 0.0)

    return max(
        0.0,
        min(
            1.0,
            confidence
        )
    )


def normalize_ai_scene_type(value):

    scene_type = normalize_event_type(
        value or "unclear"
    )

    if scene_type not in VALID_AI_SCENE_TYPES:
        scene_type = "unclear"

    return scene_type


def normalize_ai_review(data, raw_response="", parse_error=False):

    severity = str(
        data.get(
            "recommended_severity",
            data.get(
                "severity",
                "IGNORE"
            )
        )
    ).strip().upper()

    if severity not in VALID_AI_SEVERITIES:
        severity = "IGNORE"

    evidence = normalize_string_list(
        data.get("evidence", []),
        fallback="No specific evidence was returned by the AI review."
    )
    concerns = normalize_string_list(
        data.get("concerns", []),
        fallback=None
    )
    scene_type = normalize_ai_scene_type(
        data.get(
            "scene_type",
            data.get(
                "event_type",
                "unclear"
            )
        )
    )

    summary = str(data.get("summary", "")).strip()

    if not summary:
        summary = (
            f"AI evidence review scene_type={scene_type}, "
            f"recommended_severity={severity}."
        )

    recommended_action = str(data.get("recommended_action", "")).strip()

    if not recommended_action:
        recommended_action = "Review manually if needed."

    raw_text = str(
        data.get(
            "raw_response",
            raw_response or ""
        )
    )
    ai_parse_error = bool(
        data.get(
            "ai_parse_error",
            parse_error
        )
    )
    ai_review_skipped_reason = str(
        data.get(
            "ai_review_skipped_reason",
            ""
        )
    ).strip()

    return to_json_safe({
        "scene_type": scene_type,
        "visible_person": bool(data.get("visible_person", False)),
        "visible_vehicle_close": bool(data.get("visible_vehicle_close", False)),
        "visible_contact": bool(data.get("visible_contact", False)),
        "visible_impact": bool(data.get("visible_impact", False)),
        "normal_passing_traffic": bool(data.get("normal_passing_traffic", False)),
        "evidence": evidence,
        "concerns": concerns,
        "recommended_severity": severity,
        "confidence": clamp_confidence(data.get("confidence", 0.0)),
        "raw_response": raw_text,
        "ai_prompt": str(data.get("ai_prompt", "") or ""),
        "ai_parse_error": ai_parse_error,
        "ai_review_skipped_reason": ai_review_skipped_reason,
        # Backwards-compatible aliases used by older UI/reporting paths.
        "severity": severity,
        "event_type": scene_type,
        "summary": summary,
        "recommended_action": recommended_action
    })


def run_ai(image_path, impact_focused=False, contact_focused=False):

    if not AI_ENABLED or not AI_REVIEW_AVAILABLE:
        return fallback_ai_review(
            "Enhanced AI review unavailable. Standard local scan used."
        )

    prompt = """
You are analyzing Tesla Sentry footage.

The image may be a contact sheet showing START, PEAK, and END frames from the same event.
Review the full event sequence, not just one frame.
Determine what likely happened over time.
Do not overstate certainty.
Use the word "possible" when the activity is uncertain.

Return ONLY valid JSON with this exact shape:
{
  "scene_type": "person_near_vehicle",
  "visible_person": true,
  "visible_vehicle_close": false,
  "visible_contact": false,
  "visible_impact": false,
  "normal_passing_traffic": false,
  "evidence": ["Person visible near the vehicle", "No clear door contact shown"],
  "concerns": ["Interaction is partly occluded"],
  "recommended_severity": "REVIEW",
  "confidence": 0.62
}
Do not include markdown, code fences, or extra commentary.

Allowed scene_type values are normal_traffic, person_near_vehicle, possible_contact, possible_impact, unclear.
Allowed recommended_severity values are IMPORTANT, REVIEW, and IGNORE.

IMPORTANT:
- likely contact with vehicle
- possible vandalism
- person clearly interacting with door/window/handle
- possible impact/collision
- person lingering very close to vehicle

REVIEW:
- person near vehicle
- vehicle stopped nearby
- unclear movement
- uncertain possible interaction

IGNORE:
- normal traffic
- distant pedestrians
- harmless movement
- empty scene

Be conservative.
Most clips should be IGNORE.
Use REVIEW when the event is ambiguous or worth quick human review.
You are not the final classifier. Return structured visual evidence only.
""".strip()

    if impact_focused:
        prompt = (
            prompt
            + """

Impact safety note:
- This contact sheet may show a parked vehicle impact.
- Look for sudden collision/contact with the POV vehicle.
- Do not mark as IGNORE if there is possible impact, collision, or damage.
- Use IMPORTANT for likely crash, contact, impact, vandalism, or damage.
- Return valid JSON only.
""".rstrip()
        )

    if contact_focused:
        prompt = (
            prompt
            + """

Contact safety note:
- This contact sheet may show a door ding or close side contact.
- Look for subtle local movement near the side, mirror, bumper, door, handle, or nearby vehicle.
- Do not mark as IGNORE if there is possible door ding, contact, hit, scratch, damage, or vehicle touch.
- Use IMPORTANT for likely door ding, vehicle contact, side impact, vandalism, or damage.
- Return valid JSON only.
""".rstrip()
        )

    try:

        emit_progress(
            "reviewing_suspicious_moments",
            "AI review started.",
            current=PROGRESS_CONTEXT.get("current_video_index"),
            total=PROGRESS_CONTEXT.get("total_videos"),
            percent=progress_stage_percent(
                "scanning_video",
                current=PROGRESS_CONTEXT.get("current_video_index"),
                total=PROGRESS_CONTEXT.get("total_videos")
            )
        )

        with open(image_path, "rb") as f:
            img = base64.b64encode(f.read()).decode()

        add_performance_value(
            "ai_calls",
            1
        )

        ai_started = time.perf_counter()

        try:

            with profile_stage("enhanced_ai_review"):
                r = requests.post(
                    "http://localhost:11434/api/generate",
                    json={
                        "model": LLM_MODEL,
                        "prompt": prompt,
                        "images": [img],
                        "stream": False
                    },
                    timeout=60
                )

        finally:

            add_performance_value(
                "ai_runtime_sec",
                time.perf_counter() - ai_started
            )

        response = (
            r.json()
            .get("response", "IGNORE")
            .strip()
        )

        emit_progress(
            "reviewing_suspicious_moments",
            "AI review completed.",
            current=PROGRESS_CONTEXT.get("current_video_index"),
            total=PROGRESS_CONTEXT.get("total_videos"),
            percent=progress_stage_percent(
                "scanning_video",
                current=PROGRESS_CONTEXT.get("current_video_index"),
                total=PROGRESS_CONTEXT.get("total_videos")
            )
        )

        try:

            parsed = json.loads(
                extract_json_response(response)
            )

            if not isinstance(parsed, dict):
                raise ValueError("AI JSON was not an object")

            ai_review = normalize_ai_review(
                parsed,
                raw_response=response,
                parse_error=False
            )
            ai_review["ai_prompt"] = prompt
            return ai_review

        except Exception as e:

            console.print(
                f"[yellow]AI JSON FALLBACK:[/yellow] {e}"
            )

            ai_review = fallback_ai_review(
                "AI output was not valid JSON.",
                response,
                parse_error=True
            )
            ai_review["ai_prompt"] = prompt
            return ai_review

    except Exception as e:

        console.print(
            f"[red]AI ERROR:[/red] {e}"
        )
        mark_ai_review_unavailable(e)
        console.print(
            "[yellow]Enhanced AI review unavailable. Continuing with standard local scan.[/yellow]"
        )

        emit_progress(
            "reviewing_suspicious_moments",
            "AI review failed; using safe fallback.",
            current=PROGRESS_CONTEXT.get("current_video_index"),
            total=PROGRESS_CONTEXT.get("total_videos"),
            percent=progress_stage_percent(
                "scanning_video",
                current=PROGRESS_CONTEXT.get("current_video_index"),
                total=PROGRESS_CONTEXT.get("total_videos")
            )
        )

        ai_review = fallback_ai_review(
            "AI review failed."
        )
        ai_review["ai_prompt"] = prompt
        return ai_review

# =========================================================
# PROXIMITY
# =========================================================

def proximity_bonus(box, frame_width, frame_height):

    x1, y1, x2, y2 = box.xyxy[0]

    center_x = (x1 + x2) / 2
    center_y = (y1 + y2) / 2

    dx = abs(
        center_x - frame_width / 2
    ) / (frame_width / 2)

    dy = abs(
        center_y - frame_height / 2
    ) / (frame_height / 2)

    distance = (dx + dy) / 2

    return max(0, 1.0 - distance)

# =========================================================
# ANALYZE FRAME
# =========================================================

def analyze(frame, return_detections=False):

    h, w = frame.shape[:2]

    # crop away top traffic area
    frame = frame[int(h * IGNORE_TOP_RATIO):, :]

    h, w = frame.shape[:2]

    yolo_started = time.perf_counter()

    try:

        with profile_stage("yolo_detection"):
            results = yolo(frame, verbose=False)

    finally:

        add_performance_value(
            "yolo_runtime_sec",
            time.perf_counter() - yolo_started
        )

    score = 0

    persons = 0
    vehicles = 0
    detections = []

    for r in results:

        for box in r.boxes:

            conf = float(box.conf[0])

            if conf < MIN_CONF:
                continue

            cls = int(box.cls[0])

            x1, y1, x2, y2 = box.xyxy[0]
            x1 = float(x1)
            y1 = float(y1)
            x2 = float(x2)
            y2 = float(y2)

            area = float(
                (x2 - x1) *
                (y2 - y1)
            )

            area_ratio = area / (w * h)

            # ignore tiny distant detections
            if area_ratio < MIN_AREA_RATIO:
                continue

            prox = proximity_bonus(box, w, h)

            # =================================================
            # PERSON
            # =================================================

            if cls == PERSON:

                persons += 1
                detections.append({
                    "class_name": "person",
                    "class_id": cls,
                    "confidence": conf,
                    "bbox": [
                        round(x1, 2),
                        round(y1, 2),
                        round(x2, 2),
                        round(y2, 2)
                    ],
                    "proximity_score": round(to_float(prox), 3),
                    "close_to_vehicle_zone": bool(prox >= OBJECT_CLOSE_PROXIMITY_THRESHOLD)
                })

                person_score = 0

                # people matter heavily
                person_score += prox * 12

                person_score += area_ratio * 50

                if conf > 0.75:
                    person_score += 2

                score += person_score

            # =================================================
            # VEHICLES
            # =================================================

            elif cls in VEHICLES:

                vehicles += 1
                detections.append({
                    "class_name": "vehicle",
                    "class_id": cls,
                    "confidence": conf,
                    "bbox": [
                        round(x1, 2),
                        round(y1, 2),
                        round(x2, 2),
                        round(y2, 2)
                    ],
                    "proximity_score": round(to_float(prox), 3),
                    "close_to_vehicle_zone": bool(prox >= OBJECT_CLOSE_PROXIMITY_THRESHOLD)
                })

                vehicle_score = 0

                # vehicles matter much less
                vehicle_score += prox * 1.5

                vehicle_score += area_ratio * 6

                if conf > 0.75:
                    vehicle_score += 1

                score += vehicle_score

    result = (
        to_float(score),
        to_int(persons),
        to_int(vehicles)
    )

    if return_detections:
        return result + (
            detections,
        )

    return result


def bbox_area(bbox):

    if not bbox or len(bbox) != 4:
        return 0.0

    return max(
        0.0,
        to_float(bbox[2]) - to_float(bbox[0])
    ) * max(
        0.0,
        to_float(bbox[3]) - to_float(bbox[1])
    )


def bbox_iou(left, right):

    if not left or not right or len(left) != 4 or len(right) != 4:
        return 0.0

    x1 = max(to_float(left[0]), to_float(right[0]))
    y1 = max(to_float(left[1]), to_float(right[1]))
    x2 = min(to_float(left[2]), to_float(right[2]))
    y2 = min(to_float(left[3]), to_float(right[3]))
    intersection = bbox_area([x1, y1, x2, y2])
    union = bbox_area(left) + bbox_area(right) - intersection

    if union <= 0:
        return 0.0

    return intersection / union


def bbox_centroid(bbox):

    if not bbox or len(bbox) != 4:
        return (0.0, 0.0)

    return (
        (to_float(bbox[0]) + to_float(bbox[2])) / 2.0,
        (to_float(bbox[1]) + to_float(bbox[3])) / 2.0
    )


def centroid_distance(left, right):

    left_x, left_y = bbox_centroid(left)
    right_x, right_y = bbox_centroid(right)

    return math.sqrt(
        (left_x - right_x) ** 2
        + (left_y - right_y) ** 2
    )


def create_object_tracker():

    return {
        "next_track_id": 1,
        "tracks": []
    }


def create_object_track(tracker, detection, frame_index, time_sec):

    track = {
        "track_id": tracker["next_track_id"],
        "class_name": detection.get("class_name"),
        "class_id": detection.get("class_id"),
        "first_seen_time_sec": to_float(time_sec),
        "last_seen_time_sec": to_float(time_sec),
        "first_frame_index": to_int(frame_index),
        "last_frame_index": to_int(frame_index),
        "frame_count": 1,
        "consecutive_frame_count_current": 1,
        "consecutive_frame_count_max": 1,
        "confidence_values": [to_float(detection.get("confidence"))],
        "confidence_avg": to_float(detection.get("confidence")),
        "confidence_max": to_float(detection.get("confidence")),
        "bbox_first": detection.get("bbox"),
        "bbox_last": detection.get("bbox"),
        "bbox_largest": detection.get("bbox"),
        "largest_area": bbox_area(detection.get("bbox")),
        "proximity_score": to_float(detection.get("proximity_score")),
        "proximity_score_max": to_float(detection.get("proximity_score")),
        "closest_bbox_to_vehicle_zone": detection.get("bbox"),
        "close_to_vehicle_zone": bool(detection.get("close_to_vehicle_zone")),
        "missed_frames": 0
    }
    tracker["next_track_id"] += 1
    tracker["tracks"].append(track)


def update_object_track(track, detection, frame_index, time_sec):

    previous_frame_index = to_int(track.get("last_frame_index"))
    current_frame_index = to_int(frame_index)
    is_consecutive = current_frame_index > previous_frame_index

    track["last_seen_time_sec"] = to_float(time_sec)
    track["last_frame_index"] = current_frame_index
    track["frame_count"] = to_int(track.get("frame_count")) + 1
    track["bbox_last"] = detection.get("bbox")
    track["missed_frames"] = 0

    if is_consecutive:
        track["consecutive_frame_count_current"] = to_int(
            track.get("consecutive_frame_count_current")
        ) + 1
    else:
        track["consecutive_frame_count_current"] = 1

    track["consecutive_frame_count_max"] = max(
        to_int(track.get("consecutive_frame_count_max")),
        to_int(track.get("consecutive_frame_count_current"))
    )

    confidence = to_float(detection.get("confidence"))
    track.setdefault("confidence_values", []).append(confidence)
    confidence_values = track.get("confidence_values", [])
    track["confidence_avg"] = round(
        sum(confidence_values) / max(1, len(confidence_values)),
        3
    )
    track["confidence_max"] = max(
        to_float(track.get("confidence_max")),
        confidence
    )

    area = bbox_area(detection.get("bbox"))

    if area > to_float(track.get("largest_area")):
        track["largest_area"] = area
        track["bbox_largest"] = detection.get("bbox")

    proximity = to_float(detection.get("proximity_score"))
    previous_max_proximity = to_float(track.get("proximity_score_max"))
    track["proximity_score"] = round(proximity, 3)
    track["proximity_score_max"] = max(
        previous_max_proximity,
        proximity
    )
    if proximity >= previous_max_proximity:
        track["closest_bbox_to_vehicle_zone"] = detection.get("bbox")
    track["close_to_vehicle_zone"] = bool(
        track.get("close_to_vehicle_zone")
        or detection.get("close_to_vehicle_zone")
    )


def update_object_tracker(tracker, detections, frame_index, time_sec, frame_shape):

    if not isinstance(tracker, dict):
        return

    detections = [
        detection
        for detection in detections or []
        if isinstance(detection, dict)
        and detection.get("class_name") in {"person", "vehicle"}
    ]
    matched_tracks = set()
    matched_detections = set()
    height, width = frame_shape[:2]
    diagonal = max(
        1.0,
        math.sqrt(width ** 2 + height ** 2)
    )

    for detection_index, detection in enumerate(detections):
        best_track = None
        best_score = -1.0

        for track in tracker.get("tracks", []):
            track_key = to_int(track.get("track_id"))

            if track_key in matched_tracks:
                continue

            if track.get("class_name") != detection.get("class_name"):
                continue

            iou = bbox_iou(
                track.get("bbox_last"),
                detection.get("bbox")
            )
            distance_ratio = centroid_distance(
                track.get("bbox_last"),
                detection.get("bbox")
            ) / diagonal

            if (
                iou < OBJECT_TRACK_IOU_THRESHOLD
                and distance_ratio > OBJECT_TRACK_CENTROID_DISTANCE_RATIO
            ):
                continue

            score = iou + max(
                0.0,
                OBJECT_TRACK_CENTROID_DISTANCE_RATIO - distance_ratio
            )

            if score > best_score:
                best_score = score
                best_track = track

        if best_track is None:
            continue

        update_object_track(
            best_track,
            detection,
            frame_index,
            time_sec
        )
        matched_tracks.add(to_int(best_track.get("track_id")))
        matched_detections.add(detection_index)

    for track in tracker.get("tracks", []):
        if to_int(track.get("track_id")) not in matched_tracks:
            track["missed_frames"] = to_int(track.get("missed_frames")) + 1
            if to_int(track.get("missed_frames")) > OBJECT_TRACK_MAX_MISSED_FRAMES:
                track["consecutive_frame_count_current"] = 0

    for detection_index, detection in enumerate(detections):
        if detection_index not in matched_detections:
            create_object_track(
                tracker,
                detection,
                frame_index,
                time_sec
            )


def approximate_motion_direction(track):

    first = bbox_centroid(track.get("bbox_first"))
    last = bbox_centroid(track.get("bbox_last"))
    dx = last[0] - first[0]
    dy = last[1] - first[1]

    if abs(dx) < 8 and abs(dy) < 8:
        return "mostly_static"

    horizontal = "right" if dx > 0 else "left"
    vertical = "down" if dy > 0 else "up"

    if abs(dx) >= abs(dy) * 1.5:
        return horizontal

    if abs(dy) >= abs(dx) * 1.5:
        return vertical

    return f"{vertical}_{horizontal}"


def finalize_object_track(track):

    dwell_time_sec = max(
        0.0,
        to_float(track.get("last_seen_time_sec"))
        - to_float(track.get("first_seen_time_sec"))
    )
    class_name = str(track.get("class_name") or "")
    passby_max_sec = (
        PERSON_PASSBY_MAX_SEC
        if class_name == "person"
        else VEHICLE_PASSBY_MAX_SEC
    )
    movement_direction = approximate_motion_direction(track)
    entered_and_exited_quickly = bool(
        dwell_time_sec <= passby_max_sec
        or to_int(track.get("frame_count")) <= OBJECT_BRIEF_MAX_FRAMES
    )

    return to_json_safe({
        "track_id": to_int(track.get("track_id")),
        "class_name": class_name,
        "class_id": to_int(track.get("class_id")),
        "first_seen_time_sec": round(to_float(track.get("first_seen_time_sec")), 2),
        "last_seen_time_sec": round(to_float(track.get("last_seen_time_sec")), 2),
        "dwell_time_sec": round(dwell_time_sec, 2),
        "frame_count": to_int(track.get("frame_count")),
        "consecutive_frame_count_max": to_int(track.get("consecutive_frame_count_max")),
        "max_consecutive_frames": to_int(track.get("consecutive_frame_count_max")),
        "confidence_avg": round(to_float(track.get("confidence_avg")), 3),
        "confidence_max": round(to_float(track.get("confidence_max")), 3),
        "bbox_first": track.get("bbox_first"),
        "bbox_last": track.get("bbox_last"),
        "bbox_largest": track.get("bbox_largest"),
        "closest_bbox_to_vehicle_zone": track.get("closest_bbox_to_vehicle_zone"),
        "approximate_motion_direction": movement_direction,
        "movement_direction": movement_direction,
        "entered_and_exited_quickly": entered_and_exited_quickly,
        "proximity_score": round(to_float(track.get("proximity_score_max")), 3),
        "close_to_vehicle_zone": bool(track.get("close_to_vehicle_zone"))
    })


def empty_object_persistence_summary():

    return {
        "persons": {
            "track_count": 0,
            "max_dwell_time_sec": 0.0,
            "max_consecutive_frames": 0,
            "long_linger_detected": False,
            "brief_only": True,
            "passby_detected": False,
            "lingering_detected": False,
            "approaching_detected": False,
            "irrelevant_distant": True
        },
        "vehicles": {
            "track_count": 0,
            "max_dwell_time_sec": 0.0,
            "max_consecutive_frames": 0,
            "long_linger_detected": False,
            "brief_only": True,
            "passby_detected": False,
            "lingering_detected": False,
            "approaching_detected": False,
            "irrelevant_distant": True
        }
    }


def summarize_object_tracks(object_tracks):

    summary = empty_object_persistence_summary()

    for class_name, summary_key in [
        ("person", "persons"),
        ("vehicle", "vehicles")
    ]:
        tracks = [
            track
            for track in object_tracks
            if track.get("class_name") == class_name
        ]
        track_count = len(tracks)
        max_dwell = max(
            [to_float(track.get("dwell_time_sec")) for track in tracks],
            default=0.0
        )
        max_consecutive = max(
            [to_int(track.get("consecutive_frame_count_max")) for track in tracks],
            default=0
        )
        linger_min_sec = (
            PERSON_LINGER_MIN_SEC
            if class_name == "person"
            else VEHICLE_LINGER_MIN_SEC
        )
        passby_max_sec = (
            PERSON_PASSBY_MAX_SEC
            if class_name == "person"
            else VEHICLE_PASSBY_MAX_SEC
        )
        long_linger = any(
            to_float(track.get("dwell_time_sec")) >= linger_min_sec
            for track in tracks
        )
        passby_detected = any(
            bool(track.get("entered_and_exited_quickly"))
            or to_float(track.get("dwell_time_sec")) <= passby_max_sec
            or to_int(track.get("frame_count")) <= OBJECT_BRIEF_MAX_FRAMES
            for track in tracks
        )
        approaching_detected = any(
            str(track.get("movement_direction", track.get("approximate_motion_direction", "")))
            in {"down", "down_left", "down_right"}
            and bool(track.get("close_to_vehicle_zone"))
            for track in tracks
        )
        irrelevant_distant = (
            track_count == 0
            or not any(bool(track.get("close_to_vehicle_zone")) for track in tracks)
        )
        brief_only = (
            track_count == 0
            or all(
                to_float(track.get("dwell_time_sec")) <= passby_max_sec
                or to_int(track.get("frame_count")) <= OBJECT_BRIEF_MAX_FRAMES
                for track in tracks
            )
        )
        summary[summary_key] = {
            "track_count": track_count,
            "max_dwell_time_sec": round(max_dwell, 2),
            "max_consecutive_frames": max_consecutive,
            "long_linger_detected": bool(long_linger),
            "brief_only": bool(brief_only),
            "passby_detected": bool(passby_detected),
            "lingering_detected": bool(long_linger),
            "approaching_detected": bool(approaching_detected),
            "irrelevant_distant": bool(irrelevant_distant)
        }

    return to_json_safe(summary)


def finalize_object_persistence(tracker):

    if not isinstance(tracker, dict):
        object_tracks = []
    else:
        object_tracks = [
            finalize_object_track(track)
            for track in tracker.get("tracks", [])
            if to_int(track.get("frame_count")) > 0
        ]

    summary = summarize_object_tracks(object_tracks)
    brief_vehicle_only = (
        summary["vehicles"]["track_count"] > 0
        and summary["vehicles"]["brief_only"]
        and summary["persons"]["track_count"] == 0
    )
    brief_person_only = (
        summary["persons"]["track_count"] > 0
        and summary["persons"]["brief_only"]
        and summary["vehicles"]["track_count"] == 0
    )
    lingering_person_detected = bool(
        summary["persons"]["long_linger_detected"]
    )
    lingering_vehicle_detected = bool(
        summary["vehicles"]["long_linger_detected"]
    )
    person_passby_detected = bool(
        summary["persons"].get("passby_detected", False)
    )
    person_lingering_detected = bool(
        summary["persons"].get("lingering_detected", lingering_person_detected)
    )
    vehicle_passby_detected = bool(
        summary["vehicles"].get("passby_detected", False)
    )
    vehicle_lingering_detected = bool(
        summary["vehicles"].get("lingering_detected", lingering_vehicle_detected)
    )
    normal_passing_traffic_evidence = bool(
        (brief_vehicle_only or vehicle_passby_detected)
        and not lingering_person_detected
        and not lingering_vehicle_detected
    )

    return to_json_safe({
        "object_tracks": object_tracks,
        "object_persistence_summary": summary,
        "brief_vehicle_only": brief_vehicle_only,
        "brief_person_only": brief_person_only,
        "lingering_person_detected": lingering_person_detected,
        "lingering_vehicle_detected": lingering_vehicle_detected,
        "person_passby_detected": person_passby_detected,
        "person_lingering_detected": person_lingering_detected,
        "vehicle_passby_detected": vehicle_passby_detected,
        "vehicle_lingering_detected": vehicle_lingering_detected,
        "normal_passing_traffic_evidence": normal_passing_traffic_evidence
    })


def incident_object_persistence(incident):

    if not isinstance(incident, dict):
        return finalize_object_persistence(None)

    object_tracks = incident.get("object_tracks", [])

    if not isinstance(object_tracks, list):
        object_tracks = []

    summary = incident.get(
        "object_persistence_summary",
        summarize_object_tracks(object_tracks)
    )

    if not isinstance(summary, dict):
        summary = summarize_object_tracks(object_tracks)

    return to_json_safe({
        "object_tracks": object_tracks,
        "object_persistence_summary": summary,
        "brief_vehicle_only": bool(incident.get("brief_vehicle_only", False)),
        "brief_person_only": bool(incident.get("brief_person_only", False)),
        "lingering_person_detected": bool(incident.get("lingering_person_detected", False)),
        "lingering_vehicle_detected": bool(incident.get("lingering_vehicle_detected", False)),
        "person_passby_detected": bool(incident.get("person_passby_detected", False)),
        "person_lingering_detected": bool(incident.get("person_lingering_detected", False)),
        "vehicle_passby_detected": bool(incident.get("vehicle_passby_detected", False)),
        "vehicle_lingering_detected": bool(incident.get("vehicle_lingering_detected", False)),
        "normal_passing_traffic_evidence": bool(incident.get("normal_passing_traffic_evidence", False))
    })


def merge_incident_object_persistence(group_incidents):

    merged_tracks = []

    for incident_index, incident in enumerate(group_incidents or []):
        if not isinstance(incident, dict):
            continue

        camera = camera_for_incident(incident)
        persistence = incident_object_persistence(incident)

        for track in persistence.get("object_tracks", []):
            if not isinstance(track, dict):
                continue

            copied_track = dict(track)
            copied_track["camera"] = camera or copied_track.get("camera") or "unknown"
            copied_track["source_incident_id"] = incident.get("id", "")
            copied_track["group_track_id"] = (
                f"{copied_track.get('camera', 'camera')}-"
                f"{copied_track.get('track_id', incident_index)}"
            )
            merged_tracks.append(copied_track)

    summary = summarize_object_tracks(merged_tracks)
    brief_vehicle_only = (
        summary["vehicles"]["track_count"] > 0
        and summary["vehicles"]["brief_only"]
        and summary["persons"]["track_count"] == 0
    )
    brief_person_only = (
        summary["persons"]["track_count"] > 0
        and summary["persons"]["brief_only"]
        and summary["vehicles"]["track_count"] == 0
    )
    lingering_person_detected = bool(
        summary["persons"]["long_linger_detected"]
    )
    lingering_vehicle_detected = bool(
        summary["vehicles"]["long_linger_detected"]
    )
    person_passby_detected = bool(
        summary["persons"].get("passby_detected", False)
    )
    person_lingering_detected = bool(
        summary["persons"].get("lingering_detected", lingering_person_detected)
    )
    vehicle_passby_detected = bool(
        summary["vehicles"].get("passby_detected", False)
    )
    vehicle_lingering_detected = bool(
        summary["vehicles"].get("lingering_detected", lingering_vehicle_detected)
    )
    normal_passing_traffic_evidence = bool(
        (brief_vehicle_only or vehicle_passby_detected)
        and not lingering_person_detected
        and not lingering_vehicle_detected
    )

    return to_json_safe({
        "object_tracks": merged_tracks,
        "object_persistence_summary": summary,
        "brief_vehicle_only": brief_vehicle_only,
        "brief_person_only": brief_person_only,
        "lingering_person_detected": lingering_person_detected,
        "lingering_vehicle_detected": lingering_vehicle_detected,
        "person_passby_detected": person_passby_detected,
        "person_lingering_detected": person_lingering_detected,
        "vehicle_passby_detected": vehicle_passby_detected,
        "vehicle_lingering_detected": vehicle_lingering_detected,
        "normal_passing_traffic_evidence": normal_passing_traffic_evidence
    })

# =========================================================
# MOTION SIGNAL
# =========================================================

def frame_motion_score(previous_frame, current_frame):

    if previous_frame is None:
        return 0.0

    previous = cv2.resize(
        previous_frame,
        MOTION_FRAME_SIZE
    )

    current = cv2.resize(
        current_frame,
        MOTION_FRAME_SIZE
    )

    previous = cv2.cvtColor(
        previous,
        cv2.COLOR_BGR2GRAY
    )

    current = cv2.cvtColor(
        current,
        cv2.COLOR_BGR2GRAY
    )

    previous = cv2.GaussianBlur(
        previous,
        (5, 5),
        0
    )

    current = cv2.GaussianBlur(
        current,
        (5, 5),
        0
    )

    diff = cv2.absdiff(
        previous,
        current
    )

    return float(diff.mean())


def normalized_score(value, scale):

    scale_value = to_float(scale, 1.0)

    if scale_value <= 0:
        return 0.0

    return max(
        0.0,
        min(
            1.0,
            to_float(value) / scale_value
        )
    )


def optical_flow_score(previous_frame, current_frame):

    if previous_frame is None:
        return 0.0

    try:

        previous = cv2.resize(
            previous_frame,
            MOTION_FRAME_SIZE
        )

        current = cv2.resize(
            current_frame,
            MOTION_FRAME_SIZE
        )

        previous = cv2.cvtColor(
            previous,
            cv2.COLOR_BGR2GRAY
        )

        current = cv2.cvtColor(
            current,
            cv2.COLOR_BGR2GRAY
        )

        flow = cv2.calcOpticalFlowFarneback(
            previous,
            current,
            None,
            0.5,
            3,
            15,
            3,
            5,
            1.2,
            0
        )

        magnitude, _angle = cv2.cartToPolar(
            flow[..., 0],
            flow[..., 1]
        )

        return to_float(
            magnitude.mean()
        )

    except Exception:
        return 0.0


def scene_change_score(previous_frame, current_frame):

    if previous_frame is None:
        return 0.0

    try:

        previous = cv2.resize(
            previous_frame,
            MOTION_FRAME_SIZE
        )

        current = cv2.resize(
            current_frame,
            MOTION_FRAME_SIZE
        )

        previous_gray = cv2.cvtColor(
            previous,
            cv2.COLOR_BGR2GRAY
        )

        current_gray = cv2.cvtColor(
            current,
            cv2.COLOR_BGR2GRAY
        )

        diff = cv2.absdiff(
            previous_gray,
            current_gray
        )
        brightness_delta = abs(
            to_float(previous_gray.mean())
            - to_float(current_gray.mean())
        )

        previous_hist = cv2.calcHist(
            [previous_gray],
            [0],
            None,
            [32],
            [0, 256]
        )
        current_hist = cv2.calcHist(
            [current_gray],
            [0],
            None,
            [32],
            [0, 256]
        )

        cv2.normalize(previous_hist, previous_hist)
        cv2.normalize(current_hist, current_hist)

        correlation = cv2.compareHist(
            previous_hist,
            current_hist,
            cv2.HISTCMP_CORREL
        )
        histogram_delta = max(
            0.0,
            1.0 - to_float(correlation)
        ) * 40.0

        return to_float(diff.mean()) * 0.45 + brightness_delta * 0.35 + histogram_delta * 0.20

    except Exception:
        return 0.0


def local_edge_motion_score(previous_frame, current_frame):

    if previous_frame is None:
        return 0.0

    try:

        previous = cv2.resize(previous_frame, MOTION_FRAME_SIZE)
        current = cv2.resize(current_frame, MOTION_FRAME_SIZE)

        previous_gray = cv2.cvtColor(previous, cv2.COLOR_BGR2GRAY)
        current_gray = cv2.cvtColor(current, cv2.COLOR_BGR2GRAY)
        diff = cv2.absdiff(previous_gray, current_gray)

        height, width = diff.shape[:2]
        side_width = max(
            1,
            int(width * 0.22)
        )
        band_height = max(
            1,
            int(height * 0.18)
        )

        regions = [
            diff[:, :side_width],
            diff[:, width - side_width:],
            diff[:band_height, :],
            diff[height - band_height:, :],
        ]

        return max(
            to_float(region.mean())
            for region in regions
        )

    except Exception:
        return 0.0


def crash_safety_trigger(
    motion_score,
    scene_score,
    flow_score,
    recent_motion_scores,
    vehicle_count=0,
    proximity_score=0.0
):

    if recent_motion_scores:
        baseline_motion = sum(
            to_float(value)
            for value in recent_motion_scores
        ) / max(
            1,
            len(recent_motion_scores)
        )
    else:
        baseline_motion = 0.0

    motion_value = to_float(motion_score)
    scene_value = to_float(scene_score)
    flow_value = to_float(flow_score)
    vehicle_count_value = to_int(vehicle_count)
    proximity_value = to_float(proximity_score)
    impact_spike_score = (
        motion_value
        + scene_value
        + flow_value * 2.0
    )
    reasons = []

    mostly_static_before_spike = (
        len(recent_motion_scores) >= 3
        and baseline_motion <= CRASH_STATIC_BASELINE_THRESHOLD
        and motion_value >= CRASH_GLOBAL_MOTION_TRIGGER * 0.85
        and scene_value >= CRASH_SCENE_CHANGE_TRIGGER * 0.70
    )

    strong_global_scene_change = (
        motion_value >= CRASH_GLOBAL_MOTION_TRIGGER
        and scene_value >= CRASH_SCENE_CHANGE_TRIGGER
    )

    strong_scene_flow_change = (
        scene_value >= CRASH_SCENE_CHANGE_TRIGGER * 1.35
        and flow_value >= CRASH_FLOW_TRIGGER
    )

    abrupt_static_scene_change = (
        len(recent_motion_scores) >= 3
        and baseline_motion <= CRASH_STATIC_BASELINE_THRESHOLD
        and scene_value >= CRASH_SCENE_CHANGE_TRIGGER * 1.20
        and motion_value >= CRASH_GLOBAL_MOTION_TRIGGER * 0.35
    )

    strong_impact_spike = (
        impact_spike_score >= CRASH_IMPACT_SPIKE_TRIGGER
        and (
            motion_value >= CRASH_GLOBAL_MOTION_TRIGGER * 0.75
            or scene_value >= CRASH_SCENE_CHANGE_TRIGGER
        )
    )

    vehicle_flow_after_static = (
        len(recent_motion_scores) >= 3
        and baseline_motion <= CRASH_STATIC_BASELINE_THRESHOLD
        and vehicle_count_value >= 3
        and proximity_value >= 4.0
        and flow_value >= CRASH_FLOW_TRIGGER * 1.25
        and (
            scene_value >= CRASH_SCENE_CHANGE_TRIGGER * 0.35
            or motion_value >= CRASH_GLOBAL_MOTION_TRIGGER * 0.35
        )
    )

    if mostly_static_before_spike:
        reasons.append(
            "sudden motion after mostly static frames"
        )

    if strong_global_scene_change:
        reasons.append(
            "strong sudden global motion and scene change"
        )

    if strong_scene_flow_change:
        reasons.append(
            "abrupt scene change with optical flow spike"
        )

    if abrupt_static_scene_change:
        reasons.append(
            "abrupt scene change after mostly static frames"
        )

    if strong_impact_spike:
        reasons.append(
            "impact-like motion spike detected"
        )

    if vehicle_flow_after_static:
        reasons.append(
            "vehicle-only optical flow spike after mostly static frames"
        )

    triggered = bool(reasons)

    return to_json_safe({
        "triggered": triggered,
        "motion_triggered": triggered,
        "crash_safety_triggered": triggered,
        "trigger_reasons": reasons,
        "impact_spike_score": round(
            to_float(impact_spike_score),
            2
        ),
        "baseline_motion_score": round(
            to_float(baseline_motion),
            2
        )
    })


def impact_level_for_score(impact_score):

    score = to_float(impact_score)

    if score >= 0.70:
        return "HIGH"

    if score >= 0.45:
        return "MEDIUM"

    if score >= 0.20:
        return "LOW"

    return "NONE"


def contact_level_for_score(contact_score):

    score = to_float(contact_score)

    if score >= 0.68:
        return "HIGH"

    if score >= 0.45:
        return "MEDIUM"

    if score >= 0.20:
        return "LOW"

    return "NONE"


def source_suggests_side_camera(path):

    text = os.path.basename(
        str(path or "")
    ).lower()

    if text_has_negated_high_risk(text):
        return False

    return any(
        token in text
        for token in [
            "left_repeater",
            "right_repeater",
            "left_pillar",
            "right_pillar",
            "-left",
            "-right",
            "side",
        ]
    )


def build_contact_analysis(
    path,
    max_motion_score,
    contact_time_sec,
    local_motion_score,
    optical_flow_score_value,
    scene_change_score_value,
    proximity_score,
    persons=0,
    vehicles=0
):

    proximity_component = normalized_score(
        proximity_score,
        PROXIMITY_SCORE_SCALE
    )
    local_motion_component = normalized_score(
        local_motion_score,
        LOCAL_EDGE_MOTION_SCORE_SCALE
    )
    motion_component = normalized_score(
        max_motion_score,
        MOTION_SPIKE_THRESHOLD
    )
    flow_component = normalized_score(
        optical_flow_score_value,
        OPTICAL_FLOW_SCORE_SCALE
    )
    scene_component = normalized_score(
        scene_change_score_value,
        SCENE_CHANGE_SCORE_SCALE
    )
    person_component = normalized_score(
        persons,
        5.0
    )
    vehicle_component = normalized_score(
        vehicles,
        32.0
    )
    side_camera_component = 1.0 if source_suggests_side_camera(path) else 0.0

    contact_score = (
        proximity_component * 0.34
        + local_motion_component * 0.24
        + motion_component * 0.16
        + flow_component * 0.08
        + scene_component * 0.06
        + max(person_component, vehicle_component) * 0.08
        + side_camera_component * 0.04
    )

    if (
        proximity_component >= 0.75
        and local_motion_component >= 0.55
    ):
        contact_score = max(
            contact_score,
            0.72
        )

    elif (
        proximity_component >= 0.70
        and motion_component >= 0.55
    ):
        contact_score = max(
            contact_score,
            0.58
        )

    elif (
        proximity_component >= 0.55
        and local_motion_component >= 0.35
        and (to_int(persons) > 0 or to_int(vehicles) > 0)
    ):
        contact_score = max(
            contact_score,
            0.48
        )

    contact_score = max(
        0.0,
        min(
            1.0,
            contact_score
        )
    )
    contact_level = contact_level_for_score(
        contact_score
    )
    possible_contact = contact_level in {
        "MEDIUM",
        "HIGH"
    }

    reasons = []

    if proximity_component >= 0.70:
        reasons.append(
            "object detected very close to the parked vehicle"
        )

    if local_motion_component >= 0.45:
        reasons.append(
            "local edge motion suggests possible side contact"
        )

    if motion_component >= 0.55 and proximity_component >= 0.55:
        reasons.append(
            "proximity plus motion spike suggests possible contact"
        )

    if to_int(persons) > 0 and proximity_component >= 0.60:
        reasons.append(
            "person close to vehicle during contact-like movement"
        )

    if to_int(vehicles) > 0 and proximity_component >= 0.60:
        reasons.append(
            "vehicle close to POV vehicle during contact-like movement"
        )

    if side_camera_component > 0:
        reasons.append(
            "side camera clip can reveal subtle door or side contact"
        )

    if possible_contact and not reasons:
        reasons.append(
            "combined proximity and motion signals suggest possible contact"
        )

    return to_json_safe({
        "contact_time_sec": (
            None
            if contact_time_sec is None
            else round(
                to_float(contact_time_sec),
                2
            )
        ),
        "local_motion_score": round(
            to_float(local_motion_score),
            2
        ),
        "contact_score": round(
            contact_score,
            2
        ),
        "contact_level": contact_level,
        "possible_contact": possible_contact,
        "contact_reasons": reasons
    })


def build_impact_analysis(
    max_motion_score,
    motion_spike_time_sec,
    camera_shake_score,
    optical_flow_score_value,
    scene_change_score_value,
    proximity_score,
    persons=0,
    vehicles=0,
    crash_safety_triggered=False,
    motion_triggered=False,
    trigger_reasons=None
):

    motion_component = normalized_score(
        max_motion_score,
        MOTION_SPIKE_THRESHOLD * 2.0
    )

    camera_component = normalized_score(
        camera_shake_score,
        CAMERA_SHAKE_SCORE_SCALE
    )

    flow_component = normalized_score(
        optical_flow_score_value,
        OPTICAL_FLOW_SCORE_SCALE
    )

    proximity_component = normalized_score(
        proximity_score,
        PROXIMITY_SCORE_SCALE
    )

    scene_component = normalized_score(
        scene_change_score_value,
        SCENE_CHANGE_SCORE_SCALE
    )

    vehicle_count_component = normalized_score(
        vehicles,
        35.0
    )

    impact_score = (
        motion_component * 0.35
        + camera_component * 0.15
        + flow_component * 0.15
        + scene_component * 0.25
        + proximity_component * 0.10
    )

    vehicle_only_impact_score = 0.0

    if (
        to_int(vehicles) >= 8
        and to_int(persons) == 0
        and (
            motion_component >= 0.25
            or scene_component >= 0.18
            or flow_component >= 0.20
        )
    ):
        vehicle_only_impact_score = (
            motion_component * 0.40
            + scene_component * 0.30
            + flow_component * 0.15
            + vehicle_count_component * 0.15
        )

        if vehicle_only_impact_score >= 0.30:
            impact_score = max(
                impact_score,
                0.72
            )

        elif vehicle_only_impact_score >= 0.22:
            impact_score = max(
                impact_score,
                0.50
            )

    trigger_reasons = [
        str(reason)
        for reason in (
            trigger_reasons
            if isinstance(trigger_reasons, list)
            else []
        )
    ]
    crash_safety_triggered = bool(crash_safety_triggered)
    motion_triggered = bool(motion_triggered or crash_safety_triggered)

    if vehicle_only_impact_score >= 0.30:
        crash_safety_triggered = True
        motion_triggered = True
        vehicle_only_reason = (
            "vehicle-only sudden motion suggests possible front or rear impact"
        )
        if vehicle_only_reason not in trigger_reasons:
            trigger_reasons.append(
                vehicle_only_reason
            )

    if crash_safety_triggered:
        if (
            motion_component >= 0.55
            and scene_component >= 0.45
        ) or (
            scene_component >= 0.60
            and flow_component >= 0.30
        ):
            impact_score = max(
                impact_score,
                0.76
            )
        else:
            impact_score = max(
                impact_score,
                0.52
            )

    impact_score = max(
        0.0,
        min(
            1.0,
            impact_score
        )
    )

    impact_level = impact_level_for_score(
        impact_score
    )

    reasons = []

    reasons.extend(trigger_reasons)

    if motion_component >= 0.50:
        reasons.append(
            "possible sudden movement detected"
        )

    if camera_component >= 0.45:
        reasons.append(
            "camera shake suggests possible contact event"
        )

    if flow_component >= 0.35:
        reasons.append(
            "optical flow indicates possible sudden movement"
        )

    if scene_component >= 0.45:
        reasons.append(
            "abrupt scene or brightness change suggests possible collision"
        )

    if proximity_component >= 0.40:
        reasons.append(
            "nearby or large object activity suggests possible contact event"
        )

    if (
        motion_component >= 0.60
        and scene_component >= 0.45
    ):
        reasons.append(
            "strong sudden global motion and scene change suggest possible parked-vehicle impact"
        )

    if (
        scene_component >= 0.65
        and (
            camera_component >= 0.45
            or flow_component >= 0.35
        )
    ):
        reasons.append(
            "impact-like scene change with camera movement detected"
        )

    if vehicle_only_impact_score >= 0.30:
        reasons.append(
            "vehicle-only sudden motion suggests possible front or rear impact"
        )

    elif vehicle_only_impact_score >= 0.22:
        reasons.append(
            "vehicle activity and scene movement suggest possible contact event"
        )

    possible_impact = impact_level in {
        "MEDIUM",
        "HIGH"
    }

    if possible_impact and not reasons:
        reasons.append(
            "combined motion signals suggest possible sudden movement"
        )

    return to_json_safe({
        "crash_safety_triggered": crash_safety_triggered,
        "motion_triggered": motion_triggered,
        "trigger_reasons": trigger_reasons,
        "motion_spike_time_sec": (
            None
            if motion_spike_time_sec is None
            else round(
                to_float(motion_spike_time_sec),
                2
            )
        ),
        "camera_shake_score": round(
            to_float(camera_shake_score),
            2
        ),
        "optical_flow_score": round(
            to_float(optical_flow_score_value),
            2
        ),
        "scene_change_score": round(
            to_float(scene_change_score_value),
            2
        ),
        "proximity_score": round(
            to_float(proximity_score),
            2
        ),
        "vehicle_only_impact_score": round(
            to_float(vehicle_only_impact_score),
            2
        ),
        "impact_score": round(
            impact_score,
            2
        ),
        "impact_level": impact_level,
        "possible_impact": possible_impact,
        "impact_reasons": reasons
    })


def ai_clearly_ignored(ai_review):

    return (
        ai_review["severity"] == "IGNORE"
        and ai_review["confidence"] >= AI_CLEAR_IGNORE_CONFIDENCE
    )


def severity_priority(label):

    value = str(
        label or "IGNORE"
    ).upper()

    if value == "IMPORTANT":
        return 2

    if value == "REVIEW":
        return 1

    return 0


def priority_label(priority):

    value = to_int(
        priority,
        0
    )

    if value >= 2:
        return "IMPORTANT"

    if value == 1:
        return "REVIEW"

    return "IGNORE"


def priority_color(priority):

    value = to_int(
        priority,
        0
    )

    if value >= 2:
        return "red"

    if value == 1:
        return "yellow"

    return "green"


def normalized_text_blob(*parts):

    text_parts = []

    for part in parts:

        if part is None:
            continue

        if isinstance(part, list):
            text_parts.extend(
                str(item)
                for item in part
            )
            continue

        text_parts.append(
            str(part)
        )

    return " ".join(text_parts).lower()


HIGH_RISK_EVENT_KEYWORDS = [
    "impact",
    "collision",
    "contact",
    "vandalism",
    "attempted_entry",
    "attempted entry",
    "door_interaction",
    "door interaction",
    "handle_interaction",
    "handle interaction",
    "window_interaction",
    "window interaction",
    "object_thrown",
    "object thrown",
    "person_touching_vehicle",
    "person touching vehicle",
]

HIGH_RISK_TEXT_KEYWORDS = [
    "contact with vehicle",
    "hit the vehicle",
    "hits the vehicle",
    "struck the vehicle",
    "collision",
    "impact",
    "vandalism",
    "vandal",
    "door handle",
    "handle",
    "attempted entry",
    "trying to enter",
    "object thrown",
    "threw object",
    "damage",
    "damaged",
    "touching the vehicle",
    "touched the vehicle",
]

CONTACT_TEXT_KEYWORDS = [
    "door ding",
    "ding",
    "contact",
    "door",
    "handle",
    "hit",
    "hits",
    "touch",
    "touched",
    "scratch",
    "scratched",
    "damage",
    "damaged",
    "mirror",
    "side",
    "bumper",
    "side impact",
    "vehicle touched",
    "vehicle contact",
]

POSSIBLE_INTERACTION_KEYWORDS = [
    "possible interaction",
    "possible contact",
    "near the vehicle",
    "close to the vehicle",
    "lingering near",
    "reached toward",
    "approached the vehicle",
]

PERSON_INTERACTION_KEYWORDS = [
    "touching the vehicle",
    "touched the vehicle",
    "touches the vehicle",
    "clear contact",
    "door handle",
    "handle",
    "trying door",
    "tries door",
    "trying to open",
    "attempted entry",
    "trying to enter",
    "leaning into",
    "leans into",
    "tamper",
    "tampering",
    "vandal",
    "vandalism",
]

PERSON_NEAR_ONLY_KEYWORDS = [
    "person near vehicle",
    "person_near_vehicle",
    "near the vehicle",
    "close to the vehicle",
    "walking near",
    "walking past",
    "walks past",
    "passes by",
    "passing by",
]

NORMAL_TRAFFIC_KEYWORDS = [
    "normal traffic",
    "distant pedestrian",
    "distant pedestrians",
    "harmless movement",
    "empty scene",
    "passing traffic",
    "walking past",
    "walks past",
    "passes by",
    "passing by",
]


def text_has_keyword(text, keywords):

    return any(
        keyword in text
        for keyword in keywords
    )


NEGATED_HIGH_RISK_PATTERNS = [
    r"\bno\s+(clear|visible|obvious|confirmed)?\s*(door\s+)?(contact|impact|collision|damage|interaction)\b",
    r"\bnot\s+(clear|visible|obvious|confirmed)?\s*(door\s+)?(contact|impact|collision|damage|interaction)\b",
    r"\bwithout\s+(clear\s+)?(contact|impact|collision|damage|interaction)\b",
    r"\bdoes\s+not\s+show\s+(clear\s+)?(contact|impact|collision|damage|interaction)\b",
    r"\bno\s+evidence\s+of\s+(contact|impact|collision|damage|interaction)\b",
]


def text_has_negated_high_risk(text):

    return any(
        re.search(pattern, text)
        for pattern in NEGATED_HIGH_RISK_PATTERNS
    )


def build_local_evidence_summary(
    event_score,
    motion_score,
    impact_analysis,
    contact_analysis,
    persons,
    vehicles,
    active_frames,
    object_persistence=None,
    source_discovery_metadata=None,
    tesla_event_metadata=None,
    tesla_event_group_metadata=None
):

    source_discovery_metadata = source_discovery_metadata or {}
    tesla_event_metadata = tesla_event_metadata or {}
    tesla_event_group_metadata = tesla_event_group_metadata or {}
    object_persistence = object_persistence or {}
    camera = (
        source_discovery_metadata.get("camera")
        or tesla_event_group_metadata.get("tesla_camera")
        or tesla_event_metadata.get("tesla_camera")
        or "unknown"
    )

    camera_evidence = {
        "camera": camera,
        "event_group_id": source_discovery_metadata.get(
            "camera_group_id",
            tesla_event_group_metadata.get("tesla_event_group_id")
        ),
        "available_cameras": source_discovery_metadata.get(
            "cameras_found",
            tesla_event_group_metadata.get("tesla_event_cameras", [])
        )
    }

    return to_json_safe({
        "motion_score": round(to_float(motion_score), 2),
        "impact_score": round(to_float(impact_analysis.get("impact_score", 0.0)), 2),
        "impact_level": str(impact_analysis.get("impact_level", "NONE")).upper(),
        "contact_score": round(to_float(contact_analysis.get("contact_score", 0.0)), 2),
        "contact_level": str(contact_analysis.get("contact_level", "NONE")).upper(),
        "person_passby_detected": bool(object_persistence.get("person_passby_detected", False)),
        "person_lingering_detected": bool(object_persistence.get("person_lingering_detected", False)),
        "vehicle_passby_detected": bool(object_persistence.get("vehicle_passby_detected", False)),
        "vehicle_lingering_detected": bool(object_persistence.get("vehicle_lingering_detected", False)),
        "person_proximity": {
            "persons_detected": to_int(persons),
            "proximity_score": round(to_float(impact_analysis.get("proximity_score", 0.0)), 2)
        },
        "vehicle_proximity": {
            "vehicles_detected": to_int(vehicles),
            "proximity_score": round(to_float(impact_analysis.get("proximity_score", 0.0)), 2)
        },
        "camera_shake": round(to_float(impact_analysis.get("camera_shake_score", 0.0)), 2),
        "scene_change": round(to_float(impact_analysis.get("scene_change_score", 0.0)), 2),
        "event_metadata": {
            "event_score": round(to_float(event_score), 2),
            "active_frames": to_int(active_frames),
            "crash_safety_triggered": bool(impact_analysis.get("crash_safety_triggered", False)),
            "motion_triggered": bool(impact_analysis.get("motion_triggered", False)),
            "possible_impact": bool(impact_analysis.get("possible_impact", False)),
            "possible_contact": bool(contact_analysis.get("possible_contact", False)),
            "trigger_reasons": normalize_string_list(impact_analysis.get("trigger_reasons", []), limit=8),
            "impact_reasons": normalize_string_list(impact_analysis.get("impact_reasons", []), limit=8),
            "contact_reasons": normalize_string_list(contact_analysis.get("contact_reasons", []), limit=8)
        },
        "object_persistence": {
            "summary": object_persistence.get("object_persistence_summary", empty_object_persistence_summary()),
            "brief_vehicle_only": bool(object_persistence.get("brief_vehicle_only", False)),
            "brief_person_only": bool(object_persistence.get("brief_person_only", False)),
            "lingering_person_detected": bool(object_persistence.get("lingering_person_detected", False)),
            "lingering_vehicle_detected": bool(object_persistence.get("lingering_vehicle_detected", False)),
            "person_passby_detected": bool(object_persistence.get("person_passby_detected", False)),
            "person_lingering_detected": bool(object_persistence.get("person_lingering_detected", False)),
            "vehicle_passby_detected": bool(object_persistence.get("vehicle_passby_detected", False)),
            "vehicle_lingering_detected": bool(object_persistence.get("vehicle_lingering_detected", False)),
            "normal_passing_traffic_evidence": bool(object_persistence.get("normal_passing_traffic_evidence", False))
        },
        "camera_angle_evidence": camera_evidence
    })


def resolve_final_severity(
    current_severity,
    ai_review,
    impact_analysis,
    contact_analysis,
    max_motion_score,
    persons,
    vehicles,
    active_frames,
    timeline_markers,
    object_persistence=None
):

    normalized_ai = ensure_ai_review(ai_review)
    object_persistence = object_persistence or {}
    ai_fields = ai_review_json_fields(normalized_ai)
    pre_severity = priority_label(
        severity_priority(
            current_severity
        )
    )
    reasons = []
    blocked_reason = ""
    ai_allowed_to_change = False
    final_decision_source = "local_rules"

    event_type_text = str(ai_fields.get("event_type", "")).lower()
    ai_text_blob = normalized_text_blob(
        ai_fields.get("summary"),
        ai_fields.get("evidence", []),
        ai_fields.get("concerns", [])
    )
    combined_review_blob = normalized_text_blob(
        event_type_text,
        ai_text_blob
    )

    impact_level = str(impact_analysis.get("impact_level", "NONE")).upper()
    impact_score = to_float(impact_analysis.get("impact_score", 0.0))
    possible_impact = bool(impact_analysis.get("possible_impact", False))
    impact_reasons = impact_analysis.get("impact_reasons", [])
    crash_safety_triggered = bool(impact_analysis.get("crash_safety_triggered", False))
    motion_triggered = bool(impact_analysis.get("motion_triggered", False))
    trigger_reasons = impact_analysis.get("trigger_reasons", [])
    contact_level = str(contact_analysis.get("contact_level", "NONE")).upper()
    contact_score = to_float(contact_analysis.get("contact_score", 0.0))
    possible_contact = bool(contact_analysis.get("possible_contact", False))
    contact_reasons = contact_analysis.get("contact_reasons", [])
    ai_severity = str(ai_fields.get("ai_decision", "IGNORE")).upper()
    ai_confidence = to_float(ai_fields.get("ai_confidence", 0.0))
    visible_person = bool(normalized_ai.get("visible_person", False))
    visible_vehicle_close = bool(normalized_ai.get("visible_vehicle_close", False))
    visible_contact = bool(normalized_ai.get("visible_contact", False))
    visible_impact = bool(normalized_ai.get("visible_impact", False))
    normal_passing_traffic = bool(normalized_ai.get("normal_passing_traffic", False))
    brief_vehicle_only = bool(object_persistence.get("brief_vehicle_only", False))
    brief_person_only = bool(object_persistence.get("brief_person_only", False))
    lingering_person_detected = bool(object_persistence.get("lingering_person_detected", False))
    lingering_vehicle_detected = bool(object_persistence.get("lingering_vehicle_detected", False))
    person_passby_detected = bool(object_persistence.get("person_passby_detected", False))
    person_lingering_detected = bool(
        object_persistence.get("person_lingering_detected", lingering_person_detected)
    )
    vehicle_passby_detected = bool(object_persistence.get("vehicle_passby_detected", False))
    vehicle_lingering_detected = bool(
        object_persistence.get("vehicle_lingering_detected", lingering_vehicle_detected)
    )
    normal_passing_traffic_evidence = bool(object_persistence.get("normal_passing_traffic_evidence", False))

    ai_text_has_high_risk = (
        text_has_keyword(ai_text_blob, HIGH_RISK_TEXT_KEYWORDS)
        and not text_has_negated_high_risk(ai_text_blob)
    )
    ai_text_has_possible_interaction = (
        text_has_keyword(ai_text_blob, POSSIBLE_INTERACTION_KEYWORDS)
        and not text_has_negated_high_risk(ai_text_blob)
    )
    ai_text_has_person_interaction = (
        text_has_keyword(combined_review_blob, PERSON_INTERACTION_KEYWORDS)
        and not text_has_negated_high_risk(combined_review_blob)
    )
    ai_text_has_person_near_only = (
        text_has_keyword(combined_review_blob, PERSON_NEAR_ONLY_KEYWORDS)
        and not ai_text_has_person_interaction
        and not text_has_negated_high_risk(combined_review_blob)
    )
    ai_text_has_contact_language = (
        text_has_keyword(ai_text_blob, CONTACT_TEXT_KEYWORDS)
        and not text_has_negated_high_risk(ai_text_blob)
    )
    has_normal_only_language = (
        normal_passing_traffic
        or (
        text_has_keyword(ai_text_blob, NORMAL_TRAFFIC_KEYWORDS)
        and not ai_text_has_high_risk
        and not possible_contact
        and impact_level not in {"MEDIUM", "HIGH"}
        )
    )

    important_marker_found = any(
        isinstance(marker, dict)
        and marker.get("severity") == "IMPORTANT"
        and marker.get("type") in {"possible_impact", "possible_contact"}
        for marker in timeline_markers
    )
    suspicious_marker_found = any(
        isinstance(marker, dict)
        and marker.get("type") in {"possible_impact", "possible_contact"}
        for marker in timeline_markers
    )

    if impact_level == "HIGH":
        reasons.append("impact_level=HIGH")

    if impact_score >= 0.75:
        reasons.append("impact_score>=0.75")

    if crash_safety_triggered:
        reasons.append("crash_safety_triggered prevents IGNORE")

    if motion_triggered and trigger_reasons:
        reasons.append("motion trigger found impact-like frame change")

    if impact_level == "MEDIUM" and pre_severity == "IGNORE":
        reasons.append("impact_level=MEDIUM prevents IGNORE")

    if contact_level == "HIGH":
        reasons.append("contact_level=HIGH")

    if contact_level == "MEDIUM" and pre_severity == "IGNORE":
        reasons.append("contact_level=MEDIUM prevents IGNORE")

    if possible_contact and contact_score >= 0.55:
        reasons.append("possible_contact with high contact_score")

    if possible_impact and impact_score >= 0.58:
        reasons.append("possible_impact with high impact_score")

    if (
        event_type_text != "ai_review_fallback"
        and text_has_keyword(event_type_text, HIGH_RISK_EVENT_KEYWORDS)
    ):
        reasons.append("AI scene_type suggests contact, impact, entry, or vandalism")

    if ai_text_has_high_risk:
        reasons.append("AI evidence suggests possible contact with vehicle")

    if visible_contact:
        reasons.append("AI visible_contact=true")

    if visible_impact:
        reasons.append("AI visible_impact=true")

    if visible_person:
        reasons.append("AI visible_person=true")

    if visible_vehicle_close:
        reasons.append("AI visible_vehicle_close=true")

    if normal_passing_traffic_evidence:
        reasons.append("object persistence suggests brief normal passing traffic")

    if lingering_person_detected or person_lingering_detected:
        reasons.append("lingering person detected")

    if lingering_vehicle_detected or vehicle_lingering_detected:
        reasons.append("lingering vehicle detected")

    contact_hard_evidence = (
        visible_contact
        or contact_level == "HIGH"
        or contact_score >= 0.68
        or ai_text_has_person_interaction
    )
    impact_hard_evidence = (
        visible_impact
        or impact_level == "HIGH"
        or impact_score >= 0.75
        or crash_safety_triggered
    )
    strong_motion_spike = (
        to_float(max_motion_score) >= MOTION_SPIKE_THRESHOLD
        or to_float(max_motion_score) >= CRASH_GLOBAL_MOTION_TRIGGER
        or motion_triggered
    )
    person_interaction_evidence = bool(ai_text_has_person_interaction)
    person_presence_evidence = (
        to_int(persons) > 0
        or visible_person
        or event_type_text == "person_near_vehicle"
        or ai_text_has_person_near_only
    )
    person_passby_evidence = (
        brief_person_only
        or person_passby_detected
        or normal_passing_traffic
        or normal_passing_traffic_evidence
        or text_has_keyword(combined_review_blob, ["walking past", "walks past", "passes by", "passing by"])
    )
    important_evidence_found = bool(
        contact_hard_evidence
        or impact_hard_evidence
        or strong_motion_spike
        or person_interaction_evidence
    )
    person_near_only = bool(
        person_presence_evidence
        and not important_evidence_found
        and contact_level in {"NONE", "LOW", "MEDIUM"}
        and impact_level in {"NONE", "LOW", "MEDIUM"}
        and not possible_impact
        and not (
            possible_contact
            and contact_level == "HIGH"
        )
    )

    if person_near_only and not person_interaction_evidence:
        reasons.append("Person visible near vehicle, but no contact or tampering evidence.")

    if (
        contact_level == "MEDIUM"
        and event_type_text != "ai_review_fallback"
        and (
            ai_text_has_contact_language
            or visible_contact
            or text_has_keyword(event_type_text, CONTACT_TEXT_KEYWORDS)
        )
    ):
        reasons.append("contact_level=MEDIUM with door ding or contact language")

    if important_marker_found:
        reasons.append("timeline marker indicates IMPORTANT possible contact or impact")

    if suspicious_marker_found and pre_severity == "REVIEW" and possible_impact:
        reasons.append("timeline marker and impact analysis both indicate possible contact event")

    if (
        ai_severity == "REVIEW"
        and possible_impact
        and impact_reasons
    ):
        reasons.append("AI REVIEW plus impact reasons")

    if (
        ai_severity in {"REVIEW", "IGNORE"}
        and possible_contact
        and contact_reasons
    ):
        reasons.append("AI did not override contact safety signals")

    if (
        ai_severity == "REVIEW"
        and (
            visible_contact
            or visible_impact
            or text_has_keyword(event_type_text, HIGH_RISK_EVENT_KEYWORDS)
            or ai_text_has_high_risk
            or ai_text_has_contact_language
        )
    ):
        reasons.append("AI REVIEW with high-risk interaction language")

    if (
        pre_severity == "REVIEW"
        and to_int(persons) > 0
        and to_int(active_frames) >= 8
        and ai_text_has_possible_interaction
        and not has_normal_only_language
    ):
        reasons.append("person activity near vehicle with possible interaction language")

    if (
        pre_severity == "REVIEW"
        and possible_impact
        and impact_score >= 0.45
        and (
            to_float(max_motion_score) >= MOTION_SPIKE_THRESHOLD * 0.60
            or to_int(vehicles) > 0
            or to_int(persons) > 0
        )
    ):
        reasons.append("possible sudden movement near detected object")

    local_supports_important = (
        impact_level == "HIGH"
        or impact_score >= 0.75
        or contact_level == "HIGH"
        or visible_contact
        or visible_impact
        or person_interaction_evidence
        or important_marker_found
    )
    local_supports_review = (
        local_supports_important
        or impact_level == "MEDIUM"
        or crash_safety_triggered
        or motion_triggered
        or possible_impact
        or contact_level == "MEDIUM"
        or possible_contact
        or suspicious_marker_found
        or to_float(max_motion_score) >= MOTION_SPIKE_THRESHOLD * 0.60
        or to_int(persons) > 0
        or to_int(vehicles) > 0
        or lingering_person_detected
        or lingering_vehicle_detected
        or person_lingering_detected
        or vehicle_lingering_detected
    )
    local_supports_ai_important = (
        local_supports_important
        or crash_safety_triggered
        or motion_triggered
        or impact_score >= 0.75
        or contact_score >= 0.68
    )
    protected_local_signal = (
        crash_safety_triggered
        or impact_level == "HIGH"
        or contact_level == "HIGH"
        or impact_score >= 0.75
        or contact_score >= 0.75
        or to_float(max_motion_score) >= MOTION_SPIKE_THRESHOLD
        or to_float(max_motion_score) >= CRASH_GLOBAL_MOTION_TRIGGER
        or motion_triggered
    )

    important_reasons = [
        reason
        for reason in reasons
        if reason not in {
            "impact_level=MEDIUM prevents IGNORE",
            "contact_level=MEDIUM prevents IGNORE",
            "person activity near vehicle with possible interaction language",
            "AI did not override contact safety signals",
            "AI visible_person=true",
            "AI visible_vehicle_close=true",
        }
    ]

    local_rule_severity = pre_severity

    if impact_level == "HIGH":
        local_rule_severity = "IMPORTANT"

    elif impact_score >= 0.75:
        local_rule_severity = "IMPORTANT"

    elif contact_level == "HIGH":
        local_rule_severity = "IMPORTANT"

    elif important_reasons and local_supports_important and not has_normal_only_language:
        local_rule_severity = "IMPORTANT"

    elif impact_level == "MEDIUM" and severity_priority(local_rule_severity) < 1:
        local_rule_severity = "REVIEW"

    elif crash_safety_triggered and severity_priority(local_rule_severity) < 1:
        local_rule_severity = "REVIEW"

    elif contact_level == "MEDIUM" and severity_priority(local_rule_severity) < 1:
        local_rule_severity = "REVIEW"

    elif lingering_person_detected and severity_priority(local_rule_severity) < 1:
        local_rule_severity = "REVIEW"

    elif lingering_vehicle_detected and severity_priority(local_rule_severity) < 1:
        local_rule_severity = "REVIEW"

    passby_logic_applied = False
    passby_logic_reason = ""

    if (
        (brief_person_only or person_passby_detected)
        and not possible_impact
        and not possible_contact
        and not crash_safety_triggered
        and not person_interaction_evidence
        and not protected_local_signal
    ):
        local_rule_severity = "IGNORE"
        passby_logic_applied = True
        passby_logic_reason = "Person pass-by without contact, impact, or tampering evidence kept at Ignore."
        if "Ignored because person/vehicle was brief pass-by with no contact or impact." not in reasons:
            reasons.append("Ignored because person/vehicle was brief pass-by with no contact or impact.")

    if (
        (brief_vehicle_only or vehicle_passby_detected)
        and not possible_impact
        and not possible_contact
        and not crash_safety_triggered
        and not visible_person
        and severity_priority(local_rule_severity) < 2
        and not protected_local_signal
    ):
        local_rule_severity = "IGNORE"
        passby_logic_applied = True
        passby_logic_reason = "Vehicle pass-by without contact or impact evidence kept at Ignore."
        if "brief vehicle-only track without impact/contact evidence" not in reasons:
            reasons.append("brief vehicle-only track without impact/contact evidence")

    if (
        person_lingering_detected
        and not person_interaction_evidence
        and not contact_hard_evidence
        and not impact_hard_evidence
        and severity_priority(local_rule_severity) < severity_priority("REVIEW")
    ):
        local_rule_severity = "REVIEW"
        passby_logic_applied = True
        passby_logic_reason = "Lingering person near vehicle kept for Review."

    if (
        vehicle_lingering_detected
        and not contact_hard_evidence
        and not impact_hard_evidence
        and severity_priority(local_rule_severity) < severity_priority("REVIEW")
    ):
        local_rule_severity = "REVIEW"
        passby_logic_applied = True
        passby_logic_reason = "Lingering vehicle near vehicle kept for Review."

    severity_cap_applied = False
    severity_cap_reason = ""

    if (
        person_near_only
        and not important_evidence_found
        and severity_priority(local_rule_severity) > severity_priority("REVIEW")
    ):
        local_rule_severity = "REVIEW"
        severity_cap_applied = True
        severity_cap_reason = "Capped at Review because no hard Important evidence was found."
        reasons.append(severity_cap_reason)

    final_severity = local_rule_severity

    clear_ai_review_evidence = (
        visible_person
        or visible_contact
        or visible_impact
        or text_has_keyword(event_type_text, ["person_near_vehicle", "possible_contact", "possible_impact"])
        or ai_text_has_possible_interaction
        or ai_text_has_contact_language
        or ai_text_has_high_risk
    )
    clear_ai_important_evidence = (
        visible_contact
        or visible_impact
        or contact_level == "HIGH"
        or impact_level == "HIGH"
        or contact_score >= 0.68
        or impact_score >= 0.75
        or person_interaction_evidence
        or text_has_keyword(combined_review_blob, ["tamper", "vandal", "damage", "collision", "impact"])
        or ai_text_has_high_risk
    )

    if severity_priority(ai_severity) < severity_priority(local_rule_severity):
        if protected_local_signal:
            blocked_reason = "AI downgrade blocked by protected local crash/contact/motion evidence"
        else:
            blocked_reason = "AI downgrade blocked because local rules are primary"

    elif (
        local_rule_severity == "IGNORE"
        and severity_priority(ai_severity) >= 1
        and clear_ai_review_evidence
    ):
        final_severity = "REVIEW"
        ai_allowed_to_change = True
        final_decision_source = "ai_escalated"
        reasons.append("AI escalated IGNORE to REVIEW with clear person/contact/impact evidence")

    elif (
        local_rule_severity == "REVIEW"
        and ai_severity == "IMPORTANT"
        and clear_ai_important_evidence
    ):
        if person_near_only and not important_evidence_found:
            blocked_reason = "AI important recommendation blocked because local evidence showed person near only with no contact/impact."
        elif has_normal_only_language and not local_supports_ai_important:
            blocked_reason = "AI IMPORTANT blocked: normal passing traffic without local support"
        else:
            final_severity = "IMPORTANT"
            ai_allowed_to_change = True
            final_decision_source = "ai_escalated"
            reasons.append("AI escalated REVIEW to IMPORTANT with clear contact/impact/tampering evidence")

    elif severity_priority(ai_severity) > severity_priority(local_rule_severity):
        if ai_severity == "IMPORTANT" and person_near_only and not important_evidence_found:
            blocked_reason = "AI important recommendation blocked because local evidence showed person near only with no contact/impact."
        else:
            blocked_reason = "AI escalation blocked: evidence was not clear enough"

    if final_decision_source == "local_rules" and ai_severity == final_severity and ai_severity != "IGNORE":
        final_decision_source = "ai_supported"

    if final_severity == "IMPORTANT" and has_normal_only_language and not local_supports_ai_important:
        final_severity = local_rule_severity
        final_decision_source = "local_rules"
        ai_allowed_to_change = False
        blocked_reason = "AI IMPORTANT blocked: normal passing traffic without local impact/contact/person support"

    if final_severity == "IMPORTANT" and person_near_only and not important_evidence_found:
        final_severity = "REVIEW" if not person_passby_evidence else "IGNORE"
        final_decision_source = "local_rules"
        ai_allowed_to_change = False
        severity_cap_applied = True
        severity_cap_reason = "Capped at Review because no hard Important evidence was found."
        if ai_severity == "IMPORTANT":
            blocked_reason = "AI important recommendation blocked because local evidence showed person near only with no contact/impact."
        if severity_cap_reason not in reasons:
            reasons.append(severity_cap_reason)

    escalation_applied = final_severity != pre_severity

    return to_json_safe({
        "pre_escalation_severity": pre_severity,
        "local_rule_severity": local_rule_severity,
        "final_severity": final_severity,
        "severity_reasons": reasons,
        "escalation_applied": escalation_applied,
        "ai_recommended_severity": ai_severity,
        "ai_confidence": ai_confidence,
        "final_decision_source": final_decision_source,
        "ai_allowed_to_change": ai_allowed_to_change,
        "ai_blocked_reason": blocked_reason,
        "brief_vehicle_only": brief_vehicle_only,
        "brief_person_only": brief_person_only,
        "lingering_person_detected": lingering_person_detected,
        "lingering_vehicle_detected": lingering_vehicle_detected,
        "person_passby_detected": person_passby_detected,
        "person_lingering_detected": person_lingering_detected,
        "vehicle_passby_detected": vehicle_passby_detected,
        "vehicle_lingering_detected": vehicle_lingering_detected,
        "normal_passing_traffic_evidence": normal_passing_traffic_evidence,
        "person_near_only": person_near_only,
        "person_passby_evidence": person_passby_evidence,
        "person_interaction_evidence": person_interaction_evidence,
        "contact_evidence_level": contact_level,
        "impact_evidence_level": impact_level,
        "important_requires_hard_evidence": True,
        "important_evidence_found": important_evidence_found,
        "severity_cap_applied": severity_cap_applied,
        "severity_cap_reason": severity_cap_reason,
        "passby_logic_applied": passby_logic_applied,
        "passby_logic_reason": passby_logic_reason
    })

# =========================================================
# SAVE DECISION
# =========================================================

def save_decision(decisions, path, priority):

    if path not in decisions:

        decisions[path] = priority

    else:

        decisions[path] = max(
            decisions[path],
            priority
        )

# =========================================================
# SESSION JSON
# =========================================================

def timestamp():

    return datetime.now().replace(microsecond=0).isoformat()


def incident_json_path_for_incident(incident):

    contact_sheet = incident.get(
        "contact_sheet"
    )

    if not contact_sheet:
        return None

    return os.path.join(
        os.path.dirname(str(contact_sheet)),
        "incident.json"
    )


def write_incident_json(incident):

    incident_json_path = incident_json_path_for_incident(
        incident
    )

    if not incident_json_path:
        return

    with profile_stage("json_writing"):
        with open(incident_json_path, "w", encoding="utf-8") as f:
            json.dump(
                to_json_safe(incident),
                f,
                indent=2
            )


def safe_write_text(path, value):

    with open(path, "w", encoding="utf-8") as file:
        file.write(str(value or ""))


def safe_write_json(path, value):

    with open(path, "w", encoding="utf-8") as file:
        json.dump(
            to_json_safe(value),
            file,
            indent=2
        )


def copy_audit_file(source_path, output_path):

    if not source_path:
        return False

    source = Path(str(source_path))

    if not source.exists() or not source.is_file():
        return False

    shutil.copy2(
        source,
        output_path
    )

    return True


def audit_local_evidence_for_incident(incident):

    local_summary = incident.get(
        "local_evidence_summary",
        {}
    )
    classification_debug = incident.get(
        "classification_debug",
        {}
    )

    if not isinstance(local_summary, dict):
        local_summary = {}

    if not isinstance(classification_debug, dict):
        classification_debug = {}

    camera_evidence = local_summary.get(
        "camera_angle_evidence",
        {}
    )

    if not isinstance(camera_evidence, dict):
        camera_evidence = {}

    event_metadata = local_summary.get(
        "event_metadata",
        {}
    )

    if not isinstance(event_metadata, dict):
        event_metadata = {}

    return {
        "source_video": incident.get("source_video"),
        "event_group_id": incident.get("event_group_id")
        or incident.get("camera_group_id")
        or camera_evidence.get("event_group_id"),
        "available_cameras": incident.get("available_cameras")
        or camera_evidence.get("available_cameras")
        or [],
        "primary_camera": incident.get("primary_camera")
        or incident.get("camera")
        or camera_evidence.get("camera"),
        "motion_score": incident.get("max_motion_score")
        or local_summary.get("motion_score"),
        "impact_score": incident.get("impact_score")
        or local_summary.get("impact_score"),
        "contact_score": incident.get("contact_score")
        or local_summary.get("contact_score"),
        "camera_shake_score": incident.get("camera_shake_score")
        or local_summary.get("camera_shake"),
        "possible_impact": bool(
            incident.get("possible_impact")
            or event_metadata.get("possible_impact")
        ),
        "possible_contact": bool(
            incident.get("possible_contact")
            or event_metadata.get("possible_contact")
        ),
        "person_detected": to_int(incident.get("persons")) > 0,
        "vehicle_detected": to_int(incident.get("vehicles")) > 0,
        "local_rule_severity": classification_debug.get("local_rule_severity")
        or incident.get("pre_escalation_severity"),
        "final_severity": incident.get("final_severity")
        or incident.get("severity")
    }


def audit_final_decision_for_incident(incident):

    ai_review = incident.get(
        "ai_evidence_review",
        {}
    )
    classification_debug = incident.get(
        "classification_debug",
        {}
    )

    if not isinstance(ai_review, dict):
        ai_review = {}

    if not isinstance(classification_debug, dict):
        classification_debug = {}

    decision = {
        "ai_recommended_severity": classification_debug.get("ai_recommended_severity")
        or ai_review.get("recommended_severity")
        or ai_review.get("severity"),
        "final_severity": incident.get("final_severity")
        or incident.get("severity"),
        "final_decision_source": incident.get("final_decision_source")
        or classification_debug.get("final_decision_source"),
        "severity_reasons": incident.get("severity_reasons")
        or classification_debug.get("severity_reasons")
        or [],
    }

    blocked_reason = classification_debug.get(
        "ai_blocked_reason"
    )

    if blocked_reason:
        decision["ai_blocked_reason"] = blocked_reason

    return decision


def write_ai_audit_log(session, incident, ai_image_path=None):

    if not AI_AUDIT_ENABLED:
        return

    incident_id = str(
        incident.get("id")
        or incident.get("event_id")
        or "unknown_incident"
    )
    audit_folder = os.path.join(
        AI_AUDIT_OUTPUT,
        incident_id
    )

    try:

        os.makedirs(
            audit_folder,
            exist_ok=True
        )

        ai_review = incident.get(
            "ai_evidence_review",
            {}
        )

        if not isinstance(ai_review, dict):
            ai_review = {}

        safe_write_text(
            os.path.join(audit_folder, "ai_prompt.txt"),
            ai_review.get("ai_prompt", "")
        )
        safe_write_text(
            os.path.join(audit_folder, "ai_raw_response.txt"),
            incident.get("ai_raw_response")
            or ai_review.get("raw_response", "")
        )

        if not bool(incident.get("ai_parse_error", False)):
            parsed_response = {
                key: value
                for key, value in ai_review.items()
                if key not in {
                    "raw_response",
                    "ai_prompt"
                }
            }
            safe_write_json(
                os.path.join(audit_folder, "ai_parsed_response.json"),
                parsed_response
            )

        safe_write_json(
            os.path.join(audit_folder, "local_evidence.json"),
            audit_local_evidence_for_incident(incident)
        )
        safe_write_json(
            os.path.join(audit_folder, "final_decision.json"),
            audit_final_decision_for_incident(incident)
        )

        audit_image_path = (
            ai_image_path
            or incident.get("contact_sheet")
            or incident.get("best_frame_image")
            or incident.get("thumbnail")
        )

        copy_audit_file(
            audit_image_path,
            os.path.join(audit_folder, "ai_review_image.jpg")
        )
        copy_audit_file(
            incident.get("start_frame_image"),
            os.path.join(audit_folder, "before_frame.jpg")
        )
        copy_audit_file(
            incident.get("best_frame_image"),
            os.path.join(audit_folder, "peak_frame.jpg")
        )
        copy_audit_file(
            incident.get("end_frame_image"),
            os.path.join(audit_folder, "after_frame.jpg")
        )

        incident["ai_audit_folder"] = absolute_path_string(
            audit_folder
        )

    except Exception as exc:

        incident["ai_audit_folder"] = absolute_path_string(
            audit_folder
        )
        add_profile_warning(
            session,
            f"AI audit warning for {incident_id}: {exc}"
        )


def absolute_path_string(path):

    return str(
        os.path.abspath(
            str(path)
        )
    )


def existing_video_preview_path(value):

    if not value:
        return None

    preview_path = absolute_path_string(
        value
    )

    if not os.path.exists(preview_path):
        return None

    if not preview_path.lower().endswith(
        (
            ".mp4",
            ".mov",
            ".m4v",
            ".avi",
            ".mkv",
            ".webm"
        )
    ):
        return None

    return preview_path


def update_incident_video_fields(incident, video_path):

    if not isinstance(incident, dict):
        return incident

    source_video = absolute_path_string(
        video_path
    )

    video_preview = existing_video_preview_path(
        incident.get(
            "video_preview"
        )
    )

    incident["source_video"] = source_video
    incident["source_clip"] = source_video

    if video_preview:

        incident["video_preview"] = video_preview
        incident["video_path"] = video_preview

    else:

        incident.pop(
            "video_preview",
            None
        )
        incident["video_path"] = source_video

    incident["video_exists"] = bool(
        os.path.exists(
            str(
                incident.get(
                    "video_path",
                    source_video
                )
            )
        )
    )

    return incident


def camera_sort_key(camera):

    order = {
        "back": 0,
        "rear": 0,
        "front": 1,
        "left_repeater": 2,
        "right_repeater": 3,
        "left_pillar": 4,
        "right_pillar": 5,
    }

    return (
        order.get(str(camera or "").lower(), 99),
        str(camera or "")
    )


def camera_clips_for_group(event_group):

    clips = event_group.get("clips", [])

    if isinstance(clips, list) and clips:
        return to_json_safe(clips)

    fallback_clips = []

    for path in event_group.get("files", []):
        path = absolute_path_string(path)
        parsed = parse_teslacam_filename(path)
        fallback_clips.append(
            {
                "camera": parsed["camera_name"] if parsed else "unknown",
                "path": path,
                "filename": os.path.basename(path),
                "duration_sec": 0.0,
                "exists": os.path.exists(path)
            }
        )

    return to_json_safe(fallback_clips)


def available_cameras_for_group(event_group):

    cameras = [
        clip.get("camera")
        for clip in camera_clips_for_group(event_group)
        if isinstance(clip, dict) and clip.get("camera")
    ]

    return sorted(
        {str(camera) for camera in cameras},
        key=camera_sort_key
    )


def camera_for_incident(incident):

    camera = incident.get("tesla_camera")

    if camera:
        return str(camera)

    path = incident.get("source_video") or incident.get("video_path")
    parsed = parse_teslacam_filename(path) if path else None

    if parsed:
        return parsed["camera_name"]

    return raw_teslacam_camera_suffix(path) if path else None


def incident_evidence_score(incident):

    impact_bonus = {
        "HIGH": 1000.0,
        "MEDIUM": 500.0,
        "LOW": 100.0,
        "NONE": 0.0,
    }.get(str(incident.get("impact_level", "NONE")).upper(), 0.0)
    contact_bonus = {
        "HIGH": 650.0,
        "MEDIUM": 300.0,
        "LOW": 80.0,
        "NONE": 0.0,
    }.get(str(incident.get("contact_level", "NONE")).upper(), 0.0)

    return (
        impact_bonus
        + contact_bonus
        + to_float(incident.get("impact_score", 0.0)) * 100.0
        + to_float(incident.get("contact_score", 0.0)) * 80.0
        + to_float(incident.get("max_motion_score", 0.0))
    )


def choose_primary_incident(group_incidents, event_group):

    if not group_incidents:
        return None

    scored = sorted(
        group_incidents,
        key=lambda incident: incident_evidence_score(incident),
        reverse=True
    )
    best = scored[0]

    if incident_evidence_score(best) > 0:
        return best

    primary_camera_candidate = event_group.get("primary_camera_candidate")

    if primary_camera_candidate:
        for incident in group_incidents:
            if camera_for_incident(incident) == primary_camera_candidate:
                return incident

    for preferred_camera in ["back", "rear", "front"]:
        for incident in group_incidents:
            if camera_for_incident(incident) == preferred_camera:
                return incident

    return group_incidents[0]


def aggregate_group_severity(group_incidents):

    if any(
        str(incident.get("impact_level", "")).upper() == "HIGH"
        for incident in group_incidents
    ):
        return "IMPORTANT"

    if any(
        str(incident.get("contact_level", "")).upper() == "HIGH"
        for incident in group_incidents
    ):
        return "IMPORTANT"

    highest = "IGNORE"

    for incident in group_incidents:
        current = str(incident.get("severity", "IGNORE")).upper()

        if severity_priority(current) > severity_priority(highest):
            highest = priority_label(severity_priority(current))

    if any(
        incident.get("possible_contact")
        for incident in group_incidents
    ) and severity_priority(highest) < severity_priority("REVIEW"):
        highest = "REVIEW"

    if any(
        incident.get("possible_impact")
        or incident.get("crash_safety_triggered")
        or incident.get("motion_triggered")
        for incident in group_incidents
    ) and severity_priority(highest) < severity_priority("REVIEW"):
        highest = "REVIEW"

    return highest


def annotate_group_incident(primary_incident, group_incidents, event_group):

    if not primary_incident:
        return None

    camera_clips = camera_clips_for_group(event_group)
    available_cameras = available_cameras_for_group(event_group)
    primary_camera = camera_for_incident(primary_incident)
    object_persistence = merge_incident_object_persistence(group_incidents)
    group_severity = aggregate_group_severity(group_incidents)
    primary_source_video = primary_incident.get("source_video") or primary_incident.get("video_path")

    primary_incident["event_group_id"] = event_group.get("event_group_id") or event_group.get("event_id")
    primary_incident["event_timestamp"] = event_group.get("event_timestamp") or event_group.get("timestamp")
    primary_incident["event_folder"] = event_group.get("event_folder")
    primary_incident["source_category"] = event_group.get("source_category")
    primary_incident["available_cameras"] = available_cameras
    primary_incident["primary_camera"] = primary_camera or (available_cameras[0] if available_cameras else "unknown")
    primary_incident["camera_clips"] = camera_clips
    primary_incident["camera_count"] = to_int(len(available_cameras) if available_cameras else len(camera_clips))
    primary_incident["grouped_camera_incident_count"] = to_int(len(group_incidents))
    primary_incident["grouping_version"] = "grouped_incidents_v1"
    primary_incident["prepass_motion_score"] = event_group.get("prepass_motion_score", 0.0)
    primary_incident["prepass_candidate_reason"] = event_group.get("prepass_candidate_reason", "")
    primary_incident["prepass_severity_hint"] = event_group.get("prepass_severity_hint", "IGNORE")
    primary_incident["deep_analysis_performed"] = bool(event_group.get("deep_analysis_performed", True))
    primary_incident["skipped_reason"] = event_group.get("skipped_reason", "")
    primary_incident["object_tracks"] = object_persistence.get("object_tracks", [])
    primary_incident["object_persistence_summary"] = object_persistence.get(
        "object_persistence_summary",
        empty_object_persistence_summary()
    )
    primary_incident["brief_vehicle_only"] = bool(object_persistence.get("brief_vehicle_only", False))
    primary_incident["brief_person_only"] = bool(object_persistence.get("brief_person_only", False))
    primary_incident["lingering_person_detected"] = bool(
        object_persistence.get("lingering_person_detected", False)
    )
    primary_incident["lingering_vehicle_detected"] = bool(
        object_persistence.get("lingering_vehicle_detected", False)
    )
    primary_incident["person_passby_detected"] = bool(
        object_persistence.get("person_passby_detected", False)
    )
    primary_incident["person_lingering_detected"] = bool(
        object_persistence.get("person_lingering_detected", False)
    )
    primary_incident["vehicle_passby_detected"] = bool(
        object_persistence.get("vehicle_passby_detected", False)
    )
    primary_incident["vehicle_lingering_detected"] = bool(
        object_persistence.get("vehicle_lingering_detected", False)
    )
    primary_incident["normal_passing_traffic_evidence"] = bool(
        object_persistence.get("normal_passing_traffic_evidence", False)
    )

    local_summary = primary_incident.get("local_evidence_summary")

    if isinstance(local_summary, dict):
        local_summary["object_persistence"] = {
            "summary": primary_incident["object_persistence_summary"],
            "brief_vehicle_only": primary_incident["brief_vehicle_only"],
            "brief_person_only": primary_incident["brief_person_only"],
            "lingering_person_detected": primary_incident["lingering_person_detected"],
            "lingering_vehicle_detected": primary_incident["lingering_vehicle_detected"],
            "person_passby_detected": primary_incident["person_passby_detected"],
            "person_lingering_detected": primary_incident["person_lingering_detected"],
            "vehicle_passby_detected": primary_incident["vehicle_passby_detected"],
            "vehicle_lingering_detected": primary_incident["vehicle_lingering_detected"],
            "normal_passing_traffic_evidence": primary_incident["normal_passing_traffic_evidence"]
        }

    if primary_source_video:
        primary_incident["video_path"] = absolute_path_string(primary_source_video)
        primary_incident["source_video"] = absolute_path_string(primary_source_video)
        primary_incident["source_clip"] = absolute_path_string(primary_source_video)
        primary_incident["video_exists"] = bool(os.path.exists(primary_incident["video_path"]))

    prepass_severity_hint = str(
        event_group.get("prepass_severity_hint", "IGNORE")
    ).upper()

    if severity_priority(prepass_severity_hint) > severity_priority(group_severity):
        group_severity = prepass_severity_hint
        primary_incident.setdefault("severity_reasons", []).append(
            "prepass metadata severity hint"
        )

    if (
        primary_incident["lingering_person_detected"]
        and severity_priority(group_severity) < severity_priority("REVIEW")
    ):
        group_severity = "REVIEW"
        primary_incident.setdefault("severity_reasons", []).append(
            "group object persistence found lingering person"
        )

    if (
        primary_incident["lingering_vehicle_detected"]
        and severity_priority(group_severity) < severity_priority("REVIEW")
    ):
        group_severity = "REVIEW"
        primary_incident.setdefault("severity_reasons", []).append(
            "group object persistence found lingering vehicle"
        )

    if (
        primary_incident.get("person_near_only")
        and not primary_incident.get("important_evidence_found")
        and not any(
            str(incident.get("impact_level", "")).upper() == "HIGH"
            or str(incident.get("contact_level", "")).upper() == "HIGH"
            or bool(incident.get("crash_safety_triggered"))
            or to_float(incident.get("max_motion_score", 0.0)) >= MOTION_SPIKE_THRESHOLD
            for incident in group_incidents
        )
        and severity_priority(group_severity) > severity_priority("REVIEW")
    ):
        group_severity = "REVIEW"
        primary_incident["severity_cap_applied"] = True
        primary_incident["severity_cap_reason"] = (
            "Capped at Review because no hard Important evidence was found."
        )
        primary_incident.setdefault("severity_reasons", []).append(
            primary_incident["severity_cap_reason"]
        )

    if severity_priority(group_severity) > severity_priority(primary_incident.get("severity")):
        primary_incident.setdefault("severity_reasons", []).append(
            "multi-camera group severity aggregation"
        )

    primary_incident["severity"] = group_severity
    primary_incident["final_severity"] = group_severity

    debug = primary_incident.setdefault("classification_debug", {})

    if isinstance(debug, dict):
        debug["grouping_version"] = "grouped_incidents_v1"
        debug["group_severity"] = group_severity
        debug["grouped_camera_incident_count"] = to_int(len(group_incidents))
        debug["prepass_motion_score"] = primary_incident["prepass_motion_score"]
        debug["prepass_candidate_reason"] = primary_incident["prepass_candidate_reason"]
        debug["prepass_severity_hint"] = primary_incident["prepass_severity_hint"]
        debug["deep_analysis_performed"] = primary_incident["deep_analysis_performed"]
        debug["skipped_reason"] = primary_incident["skipped_reason"]
        debug["brief_vehicle_only"] = primary_incident["brief_vehicle_only"]
        debug["brief_person_only"] = primary_incident["brief_person_only"]
        debug["lingering_person_detected"] = primary_incident["lingering_person_detected"]
        debug["lingering_vehicle_detected"] = primary_incident["lingering_vehicle_detected"]
        debug["person_passby_detected"] = primary_incident["person_passby_detected"]
        debug["person_lingering_detected"] = primary_incident["person_lingering_detected"]
        debug["vehicle_passby_detected"] = primary_incident["vehicle_passby_detected"]
        debug["vehicle_lingering_detected"] = primary_incident["vehicle_lingering_detected"]
        debug["normal_passing_traffic_evidence"] = primary_incident["normal_passing_traffic_evidence"]
        debug["person_near_only"] = bool(primary_incident.get("person_near_only", False))
        debug["person_passby_evidence"] = bool(primary_incident.get("person_passby_evidence", False))
        debug["person_interaction_evidence"] = bool(primary_incident.get("person_interaction_evidence", False))
        debug["contact_evidence_level"] = primary_incident.get("contact_evidence_level", "NONE")
        debug["impact_evidence_level"] = primary_incident.get("impact_evidence_level", "NONE")
        debug["important_requires_hard_evidence"] = True
        debug["important_evidence_found"] = bool(primary_incident.get("important_evidence_found", False))
        debug["severity_cap_applied"] = bool(primary_incident.get("severity_cap_applied", False))
        debug["severity_cap_reason"] = primary_incident.get("severity_cap_reason", "")

    return primary_incident


def consolidate_group_incidents(session, event_group, start_incident_index):

    try:
        new_incidents = session.get("incidents", [])[start_incident_index:]

        if not new_incidents:
            return None

        primary = choose_primary_incident(new_incidents, event_group)
        annotated = annotate_group_incident(primary, new_incidents, event_group)

        if annotated:
            write_incident_json(annotated)
            session["incidents"] = session.get("incidents", [])[:start_incident_index] + [annotated]
            return annotated

        session["incidents"] = session.get("incidents", [])[:start_incident_index]
        return None

    except Exception as exc:
        session.setdefault("storage_warnings", []).append(
            f"Multi-camera grouping warning: {exc}"
        )
        return None


def print_group_summary(event_group, primary_incident):

    try:
        cameras = ",".join(available_cameras_for_group(event_group))
        primary = primary_incident.get("primary_camera") if isinstance(primary_incident, dict) else None
        severity = primary_incident.get("severity") if isinstance(primary_incident, dict) else "IGNORE"
        incident_id = primary_incident.get("id") if isinstance(primary_incident, dict) else "none"
        console.print(
            f"GROUP {event_group.get('event_group_id') or event_group.get('event_id')} "
            f"| cameras={cameras or 'unknown'} "
            f"| incident={incident_id or 'none'} "
            f"| primary={primary or 'none'} "
            f"| severity={severity}"
        )

    except Exception:
        pass


def update_session_video_paths_after_move(session, moved_paths):

    if not moved_paths:
        return

    incidents = session.get(
        "incidents",
        []
    )

    if not isinstance(incidents, list):
        return

    for incident in incidents:

        if not isinstance(incident, dict):
            continue

        source_video = incident.get(
            "source_video"
        )

        moved_path = moved_paths.get(
            source_video
        )

        if not moved_path:
            moved_path = moved_paths.get(
                os.path.abspath(
                    str(source_video)
                )
            )

        if not moved_path:
            continue

        update_incident_video_fields(
            incident,
            moved_path
        )
        write_incident_json(
            to_json_safe(incident)
        )


def default_library_root():

    return Path.home() / "Videos" / "Mimir Library"


def scan_folder_name():

    return "scan_" + datetime.now().strftime("%Y-%m-%d_%H-%M-%S")


def unique_path(path):

    candidate = Path(path)

    if not candidate.exists():
        return candidate

    stem = candidate.stem
    suffix = candidate.suffix
    parent = candidate.parent
    index = 1

    while True:

        candidate = parent / f"{stem}_{index}{suffix}"

        if not candidate.exists():
            return candidate

        index += 1


def library_category_folder(priority):

    if priority == 2:
        return "Important"

    if priority == 1:
        return "Review"

    return "Ignore"


def should_store_priority(source_action, priority):

    if source_action in {"copy_all", "move_all"}:
        return True

    if source_action in {"copy_review", "move_review"}:
        return priority >= 1

    return False


def ensure_library_scan_folder(library_root):

    root = Path(library_root).expanduser()
    root.mkdir(parents=True, exist_ok=True)

    scan_folder = unique_path(root / scan_folder_name())
    scan_folder.mkdir(parents=True, exist_ok=True)

    for folder_name in [
        "Important",
        "Review",
        "Ignore",
        "Incidents",
    ]:
        (scan_folder / folder_name).mkdir(parents=True, exist_ok=True)

    return scan_folder


def verified_copy_file(src, dst):

    shutil.copy2(src, dst)

    return (
        os.path.exists(dst)
        and os.path.getsize(dst) > 0
    )


def update_incidents_for_library_video(session, original_path, library_path, source_action):

    original_abs = absolute_path_string(original_path)
    library_abs = absolute_path_string(library_path)

    for incident in session.get("incidents", []):

        if not isinstance(incident, dict):
            continue

        incident_source = absolute_path_string(
            incident.get(
                "original_source_video",
                incident.get(
                    "source_video",
                    ""
                )
            )
        )

        if incident_source != original_abs:
            continue

        incident["original_source_video"] = original_abs
        incident["library_video_path"] = library_abs
        incident["video_path"] = library_abs
        incident["video_exists"] = os.path.exists(library_abs)
        incident["storage_action_applied"] = source_action


def copy_incident_assets_to_library(session, library_scan_folder):

    incidents_root = Path(library_scan_folder) / "Incidents"

    for incident in session.get("incidents", []):

        if not isinstance(incident, dict):
            continue

        incident_id = str(
            incident.get(
                "id",
                ""
            )
        )

        if not incident_id:
            continue

        contact_sheet = incident.get(
            "contact_sheet"
        )

        if not contact_sheet:
            continue

        source_folder = Path(
            str(contact_sheet)
        ).parent

        if not source_folder.is_dir():
            continue

        destination_folder = incidents_root / incident_id
        destination_folder.mkdir(parents=True, exist_ok=True)

        for source_file in source_folder.iterdir():

            if not source_file.is_file():
                continue

            shutil.copy2(
                source_file,
                destination_folder / source_file.name
            )

        for field in IMAGE_PATH_FIELDS_FOR_LIBRARY():

            value = incident.get(field)

            if not value:
                continue

            source_name = Path(str(value)).name
            destination = destination_folder / source_name

            if destination.exists():
                incident[field] = absolute_path_string(destination)

        if incident.get("hero_thumbnail"):
            incident["thumbnail"] = incident["hero_thumbnail"]

        write_incident_json(
            to_json_safe(incident)
        )


def IMAGE_PATH_FIELDS_FOR_LIBRARY():

    return [
        "hero_thumbnail",
        "thumbnail",
        "contact_sheet",
        "start_frame_image",
        "best_frame_image",
        "end_frame_image",
    ]


def apply_library_storage(session, decisions, library_root, source_action):

    source_action = str(
        source_action or "analyze_only"
    ).strip().lower()

    if source_action not in SOURCE_ACTIONS:
        source_action = "analyze_only"

    session["library_root"] = absolute_path_string(library_root)
    session["source_action"] = source_action

    if source_action == "analyze_only":
        return

    library_scan_folder = ensure_library_scan_folder(library_root)
    session["library_scan_folder"] = absolute_path_string(library_scan_folder)

    files_copied = 0
    files_moved = 0
    files_failed = 0
    source_files_removed = 0
    storage_warnings = []

    for src, priority in decisions.items():

        src_path = Path(src)

        if not should_store_priority(source_action, priority):
            continue

        if not src_path.is_file():
            files_failed += 1
            storage_warnings.append(
                f"Source file missing, skipped: {src}"
            )
            continue

        category_folder = library_scan_folder / library_category_folder(priority)
        destination = unique_path(category_folder / src_path.name)

        try:

            if not verified_copy_file(str(src_path), str(destination)):
                files_failed += 1
                storage_warnings.append(
                    f"Copy verification failed: {src} -> {destination}"
                )
                continue

            files_copied += 1
            update_incidents_for_library_video(
                session,
                src_path,
                destination,
                source_action
            )

            if source_action.startswith("move_"):

                try:
                    os.remove(src_path)
                    files_moved += 1
                    source_files_removed += 1

                except Exception as exc:
                    files_failed += 1
                    storage_warnings.append(
                        f"Copied but could not remove source file: {src} ({exc})"
                    )

        except Exception as exc:

            files_failed += 1
            storage_warnings.append(
                f"Storage action failed for {src}: {exc}"
            )

    copy_incident_assets_to_library(
        session,
        library_scan_folder
    )

    session["files_copied"] = to_int(files_copied)
    session["files_moved"] = to_int(files_moved)
    session["files_failed"] = to_int(files_failed)
    session["usb_cleanup_performed"] = source_action.startswith("move_")
    session["source_files_removed"] = to_int(source_files_removed)
    session["storage_warnings"] = storage_warnings


def write_library_session_json(session):

    library_scan_folder = session.get(
        "library_scan_folder"
    )

    if not library_scan_folder:
        return

    try:

        output_path = Path(library_scan_folder) / "latest_session.json"

        with profile_stage("json_writing"):
            with open(output_path, "w", encoding="utf-8") as file:
                json.dump(
                    to_json_safe(session),
                    file,
                    indent=2
                )

    except Exception as exc:

        warnings = session.setdefault(
            "storage_warnings",
            []
        )
        warnings.append(
            f"Could not write library latest_session.json: {exc}"
        )


def print_storage_summary(session):

    console.print("\n[bold cyan]Storage:[/bold cyan]")
    console.print(f"- Source action: {session.get('source_action', 'analyze_only')}")
    console.print(f"- Library folder: {session.get('library_scan_folder') or 'not created'}")
    console.print(f"- Files copied: {to_int(session.get('files_copied'))}")
    console.print(f"- Files moved: {to_int(session.get('files_moved'))}")
    console.print(f"- Files failed: {to_int(session.get('files_failed'))}")
    console.print(f"- Source files removed: {to_int(session.get('source_files_removed'))}")

    for warning in session.get("storage_warnings", []):
        console.print(f"  [yellow]warning:[/yellow] {warning}")


def create_session(input_folder, safe_input_mode, scan_mode, library_root, source_action):

    return {
        "status": "running",
        "started_at": timestamp(),
        "finished_at": None,
        "input_folder": input_folder,
        "selected_input": input_folder,
        "detected_source_type": None,
        "drive_root": None,
        "teslacam_root": None,
        "scan_roots": [],
        "event_groups": [],
        "scan_pipeline": "grouped_candidate_pipeline_v1",
        "prepass_groups_processed": 0,
        "prepass_candidates_found": 0,
        "deep_analysis_groups": 0,
        "skipped_low_interest_groups": 0,
        "prepass_runtime_sec": 0.0,
        "deep_analysis_runtime_sec": 0.0,
        "source_categories_found": [],
        "event_groups_found": 0,
        "multi_camera_groups": 0,
        "single_camera_groups": 0,
        "camera_suffixes_found": [],
        "grouping_version": "grouped_incidents_v1",
        "source_discovery_warnings": [],
        "source_report": {
            "selected_input": input_folder,
            "detected_source_type": "unknown",
            "is_supported": False,
            "teslacam_root_found": False,
            "categories_found": [],
            "mp4_files_found": 0,
            "event_groups_found": 0,
            "multi_camera_groups": 0,
            "single_camera_groups": 0,
            "camera_suffixes_found": [],
            "event_json_files_found": 0,
            "warnings": [],
            "user_message": "No footage was found. Select the USB drive, TeslaCam folder, or a folder containing MP4 clips."
        },
        "safe_input_mode": safe_input_mode,
        "scan_mode": str(scan_mode),
        "scan_engine": SCAN_ENGINE,
        "ai_review_available": bool(AI_REVIEW_AVAILABLE),
        "ai_review_model": AI_REVIEW_MODEL,
        "ai_review_error": AI_REVIEW_ERROR,
        "ai_review_required": True,
        "ai_review_budget": to_int(AI_REVIEW_BUDGET),
        "ai_review_candidates": 0,
        "ai_reviewed_groups": 0,
        "ai_skipped_groups": 0,
        "ai_review_runtime_sec": 0.0,
        "grouped_camera_review": True,
        "ai_audit_enabled": bool(AI_AUDIT_ENABLED),
        "ai_audit_folder": absolute_path_string(AI_AUDIT_OUTPUT),
        "enhanced_ai_available": bool(AI_REVIEW_AVAILABLE),
        "enhanced_ai_model": AI_REVIEW_MODEL,
        "enhanced_ai_error": AI_REVIEW_ERROR,
        "standard_scanner_available": True,
        "progress_supported": True,
        "library_root": absolute_path_string(library_root),
        "library_scan_folder": None,
        "source_action": str(source_action),
        "files_copied": 0,
        "files_moved": 0,
        "files_failed": 0,
        "usb_cleanup_performed": False,
        "source_files_removed": 0,
        "storage_warnings": [],
        "safety_rules_version": CRASH_SAFETY_VERSION,
        "clips_processed": 0,
        "important": 0,
        "review": 0,
        "ignore": 0,
        "tesla_events_found": 0,
        "source_events_found": 0,
        "event_json_files_found": 0,
        "performance": create_performance_metrics(),
        "incidents": []
    }


def update_session_ai_capabilities(session):

    session["scan_engine"] = SCAN_ENGINE
    session["ai_review_available"] = bool(
        AI_REVIEW_AVAILABLE
    )
    session["ai_review_model"] = AI_REVIEW_MODEL
    session["ai_review_error"] = AI_REVIEW_ERROR
    session["enhanced_ai_available"] = bool(
        AI_REVIEW_AVAILABLE
    )
    session["enhanced_ai_model"] = AI_REVIEW_MODEL
    session["enhanced_ai_error"] = AI_REVIEW_ERROR
    session["ai_audit_enabled"] = bool(AI_AUDIT_ENABLED)
    session["ai_audit_folder"] = absolute_path_string(AI_AUDIT_OUTPUT)
    session["standard_scanner_available"] = True


def add_incident(
    session,
    path,
    tesla_event_groups,
    event_id,
    incident_id,
    label,
    ai_review,
    event_score,
    persons,
    vehicles,
    active_frames,
    max_motion_score,
    impact_analysis,
    contact_analysis,
    timeline_context,
    frame_path,
    start_frame_image,
    best_frame_image,
    end_frame_image,
    contact_sheet,
    hero_thumbnail,
    object_persistence=None,
    ai_review_performed=False,
    ai_image_path=None
):

    event_score_value = to_float(event_score)
    max_motion_score_value = to_float(max_motion_score)
    event_id_value = to_int(event_id)
    persons_value = to_int(persons)
    vehicles_value = to_int(vehicles)
    active_frames_value = to_int(active_frames)
    tesla_event_metadata = tesla_event_metadata_for_video(path)
    tesla_event_group_metadata = teslacam_event_group_metadata_for_video(
        path,
        tesla_event_groups
    )
    source_discovery_metadata = source_discovery_metadata_for_video(path)
    object_persistence = object_persistence or finalize_object_persistence(None)
    ai_review = ensure_ai_review(ai_review)
    ai_fields = ai_review_json_fields(ai_review)
    impact_level = str(
        impact_analysis.get(
            "impact_level",
            "NONE"
        )
    ).upper()

    if impact_level not in {"NONE", "LOW", "MEDIUM", "HIGH"}:
        impact_level = "NONE"

    contact_level = str(
        contact_analysis.get(
            "contact_level",
            "NONE"
        )
    ).upper()

    if contact_level not in {"NONE", "LOW", "MEDIUM", "HIGH"}:
        contact_level = "NONE"

    possible_impact_value = bool(
        impact_analysis.get(
            "possible_impact",
            False
        )
    )
    crash_safety_triggered_value = bool(
        impact_analysis.get(
            "crash_safety_triggered",
            False
        )
    )
    motion_triggered_value = bool(
        impact_analysis.get(
            "motion_triggered",
            False
        )
    )
    trigger_reasons_value = [
        str(reason)
        for reason in impact_analysis.get(
            "trigger_reasons",
            []
        )
    ]
    possible_contact_value = bool(
        contact_analysis.get(
            "possible_contact",
            False
        )
    )
    motion_spike_time_sec_raw = impact_analysis.get(
        "motion_spike_time_sec"
    )
    motion_spike_time_sec = (
        None
        if motion_spike_time_sec_raw is None
        else round(
            to_float(motion_spike_time_sec_raw),
            2
        )
    )
    camera_shake_score = round(
        to_float(
            impact_analysis.get(
                "camera_shake_score",
                0.0
            )
        ),
        2
    )
    optical_flow_score_value = round(
        to_float(
            impact_analysis.get(
                "optical_flow_score",
                0.0
            )
        ),
        2
    )
    scene_change_score_value = round(
        to_float(
            impact_analysis.get(
                "scene_change_score",
                0.0
            )
        ),
        2
    )
    proximity_score_value = round(
        to_float(
            impact_analysis.get(
                "proximity_score",
                0.0
            )
        ),
        2
    )
    contact_score_value = round(
        to_float(
            contact_analysis.get(
                "contact_score",
                0.0
            )
        ),
        2
    )
    local_motion_score_value = round(
        to_float(
            contact_analysis.get(
                "local_motion_score",
                0.0
            )
        ),
        2
    )
    contact_time_sec_raw = contact_analysis.get(
        "contact_time_sec"
    )
    contact_time_sec = (
        None
        if contact_time_sec_raw is None
        else round(
            to_float(contact_time_sec_raw),
            2
        )
    )
    impact_score_value = round(
        to_float(
            impact_analysis.get(
                "impact_score",
                0.0
            )
        ),
        2
    )
    local_evidence_summary = build_local_evidence_summary(
        event_score_value,
        max_motion_score_value,
        impact_analysis,
        contact_analysis,
        persons_value,
        vehicles_value,
        active_frames_value,
        object_persistence=object_persistence,
        source_discovery_metadata=source_discovery_metadata,
        tesla_event_metadata=tesla_event_metadata,
        tesla_event_group_metadata=tesla_event_group_metadata
    )
    fps_value = round(
        valid_fps(
            timeline_context.get(
                "fps"
            )
        ),
        3
    )
    video_duration_sec_value = round(
        to_float(
            timeline_context.get(
                "video_duration_sec"
            ),
            0.0
        ),
        2
    )
    with profile_stage("timeline_generation"):
        timeline_result = build_timeline_markers(
            timeline_context,
            impact_analysis,
            contact_analysis,
            ai_fields,
            label,
            impact_level,
            possible_impact_value,
            contact_level,
            possible_contact_value
        )
    timeline_markers = timeline_result["markers"]
    timeline_quality = timeline_result["timeline_quality"]
    severity_resolution = resolve_final_severity(
        label,
        ai_review,
        impact_analysis,
        contact_analysis,
        max_motion_score_value,
        persons_value,
        vehicles_value,
        active_frames_value,
        timeline_markers,
        object_persistence=object_persistence
    )
    final_label = severity_resolution["final_severity"]
    if isinstance(local_evidence_summary, dict):
        local_evidence_summary["severity_policy"] = {
            "person_near_only": bool(severity_resolution["person_near_only"]),
            "person_passby_evidence": bool(severity_resolution["person_passby_evidence"]),
            "person_interaction_evidence": bool(severity_resolution["person_interaction_evidence"]),
            "person_passby_detected": bool(severity_resolution["person_passby_detected"]),
            "person_lingering_detected": bool(severity_resolution["person_lingering_detected"]),
            "vehicle_passby_detected": bool(severity_resolution["vehicle_passby_detected"]),
            "vehicle_lingering_detected": bool(severity_resolution["vehicle_lingering_detected"]),
            "contact_evidence_level": severity_resolution["contact_evidence_level"],
            "impact_evidence_level": severity_resolution["impact_evidence_level"],
            "important_requires_hard_evidence": True,
            "important_evidence_found": bool(severity_resolution["important_evidence_found"]),
            "severity_cap_applied": bool(severity_resolution["severity_cap_applied"]),
            "severity_cap_reason": severity_resolution["severity_cap_reason"]
        }
    incident_summary = ai_fields["summary"]
    if (
        severity_resolution["person_near_only"]
        and not severity_resolution["person_interaction_evidence"]
        and not severity_resolution["important_evidence_found"]
    ):
        incident_summary = (
            "Mimir saw a person near the vehicle, but no clear contact or tampering was detected."
        )

    if final_label != label:
        with profile_stage("timeline_generation"):
            timeline_result = build_timeline_markers(
                timeline_context,
                impact_analysis,
                contact_analysis,
                ai_fields,
                final_label,
                impact_level,
                possible_impact_value,
                contact_level,
                possible_contact_value
            )
        timeline_markers = timeline_result["markers"]
        timeline_quality = timeline_result["timeline_quality"]

    incident = {
        "id": incident_id,
        "original_source_video": os.path.abspath(path),
        "source_video": os.path.abspath(path),
        "video_path": os.path.abspath(path),
        "video_exists": os.path.exists(
            os.path.abspath(path)
        ),
        "storage_action_applied": "analyze_only",
        "event_id": event_id_value,
        "severity": final_label,
        "pre_escalation_severity": severity_resolution["pre_escalation_severity"],
        "final_severity": severity_resolution["final_severity"],
        "severity_reasons": severity_resolution["severity_reasons"],
        "escalation_applied": severity_resolution["escalation_applied"],
        "local_evidence_summary": local_evidence_summary,
        "final_decision_source": severity_resolution["final_decision_source"],
        "ai_evidence_review": ai_review,
        "ai_raw_response": ai_review.get("raw_response", ""),
        "ai_parse_error": bool(ai_review.get("ai_parse_error", False)),
        "ai_review_skipped_reason": ai_review.get("ai_review_skipped_reason", ""),
        "ai_decision": ai_fields["ai_decision"],
        "ai_confidence": ai_fields["ai_confidence"],
        "event_type": ai_fields["event_type"],
        "summary": incident_summary,
        "evidence": ai_fields["evidence"],
        "recommended_action": ai_fields["recommended_action"],
        "score": round(event_score_value, 1),
        "persons": persons_value,
        "vehicles": vehicles_value,
        "active_frames": active_frames_value,
        "video_duration_sec": video_duration_sec_value,
        "fps": fps_value,
        "max_motion_score": round(max_motion_score_value, 2),
        "motion_spike_time_sec": motion_spike_time_sec,
        "camera_shake_score": camera_shake_score,
        "optical_flow_score": optical_flow_score_value,
        "scene_change_score": scene_change_score_value,
        "proximity_score": proximity_score_value,
        "local_motion_score": local_motion_score_value,
        "contact_time_sec": contact_time_sec,
        "contact_score": contact_score_value,
        "contact_level": contact_level,
        "possible_contact": possible_contact_value,
        "contact_reasons": [
            str(reason)
            for reason in contact_analysis.get(
                "contact_reasons",
                []
            )
        ],
        "impact_score": impact_score_value,
        "impact_level": impact_level,
        "possible_impact": possible_impact_value,
        "crash_safety_triggered": crash_safety_triggered_value,
        "motion_triggered": motion_triggered_value,
        "trigger_reasons": trigger_reasons_value,
        "impact_reasons": [
            str(reason)
            for reason in impact_analysis.get(
                "impact_reasons",
                []
            )
        ],
        "object_tracks": object_persistence.get("object_tracks", []),
        "object_persistence_summary": object_persistence.get(
            "object_persistence_summary",
            empty_object_persistence_summary()
        ),
        "brief_vehicle_only": bool(object_persistence.get("brief_vehicle_only", False)),
        "brief_person_only": bool(object_persistence.get("brief_person_only", False)),
        "lingering_person_detected": bool(object_persistence.get("lingering_person_detected", False)),
        "lingering_vehicle_detected": bool(object_persistence.get("lingering_vehicle_detected", False)),
        "person_passby_detected": bool(severity_resolution["person_passby_detected"]),
        "person_lingering_detected": bool(severity_resolution["person_lingering_detected"]),
        "vehicle_passby_detected": bool(severity_resolution["vehicle_passby_detected"]),
        "vehicle_lingering_detected": bool(severity_resolution["vehicle_lingering_detected"]),
        "normal_passing_traffic_evidence": bool(
            object_persistence.get("normal_passing_traffic_evidence", False)
        ),
        "person_near_only": bool(severity_resolution["person_near_only"]),
        "person_passby_evidence": bool(severity_resolution["person_passby_evidence"]),
        "person_interaction_evidence": bool(severity_resolution["person_interaction_evidence"]),
        "contact_evidence_level": severity_resolution["contact_evidence_level"],
        "impact_evidence_level": severity_resolution["impact_evidence_level"],
        "important_requires_hard_evidence": True,
        "important_evidence_found": bool(severity_resolution["important_evidence_found"]),
        "severity_cap_applied": bool(severity_resolution["severity_cap_applied"]),
        "severity_cap_reason": severity_resolution["severity_cap_reason"],
        "timeline_markers": timeline_markers,
        "timeline_quality": timeline_quality,
        "hero_thumbnail": hero_thumbnail,
        "thumbnail": hero_thumbnail,
        "start_frame_image": start_frame_image,
        "best_frame_image": best_frame_image,
        "end_frame_image": end_frame_image,
        "contact_sheet": contact_sheet,
        "classification_debug": {
            "ai_decision": ai_fields["ai_decision"],
            "local_rule_severity": severity_resolution["local_rule_severity"],
            "ai_recommended_severity": severity_resolution["ai_recommended_severity"],
            "ai_confidence": severity_resolution["ai_confidence"],
            "final_decision_source": severity_resolution["final_decision_source"],
            "ai_allowed_to_change": severity_resolution["ai_allowed_to_change"],
            "ai_blocked_reason": severity_resolution["ai_blocked_reason"],
            "ai_review_skipped_reason": ai_review.get("ai_review_skipped_reason", ""),
            "event_type": ai_fields["event_type"],
            "pre_escalation_severity": severity_resolution["pre_escalation_severity"],
            "final_severity": severity_resolution["final_severity"],
            "impact_level": impact_level,
            "impact_score": impact_score_value,
            "possible_impact": possible_impact_value,
            "crash_safety_triggered": crash_safety_triggered_value,
            "motion_triggered": motion_triggered_value,
            "trigger_reasons": trigger_reasons_value,
            "contact_level": contact_level,
            "contact_score": contact_score_value,
            "possible_contact": possible_contact_value,
            "contact_reasons": [
                str(reason)
                for reason in contact_analysis.get(
                    "contact_reasons",
                    []
                )
            ],
            "impact_reasons": [
                str(reason)
                for reason in impact_analysis.get(
                    "impact_reasons",
                    []
                )
            ],
            "severity_reasons": severity_resolution["severity_reasons"],
            "brief_vehicle_only": severity_resolution["brief_vehicle_only"],
            "brief_person_only": severity_resolution["brief_person_only"],
            "lingering_person_detected": severity_resolution["lingering_person_detected"],
            "lingering_vehicle_detected": severity_resolution["lingering_vehicle_detected"],
            "normal_passing_traffic_evidence": severity_resolution["normal_passing_traffic_evidence"],
            "person_passby_detected": severity_resolution["person_passby_detected"],
            "person_lingering_detected": severity_resolution["person_lingering_detected"],
            "vehicle_passby_detected": severity_resolution["vehicle_passby_detected"],
            "vehicle_lingering_detected": severity_resolution["vehicle_lingering_detected"],
            "person_near_only": severity_resolution["person_near_only"],
            "person_passby_evidence": severity_resolution["person_passby_evidence"],
            "person_interaction_evidence": severity_resolution["person_interaction_evidence"],
            "contact_evidence_level": severity_resolution["contact_evidence_level"],
            "impact_evidence_level": severity_resolution["impact_evidence_level"],
            "important_requires_hard_evidence": True,
            "important_evidence_found": severity_resolution["important_evidence_found"],
            "severity_cap_applied": severity_resolution["severity_cap_applied"],
            "severity_cap_reason": severity_resolution["severity_cap_reason"],
            "passby_logic_applied": severity_resolution["passby_logic_applied"],
            "passby_logic_reason": severity_resolution["passby_logic_reason"],
            "escalation_applied": severity_resolution["escalation_applied"]
        },
        "created_at": timestamp()
    }

    incident.update(tesla_event_metadata)
    incident.update(tesla_event_group_metadata)
    incident.update(source_discovery_metadata)

    if source_discovery_metadata.get("source_event_timestamp") is not None:
        incident["tesla_event_timestamp"] = source_discovery_metadata.get(
            "source_event_timestamp"
        )

    if source_discovery_metadata.get("source_event_reason") is not None:
        incident["tesla_event_reason"] = source_discovery_metadata.get(
            "source_event_reason"
        )

    if source_discovery_metadata.get("source_event_raw") is not None:
        incident["tesla_event_raw"] = source_discovery_metadata.get(
            "source_event_raw"
        )

    incident = update_incident_video_fields(
        incident,
        path
    )

    defer_publish = bool(session.get("_defer_incident_publish", False))

    if ai_review_performed and not defer_publish:
        write_ai_audit_log(
            session,
            incident,
            ai_image_path=ai_image_path
        )

    incident = to_json_safe(incident)

    session["incidents"].append(incident)

    if not defer_publish:
        add_performance_value(
            "incidents_created",
            1
        )

        write_incident_json(incident)

    return incident


def write_session_json(session):

    with profile_stage("json_writing"):
        with open(LATEST_SESSION_JSON, "w", encoding="utf-8") as f:
            json.dump(to_json_safe(session), f, indent=2)


def next_incident_id(session):

    incident_number = len(session["incidents"]) + 1

    return f"incident_{incident_number:04d}"


def valid_fps(value):

    fps_value = to_float(
        value,
        0.0
    )

    if fps_value <= 0:
        return 0.0

    return fps_value


def frame_time_sec(frame_index, fps, fallback=0.0):

    fps_value = valid_fps(
        fps
    )

    if fps_value <= 0:
        return to_float(
            fallback,
            0.0
        )

    return to_float(
        frame_index,
        0.0
    ) / fps_value


def clamp_time_sec(time_sec, video_duration_sec=None):

    value = max(
        0.0,
        to_float(
            time_sec,
            0.0
        )
    )

    duration = to_float(
        video_duration_sec,
        0.0
    )

    if duration > 0:
        value = min(
            value,
            duration
        )

    return round(
        value,
        2
    )


def video_duration_from_capture(cap, fps):

    fps_value = valid_fps(
        fps
    )

    if fps_value <= 0:
        return 0.0

    try:

        frame_count = to_float(
            cap.get(cv2.CAP_PROP_FRAME_COUNT),
            0.0
        )

    except Exception:

        frame_count = 0.0

    if frame_count <= 0:
        return 0.0

    return round(
        frame_count / fps_value,
        2
    )


def make_timeline_marker(
    frame_index,
    fps,
    video_duration_sec,
    marker_type,
    severity,
    label,
    description,
    fallback_time_sec=0.0
):

    frame_index_value = to_int(
        frame_index
    )
    time_sec = frame_time_sec(
        frame_index_value,
        fps,
        fallback_time_sec
    )

    return {
        "time_sec": clamp_time_sec(
            time_sec,
            video_duration_sec
        ),
        "frame_index": frame_index_value,
        "type": str(marker_type),
        "severity": str(severity),
        "label": str(label),
        "description": str(description)
    }


def contact_marker_label(event_type):

    text = str(event_type or "").lower()

    if "door" in text:
        return "Possible door interaction"

    if "vandal" in text:
        return "Possible vandalism"

    if "impact" in text or "collision" in text:
        return "Possible impact"

    return "Possible contact"


def ai_suggests_possible_contact(ai_fields):

    text = " ".join(
        [
            str(ai_fields.get("event_type", "")),
            str(ai_fields.get("summary", "")),
            " ".join(
                str(item)
                for item in ai_fields.get("evidence", [])
            )
        ]
    ).lower()

    return any(
        keyword in text
        for keyword in [
            "contact",
            "door",
            "handle",
            "window",
            "vandal",
            "impact",
            "collision"
        ]
    )


def marker_meaning_rank(marker):

    priority = {
        "possible_impact": 7,
        "possible_contact": 6,
        "event_peak": 5,
        "person_detected": 4,
        "vehicle_nearby": 3,
        "vehicle_detected": 3,
        "event_started": 2,
        "event_ended": 1,
    }

    severity_bonus = {
        "IMPORTANT": 0.30,
        "REVIEW": 0.20,
        "NEUTRAL": 0.0,
        "IGNORE": 0.0,
    }

    return (
        priority.get(
            marker.get(
                "type",
                ""
            ),
            0
        )
        + severity_bonus.get(
            marker.get(
                "severity",
                ""
            ),
            0
        )
    )


def cleanup_timeline_markers(markers):

    required_types = {
        "event_started",
        "event_peak",
        "event_ended",
    }
    protected_types = required_types | {
        "possible_impact",
        "possible_contact",
    }
    sorted_markers = sorted(
        markers,
        key=lambda marker: (
            to_float(marker.get("time_sec")),
            -marker_meaning_rank(marker)
        )
    )
    deduped = []

    for marker in sorted_markers:

        marker_type = marker.get("type")
        marker_time = to_float(
            marker.get("time_sec")
        )
        replaced = False
        skip_marker = False

        for index, existing in enumerate(deduped):

            existing_time = to_float(
                existing.get("time_sec")
            )
            marker_is_required = marker_type in required_types
            existing_is_required = existing.get("type") in required_types
            marker_is_protected = marker_type in protected_types
            existing_is_protected = existing.get("type") in protected_types
            same_type_nearby = (
                existing.get("type") == marker_type
                and abs(existing_time - marker_time) <= 1.0
            )
            crowded_nearby = (
                abs(existing_time - marker_time) <= 0.5
            )

            if same_type_nearby or crowded_nearby:

                if crowded_nearby and marker_is_protected and existing_is_protected:
                    continue

                if crowded_nearby and existing_is_protected and not marker_is_protected:
                    skip_marker = True

                elif crowded_nearby and marker_is_protected and not existing_is_protected:
                    deduped[index] = marker
                    replaced = True

                elif marker_meaning_rank(marker) > marker_meaning_rank(existing):
                    deduped[index] = marker
                    replaced = True

                else:
                    skip_marker = True

                break

        if replaced or skip_marker:
            continue

        deduped.append(marker)

    deduped.sort(
        key=lambda marker: (
            to_float(marker.get("time_sec")),
            to_int(marker.get("frame_index"))
        )
    )

    return deduped


def build_timeline_markers(
    timeline_context,
    impact_analysis,
    contact_analysis,
    ai_fields,
    final_label,
    impact_level,
    possible_impact,
    contact_level,
    possible_contact
):

    markers = []

    fps = timeline_context.get("fps")
    video_duration_sec = timeline_context.get("video_duration_sec")

    start_frame_index = timeline_context.get("start_frame_index")
    peak_frame_index = timeline_context.get("peak_frame_index")
    end_frame_index = timeline_context.get("end_frame_index")
    first_person_frame_index = timeline_context.get("first_person_frame_index")
    first_vehicle_frame_index = timeline_context.get("first_vehicle_frame_index")

    start_time_sec = timeline_context.get("start_time_sec")
    peak_time_sec = timeline_context.get("peak_time_sec")
    end_time_sec = timeline_context.get("end_time_sec")
    first_person_time_sec = timeline_context.get("first_person_time_sec")
    first_vehicle_time_sec = timeline_context.get("first_vehicle_time_sec")
    vehicle_proximity_score = to_float(
        impact_analysis.get(
            "proximity_score",
            0.0
        )
    )
    contact_time_sec = contact_analysis.get(
        "contact_time_sec"
    )
    contact_frame_index = timeline_context.get(
        "contact_frame_index"
    )
    contact_score = to_float(
        contact_analysis.get(
            "contact_score",
            0.0
        )
    )
    contact_reasons = [
        str(reason).lower()
        for reason in contact_analysis.get(
            "contact_reasons",
            []
        )
    ]
    contact_marker_has_hard_evidence = (
        contact_score >= 0.45
        and (
            contact_level == "HIGH"
            or any(
                token in " ".join(contact_reasons)
                for token in [
                    "local edge motion",
                    "proximity plus motion",
                    "combined proximity and motion",
                    "side contact",
                    "door",
                    "vehicle close",
                    "side camera"
                ]
            )
        )
    )
    motion_spike_time_sec = impact_analysis.get(
        "motion_spike_time_sec"
    )
    crash_safety_triggered = bool(
        impact_analysis.get(
            "crash_safety_triggered",
            False
        )
    )
    motion_spike_frame_index = timeline_context.get(
        "motion_spike_frame_index"
    )

    if start_frame_index is not None:
        markers.append(
            make_timeline_marker(
                start_frame_index,
                fps,
                video_duration_sec,
                "event_started",
                "NEUTRAL",
                "Event started",
                "Mimir detected activity worth reviewing.",
                start_time_sec
            )
        )

    if (
        first_person_frame_index is not None
        and abs(
            to_float(first_person_time_sec)
            - to_float(start_time_sec)
        ) > 1.0
    ):
        markers.append(
            make_timeline_marker(
                first_person_frame_index,
                fps,
                video_duration_sec,
                "person_detected",
                "REVIEW",
                "Person entered view",
                "A person was detected during the event.",
                first_person_time_sec
            )
        )

    if peak_frame_index is not None:
        markers.append(
            make_timeline_marker(
                peak_frame_index,
                fps,
                video_duration_sec,
                "event_peak",
                "REVIEW",
                "Highest activity",
                "This was the most active moment in the event.",
                peak_time_sec
            )
        )

    if (
        first_vehicle_frame_index is not None
        and vehicle_proximity_score >= 12.0
    ):
        markers.append(
            make_timeline_marker(
                first_vehicle_frame_index,
                fps,
                video_duration_sec,
                "vehicle_nearby",
                "REVIEW",
                "Vehicle close by",
                "A nearby vehicle was detected during the event.",
                first_vehicle_time_sec
            )
        )

    if (
        peak_frame_index is not None
        and (
            crash_safety_triggered
            or possible_impact
        )
        and (
            crash_safety_triggered
            or impact_level in {"MEDIUM", "HIGH"}
        )
    ):

        marker_severity = (
            "IMPORTANT"
            if final_label == "IMPORTANT" or impact_level == "HIGH"
            else "REVIEW"
        )
        markers.append(
            make_timeline_marker(
                (
                    peak_frame_index
                    if motion_spike_frame_index is None
                    else motion_spike_frame_index
                ),
                fps,
                video_duration_sec,
                "possible_impact",
                marker_severity,
                "Possible impact",
                "Sudden motion or scene change suggests possible contact or collision.",
                (
                    peak_time_sec
                    if motion_spike_time_sec is None
                    else motion_spike_time_sec
                )
            )
        )

    if (
        peak_frame_index is not None
        and possible_contact
        and contact_level in {"MEDIUM", "HIGH"}
        and contact_marker_has_hard_evidence
    ):

        marker_severity = (
            "IMPORTANT"
            if final_label == "IMPORTANT" or contact_level == "HIGH"
            else "REVIEW"
        )

        markers.append(
            make_timeline_marker(
                (
                    peak_frame_index
                    if contact_frame_index is None and motion_spike_frame_index is None
                    else (
                        contact_frame_index
                        if contact_frame_index is not None
                        else motion_spike_frame_index
                    )
                ),
                fps,
                video_duration_sec,
                "possible_contact",
                marker_severity,
                "Possible contact",
                "Close object movement suggests possible contact with the parked vehicle.",
                (
                    peak_time_sec
                    if contact_time_sec is None and motion_spike_time_sec is None
                    else (
                        contact_time_sec
                        if contact_time_sec is not None
                        else motion_spike_time_sec
                    )
                )
            )
        )

    if end_frame_index is not None:
        markers.append(
            make_timeline_marker(
                end_frame_index,
                fps,
                video_duration_sec,
                "event_ended",
                "NEUTRAL",
                "Event ended",
                "The reviewable activity ended.",
                end_time_sec
            )
        )

    markers_before_cleanup = len(markers)
    markers = cleanup_timeline_markers(markers)
    markers_after_cleanup = len(markers)

    return to_json_safe({
        "markers": markers,
        "timeline_quality": {
            "markers_before_cleanup": markers_before_cleanup,
            "markers_after_cleanup": markers_after_cleanup,
            "cleanup_applied": markers_after_cleanup != markers_before_cleanup
        }
    })


def resize_to_height(frame, target_height):

    h, w = frame.shape[:2]

    if h == target_height:
        return frame.copy()

    scale = target_height / h

    return cv2.resize(
        frame,
        (
            max(1, int(w * scale)),
            target_height
        )
    )


def add_label(frame, label):

    labelled = frame.copy()

    cv2.rectangle(
        labelled,
        (0, 0),
        (labelled.shape[1], 42),
        (0, 0, 0),
        -1
    )

    cv2.putText(
        labelled,
        label,
        (14, 28),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.75,
        (255, 255, 255),
        2,
        cv2.LINE_AA
    )

    return labelled


def resize_for_hero(frame, target_width=1280):

    if frame is None:
        return None

    height, width = frame.shape[:2]

    if width <= 0 or height <= 0:
        return frame

    if width <= target_width:
        return frame.copy()

    scale = target_width / width

    return cv2.resize(
        frame,
        (
            target_width,
            max(1, int(height * scale))
        ),
        interpolation=cv2.INTER_AREA
    )


def add_subtle_badge(frame, label):

    if frame is None or not label:
        return frame

    badged = frame.copy()
    text = str(label).upper()
    font = cv2.FONT_HERSHEY_SIMPLEX
    font_scale = 0.72
    thickness = 2

    text_size, _ = cv2.getTextSize(
        text,
        font,
        font_scale,
        thickness
    )
    text_width, text_height = text_size
    x = 18
    y = 20
    padding_x = 14
    padding_y = 10

    overlay = badged.copy()
    cv2.rectangle(
        overlay,
        (x, y),
        (
            x + text_width + padding_x * 2,
            y + text_height + padding_y * 2
        ),
        (0, 0, 0),
        -1
    )
    cv2.addWeighted(
        overlay,
        0.42,
        badged,
        0.58,
        0,
        badged
    )
    cv2.putText(
        badged,
        text,
        (x + padding_x, y + padding_y + text_height),
        font,
        font_scale,
        (245, 245, 245),
        thickness,
        cv2.LINE_AA
    )

    return badged


def save_hero_thumbnail(path, frame, label=None):

    try:

        hero = resize_for_hero(frame)

        if hero is None:
            return False

        hero = add_subtle_badge(
            hero,
            label
        )

        return bool(
            cv2.imwrite(
                path,
                hero
            )
        )

    except Exception:
        return False


def save_event_evidence(
    incident_folder,
    start_frame,
    best_frame,
    end_frame,
    severity_label=None,
    before_impact_frame=None,
    impact_frame=None,
    after_impact_frame=None,
    impact_focused=False,
    before_contact_frame=None,
    contact_frame=None,
    after_contact_frame=None,
    contact_focused=False
):

    os.makedirs(incident_folder, exist_ok=True)

    if start_frame is None:
        start_frame = best_frame

    if end_frame is None:
        end_frame = best_frame

    start_path = os.path.join(incident_folder, "start.jpg")
    best_path = os.path.join(incident_folder, "best.jpg")
    end_path = os.path.join(incident_folder, "end.jpg")
    contact_path = os.path.join(incident_folder, "contact_sheet.jpg")
    hero_path = os.path.join(incident_folder, "hero_thumbnail.jpg")

    sheet_start_frame = start_frame
    sheet_best_frame = best_frame
    sheet_end_frame = end_frame
    sheet_labels = (
        "START",
        "PEAK",
        "END"
    )

    if impact_focused and impact_frame is not None:

        sheet_start_frame = (
            before_impact_frame
            if before_impact_frame is not None
            else start_frame
        )
        sheet_best_frame = impact_frame
        sheet_end_frame = (
            after_impact_frame
            if after_impact_frame is not None
            else end_frame
        )
        sheet_labels = (
            "BEFORE",
            "IMPACT",
            "AFTER"
        )

    elif contact_focused and contact_frame is not None:

        sheet_start_frame = (
            before_contact_frame
            if before_contact_frame is not None
            else start_frame
        )
        sheet_best_frame = contact_frame
        sheet_end_frame = (
            after_contact_frame
            if after_contact_frame is not None
            else end_frame
        )
        sheet_labels = (
            "BEFORE",
            "CONTACT",
            "AFTER"
        )

    hero_source_frame = sheet_best_frame

    cv2.imwrite(start_path, sheet_start_frame)
    cv2.imwrite(best_path, sheet_best_frame)
    cv2.imwrite(end_path, sheet_end_frame)

    target_height = min(
        360,
        sheet_start_frame.shape[0],
        sheet_best_frame.shape[0],
        sheet_end_frame.shape[0]
    )

    contact_sheet = cv2.hconcat(
        [
            add_label(
                resize_to_height(sheet_start_frame, target_height),
                sheet_labels[0]
            ),
            add_label(
                resize_to_height(sheet_best_frame, target_height),
                sheet_labels[1]
            ),
            add_label(
                resize_to_height(sheet_end_frame, target_height),
                sheet_labels[2]
            )
        ]
    )

    cv2.imwrite(contact_path, contact_sheet)

    if not save_hero_thumbnail(
        hero_path,
        hero_source_frame,
        severity_label
    ):
        hero_path = best_path

    return {
        "start_frame_image": start_path,
        "best_frame_image": best_path,
        "end_frame_image": end_path,
        "contact_sheet": contact_path,
        "hero_thumbnail": hero_path
    }

# =========================================================
# FINALIZE EVENT
# =========================================================

def finalize_event(
    path,
    decisions,
    session,
    tesla_event_groups,
    ai_review_context,
    event_id,
    start_frame,
    best_frame,
    end_frame,
    event_score,
    persons,
    vehicles,
    active_frames,
    max_motion_score,
    motion_spike_time_sec,
    camera_shake_score,
    optical_flow_score_value,
    scene_change_score_value,
    proximity_score,
    local_motion_score,
    contact_time_sec,
    timeline_context,
    before_impact_frame=None,
    impact_frame=None,
    after_impact_frame=None,
    before_contact_frame=None,
    contact_frame=None,
    after_contact_frame=None,
    crash_safety_triggered=False,
    motion_triggered=False,
    trigger_reasons=None,
    object_persistence=None
):

    if best_frame is None:
        return

    emit_progress(
        "building_incident_timeline",
        f"Finalizing event {event_id}.",
        current=PROGRESS_CONTEXT.get("current_video_index"),
        total=PROGRESS_CONTEXT.get("total_videos"),
        percent=progress_stage_percent(
            "scanning_video",
            current=PROGRESS_CONTEXT.get("current_video_index"),
            total=PROGRESS_CONTEXT.get("total_videos")
        ),
        extra={
            "current_video": os.path.basename(path)
        }
    )

    incident_id = next_incident_id(session)

    with profile_stage("impact_analysis"):
        impact_analysis = build_impact_analysis(
            max_motion_score,
            motion_spike_time_sec,
            camera_shake_score,
            optical_flow_score_value,
            scene_change_score_value,
            proximity_score,
            persons,
            vehicles,
            crash_safety_triggered=crash_safety_triggered,
            motion_triggered=motion_triggered,
            trigger_reasons=trigger_reasons
        )
    impact_level = impact_analysis["impact_level"]
    impact_score = to_float(
        impact_analysis.get(
            "impact_score",
            0.0
        )
    )
    impact_focused_contact_sheet = (
        bool(
            impact_analysis.get(
                "crash_safety_triggered",
                False
            )
        )
        or
        impact_level in {"MEDIUM", "HIGH"}
        or impact_score >= IMPACT_FOCUSED_CONTACT_SHEET_SCORE
    )
    with profile_stage("contact_analysis"):
        contact_analysis = build_contact_analysis(
            path,
            max_motion_score,
            contact_time_sec,
            local_motion_score,
            optical_flow_score_value,
            scene_change_score_value,
            proximity_score,
            persons,
            vehicles
        )
    contact_level = contact_analysis["contact_level"]
    contact_score = to_float(
        contact_analysis.get(
            "contact_score",
            0.0
        )
    )
    contact_focused_contact_sheet = (
        not impact_focused_contact_sheet
        and (
            contact_level in {"MEDIUM", "HIGH"}
            or contact_score >= 0.45
        )
    )

    incident_folder = os.path.join(
        INCIDENTS_OUTPUT,
        incident_id
    )

    with profile_stage("thumbnail_generation"):
        evidence = save_event_evidence(
            incident_folder,
            start_frame,
            best_frame,
            end_frame,
            before_impact_frame=before_impact_frame,
            impact_frame=impact_frame,
            after_impact_frame=after_impact_frame,
            impact_focused=impact_focused_contact_sheet,
            before_contact_frame=before_contact_frame,
            contact_frame=contact_frame,
            after_contact_frame=after_contact_frame,
            contact_focused=contact_focused_contact_sheet
        )

    frame_name = (
        f"{os.path.basename(path)}"
        f"_event_{event_id}.jpg"
    )

    frame_path = os.path.join(
        FRAMES,
        frame_name
    )

    with profile_stage("thumbnail_generation"):
        cv2.imwrite(frame_path, best_frame)

    # =====================================================
    # AI FINAL DECISION
    # =====================================================

    ai_image_path = frame_path
    group_id = event_group_id_for_value(path)
    ai_review_skipped_reason = ""

    if os.path.exists(
        evidence["contact_sheet"]
    ):
        ai_image_path = evidence["contact_sheet"]

    should_review_with_ai, ai_review_skipped_reason = should_run_group_ai_review(
        ai_review_context,
        group_id,
        event_score
    )

    if should_review_with_ai:

        ai_review_started = time.perf_counter()
        ai_review = run_ai(
            ai_image_path,
            impact_focused=impact_focused_contact_sheet,
            contact_focused=contact_focused_contact_sheet
        )
        mark_group_ai_reviewed(
            ai_review_context,
            group_id,
            time.perf_counter() - ai_review_started
        )

    else:

        ai_review = fallback_ai_review(
            ai_review_skipped_reason
            or "AI review skipped by candidate budget."
        )
        ai_review["ai_review_skipped_reason"] = (
            ai_review_skipped_reason
            or "AI review skipped by candidate budget."
        )

    possible_impact = impact_analysis["possible_impact"]
    possible_contact = contact_analysis["possible_contact"]

    if possible_impact:

        ai_review["evidence"] = (
            ai_review["evidence"]
            + [
                "possible sudden movement detected by frame motion analysis"
            ]
        )[:6]

    if possible_contact:

        ai_review["evidence"] = (
            ai_review["evidence"]
            + [
                "possible close contact detected by proximity and local motion analysis"
            ]
        )[:6]

    ai = ai_review["recommended_severity"]

    priority = 0

    if (
        impact_level == "HIGH"
        or impact_score >= 0.75
        or contact_level == "HIGH"
    ):

        priority = 2

    elif (
        impact_level == "MEDIUM"
        or bool(
            impact_analysis.get(
                "crash_safety_triggered",
                False
            )
        )
        or possible_impact
        or contact_level == "MEDIUM"
        or possible_contact
        or to_int(persons) > 0
    ):

        priority = 1

    # =====================================================
    # LABEL/COLOR
    # =====================================================

    if priority == 2:

        label = "IMPORTANT"
        color = "red"

    elif priority == 1:

        label = "REVIEW"
        color = "yellow"

    else:

        label = "IGNORE"
        color = "green"

    incident = add_incident(
        session=session,
        path=path,
        tesla_event_groups=tesla_event_groups,
        event_id=event_id,
        incident_id=incident_id,
        label=label,
        ai_review=ai_review,
        event_score=event_score,
        persons=persons,
        vehicles=vehicles,
        active_frames=active_frames,
        max_motion_score=max_motion_score,
        impact_analysis=impact_analysis,
        contact_analysis=contact_analysis,
        timeline_context=timeline_context,
        frame_path=frame_path,
        start_frame_image=evidence["start_frame_image"],
        best_frame_image=evidence["best_frame_image"],
        end_frame_image=evidence["end_frame_image"],
        contact_sheet=evidence["contact_sheet"],
        hero_thumbnail=evidence["hero_thumbnail"],
        object_persistence=object_persistence,
        ai_review_performed=bool(should_review_with_ai),
        ai_image_path=ai_image_path
    )

    emit_progress(
        "building_incident_timeline",
        f"Incident {incident_id} finalized.",
        current=PROGRESS_CONTEXT.get("current_video_index"),
        total=PROGRESS_CONTEXT.get("total_videos"),
        percent=progress_stage_percent(
            "scanning_video",
            current=PROGRESS_CONTEXT.get("current_video_index"),
            total=PROGRESS_CONTEXT.get("total_videos")
        ),
        extra={
            "current_video": os.path.basename(path),
            "incident_id": incident_id
        }
    )
    final_label = incident.get(
        "severity",
        label
    )
    classification_debug = incident.get(
        "classification_debug",
        {}
    )
    final_priority = severity_priority(
        final_label
    )
    final_color = priority_color(
        final_priority
    )

    # =====================================================
    # LOGGING
    # =====================================================

    event_score_value = to_float(event_score)
    max_motion_score_value = to_float(max_motion_score)

    console.print(
        f"  [{final_color}]EVENT {event_id}[/{final_color}] "
        f"| source={os.path.basename(path)} "
        f"| score={event_score_value:.1f} "
        f"| frames={active_frames} "
        f"| persons={persons} "
        f"| vehicles={vehicles} "
        f"| motion={max_motion_score_value:.2f} "
        f"| impact={impact_analysis['impact_level']} "
        f"| impact_score={to_float(impact_analysis.get('impact_score')):.2f} "
        f"| contact={contact_analysis['contact_level']} "
        f"| contact_score={to_float(contact_analysis.get('contact_score')):.2f} "
        f"| crash_safety={impact_analysis.get('crash_safety_triggered')} "
        f"| possible_impact={possible_impact} "
        f"| possible_contact={possible_contact} "
        f"| local_rule_severity={classification_debug.get('local_rule_severity', label)} "
        f"| ai_recommended_severity={classification_debug.get('ai_recommended_severity', ai)} "
        f"| final_severity={final_label} "
        f"| final_decision_source={incident.get('final_decision_source', 'local_rules')}"
    )
    console.print(
        "    severity_reasons: "
        + (
            "; ".join(
                incident.get(
                    "severity_reasons",
                    []
                )
            )
            or "none"
        )
    )

    save_decision(
        decisions,
        path,
        final_priority
    )

# =========================================================
# PROCESS VIDEO
# =========================================================

def process_video(path, decisions, session, tesla_event_groups, ai_review_context=None, candidate_windows=None, current_index=None, total_videos=None):

    profile_metric = profile_metric_for_video(path)
    incident_count_started = len(
        session.get(
            "incidents",
            []
        )
    )
    current_index_value = to_int(
        current_index,
        0
    )
    total_videos_value = to_int(
        total_videos,
        0
    )
    current_video_name = os.path.basename(path)

    set_progress_context(
        total=total_videos_value,
        current=current_index_value,
        current_video=current_video_name
    )

    emit_progress(
        "scanning_video",
        (
            f"Scanning clip {current_index_value} of {total_videos_value}"
            if total_videos_value
            else f"Scanning {current_video_name}"
        ),
        current=current_index_value if current_index_value else None,
        total=total_videos_value if total_videos_value else None,
        extra={
            "current_video": current_video_name
        }
    )

    with profile_stage("video_opening"):
        cap = cv2.VideoCapture(path)

    if not cap.isOpened():

        console.print(
            f"[red]FAILED TO OPEN:[/red] {path}"
        )

        emit_progress(
            "scanning_video",
            f"Could not open {current_video_name}; marking clip ignored.",
            current=current_index_value if current_index_value else None,
            total=total_videos_value if total_videos_value else None,
            extra={
                "current_video": current_video_name
            }
        )

        save_decision(
            decisions,
            path,
            0
        )

        set_metric_error(
            profile_metric,
            "Could not open video."
        )
        finish_profile_metric(
            profile_metric,
            session
        )

        return

    with profile_stage("prepass"):
        fps = valid_fps(
            cap.get(cv2.CAP_PROP_FPS)
        )

        if fps <= 0:
            fps = 30.0

        video_duration_sec = video_duration_from_capture(
            cap,
            fps
        )

        profile_metric["total_duration_sec"] = round(
            to_float(video_duration_sec),
            3
        )
        deep_candidate_windows = normalized_candidate_windows(
            candidate_windows,
            video_duration_sec
        )

        step = max(
            1,
            int(fps / SAMPLE_FPS)
        )

    frame_i = 0

    rolling = 0.0
    active = False

    event_score = 0
    active_frames = 0

    persons = 0
    vehicles = 0

    best_frame = None
    best_score = 0
    start_frame = None
    end_frame = None
    max_motion_score = 0.0
    motion_spike_time_sec = None
    motion_spike_frame_index = None
    camera_shake_score = 0.0
    max_optical_flow_score = 0.0
    max_scene_change_score = 0.0
    max_impact_spike_score = 0.0
    max_local_motion_score = 0.0
    max_contact_spike_score = 0.0
    contact_time_sec = None
    contact_frame_index = None
    proximity_score = 0.0
    before_impact_frame = None
    impact_frame = None
    after_impact_frame = None
    waiting_for_after_impact_frame = False
    before_contact_frame = None
    contact_frame = None
    after_contact_frame = None
    waiting_for_after_contact_frame = False
    event_start_frame_index = None
    event_start_time_sec = None
    first_person_frame_index = None
    first_person_time_sec = None
    first_vehicle_frame_index = None
    first_vehicle_time_sec = None
    best_frame_index = None
    best_time_sec = None
    end_frame_index = None
    end_time_sec = None
    crash_safety_triggered = False
    motion_triggered = False
    trigger_reasons = []
    event_object_tracker = create_object_tracker()

    previous_sampled_frame = None
    recent_motion_scores = []

    last_activity = 0

    event_id = 0
    sampled_frames_in_video = 0

    console.print(
        f"\n[bold blue]Scanning:[/bold blue] "
        f"{os.path.basename(path)}"
    )

    while True:

        with profile_stage("frame_sampling"):
            ret, frame = cap.read()

        if not ret:
            break

        if frame_i % step != 0:

            frame_i += 1
            continue

        frame_time_sec_value = frame_i / fps

        if not time_in_candidate_windows(
            frame_time_sec_value,
            deep_candidate_windows
        ):

            frame_i += 1
            continue

        add_performance_value(
            "frames_sampled",
            1
        )
        sampled_frames_in_video += 1
        profile_metric["sampled_frames"] = sampled_frames_in_video

        if sampled_frames_in_video == 1 or sampled_frames_in_video % 25 == 0:
            emit_progress(
                "scanning_video",
                (
                    f"Scanning clip {current_index_value} of {total_videos_value}"
                    if total_videos_value
                    else f"Scanning {current_video_name}"
                ),
                current=current_index_value if current_index_value else None,
                total=total_videos_value if total_videos_value else None,
                extra={
                    "current_video": current_video_name,
                    "sampled_frames_in_video": sampled_frames_in_video
                }
            )

        previous_frame_for_impact = (
            None
            if previous_sampled_frame is None
            else previous_sampled_frame.copy()
        )

        with profile_stage("motion_analysis"):
            motion_score = frame_motion_score(
                previous_sampled_frame,
                frame
            )

            flow_score = optical_flow_score(
                previous_sampled_frame,
                frame
            )

            scene_score = scene_change_score(
                previous_sampled_frame,
                frame
            )

            edge_score = local_edge_motion_score(
                previous_sampled_frame,
                frame
            )

        previous_sampled_frame = frame.copy()

        score, p, v, detections = analyze(
            frame,
            return_detections=True
        )
        profile_metric["yolo_frames"] = to_int(
            profile_metric.get(
                "yolo_frames",
                0
            )
        ) + 1

        crash_trigger = crash_safety_trigger(
            motion_score,
            scene_score,
            flow_score,
            recent_motion_scores,
            vehicle_count=v,
            proximity_score=score
        )

        rolling = rolling * 0.85 + score

        now = time.time()

        # =================================================
        # START EVENT
        # =================================================

        if (
            not active
            and (
                rolling > EVENT_TRIGGER
                or crash_trigger.get("triggered")
            )
        ):

            active = True

            event_score = 0
            active_frames = 0

            persons = 0
            vehicles = 0

            best_frame = None
            best_score = 0
            start_frame = None
            end_frame = None
            max_motion_score = 0.0
            motion_spike_time_sec = None
            motion_spike_frame_index = None
            camera_shake_score = 0.0
            max_optical_flow_score = 0.0
            max_scene_change_score = 0.0
            max_impact_spike_score = 0.0
            max_local_motion_score = 0.0
            max_contact_spike_score = 0.0
            contact_time_sec = None
            contact_frame_index = None
            proximity_score = 0.0
            before_impact_frame = None
            impact_frame = None
            after_impact_frame = None
            waiting_for_after_impact_frame = False
            before_contact_frame = None
            contact_frame = None
            after_contact_frame = None
            waiting_for_after_contact_frame = False
            event_start_frame_index = frame_i
            event_start_time_sec = frame_i / fps
            first_person_frame_index = None
            first_person_time_sec = None
            first_vehicle_frame_index = None
            first_vehicle_time_sec = None
            best_frame_index = None
            best_time_sec = None
            end_frame_index = None
            end_time_sec = None
            crash_safety_triggered = bool(
                crash_trigger.get("crash_safety_triggered")
            )
            motion_triggered = bool(
                crash_trigger.get("motion_triggered")
            )
            trigger_reasons = [
                str(reason)
                for reason in crash_trigger.get(
                    "trigger_reasons",
                    []
                )
            ]
            event_object_tracker = create_object_tracker()

            console.print(
                f"  [cyan]event {event_id} started[/cyan]"
                + (
                    " [magenta](motion trigger)[/magenta]"
                    if crash_safety_triggered
                    else ""
                )
            )

        # =================================================
        # ACTIVE EVENT
        # =================================================

        if active:

            active_frames += 1
            update_object_tracker(
                event_object_tracker,
                detections,
                frame_i,
                frame_time_sec_value,
                frame.shape
            )

            event_score += score

            persons += p
            vehicles += v

            if waiting_for_after_impact_frame and motion_spike_frame_index != frame_i:
                after_impact_frame = frame.copy()
                waiting_for_after_impact_frame = False

            if waiting_for_after_contact_frame and contact_frame_index != frame_i:
                after_contact_frame = frame.copy()
                waiting_for_after_contact_frame = False

            if motion_score > max_motion_score:
                max_motion_score = motion_score

            if scene_score > max_scene_change_score:
                max_scene_change_score = scene_score

            if edge_score > max_local_motion_score:
                max_local_motion_score = edge_score

            impact_spike_score = (
                motion_score
                + scene_score
                + flow_score * 2.0
            )

            if impact_spike_score > max_impact_spike_score:
                max_impact_spike_score = impact_spike_score
                motion_spike_time_sec = frame_i / fps
                motion_spike_frame_index = frame_i
                before_impact_frame = (
                    previous_frame_for_impact.copy()
                    if previous_frame_for_impact is not None
                    else frame.copy()
                )
                impact_frame = frame.copy()
                after_impact_frame = None
                waiting_for_after_impact_frame = True

            contact_spike_score = (
                edge_score * 1.25
                + motion_score * 0.45
                + score * 0.12
                + flow_score
            )

            if contact_spike_score > max_contact_spike_score:
                max_contact_spike_score = contact_spike_score
                contact_time_sec = frame_i / fps
                contact_frame_index = frame_i
                before_contact_frame = (
                    previous_frame_for_impact.copy()
                    if previous_frame_for_impact is not None
                    else frame.copy()
                )
                contact_frame = frame.copy()
                after_contact_frame = None
                waiting_for_after_contact_frame = True

            if motion_score > camera_shake_score:
                camera_shake_score = motion_score

            if flow_score > max_optical_flow_score:
                max_optical_flow_score = flow_score

            if score > proximity_score:
                proximity_score = score

            if start_frame is None:
                start_frame = frame.copy()

            end_frame = frame.copy()
            end_frame_index = frame_i
            end_time_sec = frame_i / fps

            if p > 0 and first_person_frame_index is None:
                first_person_frame_index = frame_i
                first_person_time_sec = frame_i / fps

            if v > 0 and first_vehicle_frame_index is None:
                first_vehicle_frame_index = frame_i
                first_vehicle_time_sec = frame_i / fps

            event_activity_score = max(
                to_float(score),
                to_float(impact_spike_score),
                to_float(contact_spike_score)
            )

            if event_activity_score > best_score:

                best_score = event_activity_score
                best_frame = frame.copy()
                best_frame_index = frame_i
                best_time_sec = frame_i / fps

            if score > 0:
                last_activity = now

            if crash_trigger.get("triggered"):
                crash_safety_triggered = True
                motion_triggered = True
                for reason in crash_trigger.get(
                    "trigger_reasons",
                    []
                ):
                    reason_text = str(reason)
                    if reason_text not in trigger_reasons:
                        trigger_reasons.append(
                            reason_text
                        )
                last_activity = now

            # =============================================
            # END EVENT
            # =============================================

            if (
                last_activity
                and now - last_activity >
                EVENT_END_TIMEOUT
            ):

                finalize_event(
                    path=path,
                    decisions=decisions,
                    session=session,
                    tesla_event_groups=tesla_event_groups,
                    ai_review_context=ai_review_context,
                    event_id=event_id,
                    start_frame=start_frame,
                    best_frame=best_frame,
                    end_frame=end_frame,
                    event_score=event_score,
                    persons=persons,
                    vehicles=vehicles,
                    active_frames=active_frames,
                    max_motion_score=max_motion_score,
                    motion_spike_time_sec=motion_spike_time_sec,
                    camera_shake_score=camera_shake_score,
                    optical_flow_score_value=max_optical_flow_score,
                    scene_change_score_value=max_scene_change_score,
                    proximity_score=proximity_score,
                    local_motion_score=max_local_motion_score,
                    contact_time_sec=contact_time_sec,
                    timeline_context={
                        "fps": fps,
                        "video_duration_sec": video_duration_sec,
                        "start_frame_index": event_start_frame_index,
                        "start_time_sec": event_start_time_sec,
                        "first_person_frame_index": first_person_frame_index,
                        "first_person_time_sec": first_person_time_sec,
                        "first_vehicle_frame_index": first_vehicle_frame_index,
                        "first_vehicle_time_sec": first_vehicle_time_sec,
                        "peak_frame_index": best_frame_index,
                        "peak_time_sec": best_time_sec,
                        "end_frame_index": end_frame_index,
                        "end_time_sec": end_time_sec,
                        "motion_spike_frame_index": motion_spike_frame_index,
                        "contact_frame_index": contact_frame_index
                    },
                    before_impact_frame=before_impact_frame,
                    impact_frame=impact_frame,
                    after_impact_frame=after_impact_frame,
                    before_contact_frame=before_contact_frame,
                    contact_frame=contact_frame,
                    after_contact_frame=after_contact_frame,
                    crash_safety_triggered=crash_safety_triggered,
                    motion_triggered=motion_triggered,
                    trigger_reasons=trigger_reasons,
                    object_persistence=finalize_object_persistence(
                        event_object_tracker
                    )
                )

                active = False

                rolling = 0.0

                event_score = 0
                active_frames = 0

                persons = 0
                vehicles = 0

                best_frame = None
                best_score = 0
                start_frame = None
                end_frame = None
                max_motion_score = 0.0
                motion_spike_time_sec = None
                motion_spike_frame_index = None
                camera_shake_score = 0.0
                max_optical_flow_score = 0.0
                max_scene_change_score = 0.0
                max_impact_spike_score = 0.0
                max_local_motion_score = 0.0
                max_contact_spike_score = 0.0
                contact_time_sec = None
                contact_frame_index = None
                proximity_score = 0.0
                before_impact_frame = None
                impact_frame = None
                after_impact_frame = None
                waiting_for_after_impact_frame = False
                before_contact_frame = None
                contact_frame = None
                after_contact_frame = None
                waiting_for_after_contact_frame = False
                event_start_frame_index = None
                event_start_time_sec = None
                first_person_frame_index = None
                first_person_time_sec = None
                first_vehicle_frame_index = None
                first_vehicle_time_sec = None
                best_frame_index = None
                best_time_sec = None
                end_frame_index = None
                end_time_sec = None
                crash_safety_triggered = False
                motion_triggered = False
                trigger_reasons = []
                event_object_tracker = create_object_tracker()

                last_activity = 0

                event_id += 1

        recent_motion_scores.append(
            to_float(motion_score)
        )
        recent_motion_scores = recent_motion_scores[-8:]

        frame_i += 1

    # =====================================================
    # FINALIZE UNFINISHED EVENT
    # =====================================================

    if active and best_frame is not None:

        console.print(
            "  [yellow]finalizing unfinished event[/yellow]"
        )

        finalize_event(
            path=path,
            decisions=decisions,
            session=session,
            tesla_event_groups=tesla_event_groups,
            ai_review_context=ai_review_context,
            event_id=event_id,
            start_frame=start_frame,
            best_frame=best_frame,
            end_frame=end_frame,
            event_score=event_score,
            persons=persons,
            vehicles=vehicles,
            active_frames=active_frames,
            max_motion_score=max_motion_score,
            motion_spike_time_sec=motion_spike_time_sec,
            camera_shake_score=camera_shake_score,
            optical_flow_score_value=max_optical_flow_score,
            scene_change_score_value=max_scene_change_score,
            proximity_score=proximity_score,
            local_motion_score=max_local_motion_score,
            contact_time_sec=contact_time_sec,
            timeline_context={
                "fps": fps,
                "video_duration_sec": video_duration_sec,
                "start_frame_index": event_start_frame_index,
                "start_time_sec": event_start_time_sec,
                "first_person_frame_index": first_person_frame_index,
                "first_person_time_sec": first_person_time_sec,
                "first_vehicle_frame_index": first_vehicle_frame_index,
                "first_vehicle_time_sec": first_vehicle_time_sec,
                "peak_frame_index": best_frame_index,
                "peak_time_sec": best_time_sec,
                "end_frame_index": end_frame_index,
                "end_time_sec": end_time_sec,
                "motion_spike_frame_index": motion_spike_frame_index,
                "contact_frame_index": contact_frame_index
            },
            before_impact_frame=before_impact_frame,
            impact_frame=impact_frame,
            after_impact_frame=after_impact_frame,
            before_contact_frame=before_contact_frame,
            contact_frame=contact_frame,
            after_contact_frame=after_contact_frame,
            crash_safety_triggered=crash_safety_triggered,
            motion_triggered=motion_triggered,
            trigger_reasons=trigger_reasons,
            object_persistence=finalize_object_persistence(
                event_object_tracker
            )
        )

    # =====================================================
    # NO EVENTS
    # =====================================================

    if path not in decisions:

        console.print(
            "  [green]no events detected[/green]"
        )

        save_decision(
            decisions,
            path,
            0
        )

    cap.release()
    mark_metric_incidents(
        profile_metric,
        session.get(
            "incidents",
            []
        )[incident_count_started:]
    )
    finish_profile_metric(
        profile_metric,
        session
    )

# =========================================================
# MOVE FILES
# =========================================================

def move_files(decisions):

    moved_paths = {}

    def folder(priority):

        if priority == 2:
            return IMPORTANT

        elif priority == 1:
            return REVIEW

        return IGNORE

    console.print(
        "\n[bold cyan]Moving files...[/bold cyan]"
    )

    for src, priority in decisions.items():

        dst_folder = folder(priority)

        name = os.path.basename(src)

        dst = os.path.join(
            dst_folder,
            name
        )

        base, ext = os.path.splitext(name)

        i = 1

        while os.path.exists(dst):

            dst = os.path.join(
                dst_folder,
                f"{base}_{i}{ext}"
            )

            i += 1

        try:

            shutil.move(src, dst)

            moved_paths[
                os.path.abspath(src)
            ] = os.path.abspath(dst)

            console.print(
                f"  → {name} "
                f"→ "
                f"[bold]{os.path.basename(dst_folder)}[/bold]"
            )

        except Exception as e:

            console.print(
                f"[red]MOVE FAILED:[/red] {src}"
            )

            console.print(e)

    return moved_paths

# =========================================================
# CLEAN EMPTY FOLDERS
# =========================================================

def clean_empty_dirs(folder):

    console.print(
        "\n[bold cyan]Cleaning folders...[/bold cyan]"
    )

    for root, dirs, files in os.walk(
        folder,
        topdown=False
    ):

        # remove metadata/junk
        for f in files:

            full = os.path.join(root, f)

            if f.lower().endswith(
                (
                    ".json",
                    ".thumb",
                    ".ini"
                )
            ):

                try:
                    os.remove(full)
                except:
                    pass

        remaining = os.listdir(root)

        if remaining:
            continue

        try:

            os.rmdir(root)

            console.print(
                f"  removed: {root}"
            )

        except:
            pass

# =========================================================
# SUMMARY
# =========================================================

def generate_summary(decisions):

    important = 0
    review = 0
    ignore = 0

    for p in decisions.values():

        if p == 2:
            important += 1

        elif p == 1:
            review += 1

        else:
            ignore += 1

    table = Table(title="Scan Results")

    table.add_column("Category")
    table.add_column("Count")

    table.add_row(
        "[red]IMPORTANT[/red]",
        str(important)
    )

    table.add_row(
        "[yellow]REVIEW[/yellow]",
        str(review)
    )

    table.add_row(
        "[green]IGNORE[/green]",
        str(ignore)
    )

    console.print(table)

# =========================================================
# MAIN
# =========================================================

def parse_args():

    parser = argparse.ArgumentParser(
        description="Scan Tesla Sentry footage with Mimir."
    )

    parser.add_argument(
        "--input",
        help="Optional TeslaCam/SentryClips folder to scan without moving source files."
    )

    parser.add_argument(
        "--mode",
        choices=[
            "fast",
            "balanced",
            "quality",
            "thorough"
        ],
        default="balanced",
        help="Scan mode for performance control. Defaults to balanced."
    )

    parser.add_argument(
        "--vlm",
        help="Optional Ollama vision model override, for example qwen2.5vl:7b."
    )

    parser.add_argument(
        "--ai-review-budget",
        type=int,
        default=None,
        help="Maximum event groups to review with the vision model. Defaults: fast=20, balanced=50, quality/thorough=150."
    )

    parser.add_argument(
        "--library-root",
        default=str(default_library_root()),
        help="Folder where Mimir stores organized local library scans."
    )

    parser.add_argument(
        "--source-action",
        choices=sorted(SOURCE_ACTIONS),
        default="analyze_only",
        help="What to do with source clips after scanning. Defaults to analyze_only."
    )

    return parser.parse_args()


def main():

    global AI_REVIEW_BUDGET

    scan_started_perf_counter = time.perf_counter()

    args = parse_args()

    scan_mode = configure_scan_mode(args.mode)
    AI_REVIEW_BUDGET = ai_budget_for_mode(
        scan_mode,
        args.ai_review_budget
    )
    vlm_model = configure_vlm_model(args.vlm)
    library_root = os.path.abspath(
        os.path.expanduser(args.library_root)
    )
    source_action = str(
        args.source_action or "analyze_only"
    ).strip().lower()

    emit_progress(
        "initializing",
        "Starting local scan."
    )

    safe_input_mode = args.input is not None

    if safe_input_mode:

        scan_folder = os.path.abspath(
            os.path.expanduser(args.input)
        )

        if not os.path.isdir(scan_folder):

            console.print(
                f"[bold red]Input folder does not exist:[/bold red] {scan_folder}"
            )

            emit_progress(
                "error",
                f"Input folder does not exist: {scan_folder}"
            )

            return

    else:

        scan_folder = INCOMING

    emit_progress(
        "initializing",
        "Input folder validated.",
        extra={
            "input_folder": scan_folder
        }
    )

    session = create_session(
        scan_folder,
        safe_input_mode,
        scan_mode,
        library_root,
        source_action
    )

    set_active_performance(
        session["performance"]
    )
    set_active_profiler(
        create_performance_profiler()
    )

    emit_progress(
        "reading_clips",
        "Reading clips from selected folder."
    )

    console.print(
        f"[bold cyan]Scan mode:[/bold cyan] {scan_mode} "
        f"| sample_fps={SAMPLE_FPS:g} "
        f"| vlm={vlm_model} "
        f"| ai_review_budget={AI_REVIEW_BUDGET}"
    )

    with profile_stage("source_discovery"):
        source_discovery = discover_input_source(scan_folder)
    set_source_video_metadata(
        source_discovery.get(
            "video_metadata",
            {}
        )
    )
    source_report = build_source_report(source_discovery)

    videos = source_discovery.get(
        "video_files",
        []
    )
    event_groups = source_discovery.get(
        "event_groups",
        []
    )
    event_json_paths = source_discovery.get(
        "event_json_files",
        []
    )

    session["selected_input"] = source_discovery.get(
        "selected_input"
    )
    session["detected_source_type"] = source_discovery.get(
        "detected_source_type"
    )
    session["drive_root"] = source_discovery.get(
        "drive_root"
    )
    session["teslacam_root"] = source_discovery.get(
        "teslacam_root"
    )
    session["scan_roots"] = source_discovery.get(
        "scan_roots",
        []
    )
    session["event_groups"] = source_discovery.get(
        "event_groups",
        []
    )
    session["scan_pipeline"] = "grouped_candidate_pipeline_v1"
    session["source_categories_found"] = source_discovery.get(
        "source_categories_found",
        []
    )
    session["event_groups_found"] = to_int(
        source_discovery.get(
            "event_groups_found",
            0
        )
    )
    session["multi_camera_groups"] = to_int(
        sum(
            1
            for group in event_groups
            if to_int(
                group.get(
                    "camera_count",
                    0
                )
            ) > 1
        )
    )
    session["single_camera_groups"] = to_int(
        max(
            0,
            len(event_groups) - session["multi_camera_groups"]
        )
    )
    session["camera_suffixes_found"] = source_discovery.get(
        "camera_suffixes_found",
        []
    )
    session["grouping_version"] = "grouped_incidents_v1"
    session["source_discovery_warnings"] = source_discovery.get(
        "warnings",
        []
    )
    session["source_report"] = source_report
    session["source_discovery_warnings"] = source_report.get(
        "warnings",
        []
    )

    decisions = {}

    emit_progress(
        "reading_clips",
        f"Discovered {len(videos)} video clips.",
        current=0,
        total=len(videos),
        extra={
            "event_json_files_found": len(event_json_paths),
            "detected_source_type": session["detected_source_type"]
        }
    )

    emit_progress(
        "reading_event_metadata",
        "Reading event metadata.",
        current=0,
        total=len(videos)
    )

    session["event_json_files_found"] = to_int(
        len(event_json_paths)
    )

    with profile_stage("metadata_reading"):
        session["tesla_events_found"] = to_int(
            count_readable_event_json_files(event_json_paths)
        )
    session["source_events_found"] = to_int(
        session["tesla_events_found"]
    )

    total = len(videos)

    emit_progress(
        "reading_event_metadata",
        f"Read {session['tesla_events_found']} source event metadata files.",
        current=0,
        total=total,
        extra={
            "event_json_files_found": session["event_json_files_found"],
            "tesla_events_found": session["tesla_events_found"],
            "source_events_found": session["tesla_events_found"]
        }
    )

    emit_progress(
        "grouping_camera_angles",
        "Grouping camera angles.",
        current=0,
        total=total
    )

    with profile_stage("camera_grouping"):
        tesla_event_groups = build_teslacam_event_groups(videos)

    ai_review_context = build_ai_review_context(
        event_groups,
        AI_REVIEW_BUDGET
    )
    set_active_ai_review_context(
        ai_review_context
    )
    update_session_ai_review_budget_fields(
        session,
        ai_review_context
    )

    set_progress_context(
        total=total,
        current=0,
        current_video=None
    )

    emit_progress(
        "grouping_camera_angles",
        f"Grouped {session['event_groups_found']} source event sets.",
        current=0,
        total=total,
        extra={
            "tesla_event_groups": len(tesla_event_groups),
            "event_groups_found": session["event_groups_found"],
            "ai_review_budget": session["ai_review_budget"],
            "ai_review_candidates": session["ai_review_candidates"]
        }
    )

    if total == 0:

        session["status"] = "complete"
        session["finished_at"] = timestamp()
        session["clips_processed"] = to_int(0)

        finalize_performance_metrics(
            session,
            scan_started_perf_counter
        )

        update_session_ai_capabilities(session)

        write_performance_reports(session)
        finalize_performance_metrics(
            session,
            scan_started_perf_counter
        )

        write_session_json(session)

        emit_progress(
            "error",
            "No video clips were found in the selected source.",
            current=0,
            total=0,
            percent=None
        )

        print_performance_summary(session)

        console.print(
            "[bold red]No video clips were found in the selected source.[/bold red]"
        )

        for warning in session.get("source_discovery_warnings", []):
            console.print(f"[yellow]warning:[/yellow] {warning}")

        return

    console.print(
        Panel.fit(
            f"Found {total} video clips",
            title="Tesla AI Scanner"
        )
    )

    with Progress(

        TextColumn(
            "[progress.description]{task.description}"
        ),

        BarColumn(),

        "[progress.percentage]{task.percentage:>3.0f}%",

        TimeElapsedColumn(),

        TimeRemainingColumn(),

        console=console

    ) as progress:

        task = progress.add_task(
            "[cyan]Processing event groups...",
            total=max(
                1,
                len(event_groups)
            )
        )

        video_index = 0

        for event_group in event_groups:

            prepass_started = time.perf_counter()
            with profile_stage("group_prepass"):
                prepass_result = run_event_group_prepass(
                    event_group,
                    scan_mode
                )
            prepass_elapsed = time.perf_counter() - prepass_started
            update_event_group_with_prepass(
                event_group,
                prepass_result
            )
            ai_review_context = refresh_ai_review_context(
                ai_review_context,
                event_groups,
                AI_REVIEW_BUDGET
            )
            set_active_ai_review_context(
                ai_review_context
            )
            update_session_ai_review_budget_fields(
                session,
                ai_review_context
            )
            session["prepass_groups_processed"] = to_int(
                session.get("prepass_groups_processed", 0)
            ) + 1
            session["prepass_runtime_sec"] = round(
                to_float(session.get("prepass_runtime_sec", 0.0)) + prepass_elapsed,
                3
            )

            if prepass_result.get("deep_analysis"):
                session["prepass_candidates_found"] = to_int(
                    session.get("prepass_candidates_found", 0)
                ) + 1

            else:
                session["skipped_low_interest_groups"] = to_int(
                    session.get("skipped_low_interest_groups", 0)
                ) + 1

                for clip in camera_clips_for_group(event_group):
                    video = clip.get("path") if isinstance(clip, dict) else None

                    if video:
                        save_decision(
                            decisions,
                            video,
                            0
                        )

                console.print(
                    f"Group {event_group.get('event_group_id') or event_group.get('event_id')} "
                    f"| prepass={prepass_result.get('prepass_motion_score')} "
                    f"| skipped={prepass_result.get('skipped_reason')}"
                )
                progress.advance(task)
                continue

            deep_started = time.perf_counter()
            session["deep_analysis_groups"] = to_int(
                session.get("deep_analysis_groups", 0)
            ) + 1

            group_start_incident_index = len(
                session.get(
                    "incidents",
                    []
                )
            )
            group_clips = camera_clips_for_group(
                event_group
            )
            group_session = dict(session)
            group_session["incidents"] = list(
                session.get(
                    "incidents",
                    []
                )
            )
            group_session["storage_warnings"] = session.setdefault(
                "storage_warnings",
                []
            )
            group_session["_defer_incident_publish"] = True

            for clip in group_clips:

                video = clip.get(
                    "path"
                )

                if not video:
                    continue

                video_index += 1

                process_video(
                    video,
                    decisions,
                    group_session,
                    tesla_event_groups,
                    ai_review_context=ai_review_context,
                    candidate_windows=event_group.get("candidate_windows", []),
                    current_index=video_index,
                    total_videos=total
                )

                add_performance_value(
                    "videos_processed",
                    1
                )

                emit_progress(
                    "scanning_video",
                    f"Finished clip {video_index} of {total}",
                    current=video_index,
                    total=total,
                    extra={
                        "current_video": os.path.basename(video)
                    }
                )

            primary_incident = consolidate_group_incidents(
                group_session,
                event_group,
                group_start_incident_index
            )
            if isinstance(primary_incident, dict):
                session["incidents"].append(
                    primary_incident
                )
                add_performance_value(
                    "incidents_created",
                    1
                )
                write_incident_json(
                    primary_incident
                )
                group_priority = severity_priority(
                    primary_incident.get("severity")
                )

                for clip in group_clips:
                    video = clip.get("path") if isinstance(clip, dict) else None

                    if video:
                        save_decision(
                            decisions,
                            video,
                            group_priority
                        )

            print_group_summary(
                event_group,
                primary_incident
            )
            update_session_ai_review_budget_fields(
                session,
                ai_review_context
            )
            session["deep_analysis_runtime_sec"] = round(
                to_float(session.get("deep_analysis_runtime_sec", 0.0))
                + (time.perf_counter() - deep_started),
                3
            )

            progress.advance(task)

    if safe_input_mode:

        if source_action == "analyze_only":

            console.print(
                "\n[bold cyan]Safe input mode enabled. Source files were not moved.[/bold cyan]"
            )

        else:

            console.print(
                "\n[bold cyan]Safe input mode enabled. Library storage action will copy before any removal.[/bold cyan]"
            )

    else:

        moved_paths = move_files(decisions)

        update_session_video_paths_after_move(
            session,
            moved_paths
        )

        clean_empty_dirs(INCOMING)

    generate_summary(decisions)

    important = 0
    review = 0
    ignore = 0

    for p in decisions.values():

        if p == 2:
            important += 1

        elif p == 1:
            review += 1

        else:
            ignore += 1

    session["status"] = "complete"
    session["finished_at"] = timestamp()
    session["clips_processed"] = to_int(total)
    session["important"] = to_int(important)
    session["review"] = to_int(review)
    session["ignore"] = to_int(ignore)
    session["grouping_debug"] = {
        "video_files_found": to_int(len(videos)),
        "event_groups_built": to_int(len(event_groups)),
        "incidents_created": to_int(len(session.get("incidents", []))),
        "grouping_ratio": (
            f"{to_int(len(session.get('incidents', [])))} incidents "
            f"from {to_int(len(event_groups))} groups"
        )
    }

    finalize_performance_metrics(
        session,
        scan_started_perf_counter
    )
    update_session_ai_review_budget_fields(
        session,
        ai_review_context
    )

    update_session_ai_capabilities(session)

    if safe_input_mode:

        apply_library_storage(
            session,
            decisions,
            library_root,
            source_action
        )

    elif source_action != "analyze_only":

        session["storage_warnings"].append(
            "source_action was ignored because --input was not provided; legacy INCOMING behavior was preserved."
        )

    finalize_performance_metrics(
        session,
        scan_started_perf_counter
    )
    write_performance_reports(session)
    finalize_performance_metrics(
        session,
        scan_started_perf_counter
    )

    print_performance_summary(session)
    print_storage_summary(session)

    emit_progress(
        "writing_output",
        "Writing latest session output.",
        current=total,
        total=total
    )

    write_library_session_json(session)
    write_session_json(session)

    emit_progress(
        "complete",
        "Scan complete.",
        current=total,
        total=total,
        percent=100
    )

    console.print(
        "\n[bold green]DONE.[/bold green]"
    )

# =========================================================
# START
# =========================================================

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        emit_progress(
            "error",
            f"Scan failed: {exc}"
        )
        raise
