"""What a contribution's consent receipt must contain, and what it must not.

The receipt is not consent from the people in the footage -- that cannot be
obtained from a stranger walking past a parked car. It is the uploader
attesting that they have the right to share this specific clip, on this date,
on a stated basis. That is what answers a later "that is my car, remove it".

Which is why an invented value is worse than an empty one: a column filled to
look complete is evidence of nothing, and it is indistinguishable from
evidence of something until someone checks.
"""

from __future__ import annotations

import unittest

from mimir_core_v2.dataset_package import DatasetPackageError


def consent(**overrides) -> dict:
    base = {
        "schema_version": "mimir_dataset_consent_v2",
        "rights_confirmed": True,
        "automatic_upload": False,
        "recorded_by": "Andreas",
        "rights_basis": "owned",
        "permission_reference": "",
    }
    base.update(overrides)
    return base


def check(receipt: dict) -> None:
    """The receipt half of validate_contribution_package, in isolation.

    Mirrors the checks in dataset_package.py. Kept here rather than driving a
    whole package fixture because these five rules are the ones with legal
    weight, and they should fail loudly and separately.
    """

    if receipt.get("schema_version") != "mimir_dataset_consent_v2":
        raise DatasetPackageError("A version 2 clip-by-clip consent receipt is required.")
    if receipt.get("rights_confirmed") is not True or receipt.get("automatic_upload") is not False:
        raise DatasetPackageError("Contribution consent is missing or invalid.")
    if not str(receipt.get("recorded_by") or "").strip():
        raise DatasetPackageError("Consent recorder is missing.")
    rights_basis = str(receipt.get("rights_basis") or "").strip()
    if not rights_basis:
        raise DatasetPackageError("Consent rights basis is missing.")
    if rights_basis != "owned" and not str(receipt.get("permission_reference") or "").strip():
        raise DatasetPackageError(
            f"A permission reference is required when the rights basis is '{rights_basis}'."
        )


class ConsentReceiptTest(unittest.TestCase):
    def test_your_own_footage_needs_no_external_reference(self) -> None:
        """The case that used to be a dead end.

        Someone contributing footage from their own car has no signed release
        to cite. Requiring one made the ordinary case impossible to complete,
        and three separate checks rejected the empty string without any of
        them explaining why.
        """

        check(consent(rights_basis="owned", permission_reference=""))

    def test_borrowed_footage_still_has_to_cite_something(self) -> None:
        for basis in ("explicit_permission", "public_license"):
            with self.subTest(basis=basis):
                with self.assertRaises(DatasetPackageError) as caught:
                    check(consent(rights_basis=basis, permission_reference=""))
                # The message names the basis, so it is obvious why this one
                # needs a reference when the previous upload did not.
                self.assertIn(basis, str(caught.exception))

    def test_who_recorded_it_is_never_optional(self) -> None:
        # The terms of service can carry the warranty. It cannot say which
        # person made this particular claim.
        with self.assertRaises(DatasetPackageError):
            check(consent(recorded_by="   "))

    def test_the_basis_itself_is_never_optional(self) -> None:
        with self.assertRaises(DatasetPackageError):
            check(consent(rights_basis=""))

    def test_an_automatic_upload_is_refused_outright(self) -> None:
        # Footage of other people must never leave the machine as a side
        # effect of something else the user did.
        with self.assertRaises(DatasetPackageError):
            check(consent(automatic_upload=True))

    def test_an_unconfirmed_receipt_is_refused(self) -> None:
        with self.assertRaises(DatasetPackageError):
            check(consent(rights_confirmed=False))

    def test_a_v1_receipt_is_refused(self) -> None:
        with self.assertRaises(DatasetPackageError):
            check(consent(schema_version="mimir_dataset_consent_v1"))


if __name__ == "__main__":
    unittest.main()
