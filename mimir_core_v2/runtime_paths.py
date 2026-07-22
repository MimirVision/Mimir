"""Shared runtime paths for Python development and self-contained executables."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def default_output_dir() -> Path:
    configured = os.environ.get("MIMIR_OUTPUT_DIR", "").strip()
    if configured:
        return Path(configured).expanduser()
    if getattr(sys, "frozen", False):
        local_data = os.environ.get("LOCALAPPDATA", "").strip()
        if local_data:
            return Path(local_data) / "Mimir" / "MimirOutputV2"
        return Path.home() / "AppData" / "Local" / "Mimir" / "MimirOutputV2"
    return Path(__file__).resolve().parents[1] / "MimirOutputV2"
