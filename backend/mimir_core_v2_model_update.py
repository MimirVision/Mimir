"""PyInstaller entrypoint for installing/inspecting a detector model update.

See mimir_core_v2/model_update.py for the actual validation and install
logic -- this file only exists so PyInstaller has a standalone script to
freeze into mimir-core-v2-model-update.exe, matching the other Core v2
sidecar entrypoints.
"""

from __future__ import annotations

import sys

from mimir_core_v2.model_update import _main


if __name__ == "__main__":
    sys.exit(_main())
