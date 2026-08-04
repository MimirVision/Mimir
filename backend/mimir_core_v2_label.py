"""Fast severity-only labelling, so detection changes can be measured.

Full CVAT annotation (boxes, contact frames) is what a *fine-tune* needs, and
it costs 5-15 minutes per clip. But measuring false positives and false
negatives needs far less: just the severity a human thinks each clip deserves.
That is seconds per clip, and it is the difference between "detection feels
off" and "false-important rate is 34%".

This tool builds that label set against footage you already have, then scores
a scan against it. No consent/rights machinery is involved because nothing is
packaged or shared -- the labels stay in one local JSON file and only ever
reference filenames.

    python mimir_core_v2_label.py init     --input <footage dir> --labels labels.json
    python mimir_core_v2_label.py label    --labels labels.json
    python mimir_core_v2_label.py score    --labels labels.json --session <latest_session.json>
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA = "mimir_severity_labels_v1"
SEVERITIES = ("IMPORTANT", "REVIEW", "IGNORE")
VIDEO_SUFFIXES = {".mp4"}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def load_labels(path: Path) -> dict[str, Any]:
    data = read_json(path)
    if data.get("schema_version") != SCHEMA:
        return {"schema_version": SCHEMA, "created_at": utc_now(), "items": []}
    if not isinstance(data.get("items"), list):
        data["items"] = []
    return data


def items_by_key(labels: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in labels.get("items", []):
        if isinstance(item, dict) and item.get("filename"):
            result[str(item["filename"])] = item
    return result


def init_command(args: argparse.Namespace) -> int:
    root = Path(args.input)
    if not root.is_dir():
        print(f"Footage folder does not exist: {root}", file=sys.stderr)
        return 1

    labels_path = Path(args.labels)
    labels = load_labels(labels_path)
    existing = items_by_key(labels)

    found = sorted(
        path for path in root.rglob("*")
        if path.is_file() and path.suffix.lower() in VIDEO_SUFFIXES
    )
    added = 0
    for path in found:
        name = path.name
        if name in existing:
            continue
        labels["items"].append(
            {
                "filename": name,
                "relative_path": str(path.relative_to(root)),
                "human_severity": None,
                "note": "",
                "labelled_at": None,
            }
        )
        added += 1

    write_json(labels_path, labels)
    print(f"Found {len(found)} clip(s); added {added} new unlabelled entr{'y' if added == 1 else 'ies'}.")
    print(f"labels: {labels_path}")
    return 0


def label_command(args: argparse.Namespace) -> int:
    labels_path = Path(args.labels)
    labels = load_labels(labels_path)
    pending = [item for item in labels["items"] if not item.get("human_severity")]

    if not pending:
        print("Every clip already has a label.")
        return 0

    print(f"{len(pending)} clip(s) to label.")
    print("Enter 1=IMPORTANT, 2=REVIEW, 3=IGNORE, s=skip, q=save and quit.")
    print()

    labelled = 0
    for item in pending:
        prompt = f"[{labelled + 1}/{len(pending)}] {item['filename']}: "
        try:
            answer = input(prompt).strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if answer == "q":
            break
        if answer in {"", "s"}:
            continue

        choice = {"1": "IMPORTANT", "2": "REVIEW", "3": "IGNORE"}.get(answer)
        if choice is None:
            print("  unrecognised - skipped")
            continue

        item["human_severity"] = choice
        item["labelled_at"] = utc_now()
        labelled += 1

    write_json(labels_path, labels)
    total = sum(1 for item in labels["items"] if item.get("human_severity"))
    print()
    print(f"Labelled {labelled} this session; {total}/{len(labels['items'])} total.")
    return 0


def _session_severities(session_path: Path) -> dict[str, str]:
    session = read_json(session_path)
    result: dict[str, str] = {}
    for incident in session.get("incidents", []) if isinstance(session.get("incidents"), list) else []:
        if not isinstance(incident, dict):
            continue
        name = str(incident.get("source_filename") or "")
        if not name:
            continue
        severity = str(incident.get("final_severity") or incident.get("severity") or "").upper()
        if severity not in SEVERITIES:
            severity = "IGNORE"
        # Keep the most serious verdict when several incidents share one clip.
        rank = {"IGNORE": 0, "REVIEW": 1, "IMPORTANT": 2}
        if name not in result or rank[severity] > rank[result[name]]:
            result[name] = severity
    return result


def score_command(args: argparse.Namespace) -> int:
    labels_path = Path(args.labels)
    session_path = Path(args.session)
    labels = load_labels(labels_path)
    predicted = _session_severities(session_path)

    truth = {
        str(item["filename"]): str(item["human_severity"]).upper()
        for item in labels["items"]
        if isinstance(item, dict) and item.get("human_severity")
    }
    if not truth:
        print("No human labels yet. Run the label command first.", file=sys.stderr)
        return 1

    scored = 0
    missing_from_scan = 0
    confusion: dict[str, dict[str, int]] = {a: {b: 0 for b in SEVERITIES} for a in SEVERITIES}

    for name, human in truth.items():
        if name not in predicted:
            missing_from_scan += 1
            continue
        confusion[human][predicted[name]] += 1
        scored += 1

    if scored == 0:
        print("None of the labelled clips appear in that session.", file=sys.stderr)
        return 1

    # A "false important" is anything Mimir escalated to IMPORTANT that a human
    # did not; a "missed important" is the reverse and is the costlier error.
    false_important = sum(confusion[h]["IMPORTANT"] for h in ("REVIEW", "IGNORE"))
    missed_important = sum(confusion["IMPORTANT"][p] for p in ("REVIEW", "IGNORE"))
    human_important = sum(confusion["IMPORTANT"].values())
    non_important = sum(sum(confusion[h].values()) for h in ("REVIEW", "IGNORE"))
    exact = sum(confusion[s][s] for s in SEVERITIES)

    report = {
        "schema_version": "mimir_severity_score_v1",
        "generated_at": utc_now(),
        "session": str(session_path),
        "scored_clips": scored,
        "labelled_clips_missing_from_session": missing_from_scan,
        "exact_agreement": exact,
        "exact_agreement_rate": round(exact / scored, 4),
        "missed_important": missed_important,
        "missed_important_rate": round(missed_important / human_important, 4) if human_important else None,
        "false_important": false_important,
        "false_important_rate": round(false_important / non_important, 4) if non_important else None,
        "confusion": confusion,
    }

    print("Mimir severity agreement")
    print("========================")
    print(f"scored clips: {scored}" + (f" ({missing_from_scan} labelled clips not in this session)" if missing_from_scan else ""))
    print(f"exact agreement: {exact}/{scored} ({report['exact_agreement_rate']:.0%})")
    if human_important:
        print(f"missed IMPORTANT: {missed_important}/{human_important} ({report['missed_important_rate']:.0%})")
    else:
        print("missed IMPORTANT: no human-IMPORTANT clips labelled yet")
    if non_important:
        print(f"false IMPORTANT: {false_important}/{non_important} ({report['false_important_rate']:.0%})")
    else:
        print("false IMPORTANT: no human non-IMPORTANT clips labelled yet")
    print()
    print("human \\ mimir".ljust(16) + "".join(s.ljust(12) for s in SEVERITIES))
    for human in SEVERITIES:
        print(human.ljust(16) + "".join(str(confusion[human][p]).ljust(12) for p in SEVERITIES))

    if args.report:
        write_json(Path(args.report), report)
        print()
        print(f"report: {args.report}")

    if scored < 30:
        print()
        print(f"NOTE: {scored} clips is a small sample -- treat these rates as directional, not a measurement.")

    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)

    init = commands.add_parser("init", help="Scan a footage folder and add unlabelled entries.")
    init.add_argument("--input", required=True)
    init.add_argument("--labels", required=True)

    label = commands.add_parser("label", help="Interactively assign severities to unlabelled clips.")
    label.add_argument("--labels", required=True)

    score = commands.add_parser("score", help="Score a scan session against the human labels.")
    score.add_argument("--labels", required=True)
    score.add_argument("--session", required=True)
    score.add_argument("--report", default="")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "init":
        return init_command(args)
    if args.command == "label":
        return label_command(args)
    if args.command == "score":
        return score_command(args)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
