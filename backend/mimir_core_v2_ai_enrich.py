"""Add an experimental AI second opinion to an existing local session."""

from __future__ import annotations

import argparse

from mimir_core_v2.ai_enrichment import enrich_session
from mimir_core_v2.ai_reviewer import DEFAULT_AI_TIMEOUT_SEC


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Enrich a completed Mimir session with optional local AI.")
    parser.add_argument("--session", required=True)
    parser.add_argument("--vlm", required=True)
    parser.add_argument("--ai-review-budget", type=int, default=999)
    parser.add_argument("--ai-timeout-sec", type=int, default=DEFAULT_AI_TIMEOUT_SEC)
    parser.add_argument("--ai-debug-review-all", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    session = enrich_session(
        args.session,
        args.vlm,
        budget=args.ai_review_budget,
        timeout_sec=args.ai_timeout_sec,
        debug_review_all=args.ai_debug_review_all,
    )
    print("Mimir AI enrichment complete")
    print(f"- session revision: {session.get('session_revision')}")
    print(f"- model: {session.get('ai_model')}")
    print(f"- reviewed groups: {session.get('ai_reviewed_groups')}")
    print(f"- failed groups: {session.get('ai_failed_groups')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
