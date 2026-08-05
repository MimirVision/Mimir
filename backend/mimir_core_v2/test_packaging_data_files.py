"""Data files loaded from beside a module must be bundled into the exe.

PyInstaller freezes imported *modules*. A JSON file sitting next to one is
invisible to it unless something says --add-data, and the failure only shows
up in a packaged build: from source the file is on disk, so development never
sees it.

That is exactly how contributions stayed broken from the first packaged build
to 2026-08-05. dataset_package.py reads training_exclusions.json via
Path(__file__).with_name(...), nothing bundled it, and every attempt from an
installed Mimir died with "Invalid JSON file ...\\_MEIxxxx\\mimir_core_v2\\
training_exclusions.json: No such file or directory". Feedback kept working
because export-feedback never touches the exclusion list, so the one code path
that had never worked was also the one nobody could see failing.

This asserts the general shape rather than that single file: every data file a
shipped module loads from beside itself is either bundled or explicitly listed
here as developer-only.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
BACKEND_ROOT = PACKAGE_DIR.parent
BUILD_SCRIPT = BACKEND_ROOT / "build_backend_exe.py"

# `Path(__file__).with_name("something.json")` -- the pattern that bites.
WITH_NAME = re.compile(r"""Path\(__file__\)\.with_name\(\s*["']([^"']+)["']\s*\)""")

# Modules that never reach a packaged executable. cvat_client talks to a local
# CVAT server during annotation, which is a developer-side workflow; the build
# excludes it, so its project definition has no business in a user's install.
DEVELOPER_ONLY_MODULES = {"cvat_client.py"}


class PackagedDataFilesTest(unittest.TestCase):
    def test_side_loaded_data_files_are_bundled(self) -> None:
        build_text = BUILD_SCRIPT.read_text(encoding="utf-8")
        missing: list[str] = []

        for module in sorted(PACKAGE_DIR.glob("*.py")):
            if module.name.startswith("test_") or module.name in DEVELOPER_ONLY_MODULES:
                continue

            for data_name in WITH_NAME.findall(module.read_text(encoding="utf-8")):
                data_path = PACKAGE_DIR / data_name
                if not data_path.exists():
                    # A module referencing a file that is not there is its own
                    # bug, and would fail the same way in a packaged build.
                    missing.append(f"{module.name} loads {data_name}, which does not exist in the package")
                    continue
                if data_name not in build_text:
                    missing.append(
                        f"{module.name} loads {data_name} from beside itself, "
                        f"but build_backend_exe.py never adds it with --add-data"
                    )

        self.assertEqual(
            missing,
            [],
            "Data files loaded next to a module are not bundled by PyInstaller unless the build "
            "says so. Add an --add-data entry in build_backend_exe.py for each, or add the module "
            "to DEVELOPER_ONLY_MODULES if it genuinely never ships.\n  " + "\n  ".join(missing),
        )

    def test_the_exclusions_list_is_bundled_for_the_dataset_exe(self) -> None:
        """The specific regression, pinned by name.

        The general test above would pass if training_exclusions.json were
        bundled into *some* executable. It has to be in the dataset one --
        that is what exports a contribution.
        """

        import build_backend_exe  # noqa: PLC0415 - imported here so the suite runs without it on sys.path

        command = build_backend_exe.pyinstaller_command(
            "mimir-core-v2-dataset", BACKEND_ROOT / "mimir_core_v2_dataset.py"
        )
        added = " ".join(command)
        self.assertIn(
            "training_exclusions.json",
            added,
            "mimir-core-v2-dataset must bundle training_exclusions.json; dataset_package.py "
            "reads it on every export-encrypted, and without it a contribution cannot be built "
            "from an installed Mimir.",
        )


if __name__ == "__main__":
    unittest.main()
