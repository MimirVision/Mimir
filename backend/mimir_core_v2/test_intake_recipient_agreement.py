"""The documented intake key must be the one the app actually encrypts to.

Two age key pairs exist on the maintainer's machine, and for the whole life of
the beta TRAINING_DATA_GUIDE.md documented the wrong one. The guide said to
encrypt with mimir-training-recipient.txt and decrypt with
mimir-training-intake-identity.txt -- a self-consistent pair, so following the
guide end to end always worked. It just was not the pair the desktop app uses.

Nothing exercised the difference, because until 2026-08-05 no packaged build
could produce a contribution at all (see test_packaging_data_files.py). The
moment real ones started arriving, the documented intake command would have
failed on every one of them with "no identity matched any of the recipients" --
a message that reads like a corrupt package, not a wrong key.

The private keys are gitignored, so this cannot verify the pair cryptographically
in CI. It checks the thing that actually drifted: the recipient compiled into
the app versus the recipient the guide tells a human to use.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
MAIN_RS = REPO_ROOT / "desktop" / "src-tauri" / "src" / "main.rs"
GUIDE = BACKEND_ROOT / "TRAINING_DATA_GUIDE.md"

AGE_RECIPIENT = re.compile(r"age1[a-z0-9]{50,}")


class IntakeRecipientAgreementTest(unittest.TestCase):
    def setUp(self) -> None:
        if not MAIN_RS.exists():
            self.skipTest(
                f"{MAIN_RS} is not present. This check needs the desktop app and the "
                "backend in one checkout, which is the normal monorepo layout."
            )

    def app_recipient(self) -> str:
        text = MAIN_RS.read_text(encoding="utf-8", errors="replace")
        found = AGE_RECIPIENT.findall(text)
        self.assertTrue(
            found,
            f"No age recipient found in {MAIN_RS}. If TRAINING_AGE_RECIPIENT moved or was "
            "renamed, point this test at its new home rather than deleting it.",
        )
        self.assertEqual(
            len(set(found)),
            1,
            f"{MAIN_RS} names more than one age recipient: {sorted(set(found))}. Exactly one "
            "key can be the live intake, so this is ambiguous by construction.",
        )
        return found[0]

    def test_the_guide_documents_the_recipient_the_app_encrypts_to(self) -> None:
        expected = self.app_recipient()
        guide_text = GUIDE.read_text(encoding="utf-8", errors="replace")

        # Deliberately not assertIn: on failure it prints the haystack, and the
        # haystack here is the whole guide.
        self.assertTrue(
            expected in guide_text,
            f"{GUIDE.name} never mentions {expected}, which is the recipient the desktop app "
            "encrypts every contribution to. Whatever key the guide documents instead cannot "
            "open a single package a tester sends.",
        )

    def test_the_guide_does_not_tell_anyone_to_encrypt_to_a_retired_key(self) -> None:
        expected = self.app_recipient()
        stale = []

        for number, line in enumerate(GUIDE.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            # Only lines that direct an export are a hazard. The key table
            # deliberately names the retired recipient so pre-rekey packages
            # stay openable, and must not trip this.
            if "--recipient" not in line:
                continue
            for recipient in AGE_RECIPIENT.findall(line):
                if recipient != expected:
                    stale.append(f"{GUIDE.name}:{number} exports to {recipient}")

        self.assertEqual(
            stale,
            [],
            "An export command in the guide encrypts to something other than the live intake "
            f"recipient ({expected}). Packages made that way cannot be decrypted by the intake "
            "pipeline.\n  " + "\n  ".join(stale),
        )


if __name__ == "__main__":
    unittest.main()
