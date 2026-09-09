"""The bits that decide whether Core runs on a GPU, and whether it admits when it isn't.

These are cheap, pure-logic tests on purpose. The expensive question -- does the
CUDA image actually work -- can only be answered by building it on a Linux box
with a GPU. What can be answered here is the part that was already wrong once:
the provider default was a hardcoded "DmlExecutionProvider", which on Linux
names a provider that cannot exist, so the detector fell through to CPU without
a word.
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from mimir_core_v2 import doctor
from mimir_core_v2.onnx_object_detector import _preferred_provider


class ProviderSelection(unittest.TestCase):
    """What the detector asks ONNX Runtime for, given what is installed."""

    def setUp(self):
        self._saved = os.environ.pop("MIMIR_ONNX_PROVIDER", None)

    def tearDown(self):
        os.environ.pop("MIMIR_ONNX_PROVIDER", None)
        if self._saved is not None:
            os.environ["MIMIR_ONNX_PROVIDER"] = self._saved

    def test_windows_directml_box_is_unchanged(self):
        # The shipping configuration. If this ever changes, the desktop app
        # quietly lost its GPU.
        self.assertEqual(
            _preferred_provider(["DmlExecutionProvider", "CPUExecutionProvider"]),
            "DmlExecutionProvider",
        )

    def test_linux_cuda_box_asks_for_cuda(self):
        # The case the old hardcoded default got wrong: it asked for DirectML,
        # which is not in this list, so the caller fell back to CPU on a machine
        # with a working NVIDIA card.
        self.assertEqual(
            _preferred_provider(["CUDAExecutionProvider", "CPUExecutionProvider"]),
            "CUDAExecutionProvider",
        )

    def test_cpu_only_wheel_says_cpu(self):
        self.assertEqual(_preferred_provider(["CPUExecutionProvider"]), "CPUExecutionProvider")

    def test_never_names_a_provider_that_is_not_installed(self):
        # The whole failure mode in one assertion: whatever comes back must be
        # something onnxruntime actually has, or the caller silently degrades.
        for available in (
            ["CPUExecutionProvider"],
            ["DmlExecutionProvider", "CPUExecutionProvider"],
            ["CUDAExecutionProvider", "CPUExecutionProvider"],
            ["ROCMExecutionProvider", "CPUExecutionProvider"],
            ["AzureExecutionProvider", "CPUExecutionProvider"],
        ):
            self.assertIn(_preferred_provider(available), available, f"for {available}")

    def test_gpu_wins_when_both_are_somehow_present(self):
        # Should not happen -- the two wheels conflict -- but if an environment
        # ends up with both, take the one with a real device behind it rather
        # than whichever the list happens to start with.
        self.assertEqual(
            _preferred_provider(
                ["DmlExecutionProvider", "CUDAExecutionProvider", "CPUExecutionProvider"]
            ),
            "CUDAExecutionProvider",
        )

    def test_override_wins_even_when_absent(self):
        # Deliberate: an override that silently did nothing when the provider
        # was missing would make a reproducibility run impossible to trust.
        # Asking for something uninstallable should fail loudly downstream, not
        # be quietly reinterpreted here.
        os.environ["MIMIR_ONNX_PROVIDER"] = "CUDAExecutionProvider"
        self.assertEqual(
            _preferred_provider(["DmlExecutionProvider", "CPUExecutionProvider"]),
            "CUDAExecutionProvider",
        )

    def test_override_can_pin_cpu_on_a_gpu_box(self):
        os.environ["MIMIR_ONNX_PROVIDER"] = "CPUExecutionProvider"
        self.assertEqual(
            _preferred_provider(["CUDAExecutionProvider", "CPUExecutionProvider"]),
            "CPUExecutionProvider",
        )

    def test_blank_override_is_not_an_override(self):
        os.environ["MIMIR_ONNX_PROVIDER"] = "   "
        self.assertEqual(
            _preferred_provider(["CUDAExecutionProvider", "CPUExecutionProvider"]),
            "CUDAExecutionProvider",
        )


class Doctor(unittest.TestCase):
    """The startup check a cloud worker gates on."""

    def test_every_check_reports_rather_than_raising(self):
        # A diagnostic that dies on a broken machine tells you nothing about the
        # machine. Each check must come back with a verdict of its own.
        report = doctor.run_all()
        self.assertEqual(set(report["checks"]), set(doctor.CHECKS))
        for name, result in report["checks"].items():
            self.assertIn("ok", result, f"{name} reported no verdict")

    def test_one_broken_check_does_not_hide_the_others(self):
        # Patched through CHECKS rather than the module attribute: the dict
        # captures function references at import, so it is what run_all()
        # actually calls.
        def explode():
            raise RuntimeError("model exploded")

        with patch.dict(doctor.CHECKS, {"model": explode}):
            report = doctor.run_all()
        self.assertFalse(report["checks"]["model"]["ok"])
        self.assertIn("model exploded", report["checks"]["model"]["error"])
        self.assertIn("ok", report["checks"]["python"])

    def test_missing_binary_is_an_answer_not_a_crash(self):
        self.assertEqual(doctor._run(["definitely-not-a-real-binary-xyz"]), "")

    def test_a_missing_model_fails_unless_explicitly_allowed(self):
        """The flag CI needs must not weaken what a container demands.

        The weights are a .onnx, which the repository forbids tracking, so a CI
        checkout can never have one -- but a container without a model is an
        image that cannot infer, and must not start. Both halves are asserted
        here against the real main(), because the first version of this test
        built its own parser and therefore proved nothing.
        """

        def absent():
            return {"ok": False, "error": "model file not found"}

        with patch.dict(doctor.CHECKS, {"model": absent, "session": absent}):
            with patch("sys.argv", ["doctor", "--json"]):
                with patch("builtins.print"):
                    strict = doctor.main()
            with patch("sys.argv", ["doctor", "--json", "--allow-missing-model"]):
                with patch("builtins.print"):
                    lenient = doctor.main()

        self.assertEqual(strict, 1, "a container must refuse to start without weights")
        self.assertEqual(lenient, 0, "CI must be able to run this without a model")

    def test_gpu_flag_follows_the_chosen_provider(self):
        with patch.dict(os.environ, {"MIMIR_ONNX_PROVIDER": "CPUExecutionProvider"}):
            result = doctor.check_onnxruntime()
        if result.get("ok"):
            self.assertFalse(result["gpu"], "CPU provider must not report as a GPU")


if __name__ == "__main__":
    unittest.main()
