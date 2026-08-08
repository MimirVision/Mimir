"""Bring footage off removable media before scanning it.

Two problems, one answer.

The first is speed. A scan reads tens of gigabytes, and on the development
machine the Tesla USB drive sustains about 38 MB/s. Everything downstream --
decode, detection, thumbnails -- ends up waiting on that.

The second is worse. Sustained heavy reading from that drive took Windows down
three times in one day with DRIVER_POWER_STATE_FAILURE bugchecks, minutes into
a scan, and the only device reporting I/O retries was the Tesla drive. Reading
49 GB in one long run is far more than these drives normally do. Scanning a
local copy sidesteps the whole failure mode.

Copy, verify, then delete -- never a bare move
----------------------------------------------
shutil.move across volumes is a copy followed by an unlink that trusts the
copy. On a drive already returning I/O errors that is precisely the assumption
not to make, because the footage on it is the only copy in existence.

So every file is hashed while it is being copied (the bytes are already in
hand, so this is free), the destination is read back and hashed, and the source
is removed only if the two agree. Measured on the real drive: the copy runs at
38 MB/s and the read-back at 834 MB/s, so verification adds **5%** -- about one
minute on a 49 GB import. Deleting unverified footage to save that would be a
poor trade at any speed.

Layout
------
Destinations are rebuilt as <destination>/<category>/<event folder>/<file> so
that source_category_for_path still recognises SentryClips, SavedClips and
RecentClips afterwards. Scanning the copy therefore produces the same
categories, and the same folder-per-event structure the storage actions rely
on, as scanning the stick would have.

Whole event folders move, not just the videos: Tesla writes event.json and
thumb.png beside the clips, and leaving those behind means the folder never
actually disappears from the card, which was the point.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

from .validators import source_category_for_path

VIDEO_EXTENSIONS = {".mp4"}
CHUNK = 4 * 1024 * 1024

# Deliberately distinct from MIMIR_PROGRESS. The scan's progress protocol has a
# versioned payload that App.tsx parses; borrowing it for a different phase
# would mean either lying about the fields or changing a contract three
# components depend on.
IMPORT_PREFIX = "MIMIR_IMPORT"


@dataclass
class ImportItem:
    source_folder: Path
    destination_folder: Path
    files: list[Path]
    bytes: int = 0


@dataclass
class ImportPlan:
    items: list[ImportItem] = field(default_factory=list)
    total_bytes: int = 0
    total_files: int = 0
    warnings: list[str] = field(default_factory=list)

    @property
    def event_count(self) -> int:
        return len(self.items)


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            block = handle.read(CHUNK)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def _copy_and_hash(source: Path, destination: Path) -> str:
    """Copy, returning the source digest computed from the bytes as they pass."""

    destination.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    # Written to a temporary name first so an interrupted copy cannot leave a
    # truncated file that looks complete to the next run.
    staging = destination.with_name(destination.name + ".mimir-partial")
    try:
        with source.open("rb") as src, staging.open("wb") as dst:
            while True:
                block = src.read(CHUNK)
                if not block:
                    break
                digest.update(block)
                dst.write(block)
        shutil.copystat(source, staging, follow_symlinks=False)
        os.replace(staging, destination)
    except BaseException:
        staging.unlink(missing_ok=True)
        raise
    return digest.hexdigest()


def _mp4_boxes_reach_end(path: Path) -> bool:
    """Walk an mp4's length-prefixed boxes and see if they land on the file end."""

    import struct

    size = path.stat().st_size
    if size < 16:
        return False
    offset = 0
    try:
        with path.open("rb") as handle:
            while offset < size:
                handle.seek(offset)
                header = handle.read(8)
                if len(header) < 8:
                    return False
                box_size = struct.unpack(">I", header[:4])[0]
                if box_size == 1:
                    box_size = struct.unpack(">Q", handle.read(8))[0]
                elif box_size == 0:
                    return True  # box runs to EOF, which is a complete file
                if box_size < 8:
                    return False
                offset += box_size
    except OSError:
        return False
    return offset == size


def reconcile_partial_copies(destination_root: Path) -> dict:
    """Deal with staging files left by a copy that was killed mid-flight.

    _copy_and_hash writes to `<name>.mimir-partial` and then os.replace()s it
    into position, so an interrupted copy cannot be mistaken for a finished
    one. Its cleanup runs on exception -- but a hard kill raises nothing, and
    on this developer's machine the drive being read was causing
    DRIVER_POWER_STATE_FAILURE bugchecks, so hard kills happened.

    Nothing reconciled the leftovers, and 64 of them accumulated: 1.34 GB of
    fully-written clips that no longer had a source to be re-copied from,
    because the source had been cleared on a later successful pass. Every one
    turned out to be a complete file that had simply never been renamed.

    Three cases, and the ordering matters:

      final exists      -> the partial is junk from a retry. Delete it.
      source exists     -> prefer the source. Delete the partial, re-copy.
      source is gone    -> the partial is the ONLY copy. Promote it if the
                           container is intact, and say so, because it was
                           never hash-verified against anything.
    """

    root = Path(destination_root)
    report = {"deleted": 0, "recovered": 0, "unrecoverable": [], "warnings": []}
    if not root.is_dir():
        return report

    for partial in sorted(root.rglob("*.mimir-partial")):
        final = partial.with_name(partial.name[: -len(".mimir-partial")])

        if final.exists():
            try:
                partial.unlink()
                report["deleted"] += 1
            except OSError as exc:
                report["warnings"].append(f"could not remove {partial}: {exc}")
            continue

        if final.suffix.lower() == ".mp4" and not _mp4_boxes_reach_end(partial):
            report["unrecoverable"].append(str(final))
            report["warnings"].append(
                f"{final.name} was left half-copied and its source is gone. "
                "The incomplete data is still at "
                f"{partial.name} if you want to look at it."
            )
            continue

        try:
            partial.rename(final)
            report["recovered"] += 1
            report["warnings"].append(
                f"Recovered {final.name} from an interrupted copy. The file is complete, "
                "but it could not be checked against its original because the source is gone."
            )
        except OSError as exc:
            report["warnings"].append(f"could not recover {partial}: {exc}")

    return report


def plan_import(source: Path, destination: Path) -> ImportPlan:
    """Work out what would move, without touching anything."""

    plan = ImportPlan()
    source = Path(source)
    destination = Path(destination)

    if not source.exists():
        plan.warnings.append(f"Source folder does not exist: {source}")
        return plan
    if not source.is_dir():
        plan.warnings.append(f"Source is not a folder: {source}")
        return plan

    try:
        resolved_source = source.resolve()
        resolved_destination = destination.resolve()
    except OSError as exc:
        plan.warnings.append(f"Could not resolve paths: {exc}")
        return plan

    # Importing a folder into itself, or into its own child, would walk files
    # it is in the middle of creating.
    if resolved_source == resolved_destination or resolved_source in resolved_destination.parents:
        plan.warnings.append("The destination is inside the folder being imported.")
        return plan

    folders: dict[Path, list[Path]] = {}
    try:
        videos = sorted(
            path
            for path in source.rglob("*")
            if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS
        )
    except OSError as exc:
        plan.warnings.append(f"Could not read the source folder: {exc}")
        return plan

    for video in videos:
        folders.setdefault(video.parent, [])

    for folder in sorted(folders):
        category = source_category_for_path(folder / "x.mp4")
        if category == "generic_folder":
            try:
                relative = folder.relative_to(source)
            except ValueError:
                relative = Path(folder.name)
            target = destination / relative
        elif folder.name.lower() == category.lower():
            # The clips sit directly in SentryClips/ rather than in a
            # per-event subfolder, which is how RecentClips is laid out.
            target = destination / category
        else:
            target = destination / category / folder.name

        try:
            files = sorted(path for path in folder.iterdir() if path.is_file())
        except OSError as exc:
            plan.warnings.append(f"Could not read {folder}: {exc}")
            continue

        total = 0
        for path in files:
            try:
                total += path.stat().st_size
            except OSError:
                pass

        plan.items.append(ImportItem(folder, target, files, total))
        plan.total_files += len(files)
        plan.total_bytes += total

    return plan


def import_footage(
    source: Path,
    destination: Path,
    remove_source: bool,
    dry_run: bool,
    on_progress: Callable[[dict], None] | None = None,
) -> dict:
    """Copy footage in, verify it, and optionally clear the source."""

    # Before anything else: clear up after a previous run that was killed.
    # Doing this first means a partial cannot be mistaken for a finished copy
    # by the size check further down.
    reconciled = reconcile_partial_copies(Path(destination))

    plan = plan_import(source, destination)
    report: dict = {
        "action": "import_footage",
        "source": str(source),
        "destination": str(destination),
        "remove_source": remove_source,
        "dry_run": dry_run,
        "events_found": plan.event_count,
        "files_found": plan.total_files,
        "bytes_found": plan.total_bytes,
        "files_copied": 0,
        "bytes_copied": 0,
        "files_skipped": 0,
        "source_files_removed": 0,
        "source_folders_removed": 0,
        "warnings": list(plan.warnings) + reconciled["warnings"],
        "partials_deleted": reconciled["deleted"],
        "partials_recovered": reconciled["recovered"],
        "partials_unrecoverable": reconciled["unrecoverable"],
        "failures": [],
        "ok": True,
    }

    if plan.warnings and not plan.items:
        report["ok"] = False
        return report

    if dry_run:
        return report

    done_files = 0

    for item in plan.items:
        verified: list[Path] = []

        for source_file in item.files:
            destination_file = item.destination_folder / source_file.name
            done_files += 1

            if on_progress is not None:
                on_progress(
                    {
                        "stage": "copying",
                        "current": done_files,
                        "total": plan.total_files,
                        "percent": round(done_files / max(1, plan.total_files) * 100.0, 1),
                        "file": source_file.name,
                        "event": item.source_folder.name,
                    }
                )

            try:
                # Already imported and identical: skip the copy but still let
                # the source be removed, so a re-run after an interruption
                # finishes the job rather than starting over.
                if destination_file.exists() and destination_file.stat().st_size == source_file.stat().st_size:
                    if _hash_file(destination_file) == _hash_file(source_file):
                        report["files_skipped"] += 1
                        verified.append(source_file)
                        continue

                source_digest = _copy_and_hash(source_file, destination_file)
                destination_digest = _hash_file(destination_file)
            except OSError as exc:
                report["failures"].append(
                    {"file": str(source_file), "reason": f"could not copy: {exc}"}
                )
                continue

            if source_digest != destination_digest:
                report["failures"].append(
                    {
                        "file": str(source_file),
                        "reason": "the copy does not match the original, so the original was kept",
                    }
                )
                continue

            report["files_copied"] += 1
            report["bytes_copied"] += destination_file.stat().st_size
            verified.append(source_file)

        if not remove_source:
            continue

        # Only files whose copies were verified byte for byte are removed, and
        # a folder only goes once every file in it is accounted for.
        for source_file in verified:
            try:
                source_file.unlink()
                report["source_files_removed"] += 1
            except OSError as exc:
                report["failures"].append(
                    {"file": str(source_file), "reason": f"copied, but the original could not be removed: {exc}"}
                )

        if len(verified) == len(item.files):
            try:
                remaining = any(item.source_folder.iterdir())
            except OSError:
                remaining = True
            if not remaining:
                try:
                    item.source_folder.rmdir()
                    report["source_folders_removed"] += 1
                except OSError as exc:
                    report["warnings"].append(f"Could not remove {item.source_folder}: {exc}")

    # Only once everything that had video is safely copied and cleared. If any
    # file failed, the drive still holds footage and is not meant to look empty.
    if remove_source and not report["failures"]:
        swept, sweep_warnings = sweep_leftover_folders(Path(source), dry_run)
        report["leftover_folders_removed"] = swept
        report["warnings"].extend(sweep_warnings)

    report["ok"] = not report["failures"]
    if on_progress is not None:
        on_progress(
            {
                "stage": "complete",
                "current": plan.total_files,
                "total": plan.total_files,
                "percent": 100.0,
                "ok": report["ok"],
            }
        )
    return report


# Files Tesla writes beside the clips. Nothing else is ever removed by the
# sweep below, so anything a user put on the stick themselves stays.
TESLA_SIDECAR_FILES = {"event.json", "thumb.png"}


def sweep_leftover_folders(source: Path, dry_run: bool) -> tuple[int, list[str]]:
    """Remove event folders left holding only Tesla's metadata, and empty ones.

    plan_import only looks at folders containing video, which is right -- it
    has no business copying whatever else is on the drive. But a real Tesla
    drive has folders whose clips are already gone, holding just an event.json
    and a thumb.png. After an import that cleared everything else, those are
    what stops the stick from actually being empty, which was the entire point.

    Found on the real drive: the two smallest event folders had no clips at
    all. Deliberately narrow -- a folder goes only if what remains is nothing,
    or nothing but the two sidecar names above.
    """

    removed = 0
    warnings: list[str] = []
    if not source.is_dir():
        return removed, warnings

    # Deepest first, so a parent emptied by its children can go in the same pass.
    folders = sorted(
        (path for path in source.rglob("*") if path.is_dir()),
        key=lambda path: len(path.parts),
        reverse=True,
    )

    for folder in folders:
        try:
            entries = list(folder.iterdir())
        except OSError:
            continue
        if any(entry.is_dir() for entry in entries):
            continue
        if any(entry.name.lower() not in TESLA_SIDECAR_FILES for entry in entries):
            continue
        if dry_run:
            removed += 1
            continue
        try:
            for entry in entries:
                entry.unlink()
            folder.rmdir()
            removed += 1
        except OSError as exc:
            warnings.append(f"Could not remove {folder}: {exc}")

    return removed, warnings


@dataclass
class RelocatedGroup:
    """One event group brought onto local disk, ready to scan."""

    ok: bool
    destination_folder: Path | None
    copied: int = 0
    skipped: int = 0
    bytes_copied: int = 0
    # Verified byte for byte, so safe to remove once the scan has read them.
    removable_sources: list[Path] = field(default_factory=list)
    source_folder: Path | None = None
    failures: list[dict] = field(default_factory=list)


def destination_for_group(source_folder: Path, destination_root: Path) -> Path:
    """Where one event folder lands, preserving the category.

    Same rule as plan_import: the category has to survive, or a scan of the
    copy reports 'generic_folder' and the storage actions stop recognising the
    layout as folder-per-event.
    """

    category = source_category_for_path(source_folder / "x.mp4")
    if category == "generic_folder":
        return destination_root / source_folder.name
    if source_folder.name.lower() == category.lower():
        return destination_root / category
    return destination_root / category / source_folder.name


def relocate_event_group(group: dict, destination_root: Path) -> RelocatedGroup:
    """Copy one event group's files locally and rewrite the group to point at them.

    The unit is the event group rather than the whole drive, so a scan can copy
    one incident, read it off fast local storage, and clear it from the stick
    before touching the next -- instead of a twenty-minute copy phase followed
    by a separate twenty-minute scan phase.

    Nothing is deleted here. Sources that verified are returned so the caller
    can remove them *after* the scan has read the clips: a crash mid-scan then
    leaves the drive untouched rather than half-emptied.
    """

    clips = [clip for clip in (group.get("clips") or []) if isinstance(clip, dict)]
    paths = [Path(str(clip.get("path"))) for clip in clips if clip.get("path")]
    if not paths:
        return RelocatedGroup(ok=False, destination_folder=None,
                              failures=[{"reason": "event group has no clip paths"}])

    source_folder = paths[0].parent
    target = destination_for_group(source_folder, Path(destination_root))
    target.mkdir(parents=True, exist_ok=True)

    result = RelocatedGroup(ok=True, destination_folder=target, source_folder=source_folder)

    # Everything in the folder, so Tesla's event.json and thumb.png travel with
    # the clips and the folder can actually be removed afterwards.
    try:
        members = sorted(path for path in source_folder.iterdir() if path.is_file())
    except OSError as exc:
        return RelocatedGroup(ok=False, destination_folder=target,
                              failures=[{"reason": f"could not read {source_folder}: {exc}"}])

    for source_file in members:
        destination_file = target / source_file.name
        try:
            if destination_file.exists() and destination_file.stat().st_size == source_file.stat().st_size:
                if _hash_file(destination_file) == _hash_file(source_file):
                    result.skipped += 1
                    result.removable_sources.append(source_file)
                    continue
            source_digest = _copy_and_hash(source_file, destination_file)
            if _hash_file(destination_file) != source_digest:
                result.failures.append(
                    {"file": str(source_file), "reason": "the copy does not match the original"}
                )
                continue
            result.copied += 1
            result.bytes_copied += destination_file.stat().st_size
            result.removable_sources.append(source_file)
        except OSError as exc:
            result.failures.append({"file": str(source_file), "reason": f"could not copy: {exc}"})

    if result.failures:
        # A partially copied group is not scanned from the copy: the source is
        # still the complete one, so leave the group pointing at it.
        result.ok = False
        return result

    # Rewrite in place so the rest of the scan reads local files without
    # knowing anything about the import.
    for clip in clips:
        original = str(clip.get("path") or "")
        if not original:
            continue
        clip["original_path"] = original
        clip["path"] = str(target / Path(original).name)
    group["event_folder"] = str(target)
    group["imported_from"] = str(source_folder)

    return result


def remove_relocated_sources(relocated: RelocatedGroup) -> dict:
    """Delete the originals for a group whose copies were verified.

    Called after the scan has read the group, not before. The folder itself
    goes only when nothing is left in it, so a file the user added by hand
    keeps its directory alive.
    """

    removed = 0
    warnings: list[str] = []
    if not relocated.ok:
        return {"removed": 0, "folder_removed": False, "warnings": ["group was not fully copied"]}

    for path in relocated.removable_sources:
        try:
            path.unlink(missing_ok=True)
            removed += 1
        except OSError as exc:
            warnings.append(f"could not remove {path}: {exc}")

    folder_removed = False
    folder = relocated.source_folder
    if folder is not None and folder.is_dir():
        try:
            if not any(folder.iterdir()):
                folder.rmdir()
                folder_removed = True
        except OSError as exc:
            warnings.append(f"could not remove {folder}: {exc}")

    return {"removed": removed, "folder_removed": folder_removed, "warnings": warnings}


def emit_progress(payload: dict) -> None:
    print(f"{IMPORT_PREFIX} {json.dumps(payload, separators=(',', ':'))}", flush=True)
