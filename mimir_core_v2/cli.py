"""Command-line entry point for Mimir Core v2."""

from __future__ import annotations

import argparse
from pathlib import Path

from .ai_reviewer import empty_ai_review, review_event_group, should_review_with_ai
from .event_grouping import group_videos
from .evidence_extractor import extract_evidence
from .frame_sampler import sample_event_group
from .output_writer import build_session, incident_from_group, write_latest_session
from .severity_resolver import resolve_severity
from .source_discovery import discover_videos


DEFAULT_OUTPUT = Path(r"C:\Mimir_Backend\MimirOutputV2")
AI_BUDGET_DEFAULTS = {"fast": 20, "balanced": 50, "thorough": 150}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the clean Mimir Core v2 scanner.")
    parser.add_argument("--input", required=True, help="Folder containing TeslaCam or other MP4 footage.")
    parser.add_argument("--mode", default="balanced", choices=["fast", "balanced", "thorough"], help="Scan quality mode.")
    parser.add_argument("--vlm", default="", help="Optional vision-language model name for structured AI evidence review.")
    parser.add_argument("--ai-review-budget", type=int, default=None, help="Maximum number of event groups reviewed by AI.")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Output folder for latest_session.json.")
    return parser


def run_scan(
    input_folder: str,
    mode: str = "balanced",
    vlm: str = "",
    output: str | Path = DEFAULT_OUTPUT,
    ai_review_budget: int | None = None,
) -> dict:
    warnings: list[str] = []
    videos, discovery_warnings = discover_videos(input_folder)
    warnings.extend(discovery_warnings)

    event_groups = group_videos(videos)
    incidents: list[dict] = []
    budget = AI_BUDGET_DEFAULTS.get(mode, 50) if ai_review_budget is None else max(0, ai_review_budget)
    ai_review_candidates = 0
    ai_reviewed_groups = 0
    ai_skipped_groups = 0

    for index, event_group in enumerate(event_groups, start=1):
        sample_result, sample_warnings = sample_event_group(event_group, mode=mode)
        warnings.extend(sample_warnings)
        evidence = extract_evidence(event_group, sample_result)
        should_review, skipped_reason = should_review_with_ai(evidence)
        if should_review:
            ai_review_candidates += 1

        if not vlm:
            ai_review = empty_ai_review("", "AI model not configured.")
            ai_skipped_groups += 1
        elif not should_review:
            ai_review = empty_ai_review(vlm, skipped_reason)
            ai_skipped_groups += 1
        elif ai_reviewed_groups >= budget:
            ai_review = empty_ai_review(vlm, "AI review budget exhausted.")
            ai_skipped_groups += 1
        else:
            ai_review = review_event_group(event_group, evidence, vlm=vlm)
            if ai_review.get("ai_reviewed"):
                ai_reviewed_groups += 1
            else:
                ai_skipped_groups += 1
        severity = resolve_severity(evidence, ai_review)
        incidents.append(incident_from_group(index, event_group, evidence, severity, ai_review))

    session = build_session(str(Path(input_folder)), event_groups, incidents, warnings)
    session["ai_review_required"] = bool(vlm)
    session["ai_review_budget"] = budget
    session["ai_review_candidates"] = ai_review_candidates
    session["ai_reviewed_groups"] = ai_reviewed_groups
    session["ai_skipped_groups"] = ai_skipped_groups
    output_path = write_latest_session(session, output)

    print("Mimir Core v2 scan complete")
    print(f"- videos found: {len(videos)}")
    print(f"- event groups: {len(event_groups)}")
    print(f"- incidents: {len(incidents)}")
    print(f"- AI reviewed groups: {ai_reviewed_groups}")
    print(f"- AI skipped groups: {ai_skipped_groups}")
    print(f"- output: {output_path}")
    if warnings:
        print(f"- warnings: {len(warnings)}")

    return session


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    session = run_scan(args.input, mode=args.mode, vlm=args.vlm, output=args.output, ai_review_budget=args.ai_review_budget)
    return 0 if session.get("schema_version") == "mimir_v2" else 1


if __name__ == "__main__":
    raise SystemExit(main())
