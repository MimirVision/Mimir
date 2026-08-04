"""Measure how much footage a detector-free triage pass would keep.

This is the measurement the selective-upload plan rests on. In the proposed
cloud split, a cheap motion-only pass runs on the user's machine and only the
groups it flags get their frames uploaded for the expensive RF-DETR pass. The
open question is what fraction that is: if triage keeps 6% of groups, selective
upload wins overwhelmingly; if it keeps 60%, it collapses toward full upload
and the economics change.

It runs the same footage twice -- once with `--disable-yolo` (the triage
proxy), once normally (the full pipeline) -- and compares.

WHAT THIS MEASURES
    kept_fraction   Groups the detector-free pass rates above IGNORE, i.e. the
                    ones that would need uploading. This is the cost driver.
    dropped         Groups the FULL pipeline rated IMPORTANT or REVIEW that the
                    detector-free pass rated IGNORE. Under selective upload,
                    these never reach the detector, so they are what triage
                    would cost you.

WHAT THIS DOES NOT MEASURE
    Real recall. The full pipeline is a stand-in for ground truth, not ground
    truth -- it has its own false positives, and the beta feedback suggests a
    lot of them. A group both passes rate IGNORE could still be a real event
    both missed. Answering that needs human labels (see MODEL_CARD.md's locked
    evaluation set), not this script.

    Detector-free mode may also deliberately escalate to stay safe when object
    evidence is unavailable, which would inflate kept_fraction. Treat the
    number as an upper bound on what triage keeps.

Usage:
    python experiment_triage_fraction.py --input D:\\TeslaCam\\SentryClips --groups 40
    python experiment_triage_fraction.py --input <dir> --groups 0   # all groups
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

BASE = Path(__file__).resolve().parent
SCANNER = BASE / "mimir_core_v2_scan.py"

KEEP_SEVERITIES = ("IMPORTANT", "REVIEW")


def group_key(incident: dict) -> str:
    """Identify the event group an incident belongs to.

    Prefer the source folder, since event_grouping.py groups on
    (source_folder, event_timestamp); fall back to the incident id so a
    session with an unexpected shape still counts something rather than
    silently collapsing every incident into one bucket.
    """

    for field in ("source_folder", "event_folder", "source_group"):
        value = str(incident.get(field) or "").strip()
        if value:
            return value.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]

    return str(incident.get("id") or incident.get("event_id") or "unknown")


def severity_of(incident: dict) -> str:
    return str(incident.get("final_severity") or incident.get("severity") or "").upper()


def build_subset(source: Path, count: int, workdir: Path, stage_local: bool) -> Path:
    """Make the chosen groups available for scanning.

    Junctions by default (instant, nothing copied). With `stage_local`, the
    footage is copied to local disk first instead.

    Staging locally matters for two reasons. Practically, scanning straight off
    a USB drive means a 5-10 minute sustained read, and a dropout part-way
    through kills the run -- which is exactly how the first two attempts at
    this measurement died. Methodologically, it is also the better experiment:
    reading over USB inflates decode time, which understates the detector's
    share of scan cost, and that share is the number this script exists to
    measure. A short copy burst is far less likely to drop than a long scan.
    """

    groups = sorted(item for item in source.iterdir() if item.is_dir())
    if count > 0:
        groups = groups[:count]

    if not groups:
        raise SystemExit(f"No event-group folders under {source}")

    subset = workdir / "subset"
    subset.mkdir(parents=True, exist_ok=True)

    for index, group in enumerate(groups, start=1):
        target = subset / group.name

        if stage_local:
            print(f"  staging {index}/{len(groups)}: {group.name}", flush=True)
            try:
                shutil.copytree(group, target)
            except OSError as error:
                raise SystemExit(
                    f"Could not copy {group.name} ({error}).\n"
                    "If the source is a removable drive, it may have dropped out mid-copy."
                ) from error
            continue

        # /J is a directory junction: no copy, no admin rights needed.
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(target), str(group)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise SystemExit(f"Could not junction {group.name}: {result.stderr.strip()}")

    return subset


def run_scan(source: Path, output: Path, disable_yolo: bool, cache_dir: Path) -> tuple[dict, float]:
    output.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        str(SCANNER),
        "--input",
        str(source),
        "--mode",
        "balanced",
        "--output",
        str(output),
    ]
    if disable_yolo:
        command.append("--disable-yolo")

    # The detector cache is GLOBAL by default (%LOCALAPPDATA%\Mimir\cache\
    # detector) and persists across runs, so a pass over footage that was
    # scanned before is served from it and reports near-zero detector time.
    # That silently invalidates any timing comparison. Point each pass at its
    # own empty directory so both are genuinely cold -- and so the developer's
    # real cache is left untouched.
    environment = dict(os.environ)
    environment["MIMIR_DETECTOR_CACHE_DIR"] = str(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)

    label = "detector-free triage" if disable_yolo else "full pipeline"
    print(f"  running {label} (cold cache)...", flush=True)
    started = time.monotonic()
    # Output is captured rather than streamed so a failure leaves the real
    # error behind instead of an empty log.
    result = subprocess.run(command, capture_output=True, text=True, env=environment)
    elapsed = time.monotonic() - started

    if result.returncode != 0:
        tail = (result.stderr or result.stdout or "").strip().splitlines()[-15:]
        raise SystemExit(f"{label} failed (exit {result.returncode}):\n" + "\n".join(tail))

    session_file = output / "latest_session.json"
    if not session_file.is_file():
        raise SystemExit(f"{label} wrote no latest_session.json to {output}")

    return json.loads(session_file.read_text(encoding="utf-8")), elapsed


def summarise(session: dict) -> dict[str, str]:
    """Highest severity seen per event group."""

    rank = {"IGNORE": 0, "REVIEW": 1, "IMPORTANT": 2}
    best: dict[str, str] = {}

    for incident in session.get("incidents", []) or []:
        key = group_key(incident)
        severity = severity_of(incident)
        if severity not in rank:
            continue
        if key not in best or rank[severity] > rank[best[key]]:
            best[key] = severity

    return best


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input", required=True, help="Folder containing event-group subfolders.")
    parser.add_argument("--groups", type=int, default=40, help="How many groups to use. 0 means all.")
    parser.add_argument("--keep-output", action="store_true", help="Leave the scan output on disk.")
    parser.add_argument(
        "--stage-local",
        action="store_true",
        help="Copy the footage to local disk before scanning. Use when the source is a removable drive.",
    )
    args = parser.parse_args()

    source = Path(args.input)
    if not source.is_dir():
        raise SystemExit(f"No such folder: {source}")

    workdir = Path(tempfile.mkdtemp(prefix="mimir-exp0-"))
    try:
        subset = build_subset(source, args.groups, workdir, args.stage_local)
        group_count = len(list(subset.iterdir()))
        clip_paths = list(subset.rglob("*.mp4"))
        clips = len(clip_paths)
        gigabytes = sum(path.stat().st_size for path in clip_paths) / (1024**3)
        placement = "copied to local disk" if args.stage_local else "junctioned, nothing copied"
        print(f"Subset: {group_count} groups, {clips} clips, {gigabytes:.1f} GB ({placement})")
        print()

        triage, triage_seconds = run_scan(
            subset, workdir / "out_noyolo", disable_yolo=True, cache_dir=workdir / "cache_noyolo"
        )
        full, full_seconds = run_scan(
            subset, workdir / "out_full", disable_yolo=False, cache_dir=workdir / "cache_full"
        )

        triage_best = summarise(triage)
        full_best = summarise(full)
        groups = sorted(set(triage_best) | set(full_best))

        kept = [key for key in groups if triage_best.get(key, "IGNORE") in KEEP_SEVERITIES]
        full_interesting = [key for key in groups if full_best.get(key, "IGNORE") in KEEP_SEVERITIES]
        dropped = [key for key in full_interesting if triage_best.get(key, "IGNORE") not in KEEP_SEVERITIES]
        dropped_important = [key for key in dropped if full_best.get(key) == "IMPORTANT"]

        total = len(groups) or 1
        print()
        print("=" * 62)
        print(f"Groups analysed:            {len(groups)}")
        print(f"Clips:                      {clips}")
        print()
        print(f"Triage runtime:             {triage_seconds:6.1f}s  ({triage_seconds / max(clips, 1):.2f}s/clip)")
        print(f"Full pipeline runtime:      {full_seconds:6.1f}s  ({full_seconds / max(clips, 1):.2f}s/clip)")
        if triage_seconds > 0:
            print(f"Triage is                   {full_seconds / triage_seconds:6.1f}x cheaper")
        print()

        # The self-reported performance block is the trustworthy source: wall
        # clock includes process startup and model load, and cannot tell a real
        # inference run from a cache hit.
        for label, session in (("triage", triage), ("full", full)):
            performance = session.get("performance") or {}
            hits = performance.get("detector_cache_hits", 0)
            misses = performance.get("detector_cache_misses", 0)
            detector_seconds = performance.get("object_detector_runtime_sec", 0.0) or 0.0
            ready = performance.get("local_results_ready_sec", 0.0) or 0.0
            share = (100 * detector_seconds / ready) if ready else 0.0
            print(f"{label} pass:")
            print(f"  detector runtime:         {detector_seconds:7.1f}s  ({share:.0f}% of scan)")
            print(f"  inferences:               {performance.get('object_detector_inference_count', 0)}")
            print(f"  provider:                 {performance.get('object_detector_provider', 'unknown')}")
            print(f"  cache hits / misses:      {hits} / {misses}")
            if hits:
                print("  !! WARNING: cache hits above zero -- this pass was NOT cold,")
                print("     so its detector timing understates the real cost.")
            parts = performance.get("parts_sec") or {}
            if parts:
                ranked = sorted(parts.items(), key=lambda item: -(item[1] or 0))
                print("  slowest stages:           " + ", ".join(f"{k}={v:.1f}s" for k, v in ranked[:4]))
            print()
        print(f"KEPT BY TRIAGE:             {len(kept)}/{len(groups)}  ({100 * len(kept) / total:.1f}%)")
        print("  ^ the fraction that would need uploading under selective upload")
        print()
        print(f"Full pipeline non-IGNORE:   {len(full_interesting)}")
        print(f"  of those, triage dropped: {len(dropped)}  (IMPORTANT among them: {len(dropped_important)})")
        print("  ^ what selective upload would cost, measured against the full")
        print("    pipeline as a stand-in for truth -- NOT real recall")
        print()
        print("Severity mix per pass:")
        print(f"  triage: {dict(Counter(triage_best.values()))}")
        print(f"  full:   {dict(Counter(full_best.values()))}")
        if dropped:
            print()
            print("Dropped groups (inspect these -- they are the risk):")
            for key in dropped[:20]:
                print(f"  {key}: full={full_best.get(key)} triage={triage_best.get(key, 'IGNORE')}")
        print("=" * 62)

        if args.keep_output:
            print(f"\nScan output kept at {workdir}")
        return 0
    finally:
        if not args.keep_output:
            shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
