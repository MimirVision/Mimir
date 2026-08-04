"""Tests for the metadata-only detector cache."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from .detector_cache import DetectorCache


class DetectorCacheTests(unittest.TestCase):
    def test_cache_uses_source_hash_and_never_stores_frame_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "clip.mp4"
            source.write_bytes(b"real-source-fixture")
            cache = DetectorCache(root / "cache")
            source_hash = cache.source_sha256(str(source))
            key = cache.cache_key(source_hash, 12, "a" * 64, "policy-v1", "CPUExecutionProvider")
            expected = [{"class_name": "vehicle", "bbox": [1, 2, 3, 4]}]
            self.assertIsNone(cache.get(key))
            cache.put(key, expected)
            self.assertEqual(cache.get(key), expected)
            metric_key = cache.cache_key(source_hash, 0, "b" * 64, "refinement-v1", "local-motion")
            self.assertIsNone(cache.get_metric(metric_key))
            cache.put_metric(metric_key, {"refined_time_sec": 1.25, "confidence": 0.8})
            self.assertEqual(cache.get_metric(metric_key)["refined_time_sec"], 1.25)
            cache.close()
            database_bytes = (root / "cache" / "detections.sqlite3").read_bytes()
            self.assertNotIn(b"real-source-fixture", database_bytes)

    def test_put_overwrites_previous_value_for_same_key(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            cache = DetectorCache(Path(temporary) / "cache")
            key = cache.cache_key("source-hash", 1, "model-hash", "policy-v1", "CPUExecutionProvider")
            cache.put(key, [{"class_name": "vehicle"}])
            cache.put(key, [{"class_name": "person"}])
            self.assertEqual(cache.get(key), [{"class_name": "person"}])
            cache.close()

    def test_disabled_cache_is_a_safe_no_op(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            os.environ["MIMIR_DISABLE_DETECTOR_CACHE"] = "1"
            try:
                cache = DetectorCache(Path(temporary) / "cache")
                self.assertFalse(cache.enabled)
                key = cache.cache_key("source-hash", 0, "model-hash", "policy-v1", "CPUExecutionProvider")
                cache.put(key, [{"class_name": "vehicle"}])
                self.assertIsNone(cache.get(key))
                self.assertFalse((Path(temporary) / "cache" / "detections.sqlite3").exists())
                cache.close()
            finally:
                del os.environ["MIMIR_DISABLE_DETECTOR_CACHE"]

    def test_diagnostics_reflect_hit_and_miss_counts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            cache = DetectorCache(Path(temporary) / "cache")
            key = cache.cache_key("source-hash", 2, "model-hash", "policy-v1", "CPUExecutionProvider")
            cache.get(key)
            cache.put(key, [{"class_name": "vehicle"}])
            cache.get(key)
            diagnostics = cache.diagnostics()
            self.assertEqual(diagnostics["detector_cache_misses"], 1)
            self.assertEqual(diagnostics["detector_cache_hits"], 1)
            self.assertEqual(diagnostics["detector_cache_writes"], 1)
            self.assertGreater(diagnostics["detector_cache_database_bytes"], 0)
            cache.close()

    def test_corrupt_payload_is_treated_as_a_miss_not_a_crash(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            cache = DetectorCache(Path(temporary) / "cache")
            key = cache.cache_key("source-hash", 3, "model-hash", "policy-v1", "CPUExecutionProvider")
            connection = cache._connect()
            connection.execute(
                "INSERT INTO detections(cache_key,payload_json) VALUES(?,?)",
                (key, "not-valid-json"),
            )
            connection.commit()
            self.assertIsNone(cache.get(key))
            self.assertEqual(cache.errors, 1)
            cache.close()


if __name__ == "__main__":
    unittest.main()
