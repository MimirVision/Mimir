id="hz27zy"
import os
import cv2
import shutil
import time
import base64
import json
import requests
import argparse
import math
import re
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
        "ai_min_event_score": 28.0,
        "description": "Fast mode prioritizes speed and sends only stronger candidate events to AI review."
    },
    "balanced": {
        "sample_fps": 2.0,
        "ai_min_event_score": 0.0,
        "description": "Balanced mode uses the current default local review behavior."
    },
    "quality": {
        "sample_fps": 3.0,
        "ai_min_event_score": 0.0,
        "description": "Quality mode samples more frames and prioritizes catching suspicious events."
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

# TeslaCam camera grouping
EXPECTED_TESLACAM_CAMERAS = [
    "front",
    "back",
    "left_repeater",
    "right_repeater",
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
    "left_pillar": "left_repeater",
    "right_pillar": "right_repeater",
    "left": "left_repeater",
    "right": "right_repeater",
}

TESLACAM_CLIP_PATTERN = re.compile(
    r"^(?P<event_group_id>\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})-(?P<camera>.+)\.mp4$",
    re.IGNORECASE
)

# AI
AI_ENABLED = True
AI_REVIEW_AVAILABLE = False
AI_REVIEW_MODEL = None
AI_REVIEW_ERROR = None
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


# =========================================================
# PERFORMANCE
# =========================================================

ACTIVE_PERFORMANCE = None
PROGRESS_STARTED_AT = None
PROGRESS_LAST_PERCENT = 0.0
PROGRESS_CONTEXT = {
    "total_videos": 0,
    "current_video_index": 0,
    "current_video": None
}
SOURCE_VIDEO_METADATA = {}


def create_performance_metrics():

    return {
        "total_runtime_sec": 0.0,
        "videos_processed": 0,
        "avg_sec_per_video": 0.0,
        "yolo_runtime_sec": 0.0,
        "ai_runtime_sec": 0.0,
        "frames_sampled": 0,
        "ai_calls": 0,
        "incidents_created": 0
    }


def set_active_performance(performance):

    global ACTIVE_PERFORMANCE

    ACTIVE_PERFORMANCE = performance


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
            "incidents_created"
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
            "ai_runtime_sec"
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
            "incidents_created"
        ]:
            set_performance_value(
                session,
                metric,
                performance.get(
                    metric,
                    0
                )
            )

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
        f"- Avg per video: {to_float(performance.get('avg_sec_per_video')):.1f} sec"
    )
    console.print(
        f"- Frames sampled: {to_int(performance.get('frames_sampled'))}"
    )
    console.print(
        f"- AI calls: {to_int(performance.get('ai_calls'))}"
    )
    console.print(
        f"- YOLO time: {to_float(performance.get('yolo_runtime_sec')):.1f} sec"
    )
    console.print(
        f"- AI time: {to_float(performance.get('ai_runtime_sec')):.1f} sec"
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

    return {
        "event_group_id": match.group("event_group_id"),
        "camera_name": camera
    }


def raw_teslacam_camera_suffix(path):

    filename = os.path.basename(path)
    match = TESLACAM_CLIP_PATTERN.match(filename)

    if not match:
        return None

    return match.group("camera").lower()


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
            camera_group_id
            if camera_group_id
            else Path(video_path).stem
        )
        group_key = (
            source_category,
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
                "timestamp": camera_group_id,
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

    event_groups = []

    for group in grouped.values():

        cameras_available = group["cameras_available"]
        group["camera_count"] = to_int(
            len(cameras_available)
        )
        group["missing_common_cameras"] = [
            camera
            for camera in EXPECTED_TESLACAM_CAMERAS
            if camera not in cameras_available
        ]
        event_groups.append(group)

        for file_path in group["files"]:

            parsed = parse_teslacam_filename(file_path)
            video_metadata[
                absolute_path_string(file_path)
            ] = {
                "source_category": group["source_category"],
                "event_folder": group["event_folder"],
                "camera": parsed["camera_name"] if parsed else None,
                "camera_group_id": parsed["event_group_id"] if parsed else None,
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

    grouped = build_source_event_groups(video_files)
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


def fallback_ai_review(reason, raw_response=""):

    response = raw_response.strip().upper()
    severity = "IGNORE"
    confidence = 0.25
    summary = "AI review was unavailable or did not identify a clear interaction."
    recommended_action = "No action needed unless the footage looks suspicious."

    if response in VALID_AI_SEVERITIES:
        severity = response
        confidence = 0.5
        summary = "AI returned a legacy one-word decision."
        recommended_action = "Review according to the severity."

    else:

        for legacy_severity in VALID_AI_SEVERITIES:

            if legacy_severity in response:
                severity = legacy_severity
                confidence = 0.4
                summary = "AI returned invalid JSON but included a legacy severity decision."
                recommended_action = "Review according to the severity."
                break

    return normalize_ai_review(
        {
            "severity": severity,
            "confidence": confidence,
            "event_type": "ai_review_fallback",
            "summary": summary,
            "evidence": [reason],
            "recommended_action": recommended_action
        }
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
        "ai_decision": normalized["severity"],
        "ai_confidence": normalized["confidence"],
        "event_type": normalized["event_type"],
        "summary": normalized["summary"],
        "evidence": normalized["evidence"],
        "recommended_action": normalized["recommended_action"]
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


def normalize_ai_review(data):

    severity = str(
        data.get("severity", "IGNORE")
    ).strip().upper()

    if severity not in VALID_AI_SEVERITIES:
        severity = "IGNORE"

    evidence = data.get("evidence", [])

    if not isinstance(evidence, list):
        evidence = [str(evidence)]

    evidence = [
        str(item).strip()
        for item in evidence
        if str(item).strip()
    ]

    if not evidence:
        evidence = ["No specific evidence was returned by the AI review."]

    summary = str(
        data.get("summary", "")
    ).strip()

    if not summary:
        summary = "AI completed the review but did not provide a summary."

    recommended_action = str(
        data.get("recommended_action", "")
    ).strip()

    if not recommended_action:
        recommended_action = "Review manually if needed."

    return to_json_safe({
        "severity": severity,
        "confidence": clamp_confidence(
            data.get("confidence", 0.0)
        ),
        "event_type": normalize_event_type(
            data.get("event_type", "unknown_event")
        ),
        "summary": summary,
        "evidence": evidence[:6],
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
  "severity": "REVIEW",
  "confidence": 0.62,
  "event_type": "person_near_vehicle",
  "summary": "A person appears near the vehicle but no contact is clear.",
  "evidence": ["Person visible near the vehicle", "No clear door contact shown"],
  "recommended_action": "Review the footage briefly."
}
Do not include markdown, code fences, or extra commentary.

Allowed severity values are IMPORTANT, REVIEW, and IGNORE.

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

            return normalize_ai_review(parsed)

        except Exception as e:

            console.print(
                f"[yellow]AI JSON FALLBACK:[/yellow] {e}"
            )

            return fallback_ai_review(
                "AI output was not valid JSON.",
                response
            )

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

        return fallback_ai_review(
            "AI review failed."
        )

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

def analyze(frame):

    h, w = frame.shape[:2]

    # crop away top traffic area
    frame = frame[int(h * IGNORE_TOP_RATIO):, :]

    h, w = frame.shape[:2]

    yolo_started = time.perf_counter()

    try:

        results = yolo(frame, verbose=False)

    finally:

        add_performance_value(
            "yolo_runtime_sec",
            time.perf_counter() - yolo_started
        )

    score = 0

    persons = 0
    vehicles = 0

    for r in results:

        for box in r.boxes:

            conf = float(box.conf[0])

            if conf < MIN_CONF:
                continue

            cls = int(box.cls[0])

            x1, y1, x2, y2 = box.xyxy[0]

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

                vehicle_score = 0

                # vehicles matter much less
                vehicle_score += prox * 1.5

                vehicle_score += area_ratio * 6

                if conf > 0.75:
                    vehicle_score += 1

                score += vehicle_score

    return (
        to_float(score),
        to_int(persons),
        to_int(vehicles)
    )

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

NORMAL_TRAFFIC_KEYWORDS = [
    "normal traffic",
    "distant pedestrian",
    "distant pedestrians",
    "harmless movement",
    "empty scene",
    "passing traffic",
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


def resolve_final_severity(
    current_severity,
    ai_review,
    impact_analysis,
    contact_analysis,
    max_motion_score,
    persons,
    vehicles,
    active_frames,
    timeline_markers
):

    ai_fields = ai_review_json_fields(
        ai_review
    )
    pre_severity = priority_label(
        severity_priority(
            current_severity
        )
    )
    final_severity = pre_severity
    reasons = []

    event_type_text = str(
        ai_fields.get(
            "event_type",
            ""
        )
    ).lower()
    ai_text_blob = normalized_text_blob(
        ai_fields.get("summary"),
        ai_fields.get("evidence", [])
    )
    impact_reason_blob = normalized_text_blob(
        impact_analysis.get("impact_reasons", [])
    )
    combined_review_blob = normalized_text_blob(
        event_type_text,
        ai_text_blob
    )

    marker_blob = normalized_text_blob(
        [
            marker.get("type", "")
            for marker in timeline_markers
            if isinstance(marker, dict)
        ],
        [
            marker.get("label", "")
            for marker in timeline_markers
            if isinstance(marker, dict)
        ],
        [
            marker.get("description", "")
            for marker in timeline_markers
            if isinstance(marker, dict)
        ]
    )

    impact_level = str(
        impact_analysis.get(
            "impact_level",
            "NONE"
        )
    ).upper()
    impact_score = to_float(
        impact_analysis.get(
            "impact_score",
            0.0
        )
    )
    possible_impact = bool(
        impact_analysis.get(
            "possible_impact",
            False
        )
    )
    impact_reasons = impact_analysis.get(
        "impact_reasons",
        []
    )
    crash_safety_triggered = bool(
        impact_analysis.get(
            "crash_safety_triggered",
            False
        )
    )
    motion_triggered = bool(
        impact_analysis.get(
            "motion_triggered",
            False
        )
    )
    trigger_reasons = impact_analysis.get(
        "trigger_reasons",
        []
    )
    contact_level = str(
        contact_analysis.get(
            "contact_level",
            "NONE"
        )
    ).upper()
    contact_score = to_float(
        contact_analysis.get(
            "contact_score",
            0.0
        )
    )
    possible_contact = bool(
        contact_analysis.get(
            "possible_contact",
            False
        )
    )
    contact_reasons = contact_analysis.get(
        "contact_reasons",
        []
    )
    ai_severity = ai_fields.get(
        "ai_decision",
        "IGNORE"
    )
    ai_confidence = to_float(
        ai_fields.get(
            "ai_confidence",
            0.0
        )
    )
    ai_text_has_high_risk = (
        text_has_keyword(ai_text_blob, HIGH_RISK_TEXT_KEYWORDS)
        and not text_has_negated_high_risk(ai_text_blob)
    )
    ai_text_has_possible_interaction = (
        text_has_keyword(ai_text_blob, POSSIBLE_INTERACTION_KEYWORDS)
        and not text_has_negated_high_risk(ai_text_blob)
    )
    ai_text_has_contact_language = (
        text_has_keyword(ai_text_blob, CONTACT_TEXT_KEYWORDS)
        and not text_has_negated_high_risk(ai_text_blob)
    )
    has_normal_only_language = (
        text_has_keyword(ai_text_blob, NORMAL_TRAFFIC_KEYWORDS)
        and not ai_text_has_high_risk
        and not possible_contact
        and impact_level not in {"MEDIUM", "HIGH"}
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
        reasons.append("event_type or AI review suggests contact, impact, entry, or vandalism")

    if ai_text_has_high_risk:
        reasons.append("AI summary/evidence suggests possible contact with vehicle")

    if (
        contact_level == "MEDIUM"
        and event_type_text != "ai_review_fallback"
        and (
            ai_text_has_contact_language
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
            text_has_keyword(event_type_text, HIGH_RISK_EVENT_KEYWORDS)
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

    important_reasons = [
        reason
        for reason in reasons
        if reason not in {
            "impact_level=MEDIUM prevents IGNORE",
            "contact_level=MEDIUM prevents IGNORE",
            "person activity near vehicle with possible interaction language",
            "AI did not override contact safety signals",
        }
    ]

    if impact_level == "HIGH":
        final_severity = "IMPORTANT"

    elif impact_score >= 0.75:
        final_severity = "IMPORTANT"

    elif contact_level == "HIGH":
        final_severity = "IMPORTANT"

    elif important_reasons and not has_normal_only_language:
        final_severity = "IMPORTANT"

    elif impact_level == "MEDIUM" and severity_priority(final_severity) < 1:
        final_severity = "REVIEW"

    elif crash_safety_triggered and severity_priority(final_severity) < 1:
        final_severity = "REVIEW"

    elif contact_level == "MEDIUM" and severity_priority(final_severity) < 1:
        final_severity = "REVIEW"

    if final_severity == "IMPORTANT" and ai_severity == "IGNORE" and ai_confidence >= 0.85:
        if (
            not crash_safety_triggered
            and impact_score < 0.75
            and impact_level not in {"HIGH", "MEDIUM"}
            and contact_level not in {"HIGH", "MEDIUM"}
            and not ai_text_has_high_risk
            and not ai_text_has_contact_language
        ):
            final_severity = pre_severity
            reasons = [
                "high-confidence AI IGNORE prevented escalation"
            ]

    escalation_applied = final_severity != pre_severity

    return to_json_safe({
        "pre_escalation_severity": pre_severity,
        "final_severity": final_severity,
        "severity_reasons": reasons,
        "escalation_applied": escalation_applied
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

    with open(incident_json_path, "w", encoding="utf-8") as f:
        json.dump(
            to_json_safe(incident),
            f,
            indent=2
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
        "source_categories_found": [],
        "event_groups_found": 0,
        "camera_suffixes_found": [],
        "source_discovery_warnings": [],
        "source_report": {
            "selected_input": input_folder,
            "detected_source_type": "unknown",
            "is_supported": False,
            "teslacam_root_found": False,
            "categories_found": [],
            "mp4_files_found": 0,
            "event_groups_found": 0,
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
    hero_thumbnail
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
        timeline_markers
    )
    final_label = severity_resolution["final_severity"]

    if final_label != label:
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
        "ai_decision": ai_fields["ai_decision"],
        "ai_confidence": ai_fields["ai_confidence"],
        "event_type": ai_fields["event_type"],
        "summary": ai_fields["summary"],
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
    incident = to_json_safe(incident)

    session["incidents"].append(incident)

    add_performance_value(
        "incidents_created",
        1
    )

    write_incident_json(incident)

    return incident


def write_session_json(session):

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
    trigger_reasons=None
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

    cv2.imwrite(frame_path, best_frame)

    # =====================================================
    # AI FINAL DECISION
    # =====================================================

    ai_image_path = frame_path

    if os.path.exists(
        evidence["contact_sheet"]
    ):
        ai_image_path = evidence["contact_sheet"]

    if should_run_ai_for_event(event_score):

        ai_review = run_ai(
            ai_image_path,
            impact_focused=impact_focused_contact_sheet,
            contact_focused=contact_focused_contact_sheet
        )

    else:

        ai_review = fallback_ai_review(
            "AI review skipped in fast mode for a weaker candidate event."
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

    ai = ai_review["severity"]

    if ai == "IMPORTANT":

        priority = 2

    elif ai == "REVIEW":

        priority = 1

    else:

        priority = 0

    if not AI_REVIEW_AVAILABLE:

        if (
            impact_level == "HIGH"
            or impact_score >= 0.75
            or contact_level == "HIGH"
        ):

            priority = max(
                priority,
                2
            )

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

            priority = max(
                priority,
                1
            )

    if (
        possible_impact
        and priority == 0
        and not ai_clearly_ignored(ai_review)
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
        hero_thumbnail=evidence["hero_thumbnail"]
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
        f"| AI={ai} "
        f"| PRE={label} "
        f"| FINAL={final_label}"
    )
    console.print(
        "    severity reasons: "
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

def process_video(path, decisions, session, tesla_event_groups, current_index=None, total_videos=None):

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

        return

    fps = valid_fps(
        cap.get(cv2.CAP_PROP_FPS)
    )

    if fps <= 0:
        fps = 30.0

    video_duration_sec = video_duration_from_capture(
        cap,
        fps
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

        ret, frame = cap.read()

        if not ret:
            break

        if frame_i % step != 0:

            frame_i += 1
            continue

        add_performance_value(
            "frames_sampled",
            1
        )
        sampled_frames_in_video += 1

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

        score, p, v = analyze(frame)

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
                    trigger_reasons=trigger_reasons
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
            trigger_reasons=trigger_reasons
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
            "quality"
        ],
        default="balanced",
        help="Scan mode for performance control. Defaults to balanced."
    )

    parser.add_argument(
        "--vlm",
        help="Optional Ollama vision model override, for example qwen2.5vl:7b."
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

    scan_started_perf_counter = time.perf_counter()

    args = parse_args()

    scan_mode = configure_scan_mode(args.mode)
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

    emit_progress(
        "reading_clips",
        "Reading clips from selected folder."
    )

    console.print(
        f"[bold cyan]Scan mode:[/bold cyan] {scan_mode} "
        f"| sample_fps={SAMPLE_FPS:g} "
        f"| vlm={vlm_model}"
    )

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
    session["camera_suffixes_found"] = source_discovery.get(
        "camera_suffixes_found",
        []
    )
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

    tesla_event_groups = build_teslacam_event_groups(videos)

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
            "event_groups_found": session["event_groups_found"]
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
            "[cyan]Processing videos...",
            total=total
        )

        for video_index, video in enumerate(videos, start=1):

            process_video(
                video,
                decisions,
                session,
                tesla_event_groups,
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

    finalize_performance_metrics(
        session,
        scan_started_perf_counter
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
