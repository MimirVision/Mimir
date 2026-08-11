"""Collect the verdicts people corrected in the app, and score the rules on them.

    python mimir_core_v2_labels.py                     # what exists, and today's score
    python mimir_core_v2_labels.py --output labels.jsonl

The evaluation set the model card asks for does not have to be built by hand.
Every correction made in the app is already written into its session, next to
the evidence that produced the verdict. This reads them back.

Scoring is reported on corrected rows only. A row where a human confirmed what
Mimir already said is easy, and counting it would flatter any ruleset; the
corrections are where a change has to prove itself.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from mimir_core_v2.label_harvest import evaluate, harvest_sessions, write_label_set
from mimir_core_v2.runtime_paths import default_output_dir
from mimir_core_v2.severity_resolver import resolve_severity

BACKEND_ROOT = Path(__file__).resolve().parent


def default_roots() -> list[Path]:
    """Everywhere a session might have been written on this machine."""

    roots = [Path(default_output_dir()) / "sessions", BACKEND_ROOT / "MimirOutputV2" / "sessions"]
    appdata = Path.home() / "AppData" / "Roaming" / "com.mimir.scanner" / "MimirOutputV2" / "sessions"
    roots.append(appdata)
    return [root for root in roots if root.is_dir()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sessions", action="append", default=[], help="Extra session folder to read. Repeatable.")
    parser.add_argument("--output", default="", help="Write the label set here as JSONL.")
    parser.add_argument("--json", action="store_true", help="Print the summary as JSON.")
    args = parser.parse_args(argv)

    roots = [Path(value) for value in args.sessions] or default_roots()
    if not roots:
        print("No session folders found. Run a scan first.")
        return 1

    report = harvest_sessions(roots)

    summary = {
        "sessions_seen": report.sessions_seen,
        "sessions_with_labels": report.sessions_with_labels,
        "incidents_seen": report.incidents_seen,
        "labels": len(report.labels),
        "corrections": report.corrections,
        "agreements": report.agreements,
        "skipped_no_evidence": report.skipped_no_evidence,
    }
    if report.labels:
        summary["score_today"] = evaluate(report, resolve_severity)

    if args.output:
        path = write_label_set(report, Path(args.output))
        summary["written"] = str(path)

    if args.json:
        print(json.dumps(summary, indent=2))
        return 0

    print(f"sessions read        : {summary['sessions_seen']}")
    print(f"incidents seen       : {summary['incidents_seen']}")
    print(f"human verdicts       : {summary['labels']}")
    print(f"  corrections        : {summary['corrections']}")
    print(f"  confirmations      : {summary['agreements']}")
    if report.skipped_no_evidence:
        print(f"  skipped, no evidence: {report.skipped_no_evidence}")

    if not report.labels:
        print()
        print("No verdicts have been corrected yet, so there is nothing to measure.")
        print("Every correction made in the app lands here automatically -- reviewing a")
        print("scan and fixing what Mimir got wrong is what builds the evaluation set.")
        return 0

    score = summary["score_today"]
    print()
    print(f"On the {score['corrected_rows']} clips a human corrected, the current rules now agree with")
    if score["corrected_agreement"] is None:
        print("  (nothing to score)")
    else:
        print(f"  {score['corrected_now_matching_human']} of them -- {score['corrected_agreement'] * 100:.0f}%.")
    print()
    print("Mimir said -> human said:")
    for pair, count in sorted(score["confusion"].items(), key=lambda item: -item[1]):
        print(f"  {pair:<24} {count}")

    if summary.get("written"):
        print()
        print(f"Label set written: {summary['written']}")

    # The model card wants a locked set of at least 750 groups.
    remaining = 750 - len(report.labels)
    if remaining > 0:
        print()
        print(f"{remaining} more verdicts to reach the 750 the model card requires.")

    for warning in report.warnings:
        print(f"warning: {warning}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
