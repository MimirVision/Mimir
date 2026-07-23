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


if __name__ == "__main__":
    unittest.main()
