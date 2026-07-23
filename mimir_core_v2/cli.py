"""Command-line entry point for Mimir Core v2."""

from __future__ import annotations

import argparse
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .ai_reviewer import AI_REVIEW_VERSION, DEFAULT_AI_TIMEOUT_SEC, empty_ai_review, review_event_group, should_review_with_ai, write_ai_quality_report
from .event_grouping import group_videos
from .evidence_extractor import EVIDENCE_VERSION, extract_evidence, fallback_evidence, get_evidence_runtime_diagnostics, set_yolo_disabled
from .frame_sampler import sample_event_group
from .key_moment_refiner import apply_refined_key_moment, refine_key_moment
from .model_manifest import active_detector_manifest
from .output_writer import build_session, incident_from_group, write_latest_session
from .progress import ProgressReporter
from .runtime_paths import default_output_dir
from .severity_resolver import resolve_severity
from .source_discovery import discover_videos
from .thumbnailer import THUMBNAIL_VERSION, generate_thumbnails


DEFAULT_OUTPUT = default_output_dir()
AI_BUDGET_DEFAULTS = {"fast": 20, "balanced": 50, "thorough": 150}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _new_session_id() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"mimir-{timestamp}-{uuid.uuid4().hex[:8]}"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the clean Mimir Core v2 scanner.")
    parser.add_argument("--input", required=True, help="Folder containing TeslaCam or other MP4 footage.")
    parser.add_argument("--mode", default="balanced", choices=["fast", "balanced", "thorough"], help="Scan quality mode.")
    parser.add_argument("--vlm", default="", help="Optional vision-language model name for structured AI evidence review.")
    parser.add_argument("--ai-review-budget", type=int, default=None, help="Maximum number of event groups reviewed by AI.")
    parser.add_argument("--ai-timeout-sec", type=int, default=DEFAULT_AI_TIMEOUT_SEC, help="Maximum seconds to wait for one AI review.")
    parser.add_argument("--ai-debug-review-all", action="store_true", help="Developer option: send every event group to AI when a model is configured.")
    parser.add_argument("--defer-ai", action="store_true", help="Write local results first and leave AI review to the enrichment command.")
    parser.add_argument("--disable-yolo", action="store_true", help="Testing only: force object detection unavailable while keeping motion analysis enabled.")
    parser.add_argument("--ego-calibration", default="", help="Optional JSON file containing normalized per-camera ego-vehicle polygons.")
    parser.add_argument("--output", dest="output", default=str(DEFAULT_OUTPUT), help="Output folder for latest_session.json.")
    parser.add_argument("--output-dir", dest="output", default=argparse.SUPPRESS, help="Output folder for latest_session.json.")
    return parser


def run_scan(
    input_folder: str,
    mode: str = "balanced",
    vlm: str = "",
    output: str | Path = DEFAULT_OUTPUT,
    ai_review_budget: int | None = None,
    ai_timeout_sec: int = DEFAULT_AI_TIMEOUT_SEC,
    ai_debug_review_all: bool = False,
    defer_ai: bool = False,
    disable_yolo: bool = False,
    ego_calibration: str = "",
) -> dict:
    scan_started = time.perf_counter()
    scan_started_at = _utc_now_iso()
    session_id = _new_session_id()
    progress = ProgressReporter(session_id=session_id, started_at=scan_started)
    warnings: list[str] = []
    performance_parts = {
        "source_discovery_sec": 0.0,
        "event_grouping_sec": 0.0,
        "frame_sampling_sec": 0.0,
        "local_evidence_sec": 0.0,
        "key_moment_refinement_sec": 0.0,
        "thumbnail_generation_sec": 0.0,
        "ai_review_sec": 0.0,
        "result_writing_sec": 0.0,
    }
    set_yolo_disabled(disable_yolo)
    progress.emit("reading_clips", "Finding video clips.")
    part_started = time.perf_counter()
    videos, discovery_warnings = discover_videos(input_folder)
    performance_parts["source_discovery_sec"] += time.perf_counter() - part_started
    warnings.extend(discovery_warnings)

    progress.emit("reading_event_metadata", "Reading event metadata.", len(videos), len(videos))
    progress.emit("grouping_camera_angles", "Grouping camera angles.")
    part_started = time.perf_counter()
    event_groups = group_videos(videos)
    performance_parts["event_grouping_sec"] += time.perf_counter() - part_started
    incidents: list[dict] = []
    budget = AI_BUDGET_DEFAULTS.get(mode, 50) if ai_review_budget is None else max(0, ai_review_budget)
    ai_review_candidates = 0
    ai_review_attempted_groups = 0
    ai_reviewed_groups = 0
    ai_skipped_groups = 0
    ai_failed_groups = 0
    ai_review_runtime_sec = 0.0
    groups_with_frames = 0
    groups_without_frames = 0
    video_read_warnings: list[str] = []
    thumbnails_generated = 0
    thumbnails_failed = 0
    session_output_dir = Path(output) / "sessions" / session_id
    thumbnail_output_dir = str((session_output_dir / "thumbnails").resolve())

    for index, event_group in enumerate(event_groups, start=1):
        progress.emit(
            "detecting_activity",
            f"Reviewing event group {index} of {len(event_groups)}.",
            index - 1,
            len(event_groups),
            current_video=str(event_group.get("source_filename") or event_group.get("event_group_id") or ""),
            incidents_created=len(incidents),
        )
        part_started = time.perf_counter()
        sample_result, sample_warnings = sample_event_group(event_group, mode=mode)
        performance_parts["frame_sampling_sec"] += time.perf_counter() - part_started
        warnings.extend(sample_warnings)
        video_read_warnings.extend(sample_warnings)
        if int(sample_result.get("sampled_frames") or 0) > 0:
            groups_with_frames += 1
        else:
            groups_without_frames += 1

        try:
            part_started = time.perf_counter()
            evidence = extract_evidence(event_group, sample_result, object_detection_enabled=not disable_yolo)
        except Exception as exc:
            warning = f"Evidence extraction failed for {event_group.get('event_group_id', 'unknown')}: {exc}"
            warnings.append(warning)
            evidence = fallback_evidence(event_group, warning)
        finally:
            performance_parts["local_evidence_sec"] += time.perf_counter() - part_started

        try:
            part_started = time.perf_counter()
            refinement = refine_key_moment(
                event_group,
                evidence,
                mode=mode,
                calibration_path=ego_calibration or None,
            )
            apply_refined_key_moment(evidence, refinement)
        except Exception as exc:
            warning = f"Key-moment refinement failed for {event_group.get('event_group_id', 'unknown')}: {exc}"
            warnings.append(warning)
            evidence_warnings = list(evidence.get("evidence_warnings") or [])
            evidence_warnings.append(warning)
            evidence["evidence_warnings"] = evidence_warnings
        finally:
            performance_parts["key_moment_refinement_sec"] += time.perf_counter() - part_started

        evidence_warnings = list(evidence.get("evidence_warnings") or [])
        evidence_warnings.extend(sample_warnings)
        if int(sample_result.get("sampled_frames") or 0) == 0:
            evidence_warnings.append("No frames sampled for this event group.")
        evidence["evidence_warnings"] = evidence_warnings

        local_severity = resolve_severity(evidence, {})
        incident_id = f"incident_{index:04d}"
        part_started = time.perf_counter()
        thumbnail_result = generate_thumbnails(
            event_group,
            evidence,
            local_severity,
            sample_result,
            session_output_dir,
            incident_id,
        )
        performance_parts["thumbnail_generation_sec"] += time.perf_counter() - part_started
        evidence["thumbnail"] = thumbnail_result.get("thumbnail")
        evidence["hero_thumbnail"] = thumbnail_result.get("hero_thumbnail")
        evidence["contact_sheet"] = thumbnail_result.get("contact_sheet")
        evidence["thumbnail_primary_camera"] = thumbnail_result.get("primary_camera", "")
        evidence_warnings = list(evidence.get("evidence_warnings") or [])
        evidence_warnings.extend(thumbnail_result.get("warnings") or [])
        evidence["evidence_warnings"] = evidence_warnings
        thumbnails_generated += int(thumbnail_result.get("generated") or 0)
        thumbnails_failed += int(thumbnail_result.get("failed") or 0)

        should_review, skipped_reason = should_review_with_ai(
            evidence,
            local_severity=str(local_severity.get("final_severity") or local_severity.get("severity") or ""),
            debug_review_all=ai_debug_review_all,
        )
        if should_review:
            ai_review_candidates += 1

        if not vlm:
            ai_review = empty_ai_review("", "AI model not configured.")
            ai_skipped_groups += 1
        elif defer_ai:
            ai_review = empty_ai_review(vlm, "AI second opinion is queued for asynchronous enrichment.")
            ai_skipped_groups += 1
        elif not should_review:
            ai_review = empty_ai_review(vlm, skipped_reason)
            ai_skipped_groups += 1
        elif ai_review_attempted_groups >= budget:
            ai_review = empty_ai_review(vlm, "AI review budget exhausted.")
            ai_skipped_groups += 1
        else:
            ai_review_attempted_groups += 1
            part_started = time.perf_counter()
            ai_review = review_event_group(
                event_group,
                evidence,
                vlm=vlm,
                local_severity=str(local_severity.get("final_severity") or local_severity.get("severity") or ""),
                local_decision=local_severity,
                incident_id=incident_id,
                debug_dir=session_output_dir / "ai_debug" / incident_id,
                timeout_sec=ai_timeout_sec,
                debug_review_all=ai_debug_review_all,
            )
            performance_parts["ai_review_sec"] += time.perf_counter() - part_started
            try:
                ai_review_runtime_sec += float(ai_review.get("runtime_sec") or 0.0)
            except (TypeError, ValueError):
                pass
            if ai_review.get("ai_reviewed"):
                ai_reviewed_groups += 1
            else:
                ai_skipped_groups += 1
            if ai_review.get("ai_review_failed") or ai_review.get("ai_parse_error"):
                ai_failed_groups += 1
        severity = resolve_severity(evidence, ai_review)
        incidents.append(incident_from_group(index, event_group, evidence, severity, ai_review))
        progress.emit(
            "detecting_activity",
            f"Reviewed event group {index} of {len(event_groups)}.",
            index,
            len(event_groups),
            incidents_created=len(incidents),
        )

    post_local_ai_status = "pending" if vlm and defer_ai else "complete" if vlm else "disabled"
    if vlm:
        progress.emit(
            "reviewing_suspicious_moments",
            "Local review complete; AI enrichment is queued." if defer_ai else "Optional AI second-opinion review complete.",
            ai_review_attempted_groups if not defer_ai else len(event_groups),
            max(ai_review_attempted_groups, 1) if not defer_ai else max(len(event_groups), 1),
            incidents_created=len(incidents),
            ai_calls=ai_review_attempted_groups,
            ai_enrichment_status=post_local_ai_status,
        )
    progress.emit(
        "building_incident_timeline",
        "Finalizing incident key moments.",
        len(incidents),
        max(len(event_groups), 1),
        incidents_created=len(incidents),
        ai_enrichment_status=post_local_ai_status,
    )

    source_path = str(Path(input_folder))
    session = build_session(source_path, event_groups, incidents, warnings)
    session["session_id"] = session_id
    session["session_revision"] = 1
    session["session_created_at"] = scan_started_at
    session["session_output_dir"] = str(session_output_dir.resolve())
    session["detector_manifest"] = active_detector_manifest()
    session["confidence_provenance"] = {
        "final_severity": "local_severity_resolver",
        "object_detection": session["detector_manifest"].get("detector_id", "unknown"),
        "key_moment": "dense_stabilized_local_motion",
        "ai_can_override_hard_local_evidence": False,
    }
    scan_completed_at = _utc_now_iso()
    scan_duration_sec = round(time.perf_counter() - scan_started, 3)
    scan_summary = {
        "videos_found": len(videos),
        "clips_scanned": len(videos),
        "event_groups": len(event_groups),
        "incidents": len(incidents),
        "important_count": session.get("important", 0),
        "review_count": session.get("review", 0),
        "ignore_count": session.get("ignore", 0),
        "source_path": source_path,
        "source_name": Path(input_folder).name or source_path,
        "scan_started_at": scan_started_at,
        "scan_completed_at": scan_completed_at,
        "scan_duration_sec": scan_duration_sec,
    }
    session["scan_summary"] = scan_summary
    session["videos_found"] = len(videos)
    session["clips_scanned"] = len(videos)
    session["event_groups_count"] = len(event_groups)
    session["incidents_count"] = len(incidents)
    session["ai_enabled"] = bool(vlm and not defer_ai)
    session["ai_requested"] = bool(vlm)
    session["ai_review_required"] = bool(vlm)
    session["ai_review_budget"] = budget
    session["ai_review_candidates"] = ai_review_candidates
    session["ai_review_attempted_groups"] = ai_review_attempted_groups
    session["ai_reviewed_groups"] = ai_reviewed_groups
    session["ai_skipped_groups"] = ai_skipped_groups
    session["ai_failed_groups"] = ai_failed_groups
    session["ai_model"] = vlm
    session["ai_review_runtime_sec"] = round(ai_review_runtime_sec, 3)
    session["ai_review_version"] = AI_REVIEW_VERSION
    session["ai_debug_review_all"] = bool(ai_debug_review_all)
    session["ai_enrichment"] = {
        "status": post_local_ai_status,
        "model": vlm,
        "requested": bool(vlm),
        "can_change_final_severity": False,
        "session_revision": 1,
    }
    session["thumbnail_version"] = THUMBNAIL_VERSION
    session["thumbnails_generated"] = thumbnails_generated
    session["thumbnails_failed"] = thumbnails_failed
    session["thumbnail_output_dir"] = thumbnail_output_dir
    evidence_runtime = get_evidence_runtime_diagnostics()
    session["evidence_version"] = EVIDENCE_VERSION
    session["evidence_debug"] = {
        "groups_processed": len(event_groups),
        "groups_with_frames": groups_with_frames,
        "groups_without_frames": groups_without_frames,
        "object_detection_available": evidence_runtime.get("object_detection_available", False),
        "object_detection_failures": evidence_runtime.get("object_detection_failures", 0),
        "object_detection_forced_disabled": evidence_runtime.get("object_detection_forced_disabled", False),
        "object_detector_id": evidence_runtime.get("object_detector_id", "unknown"),
        "object_detector_version": evidence_runtime.get("object_detector_version", "unknown"),
        "object_detector_backend": evidence_runtime.get("object_detector_backend", "unknown"),
        "object_detector_providers": evidence_runtime.get("object_detector_providers", []),
        "object_detector_inference_count": evidence_runtime.get("object_detector_inference_count", 0),
        "object_detector_runtime_sec": evidence_runtime.get("object_detector_runtime_sec", 0.0),
        "object_detector_last_error": evidence_runtime.get("object_detector_last_error", ""),
        "object_detector_cpu_threads": evidence_runtime.get("object_detector_cpu_threads", 0),
        "detector_cache_version": evidence_runtime.get("detector_cache_version", ""),
        "detector_cache_enabled": evidence_runtime.get("detector_cache_enabled", False),
        "detector_cache_hits": evidence_runtime.get("detector_cache_hits", 0),
        "detector_cache_misses": evidence_runtime.get("detector_cache_misses", 0),
        "detector_cache_writes": evidence_runtime.get("detector_cache_writes", 0),
        "detector_cache_errors": evidence_runtime.get("detector_cache_errors", 0),
        "detector_cache_database_bytes": evidence_runtime.get("detector_cache_database_bytes", 0),
        "yolo_available": evidence_runtime.get("yolo_available", False),
        "yolo_failures": evidence_runtime.get("yolo_failures", 0),
        "yolo_forced_disabled": evidence_runtime.get("yolo_forced_disabled", False),
        "video_read_warnings": video_read_warnings,
    }
    session["scan_health"] = {
        "completed": True,
        "cancelled": False,
        "groups_with_readable_frames": groups_with_frames,
        "groups_without_readable_frames": groups_without_frames,
        "warning_count": len(warnings),
        "source_mutation_attempted": False,
        "incident_creation_invariant": len(incidents) == len(event_groups),
    }
    session["runtime_identity"] = {
        "session_id": session_id,
        "scanner_version": session.get("scanner_version"),
        "detector_id": session["detector_manifest"].get("detector_id", "unknown"),
        "progress_protocol": "mimir_progress_v2",
    }
    performance_parts["ai_review_sec"] = max(performance_parts["ai_review_sec"], ai_review_runtime_sec)
    session["performance"] = {
        "version": "mimir_scan_performance_v1",
        "cold_cache": int(evidence_runtime.get("detector_cache_hits") or 0) == 0,
        "local_results_ready_sec": scan_duration_sec,
        "object_detector_runtime_sec": evidence_runtime.get("object_detector_runtime_sec", 0.0),
        "object_detector_inference_count": evidence_runtime.get("object_detector_inference_count", 0),
        "object_detector_provider": (evidence_runtime.get("object_detector_providers") or ["unavailable"])[0],
        "object_detector_cpu_threads": evidence_runtime.get("object_detector_cpu_threads", 0),
        "detector_cache_hits": evidence_runtime.get("detector_cache_hits", 0),
        "detector_cache_misses": evidence_runtime.get("detector_cache_misses", 0),
        "analysis_cache_hits": evidence_runtime.get("analysis_cache_hits", 0),
        "analysis_cache_misses": evidence_runtime.get("analysis_cache_misses", 0),
        "parts_sec": {key: round(value, 3) for key, value in performance_parts.items()},
        **progress.diagnostics(),
    }
    session["local_results_ready"] = True
    ai_quality_report_path = write_ai_quality_report(session, session_output_dir)
    session["ai_quality_report"] = str(ai_quality_report_path)
    progress.emit(
        "writing_results",
        "Writing the session and diagnostics.",
        0,
        1,
        incidents_created=len(incidents),
        ai_enrichment_status=post_local_ai_status,
    )
    part_started = time.perf_counter()
    output_path = write_latest_session(session, output)
    performance_parts["result_writing_sec"] += time.perf_counter() - part_started
    local_results_ready_sec = round(time.perf_counter() - scan_started, 3)
    session["performance"]["parts_sec"] = {
        key: round(value, 3) for key, value in performance_parts.items()
    }
    session["performance"]["local_results_ready_sec"] = local_results_ready_sec
    session["performance"].update(progress.diagnostics())
    session["scan_summary"]["scan_duration_sec"] = local_results_ready_sec
    session["scan_summary"]["scan_completed_at"] = _utc_now_iso()
    output_path = write_latest_session(session, output)
    progress.emit(
        "writing_results",
        "Session written.",
        1,
        1,
        incidents_created=len(incidents),
        ai_enrichment_status=post_local_ai_status,
    )
    progress.emit(
        "local_results_ready",
        "Local results are ready.",
        len(incidents),
        max(len(incidents), 1),
        incidents_created=len(incidents),
        local_results_ready=True,
        ai_enrichment_status=session["ai_enrichment"]["status"],
    )
    progress.complete(
        "Scan complete.",
        len(incidents),
        ai_enrichment_status=session["ai_enrichment"]["status"],
    )

    print("Mimir Core v2 scan complete")
    print(f"- core version: {session.get('core_version', '')}")
    print(f"- backend runtime: {session.get('backend_runtime', '')}")
    print(f"- videos found: {len(videos)}")
    print(f"- event groups: {len(event_groups)}")
    print(f"- incidents: {len(incidents)}")
    print(f"- AI reviewed groups: {ai_reviewed_groups}")
    print(f"- AI skipped groups: {ai_skipped_groups}")
    print(f"- AI review runtime: {session['ai_review_runtime_sec']} sec")
    print(f"- output path: {session.get('output_path', output_path)}")
    print(f"- output: {output_path}")
    if warnings:
        print(f"- warnings: {len(warnings)}")

    return session


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    session = run_scan(
        args.input,
        mode=args.mode,
        vlm=args.vlm,
        output=args.output,
        ai_review_budget=args.ai_review_budget,
        ai_timeout_sec=args.ai_timeout_sec,
        ai_debug_review_all=args.ai_debug_review_all,
        defer_ai=args.defer_ai,
        disable_yolo=args.disable_yolo,
        ego_calibration=args.ego_calibration,
    )
    return 0 if session.get("schema_version") == "mimir_v2" else 1


if __name__ == "__main__":
    raise SystemExit(main())
