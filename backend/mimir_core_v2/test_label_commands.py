"""The `labels` commands Forge drives to build the evaluation set.

These write to benchmark_labels.csv, which is the evaluation set itself: hand
work that cannot be reconstructed if a bug truncates or duplicates it. So the
tests here are mostly about what the commands refuse to do.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from mimir_training_ground import labels_save_command


def rows_in(path: Path) -> list[dict]:
    """Read and close. A handle left open breaks TemporaryDirectory on Windows."""

    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def save(path: Path, group: str, severity: str = "IGNORE", category: str = "normal_traffic", notes: str = "") -> dict:
    args = argparse.Namespace(
        group=group,
        severity=severity,
        category=category,
        notes=notes,
        source_set="test_set",
        labels_csv=str(path),
    )
    output = io.StringIO()
    with redirect_stdout(output):
        code = labels_save_command(args)
    return {"exit_code": code, **json.loads(output.getvalue())}


class LabelSaveTests(unittest.TestCase):
    def test_it_writes_a_row_the_benchmark_can_read(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "labels.csv"

            result = save(path, "event_one", "IMPORTANT", "door_ding", "clear contact")

            self.assertTrue(result["saved"])
            rows = rows_in(path)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["filename_or_group"], "event_one")
            self.assertEqual(rows[0]["expected_severity"], "IMPORTANT")
            self.assertEqual(rows[0]["category"], "door_ding")
            self.assertEqual(rows[0]["source_set"], "test_set")

    def test_it_appends_rather_than_rewriting(self) -> None:
        """The file is the evaluation set. A truncating write would destroy it."""

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "labels.csv"

            save(path, "event_one")
            save(path, "event_two")
            save(path, "event_three")

            rows = rows_in(path)
            self.assertEqual([row["filename_or_group"] for row in rows], ["event_one", "event_two", "event_three"])

    def test_it_refuses_to_label_the_same_group_twice(self) -> None:
        # Two verdicts for one group is worse than none: the benchmark would
        # match whichever it read first, silently.
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "labels.csv"
            save(path, "event_one", "IGNORE")

            second = save(path, "event_one", "IMPORTANT", "rear_impact")

            self.assertFalse(second["saved"])
            self.assertIn("already labelled", second["reason"])
            rows = rows_in(path)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["expected_severity"], "IGNORE", "the first verdict must stand")

    def test_it_refuses_a_category_the_benchmark_does_not_know(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "labels.csv"

            args = argparse.Namespace(
                group="event_one",
                severity="IGNORE",
                category="something_invented",
                notes="",
                source_set="test_set",
                labels_csv=str(path),
            )
            self.assertEqual(labels_save_command(args), 1)
            self.assertFalse(path.exists(), "a rejected label must not create the file")

    def test_a_new_file_gets_a_header(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "labels.csv"

            save(path, "event_one")

            first_line = path.read_text(encoding="utf-8-sig").splitlines()[0]
            self.assertEqual(first_line, "filename_or_group,expected_severity,category,notes,source_set")


if __name__ == "__main__":
    unittest.main()
