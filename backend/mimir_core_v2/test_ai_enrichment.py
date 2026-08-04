"""Guardrails for asynchronous AI session enrichment."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from .ai_enrichment import enrich_session


class AiEnrichmentTests(unittest.TestCase):
    def test_enrichment_cannot_change_local_decision(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            session_dir = root / "sessions" / "session-test"
            session = {
                "schema_version": "mimir_v2",
                "session_id": "session-test",
                "session_revision": 1,
                "session_output_dir": str(session_dir),
                "incidents": [
                    {
                        "id": "incident_0001",
                        "final_severity": "IMPORTANT",
                        "severity": "IMPORTANT",
                        "event_type": "possible_impact",
                        "summary": "Possible impact/contact evidence was detected.",
                        "classification_debug": {"important_evidence_found": True},
                        "local_evidence": {"strong_impact_like_motion": True},
                        "camera_clips": [],
                    }
                ],
            }
            path = root / "latest_session.json"
            path.write_text(json.dumps(session), encoding="utf-8")

            def reviewer(*args: object, **kwargs: object) -> dict:
                return {
                    "enabled": True,
                    "model": "test-model",
                    "ai_model": "test-model",
                    "ai_reviewed": True,
                    "ai_evidence": {
                        "scene_type": "normal_traffic",
                        "recommended_severity": "IGNORE",
                        "confidence": 0.9,
                    },
                    "runtime_sec": 0.01,
                }

            enriched = enrich_session(path, "test-model", reviewer=reviewer)
            incident = enriched["incidents"][0]
            self.assertEqual(incident["final_severity"], "IMPORTANT")
            self.assertEqual(incident["event_type"], "possible_impact")
            self.assertEqual(incident["ai_recommended_severity"], "IGNORE")
            self.assertEqual(enriched["session_revision"], 2)
            self.assertFalse(enriched["ai_enrichment"]["can_change_final_severity"])

    def test_enrichment_cannot_change_local_decision_even_when_ai_escalates(self) -> None:
        # Mirrors the downgrade-guard test above but with an AI opinion that argues the
        # other direction (more severe). The protected fields must stay untouched either way.
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            session = {
                "schema_version": "mimir_v2",
                "session_id": "session-test",
                "session_revision": 1,
                "session_output_dir": str(root / "sessions" / "session-test"),
                "incidents": [
                    {
                        "id": "incident_0001",
                        "final_severity": "REVIEW",
                        "severity": "REVIEW",
                        "event_type": "pass_by",
                        "summary": "A vehicle passed close by.",
                        "classification_debug": {"important_evidence_found": False},
                        "local_evidence": {"possible_contact": True},
                        "camera_clips": [],
                    }
                ],
            }
            path = root / "latest_session.json"
            path.write_text(json.dumps(session), encoding="utf-8")

            def reviewer(*args: object, **kwargs: object) -> dict:
                return {
                    "ai_reviewed": True,
                    "ai_evidence": {
                        "scene_type": "contact",
                        "recommended_severity": "IMPORTANT",
                        "confidence": 0.95,
                    },
                    "runtime_sec": 0.01,
                }

            enriched = enrich_session(path, "test-model", reviewer=reviewer)
            incident = enriched["incidents"][0]
            self.assertEqual(incident["final_severity"], "REVIEW")
            self.assertEqual(incident["event_type"], "pass_by")
            self.assertEqual(incident["ai_recommended_severity"], "IMPORTANT")

    def test_budget_zero_skips_review_without_calling_reviewer(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            session = {
                "session_id": "session-test",
                "session_output_dir": str(root / "sessions" / "session-test"),
                "incidents": [
                    {
                        "id": "incident_0001",
                        "final_severity": "IMPORTANT",
                        "local_evidence": {"strong_impact_like_motion": True},
                        "camera_clips": [],
                    }
                ],
            }
            path = root / "latest_session.json"
            path.write_text(json.dumps(session), encoding="utf-8")

            def reviewer_should_not_run(*args: object, **kwargs: object) -> dict:
                raise AssertionError("reviewer must not be called when the budget is 0")

            enriched = enrich_session(path, "test-model", budget=0, reviewer=reviewer_should_not_run)
            self.assertEqual(enriched["ai_skipped_groups"], 1)
            self.assertEqual(enriched["ai_reviewed_groups"], 0)
            self.assertFalse(enriched["incidents"][0]["ai_reviewed"])

    def test_incident_without_candidate_evidence_is_skipped_without_calling_reviewer(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            session = {
                "session_id": "session-test",
                "session_output_dir": str(root / "sessions" / "session-test"),
                "incidents": [
                    {
                        "id": "incident_0001",
                        "final_severity": "IGNORE",
                        "local_evidence": {},
                        "camera_clips": [],
                    }
                ],
            }
            path = root / "latest_session.json"
            path.write_text(json.dumps(session), encoding="utf-8")

            def reviewer_should_not_run(*args: object, **kwargs: object) -> dict:
                raise AssertionError("reviewer must not be called for low-evidence incidents")

            enriched = enrich_session(path, "test-model", reviewer=reviewer_should_not_run)
            self.assertEqual(enriched["ai_reviewed_groups"], 0)
            self.assertFalse(enriched["incidents"][0]["ai_reviewed"])

    def test_non_dict_incident_entries_do_not_crash(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            session = {
                "session_id": "session-test",
                "session_output_dir": str(root / "sessions" / "session-test"),
                "incidents": [None, "not-an-incident", {
                    "id": "incident_0002",
                    "final_severity": "IGNORE",
                    "local_evidence": {},
                    "camera_clips": [],
                }],
            }
            path = root / "latest_session.json"
            path.write_text(json.dumps(session), encoding="utf-8")

            enriched = enrich_session(path, "test-model", reviewer=lambda *a, **k: {})
            self.assertEqual(len(enriched["incidents"]), 3)
            self.assertIsNone(enriched["incidents"][0])

    def test_missing_vlm_name_raises(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "latest_session.json"
            path.write_text(json.dumps({"incidents": []}), encoding="utf-8")
            with self.assertRaises(ValueError):
                enrich_session(path, "   ")

    def test_session_without_incidents_key_raises(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "latest_session.json"
            path.write_text(json.dumps({"session_id": "no-incidents"}), encoding="utf-8")
            with self.assertRaises(ValueError):
                enrich_session(path, "test-model")


if __name__ == "__main__":
    unittest.main()
