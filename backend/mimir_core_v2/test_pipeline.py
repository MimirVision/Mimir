"""Tests for the contribution pipeline automation.

The pipeline automates intake and progress reporting, but must never move a
model toward production on its own. These tests guard both the progress math
and the boundary that keeps promotion a human decision.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import mimir_core_v2_pipeline as pipeline


class GateProgressTests(unittest.TestCase):
    def _audit(self, **overrides):
        base = {
            "collections": 0,
            "items": 0,
            "complete_items": 0,
            "positive_items": 0,
            "hard_negative_items": 0,
            "blind_relabel_items": 0,
            "blind_relabel_required": 0,
            "errors": [],
        }
        base.update(overrides)
        return base

    def test_empty_dataset_reports_full_remaining(self) -> None:
        with mock.patch.object(pipeline, "audit_dataset", return_value=self._audit()):
            progress = pipeline.gate_progress(Path("unused"))
        self.assertEqual(progress["remaining"]["groups"], 100)
        self.assertEqual(progress["remaining"]["positives"], 25)
        self.assertEqual(progress["remaining"]["hard_negatives"], 25)
        self.assertFalse(progress["pilot_gate_met"])

    def test_partial_progress_reduces_remaining(self) -> None:
        audit = self._audit(complete_items=40, positive_items=10, hard_negative_items=7)
        with mock.patch.object(pipeline, "audit_dataset", return_value=audit):
            progress = pipeline.gate_progress(Path("unused"))
        self.assertEqual(progress["current"]["groups"], 40)
        self.assertEqual(progress["remaining"]["groups"], 60)
        self.assertEqual(progress["remaining"]["positives"], 15)
        self.assertEqual(progress["remaining"]["hard_negatives"], 18)
        self.assertFalse(progress["pilot_gate_met"])

    def test_gate_met_only_when_every_target_reached(self) -> None:
        audit = self._audit(complete_items=100, positive_items=25, hard_negative_items=24)
        with mock.patch.object(pipeline, "audit_dataset", return_value=audit):
            progress = pipeline.gate_progress(Path("unused"))
        self.assertFalse(progress["pilot_gate_met"], "one short on hard negatives must not pass")

        audit = self._audit(complete_items=100, positive_items=25, hard_negative_items=25)
        with mock.patch.object(pipeline, "audit_dataset", return_value=audit):
            progress = pipeline.gate_progress(Path("unused"))
        self.assertTrue(progress["pilot_gate_met"])

    def test_exceeding_targets_never_reports_negative_remaining(self) -> None:
        audit = self._audit(complete_items=500, positive_items=90, hard_negative_items=80)
        with mock.patch.object(pipeline, "audit_dataset", return_value=audit):
            progress = pipeline.gate_progress(Path("unused"))
        self.assertEqual(progress["remaining"]["groups"], 0)
        self.assertTrue(progress["pilot_gate_met"])


class PipelineLogTests(unittest.TestCase):
    def test_processed_packages_are_not_taken_in_twice(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            log = pipeline.load_log(root)
            log["processed"].append({"package": "a.mimir-dataset.age", "status": "intake_ok"})
            pipeline.write_json(pipeline.pipeline_log_path(root), log)

            reloaded = pipeline.load_log(root)
            self.assertIn("a.mimir-dataset.age", pipeline.processed_names(reloaded))

    def test_missing_log_starts_empty_rather_than_failing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log = pipeline.load_log(Path(tmp))
            self.assertEqual(log["processed"], [])

    def test_corrupt_log_does_not_crash_the_pipeline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pipeline.pipeline_log_path(root).write_text("{not valid json", encoding="utf-8")
            log = pipeline.load_log(root)
            self.assertEqual(log["processed"], [])


class PromotionBoundaryTests(unittest.TestCase):
    """The pipeline may prepare and report, but never promote."""

    def test_pipeline_exposes_no_promotion_or_training_command(self) -> None:
        parser = pipeline.build_parser()
        actions = [
            action for action in parser._actions
            if getattr(action, "choices", None) and isinstance(action.choices, dict)
        ]
        self.assertTrue(actions, "expected subcommands to be defined")
        commands = set(actions[0].choices)
        self.assertEqual(commands, {"status", "process"})
        for forbidden in ("train", "promote", "deploy", "publish"):
            self.assertNotIn(forbidden, commands)

    def test_source_does_not_invoke_training_or_promotion(self) -> None:
        """Check real call sites via AST, not raw text.

        Prose in docstrings legitimately mentions promotion in order to explain
        why it is a human step, so a substring search would flag its own
        documentation. Only actual invocations matter here.
        """
        import ast

        tree = ast.parse(Path(pipeline.__file__).read_text(encoding="utf-8"))
        called_names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Name):
                    called_names.add(func.id)
                elif isinstance(func, ast.Attribute):
                    called_names.add(func.attr)

        for forbidden in ("train_model", "install_model_package", "promote_candidate"):
            self.assertNotIn(forbidden, called_names, f"pipeline must not call {forbidden}")


if __name__ == "__main__":
    unittest.main()
