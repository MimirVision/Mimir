"""Metadata-only persistent cache for deterministic object detections."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from pathlib import Path
from typing import Any


CACHE_SCHEMA_VERSION = "mimir_detector_cache_v1"


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
        except (OSError, sqlite3.Error):
            self.errors += 1
            self.enabled = False
            return None
        return self._connection

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
        }

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None


_SHARED_CACHE = DetectorCache()


def shared_cache() -> DetectorCache:
    return _SHARED_CACHE
