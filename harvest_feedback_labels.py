"""Turn received beta feedback into a reviewable label worksheet.

Every feedback package carries three things that together are the raw material
for a regression label: the clip, what Mimir decided, and what a human said
about that decision. Until now they sat in `feedback_inbox` unused, while
`benchmark_labels.csv` held three rows.

This writes a worksheet, not labels. The severity a user asked for is derived
mechanically where the feedback choice states one; everything that needs a
person to watch the clip -- the `category` column, and any ambiguous feedback
choice -- is left blank and flagged. Rows are shaped like `benchmark_labels.csv`
so a reviewed row can be pasted straight in, with extra provenance columns that
`benchmark_mimir.py` ignores (it reads by DictReader and only requires its own
four columns to exist).

Usage:
    python harvest_feedback_labels.py
    python harvest_feedback_labels.py --inbox <dir> --output <csv>
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path

BASE = Path(__file__).resolve().parent
DEFAULT_INBOX = BASE / "MimirOutputV2" / "training" / "feedback_inbox"
DEFAULT_OUTPUT = BASE / "feedback_label_worksheet.csv"

VALID_SEVERITIES = ("IMPORTANT", "REVIEW", "IGNORE")

# The feedback choices that state a severity outright. Anything not listed here
# needs a human, and is emitted with a blank expected_severity rather than a
# guess -- a fabricated label is worse than a missing one.
FEEDBACK_TO_EXPECTED = {
    "should be ignore": "IGNORE",
    "should be review": "REVIEW",
    "should be important": "IMPORTANT",
}

# "Correct" endorses whatever Mimir already said, so the expected value is the
# recorded severity rather than a fixed one.
AGREEMENT_CHOICE = "correct"

# These say something is wrong without saying what the severity should be:
# "Weird AI flag" is about the reasoning, and "Missed obvious event" is about
# something absent rather than about this clip's verdict.
NEEDS_HUMAN = ("weird ai flag", "missed obvious event")

COLUMNS = [
    # benchmark_labels.csv's own columns, first and in order, so a reviewed row
    # can be copied across without rearranging.
    "filename_or_group",
    "expected_severity",
    "category",
    "notes",
    # Provenance. Ignored by benchmark_mimir.py.
    "review_status",
    "mimir_severity",
    "user_feedback",
    "user_notes",
    "package_id",
    "submitted_at",
    "has_video",
]


def normalize(value: object) -> str:
    return str(value or "").strip()


def derive_expected(user_feedback: str, mimir_severity: str) -> tuple[str, str]:
    """Return (expected_severity, review_status)."""

    choice = user_feedback.lower()

    if choice in FEEDBACK_TO_EXPECTED:
        return FEEDBACK_TO_EXPECTED[choice], "derived"

    if choice == AGREEMENT_CHOICE:
        if mimir_severity in VALID_SEVERITIES:
            return mimir_severity, "derived_agreement"
        return "", "needs_human"

    if choice in NEEDS_HUMAN:
        return "", "needs_human"

    return "", "needs_human"


def read_packages(inbox: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []

    for package_dir in sorted(inbox.iterdir()):
        if not package_dir.is_dir() or package_dir.name.startswith("_"):
            continue

        feedback_file = package_dir / "feedback.json"
        if not feedback_file.is_file():
            continue

        try:
            feedback = json.loads(feedback_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            print(f"  skipped {package_dir.name}: unreadable feedback.json ({error})")
            continue

        video_dir = package_dir / "video"
        videos = sorted(item.name for item in video_dir.glob("*") if item.is_file()) if video_dir.is_dir() else []

        # Prefer the recorded source filename; fall back to the packaged clip.
        # Neither is guaranteed, so a package with no identifiable clip is
        # reported rather than silently dropped.
        source = normalize(feedback.get("source_filename")) or (videos[0] if videos else "")
        mimir_severity = normalize(feedback.get("current_severity")).upper()
        user_feedback = normalize(feedback.get("user_selected_feedback"))
        expected, review_status = derive_expected(user_feedback, mimir_severity)

        if not source:
            review_status = "needs_human"

        rows.append(
            {
                "filename_or_group": source,
                "expected_severity": expected,
                # Deliberately blank: the benchmark's categories (door_ding,
                # person_near, rear_impact, ...) cannot be inferred from a
                # severity choice. Someone has to watch the clip.
                "category": "",
                "notes": f"from beta feedback: {user_feedback}" if user_feedback else "from beta feedback",
                "review_status": review_status,
                "mimir_severity": mimir_severity,
                "user_feedback": user_feedback,
                "user_notes": normalize(feedback.get("notes")),
                "package_id": package_dir.name,
                "submitted_at": normalize(feedback.get("timestamp")),
                "has_video": "yes" if videos else "no",
            }
        )

    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--inbox", default=str(DEFAULT_INBOX))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args()

    inbox = Path(args.inbox)
    if not inbox.is_dir():
        print(f"No feedback inbox at {inbox}")
        return 1

    rows = read_packages(inbox)
    if not rows:
        print(f"No feedback packages found in {inbox}")
        return 1

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    statuses = Counter(str(row["review_status"]) for row in rows)
    disagreements = sum(1 for row in rows if row["review_status"] == "derived")
    clips = Counter(str(row["filename_or_group"]) for row in rows if row["filename_or_group"])
    repeated = {name: count for name, count in clips.items() if count > 1}

    print(f"Harvested {len(rows)} feedback packages -> {output}")
    print()
    print("Review status:")
    for status, count in statuses.most_common():
        print(f"  {status}: {count}")
    print()
    print("Mimir verdict vs user request:")
    for (said, wanted), count in Counter(
        (str(row["mimir_severity"]), str(row["user_feedback"])) for row in rows
    ).most_common():
        print(f"  {said} -> {wanted}: {count}")
    print()
    print(f"Distinct clips: {len(clips)}")
    if repeated:
        print("  clips with more than one feedback submission (reconcile before using as labels):")
        for name, count in sorted(repeated.items(), key=lambda item: -item[1]):
            print(f"    {name}: {count}")
    print(f"Rows with a derived expected_severity: {disagreements + statuses['derived_agreement']}")
    print(f"Rows still needing a human: {statuses['needs_human']}")
    print()
    print("Next: fill the `category` column (watch the clip), resolve `needs_human`")
    print("rows, then copy confirmed rows' first four columns into benchmark_labels.csv.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
