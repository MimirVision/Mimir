"""Benchmark CLI contract tests."""

from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from .benchmark import main


class BenchmarkContractTests(unittest.TestCase):
    def _fixture(self, root: Path) -> tuple[Path, Path]:
        session = root / "latest_session.json"
        labels = root / "labels.csv"
        session.write_text(json.dumps({"incidents": []}), encoding="utf-8")
        with labels.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=("match_value", "expected_severity", "category", "notes", "source_set"),
            )
            writer.writeheader()
            writer.writerow(
                {
                    "match_value": "fixture.mp4",
                    "expected_severity": "IGNORE",
                    "category": "normal_traffic",
                    "notes": "fixture",
                    "source_set": "available_set",
                }
            )
        return session, labels

    def test_zero_selected_labels_still_writes_report(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            session, labels = self._fixture(root)
            report = root / "report.json"
            exit_code = main(
                [
                    "--session",
                    str(session),
                    "--labels",
                    str(labels),
                    "--report",
                    str(report),
                    "--source-set",
                    "missing_set",
                ]
            )
            self.assertEqual(exit_code, 0)
            payload = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "no_labels_for_source_set")
            self.assertEqual(payload["labels_matched"], 0)

    def test_strict_zero_selected_labels_fails_after_writing_report(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            session, labels = self._fixture(root)
            report = root / "strict-report.json"
            exit_code = main(
                [
                    "--session",
                    str(session),
                    "--labels",
                    str(labels),
                    "--report",
                    str(report),
                    "--source-set",
                    "missing_set",
                    "--strict",
                ]
            )
            self.assertEqual(exit_code, 2)
            self.assertTrue(report.exists())


if __name__ == "__main__":
    unittest.main()
