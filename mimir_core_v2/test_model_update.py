"""Tests for the model-update override mechanism.

These guard the safety property that matters most here: a broken, tampered,
or partial model package must never become the active detector. The bundled
model is always the safe fallback.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path

from . import model_update
from .model_manifest import OVERRIDE_DIR_ENV, active_detector_manifest


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_package(root: Path, *, model_bytes: bytes = b"fake-onnx-weights", version="rfdetr-test-1", approved=True, release_blocker=False, filename="models/rfdetr-nano.onnx", bad_checksum=False):
    model_path = root / filename
    model_path.parent.mkdir(parents=True, exist_ok=True)
    model_path.write_bytes(model_bytes)
    checksum = _sha256_bytes(model_bytes) if not bad_checksum else "0" * 64
    manifest = {
        "manifest_version": "mimir_model_manifest_v1",
        "detector_id": "rfdetr_nano_coco",
        "detector_version": version,
        "runtime": "onnxruntime",
        "architecture": "rf_detr",
        "model_files": [filename],
        "models": [{"filename": filename, "sha256": checksum, "size_bytes": len(model_bytes)}],
        "license": "Apache-2.0",
        "commercial_distribution_approved": approved,
        "release_blocker": release_blocker,
    }
    (root / "model_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return manifest


class ValidatePackageTests(unittest.TestCase):
    def test_valid_package_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_package(root)
            manifest = model_update.validate_package(root)
            self.assertTrue(all(item["checksum_matches"] for item in manifest["resolved_model_files"]))

    def test_rejects_checksum_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_package(root, bad_checksum=True)
            with self.assertRaises(model_update.ModelPackageError):
                model_update.validate_package(root)

    def test_rejects_missing_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(model_update.ModelPackageError):
                model_update.validate_package(Path(tmp))

    def test_rejects_unapproved_distribution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_package(root, approved=False)
            with self.assertRaises(model_update.ModelPackageError):
                model_update.validate_package(root)

    def test_rejects_release_blocker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_package(root, release_blocker=True)
            with self.assertRaises(model_update.ModelPackageError):
                model_update.validate_package(root)

    def test_rejects_missing_model_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_package(root)
            (root / "models" / "rfdetr-nano.onnx").unlink()
            with self.assertRaises(model_update.ModelPackageError):
                model_update.validate_package(root)

    def test_rejects_missing_declared_license_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = _write_package(root)
            manifest["license_file"] = "licenses/does-not-exist.txt"
            (root / "model_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaises(model_update.ModelPackageError):
                model_update.validate_package(root)


class InstallModelPackageTests(unittest.TestCase):
    def test_install_copies_files_and_reports_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package = root / "package"
            target = root / "override"
            _write_package(package, version="rfdetr-test-2")

            result = model_update.install_model_package(package, target)

            self.assertTrue(result["ok"])
            self.assertEqual(result["detector_version"], "rfdetr-test-2")
            self.assertTrue((target / "model_manifest.json").is_file())
            self.assertTrue((target / "models" / "rfdetr-nano.onnx").is_file())

    def test_invalid_package_leaves_existing_install_untouched(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            good_package = root / "good"
            bad_package = root / "bad"
            target = root / "override"
            _write_package(good_package, version="rfdetr-good")
            _write_package(bad_package, version="rfdetr-bad", bad_checksum=True)

            model_update.install_model_package(good_package, target)
            with self.assertRaises(model_update.ModelPackageError):
                model_update.install_model_package(bad_package, target)

            status = model_update.describe_active_model(target)
            self.assertEqual(status["detector_version"], "rfdetr-good")

    def test_install_replaces_prior_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "first"
            second = root / "second"
            target = root / "override"
            _write_package(first, version="rfdetr-v1")
            _write_package(second, version="rfdetr-v2")

            model_update.install_model_package(first, target)
            model_update.install_model_package(second, target)

            status = model_update.describe_active_model(target)
            self.assertEqual(status["detector_version"], "rfdetr-v2")
            # Only the current version's files should remain, not both.
            self.assertEqual(len(list((target / "models").glob("*.onnx"))), 1)


class DescribeActiveModelTests(unittest.TestCase):
    def test_reports_not_installed_when_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            status = model_update.describe_active_model(Path(tmp) / "nowhere")
            self.assertFalse(status["installed"])


class ActiveDetectorManifestOverrideTests(unittest.TestCase):
    def test_override_env_var_is_used_when_valid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_package(root, version="rfdetr-override-active")
            previous = os.environ.get(OVERRIDE_DIR_ENV)
            os.environ[OVERRIDE_DIR_ENV] = str(root)
            try:
                manifest = active_detector_manifest()
            finally:
                if previous is None:
                    os.environ.pop(OVERRIDE_DIR_ENV, None)
                else:
                    os.environ[OVERRIDE_DIR_ENV] = previous
            self.assertEqual(manifest.get("_model_source"), "override")
            self.assertEqual(manifest.get("detector_version"), "rfdetr-override-active")

    def test_falls_back_to_bundled_when_override_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_package(root, bad_checksum=True)
            previous = os.environ.get(OVERRIDE_DIR_ENV)
            os.environ[OVERRIDE_DIR_ENV] = str(root)
            try:
                manifest = active_detector_manifest()
            finally:
                if previous is None:
                    os.environ.pop(OVERRIDE_DIR_ENV, None)
                else:
                    os.environ[OVERRIDE_DIR_ENV] = previous
            self.assertEqual(manifest.get("_model_source"), "bundled")

    def test_no_override_env_var_uses_bundled(self) -> None:
        previous = os.environ.pop(OVERRIDE_DIR_ENV, None)
        try:
            manifest = active_detector_manifest()
        finally:
            if previous is not None:
                os.environ[OVERRIDE_DIR_ENV] = previous
        self.assertEqual(manifest.get("_model_source"), "bundled")


if __name__ == "__main__":
    unittest.main()
