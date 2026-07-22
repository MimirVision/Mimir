"""Structured progress reporting shared by script and packaged runtimes."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field


PROGRESS_PREFIX = "MIMIR_PROGRESS"
PROGRESS_PROTOCOL_VERSION = "mimir_progress_v1"

STAGE_WEIGHTS = {
    "reading_clips": 0.05,
    "reading_event_metadata": 0.04,
    "grouping_camera_angles": 0.05,
    "detecting_activity": 0.62,
    "reviewing_suspicious_moments": 0.12,
    "building_incident_timeline": 0.07,
    "writing_results": 0.05,
}


@dataclass
class ProgressReporter:
    session_id: str
    started_at: float = field(default_factory=time.perf_counter)

    def emit(
        self,
        stage: str,
        message: str,
        completed: int = 0,
        total: int = 0,
        **extra: object,
    ) -> None:
        elapsed = max(0.0, time.perf_counter() - self.started_at)
        stage_names = list(STAGE_WEIGHTS)
        stage_index = stage_names.index(stage) if stage in STAGE_WEIGHTS else 0
        prior_fraction = sum(STAGE_WEIGHTS[name] for name in stage_names[:stage_index])
        stage_fraction = 0.0
        if total > 0:
            stage_fraction = min(max(completed / total, 0.0), 1.0)
        overall_fraction = min(
            prior_fraction + STAGE_WEIGHTS.get(stage, 0.0) * stage_fraction,
            1.0,
        )
        eta_sec = None
        if elapsed >= 2.0 and overall_fraction >= 0.08:
            eta_sec = max(0.0, elapsed * (1.0 - overall_fraction) / overall_fraction)

        payload: dict[str, object] = {
            "protocol_version": PROGRESS_PROTOCOL_VERSION,
            "session_id": self.session_id,
            "stage": stage,
            "message": message,
            "completed": int(max(0, completed)),
            "current": int(max(0, completed)),
            "total": int(max(0, total)),
            "percent": round(overall_fraction * 100.0, 1),
            "elapsed_sec": round(elapsed, 2),
            "eta_sec": round(eta_sec, 2) if eta_sec is not None else None,
        }
        payload.update(extra)
        print(f"{PROGRESS_PREFIX} {json.dumps(payload, separators=(',', ':'))}", flush=True)

    def complete(self, message: str, incidents_created: int) -> None:
        elapsed = max(0.0, time.perf_counter() - self.started_at)
        payload = {
            "protocol_version": PROGRESS_PROTOCOL_VERSION,
            "session_id": self.session_id,
            "stage": "scan_complete",
            "message": message,
            "completed": incidents_created,
            "current": incidents_created,
            "total": incidents_created,
            "percent": 100.0,
            "elapsed_sec": round(elapsed, 2),
            "eta_sec": 0.0,
            "incidents_created": incidents_created,
        }
        print(f"{PROGRESS_PREFIX} {json.dumps(payload, separators=(',', ':'))}", flush=True)
