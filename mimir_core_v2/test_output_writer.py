"""Regression test for the session.json bloat that made a real 679-clip scan
grow latest_session.json to 777MB -- several GB of RAM and minutes to parse
on every launch, which looked to the user like Mimir had simply stopped
working."""

from __future__ import annotations

import unittest

from mimir_core_v2.output_writer import incident_from_group


class IncidentFromGroupStorageSizeTest(unittest.TestCase):
    def _incident(self) -> dict:
        event_group = {
            "event_group_id": "group_1",
            "clips": [{"camera": "front", "path": "front.mp4", "filename": "front.mp4"}],
            "available_cameras": ["front"],
        }
        evidence = {
            "primary_camera_candidate": "front",
            "motion_score": 0.4,
            # The two fields that were the actual bloat: dense per-frame
            # diagnostics, ~1MB each on a real scan, never read again once
            # severity/key-moment resolution (which already happened before
            # this function runs) are done with them.
            "camera_evidence": {"front": {"per_frame": list(range(1000))}},
            "object_tracks": [{"frame": i, "boxes": []} for i in range(500)],
        }
        severity = {"severity": "IGNORE", "final_severity": "IGNORE", "event_type": "event", "summary": ""}
        ai_review = {}
        return incident_from_group(1, event_group, evidence, severity, ai_review)

    def test_heavy_diagnostic_fields_are_not_persisted(self) -> None:
        incident = self._incident()
        local_evidence = incident["local_evidence"]
        self.assertNotIn("camera_evidence", local_evidence)
        self.assertNotIn("object_tracks", local_evidence)

    def test_lightweight_fields_survive(self) -> None:
        incident = self._incident()
        self.assertEqual(incident["local_evidence"]["motion_score"], 0.4)
        self.assertEqual(incident["local_evidence"]["primary_camera_candidate"], "front")

    def test_evidence_is_not_duplicated_under_a_second_key(self) -> None:
        # local_evidence_summary used to be assigned the exact same dict as
        # local_evidence -- doubling storage for a field nothing needs
        # (confirmed: neither the frontend, Rust, nor the deferred AI
        # enrichment pass reads local_evidence_summary while local_evidence
        # is present -- it only ever exists as a fallback for its absence).
        incident = self._incident()
        self.assertNotIn("local_evidence_summary", incident)


if __name__ == "__main__":
    unittest.main()
