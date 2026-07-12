"""Command-line entry point for Mimir Core v2."""

from __future__ import annotations

import argparse
from pathlib import Path

from .ai_reviewer import AI_REVIEW_VERSION, DEFAULT_AI_TIMEOUT_SEC, empty_ai_review, review_event_group, should_review_with_ai
from .event_grouping import group_videos
from .evidence_extractor import EVIDENCE_VERSION, extract_evidence, fallback_evidence, get_evidence_runtime_diagnostics
from .frame_sampler import sample_event_group
from .output_writer import build_session, incident_from_group, write_latest_session
from .severity_resolver import resolve_severity
from .source_discovery import discover_videos
from .thumbnailer import THUMBNAIL_VERSION, generate_thumbnails


DEFAULT_OUTPUT = Path(r"C:\Mimir_Backend\MimirOutputV2")
AI_BUDGET_DEFAULTS = {"fast": 20, "balanced": 50, "thorough": 150}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the clean Mimir Core v2 scanner.")
    parser.add_argument("--input", required=True, help="Folder containing TeslaCam or other MP4 footage.")
    parser.add_argument("--mode", default="balanced", choices=["fast", "balanced", "thorough"], help="Scan quality mode.")
    parser.add_argument("--vlm", default="", help="Optional vision-language model name for structured AI evidence review.")
    parser.add_argument("--ai-review-budget", type=int, default=None, help="Maximum number of event groups reviewed by AI.")
    parser.add_argument("--ai-timeout-sec", type=int, default=DEFAULT_AI_TIMEOUT_SEC, help="Maximum seconds to wait for one AI review.")
    parser.add_argument("--ai-debug-review-all", action="store_true", help="Developer option: send every event group to AI when a model is configured.")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Output folder for latest_session.json.")
    return parser


def run_scan(
    input_folder: str,
    mode: str = "balanced",
    vlm: str = "",
    output: str | Path = DEFAULT_OUTPUT,
    ai_review_budget: int | None = None,
    ai_timeout_sec: int = DEFAULT_AI_TIMEOUT_SEC,
    ai_debug_review_all: bool = False,
) -> dict:
    warnings: list[str] = []
    videos, discovery_warnings = discover_videos(input_folder)
    warnings.extend(discovery_warnings)

    event_groups = group_videos(videos)
    incidents: list[dict] = []
    budget = AI_BUDGET_DEFAULTS.get(mode, 50) if ai_review_budget is None else max(0, ai_review_budget)
    ai_review_candidates = 0
    ai_review_attempted_groups = 0
    ai_reviewed_groups = 0
    ai_skipped_groups = 0
    ai_review_runtime_sec = 0.0
    groups_with_frames = 0
    groups_without_frames = 0
    video_read_warnings: list[str] = []
    thumbnails_generated = 0
    thumbnails_failed = 0
    thumbnail_output_dir = str((Path(output) / "thumbnails").resolve())

    for index, event_group in enumerate(event_groups, start=1):
        sample_result, sample_warnings = sample_event_group(event_group, mode=mode)
        warnings.extend(sample_warnings)
        video_read_warnings.extend(sample_warnings)
        if int(sample_result.get("sampled_frames") or 0) > 0:
            groups_with_frames += 1
        else:
            groups_without_frames += 1

        try:
            evidence = extract_evidence(event_group, sample_result)
        except Exception as exc:
            warning = f"Evidence extraction failed for {event_group.get('event_group_id', 'unknown')}: {exc}"
            warnings.append(warning)
            evidence = fallback_evidence(event_group, warning)

        evidence_warnings = list(evidence.get("evidence_warnings") or [])
        evidence_warnings.extend(sample_warnings)
        if int(sample_result.get("sampled_frames") or 0) == 0:
            evidence_warnings.append("No frames sampled for this event group.")
        evidence["evidence_warnings"] = evidence_warnings

        should_review, skipped_reason = should_review_with_ai(evidence, debug_review_all=ai_debug_review_all)
        if should_review:
            ai_review_candidates += 1

        if not vlm:
            ai_review = empty_ai_review("", "AI model not configured.")
            ai_skipped_groups += 1
        elif not should_review:
            ai_review = empty_ai_review(vlm, skipped_reason)
            ai_skipped_groups += 1
        elif ai_review_attempted_groups >= budget:
            ai_review = empty_ai_review(vlm, "AI review budget exhausted.")
            ai_skipped_groups += 1
        else:
            ai_review_attempted_groups += 1
            ai_review = review_event_group(
                event_group,
                evidence,
                vlm=vlm,
                timeout_sec=ai_timeout_sec,
                debug_review_all=ai_debug_review_all,
            )
            try:
                ai_review_runtime_sec += float(ai_review.get("runtime_sec") or 0.0)
            except (TypeError, ValueError):
                pass
            if ai_review.get("ai_reviewed"):
                ai_reviewed_groups += 1
            else:
                ai_skipped_groups += 1
        severity = resolve_severity(evidence, ai_review)
        incident_id = f"incident_{index:04d}"
        thumbnail_result = generate_thumbnails(
            event_group,
            evidence,
            severity,
            sample_result,
            output,
            incident_id,
        )
        evidence["thumbnail"] = thumbnail_result.get("thumbnail")
        evidence["hero_thumbnail"] = thumbnail_result.get("hero_thumbnail")
        evidence["contact_sheet"] = thumbnail_result.get("contact_sheet")
        evidence["thumbnail_primary_camera"] = thumbnail_result.get("primary_camera", "")
        evidence_warnings = list(evidence.get("evidence_warnings") or [])
        evidence_warnings.extend(thumbnail_result.get("warnings") or [])
        evidence["evidence_warnings"] = evidence_warnings
        thumbnails_generated += int(thumbnail_result.get("generated") or 0)
        thumbnails_failed += int(thumbnail_result.get("failed") or 0)
        incidents.append(incident_from_group(index, event_group, evidence, severity, ai_review))

    session = build_session(str(Path(input_folder)), event_groups, incidents, warnings)
    session["ai_review_required"] = bool(vlm)
    session["ai_review_budget"] = budget
    session["ai_review_candidates"] = ai_review_candidates
    session["ai_review_attempted_groups"] = ai_review_attempted_groups
    session["ai_reviewed_groups"] = ai_reviewed_groups
    session["ai_skipped_groups"] = ai_skipped_groups
    session["ai_model"] = vlm
    session["ai_review_runtime_sec"] = round(ai_review_runtime_sec, 3)
    session["ai_review_version"] = AI_REVIEW_VERSION
    session["ai_debug_review_all"] = bool(ai_debug_review_all)
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
        "yolo_available": evidence_runtime.get("yolo_available", False),
        "yolo_failures": evidence_runtime.get("yolo_failures", 0),
        "video_read_warnings": video_read_warnings,
    }
    output_path = write_latest_session(session, output)

    print("Mimir Core v2 scan complete")
    print(f"- videos found: {len(videos)}")
    print(f"- event groups: {len(event_groups)}")
    print(f"- incidents: {len(incidents)}")
    print(f"- AI reviewed groups: {ai_reviewed_groups}")
    print(f"- AI skipped groups: {ai_skipped_groups}")
    print(f"- AI review runtime: {session['ai_review_runtime_sec']} sec")
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
    )
    return 0 if session.get("schema_version") == "mimir_v2" else 1


if __name__ == "__main__":
    raise SystemExit(main())
