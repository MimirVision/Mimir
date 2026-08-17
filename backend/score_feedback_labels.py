"""Score a scanner against the human verdicts in beta feedback.

`harvest_feedback_labels.py` turns received feedback into a worksheet: a clip,
what Mimir decided, and what a person said about that decision. Nothing then
scored a scan against those verdicts, so the only evidence about accuracy was a
tally of complaints with no way to tell whether later changes had helped.

This closes that. For every feedback row whose whole event group is still on
disk, it rescans the group and compares the result with what the person asked
for.

Two things it is careful about.

A single camera is not the event Mimir judged. Feedback carries one clip, but
severity is resolved per event group, so scoring the clip alone would measure
something the product never decided. Rows whose group is not on disk are
reported as unscorable rather than scored on partial footage.

Direction matters more than agreement. Mimir being noisier than a person wanted
costs attention; Mimir being quieter than a person wanted loses evidence, which
is the failure the whole design is meant to avoid. Those are counted separately
and under-flagging is called out on its own.

This is not a false-positive rate. People send feedback when something is wrong,
so it measures how often Mimir is wrong when someone bothered to complain. The
locked evaluation set in MODEL_CARD.md is what replaces it.

Usage:
    python score_feedback_labels.py
    python score_feedback_labels.py --scanner <exe> --library <dir>
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import tempfile
from collections import Counter
from pathlib import Path

BASE = Path(__file__).resolve().parent
DEFAULT_WORKSHEET = BASE / "feedback_label_worksheet.csv"
DEFAULT_SCANNER = BASE.parent / "desktop" / "src-tauri" / "resources" / "mimir-backend" / "mimir-core-v2-scan.exe"
DEFAULT_LIBRARY = Path.home() / "Videos" / "Mimir Library" / "Footage" / "SentryClips"

SEVERITY_RANK = {"IGNORE": 0, "REVIEW": 1, "IMPORTANT": 2}
CLIP_NAME = re.compile(r"\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}-.+\.mp4$")


def compare(human: str, mimir: str) -> str:
    """Classify a verdict pair as agreement, over-flagging, or under-flagging.

    Separated from the scanning so the judgement itself is testable without
    footage, and named for the consequence rather than the direction: 'quieter'
    is the one that loses evidence.
    """

    human_rank = SEVERITY_RANK.get(human.upper())
    mimir_rank = SEVERITY_RANK.get(mimir.upper())
    if human_rank is None or mimir_rank is None:
        return "unknown"
    if mimir_rank == human_rank:
        return "agrees"
    return "noisier" if mimir_rank > human_rank else "quieter"


def group_severity(incidents: list[dict]) -> str:
    """The group's verdict is its most severe incident, which is what the user saw."""

    severities = [
        str(item.get("final_severity") or item.get("severity") or "IGNORE").upper()
        for item in incidents
    ]
    return max(severities, key=lambda value: SEVERITY_RANK.get(value, 0)) if severities else "IGNORE"


def find_event_group(library: Path, clip_name: str) -> Path | None:
    if not library.is_dir():
        return None
    for folder in library.iterdir():
        if folder.is_dir() and (folder / clip_name).is_file():
            return folder
    return None


def scan_group(scanner: Path, folder: Path) -> list[dict]:
    with tempfile.TemporaryDirectory(prefix="mimir-feedback-score-") as temporary:
        command = [str(scanner)] if scanner.suffix.lower() == ".exe" else ["python", str(scanner)]
        result = subprocess.run(
            [*command, "--input", str(folder), "--mode", "balanced", "--output", temporary],
            cwd=BASE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        session = Path(temporary) / "latest_session.json"
        if result.returncode != 0 or not session.exists():
            return []
        return json.loads(session.read_text(encoding="utf-8")).get("incidents") or []


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--worksheet", default=str(DEFAULT_WORKSHEET))
    parser.add_argument("--scanner", default=str(DEFAULT_SCANNER))
    parser.add_argument("--library", default=str(DEFAULT_LIBRARY))
    args = parser.parse_args()

    worksheet, scanner, library = Path(args.worksheet), Path(args.scanner), Path(args.library)
    if not worksheet.is_file():
        print(f"No worksheet at {worksheet}. Run harvest_feedback_labels.py first.")
        return 1
    if not scanner.exists():
        print(f"Scanner is missing: {scanner}")
        return 1

    rows = list(csv.DictReader(worksheet.open(encoding="utf-8-sig")))
    seen: set[str] = set()
    scorable, unscorable = [], []
    for row in rows:
        name = row.get("filename_or_group", "")
        expected = (row.get("expected_severity") or "").upper()
        if not CLIP_NAME.search(name) or not expected or name in seen:
            continue
        seen.add(name)
        folder = find_event_group(library, name)
        (scorable if folder else unscorable).append((name, folder, expected, (row.get("mimir_severity") or "").upper()))

    print(f"scanner : {scanner}")
    print(f"library : {library}")
    print(f"{len(scorable)} of {len(seen)} feedback clips have their whole event group on disk\n")
    if not scorable:
        print("Nothing scorable. The event groups these clips came from are not in the library.")
        return 0

    print(f"{'event':<24}{'human':<10}{'when sent':<12}{'now':<12}")
    print("-" * 62)
    outcomes: Counter[str] = Counter()
    quieter: list[tuple[str, str, str]] = []
    for name, folder, expected, previously in scorable:
        incidents = scan_group(scanner, folder)
        if not incidents:
            print(f"{folder.name:<24}{expected:<10}{previously:<12}{'(scan failed)':<12}")
            outcomes["unscanned"] += 1
            continue
        now = group_severity(incidents)
        outcome = compare(expected, now)
        outcomes[outcome] += 1
        if outcome == "quieter":
            quieter.append((folder.name, expected, now))
        label = {"agrees": "ok", "noisier": "noisier", "quieter": "QUIETER"}.get(outcome, outcome)
        print(f"{folder.name:<24}{expected:<10}{previously:<12}{now:<12}{label}")

    scored = sum(outcomes[key] for key in ("agrees", "noisier", "quieter"))
    print(f"\nagrees with the person   : {outcomes['agrees']}/{scored}")
    print(f"noisier than they wanted : {outcomes['noisier']}/{scored}   costs attention")
    print(f"quieter than they wanted : {outcomes['quieter']}/{scored}   loses evidence")

    if quieter:
        print("\nQuieter than a person asked for -- the direction that buries an incident:")
        for folder_name, expected, now in quieter:
            print(f"  {folder_name}: person said {expected}, Mimir now says {now}")

    if unscorable:
        print(f"\n{len(unscorable)} clip(s) could not be scored: the event group is not in the library.")

    print("\nNot a false-positive rate. Feedback arrives when something is wrong, so this")
    print("measures how often Mimir is wrong when someone complained.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
