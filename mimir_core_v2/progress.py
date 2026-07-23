"""Structured progress reporting shared by script and packaged runtimes."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field


PROGRESS_PREFIX = "MIMIR_PROGRESS"
PROGRESS_PROTOCOL_VERSION = "mimir_progress_v2"

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
    session_revision: int = 1
    _active_stage: str = field(default="", init=False)
    _stage_started_at: float = field(default=0.0, init=False)
    _stage_durations: dict[str, float] = field(default_factory=dict, init=False)

    def _activate_stage(self, stage: str, now: float) -> None:
        if stage == self._active_stage:
            return
        if self._active_stage and self._stage_started_at:
            elapsed = max(0.0, now - self._stage_started_at)
            self._stage_durations[self._active_stage] = self._stage_durations.get(self._active_stage, 0.0) + elapsed
        self._active_stage = stage
        self._stage_started_at = now

    def diagnostics(self) -> dict[str, object]:
        durations = dict(self._stage_durations)
        if self._active_stage and self._stage_started_at:
            durations[self._active_stage] = durations.get(self._active_stage, 0.0) + max(
                0.0,
                time.perf_counter() - self._stage_started_at,
            )
        return {
            "progress_protocol": PROGRESS_PROTOCOL_VERSION,
            "stage_runtime_sec": {key: round(value, 3) for key, value in durations.items()},
        }

    def emit(
        self,
        stage: str,
        message: str,
        completed: int = 0,
        total: int = 0,
        **extra: object,
    ) -> None:
        now = time.perf_counter()
        self._activate_stage(stage, now)
        elapsed = max(0.0, now - self.started_at)
        stage_elapsed = max(0.0, now - self._stage_started_at)
        phase = str(extra.pop("phase", "local_scan"))
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
        if phase == "ai_enrichment" and total > 0:
            overall_fraction = min(max(completed / total, 0.0), 1.0)
        if stage in {"local_results_ready", "scan_complete", "ai_enrichment_complete"}:
            overall_fraction = 1.0
        eta_sec = None
        if stage == "detecting_activity" and completed > 0 and total > completed:
            eta_sec = stage_elapsed / completed * (total - completed) + 0.75
        elif elapsed >= 2.0 and overall_fraction >= 0.08:
            eta_sec = max(0.0, elapsed * (1.0 - overall_fraction) / overall_fraction)

        payload: dict[str, object] = {
            "protocol_version": PROGRESS_PROTOCOL_VERSION,
            "session_id": self.session_id,
            "session_revision": self.session_revision,
            "phase": phase,
            "stage": stage,
            "message": message,
            "completed": int(max(0, completed)),
            "current": int(max(0, completed)),
            "total": int(max(0, total)),
            "percent": round(overall_fraction * 100.0, 1),
            "elapsed_sec": round(elapsed, 2),
            "eta_sec": round(eta_sec, 2) if eta_sec is not None else None,
            "local_results_ready": bool(extra.pop("local_results_ready", False)),
            "ai_enrichment_status": str(extra.pop("ai_enrichment_status", "disabled")),
        }
        payload.update(extra)
        print(f"{PROGRESS_PREFIX} {json.dumps(payload, separators=(',', ':'))}", flush=True)

    def complete(self, message: str, incidents_created: int, ai_enrichment_status: str = "disabled") -> None:
        now = time.perf_counter()
        self._activate_stage("scan_complete", now)
        elapsed = max(0.0, now - self.started_at)
        payload = {
            "protocol_version": PROGRESS_PROTOCOL_VERSION,
            "session_id": self.session_id,
            "session_revision": self.session_revision,
            "phase": "local_scan",
            "stage": "scan_complete",
            "message": message,
            "completed": incidents_created,
            "current": incidents_created,
            "total": incidents_created,
            "percent": 100.0,
            "elapsed_sec": round(elapsed, 2),
            "eta_sec": 0.0,
            "incidents_created": incidents_created,
            "local_results_ready": True,
            "ai_enrichment_status": ai_enrichment_status,
        }
        print(f"{PROGRESS_PREFIX} {json.dumps(payload, separators=(',', ':'))}", flush=True)
