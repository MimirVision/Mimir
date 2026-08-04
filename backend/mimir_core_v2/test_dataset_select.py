"""Tests for the `select` batch-review helper in mimir_core_v2_dataset.py.

This only ever prints a ready-to-run export-encrypted command; it must never
export or infer consent on its own -- these tests guard that boundary.
"""

from __future__ import annotations

import argparse
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

import mimir_core_v2_dataset as dataset_cli


def make_incident(incident_id, severity, manual_override=False):
    return {
        "id": incident_id,
        "final_severity": severity,
        "manual_status_override": manual_override,
    }


class IncidentMatchesFilterTests(unittest.TestCase):
    def test_reviewed_matches_manual_override_or_feedback(self) -> None:
        reviewed = make_incident("incident_0001", "IGNORE", manual_override=True)
        untouched = make_incident("incident_0002", "IGNORE")
        has_feedback = make_incident("incident_0003", "IGNORE")

        feedback_ids = {"incident_0003"}
        self.assertTrue(dataset_cli.incident_matches_filter(reviewed, "reviewed", feedback_ids))
        self.assertTrue(dataset_cli.incident_matches_filter(has_feedback, "reviewed", feedback_ids))
        self.assertFalse(dataset_cli.incident_matches_filter(untouched, "reviewed", feedback_ids))

    def test_severity_filters(self) -> None:
        important = make_incident("a", "IMPORTANT")
        review = make_incident("b", "REVIEW")
        ignore = make_incident("c", "IGNORE")

        self.assertTrue(dataset_cli.incident_matches_filter(important, "important", set()))
        self.assertFalse(dataset_cli.incident_matches_filter(review, "important", set()))
        self.assertTrue(dataset_cli.incident_matches_filter(review, "important_or_review", set()))
        self.assertTrue(dataset_cli.incident_matches_filter(important, "important_or_review", set()))
        self.assertFalse(dataset_cli.incident_matches_filter(ignore, "important_or_review", set()))

    def test_all_matches_everything(self) -> None:
        for incident in (make_incident("a", "IGNORE"), make_incident("b", "IMPORTANT")):
            self.assertTrue(dataset_cli.incident_matches_filter(incident, "all", set()))


class FeedbackIncidentIdsTests(unittest.TestCase):
    def test_reads_incident_ids_from_feedback_folders(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "incident_0001_2026").mkdir()
            (root / "incident_0001_2026" / "feedback.json").write_text(
                json.dumps({"incident_id": "incident_0001"}), encoding="utf-8"
            )
            (root / "incident_0002_2026").mkdir()
            (root / "incident_0002_2026" / "feedback.json").write_text(
                json.dumps({"incident_id": "incident_0002"}), encoding="utf-8"
            )
            ids = dataset_cli.feedback_incident_ids(root)
        self.assertEqual(ids, {"incident_0001", "incident_0002"})

    def test_missing_folder_returns_empty_set(self) -> None:
        self.assertEqual(dataset_cli.feedback_incident_ids(Path("C:/does/not/exist")), set())
        self.assertEqual(dataset_cli.feedback_incident_ids(None), set())

    def test_malformed_feedback_file_is_skipped_not_fatal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "broken").mkdir()
            (root / "broken" / "feedback.json").write_text("not json", encoding="utf-8")
            ids = dataset_cli.feedback_incident_ids(root)
        self.assertEqual(ids, set())


class SelectIncidentsTests(unittest.TestCase):
    def _run_select(self, session, **overrides):
        with tempfile.TemporaryDirectory() as temporary:
            session_path = Path(temporary) / "latest_session.json"
            session_path.write_text(json.dumps(session), encoding="utf-8")
            args = argparse.Namespace(
                session=str(session_path),
                filter="reviewed",
                feedback=str(Path(temporary) / "no_feedback_here"),
                recorded_by="",
                rights_basis="",
                permission_reference="",
                output="",
                recipient="",
                recipient_file="",
            )
            for key, value in overrides.items():
                setattr(args, key, value)
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                exit_code = dataset_cli.select_incidents(args)
        return exit_code, buffer.getvalue()

    def test_lists_matches_without_printing_a_command_when_consent_fields_missing(self) -> None:
        session = {"incidents": [make_incident("incident_0001", "IMPORTANT", manual_override=True)]}
        exit_code, output = self._run_select(session)
        self.assertEqual(exit_code, 0)
        self.assertIn("Matched 1 of 1 incidents", output)
        self.assertIn("incident_0001", output)
        self.assertNotIn("export-encrypted", output)

    def test_prints_ready_to_run_command_covering_every_matched_incident(self) -> None:
        session = {
            "incidents": [
                make_incident("incident_0001", "IMPORTANT", manual_override=True),
                make_incident("incident_0002", "REVIEW", manual_override=True),
                make_incident("incident_0003", "IGNORE"),
            ]
        }
        exit_code, output = self._run_select(
            session,
            recorded_by="tester",
            rights_basis="owned",
            permission_reference="Recorded on my own vehicle",
            output="C:\\Exports\\batch.mimir-dataset.age",
        )
        self.assertEqual(exit_code, 0)
        self.assertIn("export-encrypted", output)
        self.assertIn('--consent-incident incident_0001', output)
        self.assertIn('--consent-incident incident_0002', output)
        # incident_0003 was never reviewed, so it must not be swept into the batch.
        self.assertNotIn('--consent-incident incident_0003', output)
        self.assertIn("--rights-confirmed", output)
        self.assertIn("--rights-basis owned", output)

    def test_empty_session_matches_nothing(self) -> None:
        exit_code, output = self._run_select({"incidents": []})
        self.assertEqual(exit_code, 0)
        self.assertIn("Matched 0 of 0 incidents", output)


if __name__ == "__main__":
    unittest.main()
