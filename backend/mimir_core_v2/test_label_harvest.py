"""Harvesting in-app corrections into a replayable evaluation set."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from mimir_core_v2.label_harvest import evaluate, harvest_sessions, write_label_set


def write_session(root: Path, name: str, incidents: list[dict]) -> Path:
    folder = root / name
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "session.json"
    path.write_text(
        json.dumps({"session_id": name, "incidents": incidents}), encoding="utf-8"
    )
    return path


def incident(
    incident_id: str,
    said: str,
    human: str | None = None,
    *,
    group: str | None = None,
    evidence: dict | None = None,
    note: str = "",
) -> dict:
    row = {
        "id": incident_id,
        "event_group_id": group or f"group_{incident_id}",
        "final_severity": said,
        "source_category": "SentryClips",
        "local_evidence": evidence if evidence is not None else {"contact_level": "MEDIUM"},
    }
    if human is not None:
        # Mirrors what save_manual_status writes: the verdict is always
        # recorded, and the flag only says whether it differs from Mimir.
        row["user_status"] = human
        row["manual_status_override"] = human != said
    if note:
        row["user_note"] = note
    return row


class HarvestTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_only_explicit_corrections_become_labels(self) -> None:
        """A user who changed nothing has not agreed -- they may not have looked."""

        write_session(
            self.root,
            "s1",
            [
                incident("incident_0001", "IMPORTANT", "REVIEW"),
                incident("incident_0002", "REVIEW"),  # untouched
                incident("incident_0003", "IGNORE"),  # untouched
            ],
        )

        report = harvest_sessions([self.root])

        self.assertEqual(report.incidents_seen, 3)
        self.assertEqual(len(report.labels), 1)
        self.assertEqual(report.labels[0].human_severity, "REVIEW")

    def test_an_explicit_confirmation_is_kept_but_marked_as_agreement(self) -> None:
        """Confirmations used to be deleted, and that lost half the signal.

        save_manual_status set manual_status_override = (status != mimir) and
        removed user_status when they matched, so only disagreements survived.
        A set with no agreements in it cannot show that a change which removed
        false positives did not take the true ones with them.
        """

        write_session(
            self.root,
            "s1",
            [incident("incident_0001", "REVIEW", "REVIEW")],
        )

        report = harvest_sessions([self.root])

        self.assertEqual(len(report.labels), 1)
        self.assertTrue(report.labels[0].agreed)
        self.assertEqual(report.corrections, 0)
        self.assertEqual(report.agreements, 1)

    def test_the_same_footage_rescanned_is_not_counted_twice(self) -> None:
        """Otherwise one clip weights any measurement taken from this set."""

        write_session(self.root, "s1", [incident("incident_0001", "IMPORTANT", "IGNORE", group="g1")])
        write_session(self.root, "s2", [incident("incident_0044", "IMPORTANT", "IGNORE", group="g1")])

        report = harvest_sessions([self.root])

        self.assertEqual(report.sessions_seen, 2)
        self.assertEqual(len(report.labels), 1)

    def test_a_row_without_evidence_cannot_be_replayed_so_is_skipped(self) -> None:
        write_session(
            self.root,
            "s1",
            [
                incident("incident_0001", "IMPORTANT", "IGNORE", evidence={}),
                incident("incident_0002", "IMPORTANT", "IGNORE"),
            ],
        )

        report = harvest_sessions([self.root])

        self.assertEqual(len(report.labels), 1)
        self.assertEqual(report.skipped_no_evidence, 1)

    def test_the_evidence_travels_with_the_label(self) -> None:
        write_session(
            self.root,
            "s1",
            [
                incident(
                    "incident_0001",
                    "IMPORTANT",
                    "IGNORE",
                    evidence={"contact_level": "HIGH", "single_camera_close_activity": True},
                    note="just a neighbour walking past",
                )
            ],
        )

        report = harvest_sessions([self.root])
        row = report.labels[0]

        self.assertEqual(row.local_evidence["contact_level"], "HIGH")
        self.assertEqual(row.note, "just a neighbour walking past")

    def test_a_malformed_session_is_reported_and_the_rest_still_harvest(self) -> None:
        (self.root / "broken").mkdir()
        (self.root / "broken" / "session.json").write_text("{not json", encoding="utf-8")
        write_session(self.root, "s1", [incident("incident_0001", "IMPORTANT", "IGNORE")])

        report = harvest_sessions([self.root])

        self.assertEqual(len(report.labels), 1)
        self.assertEqual(len(report.warnings), 1)

    def test_the_written_set_is_one_replayable_row_per_line(self) -> None:
        write_session(
            self.root,
            "s1",
            [
                incident("incident_0001", "IMPORTANT", "IGNORE"),
                incident("incident_0002", "REVIEW", "IGNORE"),
            ],
        )
        report = harvest_sessions([self.root])

        output = write_label_set(report, self.root / "labels.jsonl")

        lines = output.read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(lines), 2)
        first = json.loads(lines[0])
        self.assertIn("local_evidence", first)
        self.assertIn("human_severity", first)


class EvaluateTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_scoring_counts_only_the_rows_a_human_corrected(self) -> None:
        """Rows where the human agreed are easy and would flatter any ruleset."""

        write_session(
            self.root,
            "s1",
            [
                # corrected: Mimir said IMPORTANT, human said IGNORE
                incident("incident_0001", "IMPORTANT", "IGNORE", evidence={"want": "IGNORE"}),
                # corrected: Mimir said IMPORTANT, human said REVIEW
                incident("incident_0002", "IMPORTANT", "REVIEW", evidence={"want": "REVIEW"}),
                # agreement, must not inflate the score
                incident("incident_0003", "REVIEW", "REVIEW", evidence={"want": "REVIEW"}),
            ],
        )
        report = harvest_sessions([self.root])

        # A perfect resolver: returns whatever the evidence says the human wanted.
        perfect = evaluate(report, lambda evidence, ai: {"final_severity": evidence["want"]})
        self.assertEqual(perfect["corrected_rows"], 2)
        self.assertEqual(perfect["corrected_now_matching_human"], 2)
        self.assertEqual(perfect["corrected_agreement"], 1.0)

        # A resolver that never changed: still says IMPORTANT for everything.
        unchanged = evaluate(report, lambda evidence, ai: {"final_severity": "IMPORTANT"})
        self.assertEqual(unchanged["corrected_now_matching_human"], 0)
        self.assertEqual(unchanged["corrected_agreement"], 0.0)

    def test_scoring_an_empty_set_does_not_divide_by_zero(self) -> None:
        report = harvest_sessions([self.root])

        result = evaluate(report, lambda evidence, ai: {"final_severity": "IGNORE"})

        self.assertEqual(result["rows"], 0)
        self.assertIsNone(result["corrected_agreement"])


if __name__ == "__main__":
    unittest.main()
