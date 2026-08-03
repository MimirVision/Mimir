"""Turn a scan session into a review worksheet for building the locked evaluation set.

MODEL_CARD.md requires a source-isolated set of at least 750 event groups, with
at least 300 positives and 300 hard negatives, before any accuracy claim can be
made. Today `benchmark_labels.csv` holds three rows. This is the tool for
closing that gap against footage you already have.

It reads a session, emits one row per event group with the evidence a reviewer
needs, and leaves the verdict blank.

WHY expected_severity IS BLANK AND NOT PRE-FILLED
    It would be faster to pre-fill each row with what Mimir decided and have
    the reviewer accept or correct it. Do not do that. This set exists to
    measure Mimir; seeding it with Mimir's own answers biases every borderline
    call toward agreement and quietly turns the evaluation into a measurement
    of itself. Mimir's verdict is shown in its own clearly-named column so the
    reviewer has context, but the answer has to be typed.

ORDERING
    Rows are ordered so candidate positives come first. Real contact events are
    rare -- most Sentry triggers are nothing -- so the groups Mimir already
    rated IMPORTANT or REVIEW are where the scarce positives are most likely to
    be, and they are worth reviewing while attention is fresh. The long IGNORE
    tail is the hard-negative pool.

Usage:
    python build_label_worksheet.py --session <dir-or-session.json>
    python build_label_worksheet.py --session <...> --output labels_to_review.csv
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path

BASE = Path(__file__).resolve().parent
DEFAULT_LABELS = BASE / "benchmark_labels.csv"
DEFAULT_OUTPUT = BASE / "label_worksheet.csv"

SEVERITY_RANK = {"IGNORE": 0, "REVIEW": 1, "IMPORTANT": 2}

# benchmark_mimir.py validates against exactly these.
VALID_CATEGORIES = (
    "rear_impact",
    "door_ding",
    "side_contact",
    "person_near",
    "person_touching",
    "normal_traffic",
    "distant_pedestrian",
    "weird_unclear",
)

COLUMNS = [
    # benchmark_labels.csv's own columns first, so a completed row copies across.
    "filename_or_group",
    "expected_severity",
    "category",
    "notes",
    # Context for the reviewer. Ignored by benchmark_mimir.py.
    "source",
    "mimir_said",
    "impact_level",
    "contact_level",
    "motion_score",
    "detected",
    "mimir_reasons",
    "key_moment_sec",
    "contact_sheet",
]


def text(value: object) -> str:
    return str(value if value is not None else "").strip()


def group_key(incident: dict) -> str:
    """The label key, which must be something benchmark_mimir.py can match.

    `event_group_id` ("event_<timestamp>_<hash>") is the right choice on all
    three counts: it appears in benchmark_mimir's incident_match_values, it is
    one-per-event, and it is readable.

    Do NOT use event_folder. One Sentry folder routinely holds several distinct
    events -- event_grouping.py keys on (source_folder, event_timestamp) -- so
    folders under-count groups by about half and would force one verdict onto
    two different events. It also records an absolute path, which for a scan of
    staged footage is a temp directory that will not exist later.
    """

    return (
        text(incident.get("event_group_id"))
        or text(incident.get("source_filename"))
        or text(incident.get("id"))
        or "unknown"
    )


def source_label(incident: dict) -> str:
    """Human-readable origin, for the reviewer only. Never the label key."""

    folder = text(incident.get("event_folder")).replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
    stamp = text(incident.get("event_timestamp"))

    if folder and stamp and stamp not in folder:
        return f"{folder} @ {stamp}"

    return folder or stamp or text(incident.get("source_filename"))


def severity_of(incident: dict) -> str:
    return text(incident.get("final_severity") or incident.get("severity")).upper()


def reasons_of(incident: dict) -> str:
    collected: list[str] = []
    for field in ("severity_reasons", "impact_evidence_reasons", "contact_evidence_reasons"):
        value = incident.get(field)
        if isinstance(value, list):
            collected.extend(text(item) for item in value if text(item))
        elif text(value):
            collected.append(text(value))

    seen: list[str] = []
    for reason in collected:
        if reason not in seen:
            seen.append(reason)

    return "; ".join(seen[:4])


def detected_of(incident: dict) -> str:
    marks = []
    if incident.get("person_detected"):
        marks.append("person")
    if incident.get("vehicle_detected"):
        marks.append("vehicle")
    return "+".join(marks)


def load_session(path: Path) -> dict:
    candidate = path
    if candidate.is_dir():
        candidate = candidate / "latest_session.json"
    if not candidate.is_file():
        raise SystemExit(f"No session JSON at {candidate}")

    return json.loads(candidate.read_text(encoding="utf-8"))


def already_labelled(labels_csv: Path) -> set[str]:
    if not labels_csv.is_file():
        return set()

    with labels_csv.open(encoding="utf-8-sig", newline="") as handle:
        return {
            text(row.get("filename_or_group")).lower()
            for row in csv.DictReader(handle)
            if text(row.get("filename_or_group"))
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--session", required=True, help="Scan output folder, or a session JSON directly.")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--labels", default=str(DEFAULT_LABELS), help="Existing labels, to skip what is done.")
    parser.add_argument("--include-labelled", action="store_true", help="Do not skip already-labelled groups.")
    args = parser.parse_args()

    session = load_session(Path(args.session))
    incidents = session.get("incidents") or []
    if not incidents:
        raise SystemExit("That session contains no incidents.")

    done = set() if args.include_labelled else already_labelled(Path(args.labels))

    # One row per event group, keeping the most severe incident in each --
    # the group is the unit MODEL_CARD counts, not the individual clip.
    best: dict[str, dict] = {}
    for incident in incidents:
        name = group_key(incident)
        current = best.get(name)
        if current is None or SEVERITY_RANK.get(severity_of(incident), -1) > SEVERITY_RANK.get(severity_of(current), -1):
            best[name] = incident

    rows = []
    skipped = 0
    for name, incident in best.items():
        if name.lower() in done:
            skipped += 1
            continue

        rows.append(
            {
                "filename_or_group": name,
                "expected_severity": "",
                "category": "",
                "notes": "",
                "source": source_label(incident),
                "mimir_said": severity_of(incident),
                "impact_level": text(incident.get("impact_evidence_level") or incident.get("impact_level")),
                "contact_level": text(incident.get("contact_evidence_level") or incident.get("contact_level")),
                "motion_score": text(incident.get("max_motion_score") or incident.get("motion_score")),
                "detected": detected_of(incident),
                "mimir_reasons": reasons_of(incident),
                "key_moment_sec": text(incident.get("primary_key_moment_sec")),
                "contact_sheet": text(incident.get("contact_sheet") or incident.get("hero_thumbnail")),
            }
        )

    rows.sort(key=lambda row: (-SEVERITY_RANK.get(str(row["mimir_said"]), -1), str(row["filename_or_group"])))

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    mix = Counter(str(row["mimir_said"]) for row in rows)
    candidates = mix.get("IMPORTANT", 0) + mix.get("REVIEW", 0)

    print(f"Wrote {len(rows)} groups to review -> {output}")
    if skipped:
        print(f"Skipped {skipped} already in {Path(args.labels).name}")
    print()
    print("Mimir's verdict on these (context only -- not the label):")
    for severity in ("IMPORTANT", "REVIEW", "IGNORE"):
        if mix.get(severity):
            print(f"  {severity}: {mix[severity]}")
    print()
    print(f"Review the top {candidates} first: those are where positives are most likely.")
    print(f"The {mix.get('IGNORE', 0)} IGNORE rows are the hard-negative pool.")
    print()
    print("Fill expected_severity (IMPORTANT/REVIEW/IGNORE) and category, one of:")
    print("  " + ", ".join(VALID_CATEGORIES))
    print("Open the contact_sheet path to judge without scrubbing the video.")
    print()
    print("MODEL_CARD.md needs >=750 groups, >=300 positive, >=300 hard negative.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
