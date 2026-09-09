"""Metadata-only persistent cache for deterministic object detections."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from pathlib import Path
from typing import Any


CACHE_SCHEMA_VERSION = "mimir_detector_cache_v1"

# Ceiling for the on-disk cache, in megabytes. MIMIR_DETECTOR_CACHE_MAX_MB
# overrides it; 0 or less means no limit.
#
# There was no ceiling at all. On the machine this was found on the database
# had reached 262 MB across 339,887 detection rows -- larger than the program
# it belongs to -- with no prune, no UI showing it, and no mention in any
# user-facing document. Nothing bounded it but how much footage you scanned.
#
# 512 MB is chosen to stop the pathological case rather than to be frugal: the
# cache is what makes a re-scan fast, so evicting eagerly would trade a real
# feature for disk nobody was short of. A user who is short of disk can set the
# variable.
DEFAULT_CACHE_MAX_MB = 512

# Prune down to this fraction of the ceiling, so a scan that sits just above
# the line does not pay for a prune on every single run.
PRUNE_TARGET_FRACTION = 0.8


def _cache_limit_bytes() -> int:
    raw = os.environ.get("MIMIR_DETECTOR_CACHE_MAX_MB", "").strip()
    try:
        megabytes = int(raw) if raw else DEFAULT_CACHE_MAX_MB
    except ValueError:
        megabytes = DEFAULT_CACHE_MAX_MB
    return megabytes * 1024 * 1024 if megabytes > 0 else 0


def default_cache_dir() -> Path:
    configured = os.environ.get("MIMIR_DETECTOR_CACHE_DIR", "").strip()
    if configured:
        return Path(configured).expanduser()
    local_data = os.environ.get("LOCALAPPDATA", "").strip()
    if local_data:
        return Path(local_data) / "Mimir" / "cache" / "detector"
    return Path.home() / ".mimir" / "cache" / "detector"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class DetectorCache:
    def __init__(self, root: Path | None = None) -> None:
        self.root = (root or default_cache_dir()).resolve()
        self.enabled = os.environ.get("MIMIR_DISABLE_DETECTOR_CACHE", "").strip().lower() not in {
            "1",
            "true",
            "yes",
        }
        self._connection: sqlite3.Connection | None = None
        self._source_memory: dict[tuple[str, int, int], str] = {}
        self.hits = 0
        self.misses = 0
        self.writes = 0
        self.metric_hits = 0
        self.metric_misses = 0
        self.metric_writes = 0
        self.errors = 0
        self.pruned_rows = 0
        self.pruned_bytes = 0

    def _connect(self) -> sqlite3.Connection | None:
        if not self.enabled:
            return None
        if self._connection is not None:
            return self._connection
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(self.root / "detections.sqlite3", timeout=10.0)
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=NORMAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS source_hashes (
                    path_key TEXT PRIMARY KEY,
                    size_bytes INTEGER NOT NULL,
                    modified_ns INTEGER NOT NULL,
                    source_sha256 TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS analysis_metrics (
                    cache_key TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS detections (
                    cache_key TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            connection.commit()
            self._connection = connection
            self._prune_if_oversized(connection)
        except (OSError, sqlite3.Error):
            self.errors += 1
            self.enabled = False
            return None
        return self._connection

    def _prune_if_oversized(self, connection: sqlite3.Connection) -> None:
        """Evict oldest detections when the file has outgrown its ceiling.

        Safe to do at any time: every row is keyed by a content hash of the
        source, frame, model, policy and provider, so an evicted row is a cache
        miss and a recomputation, never a wrong answer.

        Eviction is by rowid rather than created_at. Rowid is insertion order,
        needs no index on a table with a third of a million rows, and INSERT OR
        REPLACE assigns a fresh one -- so a row that keeps being rewritten
        keeps moving to the back, which is the behaviour wanted anyway.

        VACUUM is what actually returns the space; deleting rows alone just
        leaves free pages for SQLite to reuse, which bounds growth but never
        shrinks the file someone is complaining about. It runs only when the
        ceiling was breached, so the cost lands rarely and before a scan rather
        than during one.
        """

        limit = _cache_limit_bytes()
        if limit <= 0:
            return

        database = self.root / "detections.sqlite3"
        try:
            size = database.stat().st_size
        except OSError:
            return
        if size <= limit:
            return

        try:
            total = connection.execute("SELECT COUNT(*) FROM detections").fetchone()[0]
            if not total:
                return

            # Proportional to the overshoot, so one prune is enough even for a
            # cache far over the line.
            keep_fraction = min(1.0, (limit * PRUNE_TARGET_FRACTION) / size)
            drop = int(total * (1.0 - keep_fraction))
            if drop <= 0:
                return

            connection.execute(
                "DELETE FROM detections WHERE rowid IN "
                "(SELECT rowid FROM detections ORDER BY rowid LIMIT ?)",
                (drop,),
            )
            connection.commit()
            connection.execute("VACUUM")
            connection.commit()
            self.pruned_rows = drop
            self.pruned_bytes = max(0, size - database.stat().st_size)
        except (OSError, sqlite3.Error):
            # A cache that cannot be tidied is still a usable cache. Never let
            # housekeeping take a scan down.
            self.errors += 1

    def source_sha256(self, source_video: str) -> str:
        path = Path(source_video)
        if not self.enabled or not path.is_file():
            return ""
        try:
            stat = path.stat()
            path_key = hashlib.sha256(str(path.resolve()).lower().encode("utf-8")).hexdigest()
            memory_key = (path_key, stat.st_size, stat.st_mtime_ns)
            if memory_key in self._source_memory:
                return self._source_memory[memory_key]
            connection = self._connect()
            if connection is not None:
                row = connection.execute(
                    "SELECT source_sha256 FROM source_hashes WHERE path_key=? AND size_bytes=? AND modified_ns=?",
                    (path_key, stat.st_size, stat.st_mtime_ns),
                ).fetchone()
                if row:
                    digest = str(row[0])
                    self._source_memory[memory_key] = digest
                    return digest
            digest = _sha256_file(path)
            self._source_memory[memory_key] = digest
            if connection is not None:
                connection.execute(
                    "INSERT OR REPLACE INTO source_hashes(path_key,size_bytes,modified_ns,source_sha256) VALUES(?,?,?,?)",
                    (path_key, stat.st_size, stat.st_mtime_ns, digest),
                )
                connection.commit()
            return digest
        except (OSError, sqlite3.Error):
            self.errors += 1
            return ""

    @staticmethod
    def cache_key(
        source_sha256: str,
        frame_index: int,
        model_sha256: str,
        policy: str,
        provider: str,
    ) -> str:
        payload = {
            "schema": CACHE_SCHEMA_VERSION,
            "source_sha256": source_sha256,
            "frame_index": int(frame_index),
            "model_sha256": model_sha256,
            "policy": policy,
            "provider": provider,
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()

    def get(self, cache_key: str) -> list[dict[str, Any]] | None:
        connection = self._connect()
        if connection is None or not cache_key:
            return None
        try:
            row = connection.execute(
                "SELECT payload_json FROM detections WHERE cache_key=?",
                (cache_key,),
            ).fetchone()
            if not row:
                self.misses += 1
                return None
            payload = json.loads(str(row[0]))
            if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
                self.misses += 1
                return None
            self.hits += 1
            return payload
        except (json.JSONDecodeError, sqlite3.Error):
            self.errors += 1
            self.misses += 1
            return None

    def put(self, cache_key: str, detections: list[dict[str, Any]]) -> None:
        connection = self._connect()
        if connection is None or not cache_key:
            return
        try:
            connection.execute(
                "INSERT OR REPLACE INTO detections(cache_key,payload_json) VALUES(?,?)",
                (cache_key, json.dumps(detections, separators=(",", ":"), sort_keys=True)),
            )
            connection.commit()
            self.writes += 1
        except (TypeError, sqlite3.Error):
            self.errors += 1

    def get_metric(self, cache_key: str) -> dict[str, Any] | None:
        connection = self._connect()
        if connection is None or not cache_key:
            return None
        try:
            row = connection.execute(
                "SELECT payload_json FROM analysis_metrics WHERE cache_key=?",
                (cache_key,),
            ).fetchone()
            if not row:
                self.metric_misses += 1
                return None
            payload = json.loads(str(row[0]))
            if not isinstance(payload, dict):
                self.metric_misses += 1
                return None
            self.metric_hits += 1
            return payload
        except (json.JSONDecodeError, sqlite3.Error):
            self.errors += 1
            self.metric_misses += 1
            return None

    def put_metric(self, cache_key: str, payload: dict[str, Any]) -> None:
        connection = self._connect()
        if connection is None or not cache_key:
            return
        try:
            connection.execute(
                "INSERT OR REPLACE INTO analysis_metrics(cache_key,payload_json) VALUES(?,?)",
                (cache_key, json.dumps(payload, separators=(",", ":"), sort_keys=True)),
            )
            connection.commit()
            self.metric_writes += 1
        except (TypeError, sqlite3.Error):
            self.errors += 1

    def diagnostics(self) -> dict[str, Any]:
        database = self.root / "detections.sqlite3"
        return {
            "detector_cache_version": CACHE_SCHEMA_VERSION,
            "detector_cache_enabled": self.enabled,
            "detector_cache_hits": self.hits,
            "detector_cache_misses": self.misses,
            "detector_cache_writes": self.writes,
            "analysis_cache_hits": self.metric_hits,
            "analysis_cache_misses": self.metric_misses,
            "analysis_cache_writes": self.metric_writes,
            "detector_cache_errors": self.errors,
            "detector_cache_database_bytes": database.stat().st_size if database.is_file() else 0,
            "detector_cache_limit_bytes": _cache_limit_bytes(),
            "detector_cache_pruned_rows": self.pruned_rows,
            "detector_cache_pruned_bytes": self.pruned_bytes,
        }

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None


_SHARED_CACHE = DetectorCache()


def shared_cache() -> DetectorCache:
    return _SHARED_CACHE
