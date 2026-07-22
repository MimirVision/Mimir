"""Camera-aware ego-vehicle regions used for local timing evidence.

The regions are conservative image-space priors, not physical-contact proof.
Users can replace them with normalized polygons in an optional calibration JSON.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


EGO_MASK_VERSION = "ego_mask_v2"

DEFAULT_POLYGONS: dict[str, list[list[tuple[float, float]]]] = {
    "left_repeater": [[(0.76, 0.18), (1.0, 0.18), (1.0, 1.0), (0.60, 1.0)]],
    "right_repeater": [[(0.0, 0.18), (0.24, 0.18), (0.40, 1.0), (0.0, 1.0)]],
    "front": [[(0.0, 0.80), (1.0, 0.80), (1.0, 1.0), (0.0, 1.0)]],
    "rear": [[(0.0, 0.76), (1.0, 0.76), (1.0, 1.0), (0.0, 1.0)]],
    "back": [[(0.0, 0.76), (1.0, 0.76), (1.0, 1.0), (0.0, 1.0)]],
    "unknown": [
        [(0.0, 0.50), (0.08, 0.50), (0.12, 1.0), (0.0, 1.0)],
        [(0.92, 0.50), (1.0, 0.50), (1.0, 1.0), (0.88, 1.0)],
        [(0.0, 0.94), (1.0, 0.94), (1.0, 1.0), (0.0, 1.0)],
    ],
}


def load_calibration(path: str | Path | None) -> dict[str, Any]:
    if not path:
        return {}
    calibration_path = Path(path)
    if not calibration_path.exists():
        return {}
    try:
        data = json.loads(calibration_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def polygons_for_camera(camera: str, calibration: dict[str, Any] | None = None) -> tuple[list, str]:
    normalized_camera = str(camera or "unknown").lower()
    calibration = calibration or {}
    cameras = calibration.get("cameras") if isinstance(calibration.get("cameras"), dict) else {}
    entry = cameras.get(normalized_camera) if isinstance(cameras, dict) else None
    polygons = entry.get("polygons") if isinstance(entry, dict) else None
    if isinstance(polygons, list) and polygons:
        return polygons, "user_calibration"
    return DEFAULT_POLYGONS.get(normalized_camera, DEFAULT_POLYGONS["unknown"]), "camera_template"


def mask_for_frame(cv2: Any, shape: tuple[int, int], camera: str, calibration: dict[str, Any] | None = None):
    import numpy as np  # type: ignore

    height, width = shape
    mask = np.zeros((height, width), dtype="uint8")
    polygons, source = polygons_for_camera(camera, calibration)
    for polygon in polygons:
        points = []
        for point in polygon if isinstance(polygon, list) else []:
            if not isinstance(point, (list, tuple)) or len(point) != 2:
                continue
            x = max(0, min(width - 1, int(float(point[0]) * width)))
            y = max(0, min(height - 1, int(float(point[1]) * height)))
            points.append([x, y])
        if len(points) >= 3:
            cv2.fillPoly(mask, [np.asarray(points, dtype="int32")], 255)
    return mask, source
