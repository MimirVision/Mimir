"""The judgement in score_feedback_labels, separated from the scanning.

Whether Mimir is noisier or quieter than the person who complained is the whole
point of that script, and it is decided by two small pure functions. They are
tested here so the conclusion does not depend on having footage to hand.
"""

from __future__ import annotations

import unittest

from score_feedback_labels import compare, group_severity


class CompareTest(unittest.TestCase):
    def test_matching_verdicts_agree(self) -> None:
        for severity in ("IGNORE", "REVIEW", "IMPORTANT"):
            with self.subTest(severity=severity):
                self.assertEqual(compare(severity, severity), "agrees")

    def test_flagging_harder_than_asked_is_noisier(self) -> None:
        self.assertEqual(compare("IGNORE", "REVIEW"), "noisier")
        self.assertEqual(compare("IGNORE", "IMPORTANT"), "noisier")
        self.assertEqual(compare("REVIEW", "IMPORTANT"), "noisier")

    def test_flagging_softer_than_asked_is_quieter(self) -> None:
        """The direction that loses evidence, and the reason the two are not one number."""

        self.assertEqual(compare("IMPORTANT", "REVIEW"), "quieter")
        self.assertEqual(compare("REVIEW", "IGNORE"), "quieter")
        self.assertEqual(compare("IMPORTANT", "IGNORE"), "quieter")

    def test_case_does_not_decide_the_answer(self) -> None:
        self.assertEqual(compare("review", "REVIEW"), "agrees")
        self.assertEqual(compare("Ignore", "important"), "noisier")

    def test_an_unrecognised_verdict_is_not_silently_counted(self) -> None:
        # Anything else would fold a parsing failure into the agreement rate.
        self.assertEqual(compare("", "REVIEW"), "unknown")
        self.assertEqual(compare("REVIEW", "MAYBE"), "unknown")


class GroupSeverityTest(unittest.TestCase):
    def test_the_group_takes_its_most_severe_incident(self) -> None:
        """What the user reacted to was the loudest thing in the group, not the first."""

        incidents = [
            {"final_severity": "IGNORE"},
            {"final_severity": "IMPORTANT"},
            {"final_severity": "REVIEW"},
        ]

        self.assertEqual(group_severity(incidents), "IMPORTANT")

    def test_it_falls_back_to_severity_when_final_severity_is_absent(self) -> None:
        self.assertEqual(group_severity([{"severity": "REVIEW"}]), "REVIEW")

    def test_an_empty_group_is_not_treated_as_important(self) -> None:
        self.assertEqual(group_severity([]), "IGNORE")

    def test_an_unreadable_severity_does_not_outrank_a_real_one(self) -> None:
        incidents = [{"final_severity": "REVIEW"}, {"final_severity": None}]

        self.assertEqual(group_severity(incidents), "REVIEW")


if __name__ == "__main__":
    unittest.main()
