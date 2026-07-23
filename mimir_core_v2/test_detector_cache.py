"""Tests for the metadata-only detector cache."""

from __future__ import annotations

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


if __name__ == "__main__":
    unittest.main()
