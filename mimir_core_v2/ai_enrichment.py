"""Asynchronous AI second-opinion enrichment for an existing local session."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .ai_reviewer import (
    AI_REVIEW_VERSION,
    DEFAULT_AI_TIMEOUT_SEC,
    empty_ai_review,
    review_event_group,
    should_review_with_ai,
    write_ai_quality_report,
)
from .output_writer import write_latest_session
from .progress import ProgressReporter


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _event_group_from_incident(incident: dict[str, Any]) -> dict[str, Any]:
    clips = incident.get("camera_clips") if isinstance(incident.get("camera_clips"), list) else []
    return {
        "event_group_id": incident.get("event_group_id", ""),
        "event_timestamp": incident.get("event_timestamp", ""),
        "display_title": incident.get("display_title", ""),
        "display_timestamp": incident.get("display_timestamp", ""),
        "source_filename": incident.get("source_filename", ""),
        "source_stem": incident.get("source_stem", ""),
        "primary_camera": incident.get("primary_camera", "unknown"),
        "available_cameras": incident.get("available_cameras", []),
        "camera_count": incident.get("camera_count", len(clips)),
        "clips": clips,
    }


def apply_ai_review(incident: dict[str, Any], review: dict[str, Any]) -> None:
    ai_evidence = review.get("ai_evidence") if isinstance(review.get("ai_evidence"), dict) else {}
    incident["ai_evidence"] = ai_evidence
    incident["ai_raw_response"] = review.get("ai_raw_response", "")
    incident["ai_parse_error"] = bool(review.get("ai_parse_error"))
    incident["ai_reviewed"] = bool(review.get("ai_reviewed"))
    incident["ai_review_skipped_reason"] = review.get("ai_review_skipped_reason", "")
    incident["ai_model"] = review.get("ai_model") or review.get("model", "")
    incident["ai_debug_dir"] = review.get("ai_debug_dir", "")
    incident["ai_prompt_version"] = review.get("ai_prompt_version", "")
    incident["ai_images_used"] = review.get("ai_images_used", [])
    incident["ai_image_count"] = review.get("ai_image_count", 0)
    incident["ai_scene_type"] = ai_evidence.get("scene_type", "")
    incident["ai_recommended_severity"] = ai_evidence.get(
        "recommended_severity",
        review.get("recommended_severity", ""),
    )
    incident["ai_confidence"] = ai_evidence.get("confidence", 0.0)
    incident["ai_concerns"] = ai_evidence.get("concerns", [])
    incident["ai_runtime_sec"] = review.get("runtime_sec", 0.0)
    incident["ai_evidence_review"] = review


def enrich_session(
    session_path: str | Path,
    vlm: str,
    budget: int = 999,
    timeout_sec: int = DEFAULT_AI_TIMEOUT_SEC,
    debug_review_all: bool = False,
    reviewer: Callable[..., dict[str, Any]] = review_event_group,
) -> dict[str, Any]:
    path = Path(session_path).resolve()
    session = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(session, dict) or not isinstance(session.get("incidents"), list):
        raise ValueError(f"Session does not contain incidents: {path}")
    if not vlm.strip():
        raise ValueError("A local vision model name is required.")

    incidents = session["incidents"]
    original_decisions = {
        str(incident.get("id") or index): {
            "severity": incident.get("severity"),
            "final_severity": incident.get("final_severity"),
            "event_type": incident.get("event_type"),
            "summary": incident.get("summary"),
            "classification_debug": incident.get("classification_debug"),
        }
        for index, incident in enumerate(incidents)
        if isinstance(incident, dict)
    }
    revision = max(1, int(session.get("session_revision") or 1)) + 1
    progress = ProgressReporter(
        session_id=str(session.get("session_id") or "unknown-session"),
        session_revision=revision,
    )
    attempted = 0
    reviewed = 0
    skipped = 0
    failed = 0
    runtime_sec = 0.0
    started = time.perf_counter()
    output_dir = Path(str(session.get("session_output_dir") or path.parent)).resolve()

    for index, incident in enumerate(incidents, start=1):
        if not isinstance(incident, dict):
            continue
        progress.emit(
            "ai_enrichment",
            f"AI second opinion {index} of {len(incidents)}.",
            index - 1,
            len(incidents),
            phase="ai_enrichment",
            local_results_ready=True,
            ai_enrichment_status="running",
        )
        evidence = incident.get("local_evidence") if isinstance(incident.get("local_evidence"), dict) else {}
        should_review, reason = should_review_with_ai(
            evidence,
            local_severity=str(incident.get("final_severity") or ""),
            debug_review_all=debug_review_all,
        )
        if not should_review:
            review = empty_ai_review(vlm, reason)
            skipped += 1
        elif attempted >= max(0, budget):
            review = empty_ai_review(vlm, "AI enrichment budget exhausted.")
            skipped += 1
        else:
            attempted += 1
            review = reviewer(
                _event_group_from_incident(incident),
                evidence,
                vlm=vlm,
                local_severity=str(incident.get("final_severity") or ""),
                local_decision={
                    "final_severity": incident.get("final_severity"),
                    "classification_debug": incident.get("classification_debug", {}),
                },
                incident_id=str(incident.get("id") or f"incident_{index:04d}"),
                debug_dir=output_dir / "ai_debug" / str(incident.get("id") or f"incident_{index:04d}"),
                timeout_sec=timeout_sec,
                debug_review_all=debug_review_all,
            )
            runtime_sec += float(review.get("runtime_sec") or 0.0)
            if review.get("ai_reviewed"):
                reviewed += 1
            else:
                skipped += 1
            if review.get("ai_review_failed") or review.get("ai_parse_error"):
                failed += 1
        apply_ai_review(incident, review)

    for index, incident in enumerate(incidents):
        if not isinstance(incident, dict):
            continue
        identity = str(incident.get("id") or index)
        current = {
            "severity": incident.get("severity"),
            "final_severity": incident.get("final_severity"),
            "event_type": incident.get("event_type"),
            "summary": incident.get("summary"),
            "classification_debug": incident.get("classification_debug"),
        }
        if current != original_decisions[identity]:
            raise RuntimeError(f"AI enrichment attempted to change protected local decision fields for {identity}")

    status = "complete_with_failures" if failed else "complete"
    session["session_revision"] = revision
    session["session_updated_at"] = _utc_now_iso()
    session["ai_enabled"] = True
    session["ai_requested"] = True
    session["ai_model"] = vlm
    session["ai_review_version"] = AI_REVIEW_VERSION
    session["ai_review_budget"] = max(0, budget)
    session["ai_review_attempted_groups"] = attempted
    session["ai_reviewed_groups"] = reviewed
    session["ai_skipped_groups"] = skipped
    session["ai_failed_groups"] = failed
    session["ai_review_runtime_sec"] = round(runtime_sec, 3)
    session["ai_enrichment"] = {
        "status": status,
        "model": vlm,
        "requested": True,
        "can_change_final_severity": False,
        "session_revision": revision,
        "started_from_revision": revision - 1,
        "completed_at": session["session_updated_at"],
        "wall_runtime_sec": round(time.perf_counter() - started, 3),
    }
    quality_path = write_ai_quality_report(session, output_dir)
    session["ai_quality_report"] = str(quality_path.resolve())
    write_latest_session(session, path.parent)
    progress.emit(
        "ai_enrichment_complete",
        "Experimental AI second opinion is ready.",
        len(incidents),
        max(len(incidents), 1),
        phase="ai_enrichment",
        local_results_ready=True,
        ai_enrichment_status=status,
        ai_calls=attempted,
    )
    return session
