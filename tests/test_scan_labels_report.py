"""Correction verdicts in scripts/scan_labels_report.py.

A corrected label says the gate proposed one card and the user filed another;
the report then attributes the capture to a threshold. Those attributions are
what a gate change is based on, so a wrong one sends tuning at the floor when
the margin was at fault, or at the margin when nothing was.

The labels below are built from similarities measured on real captures.
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "scan_labels_report.py"

FLOOR_LOSS = {"filed_as": "sv01-034", "proposed_id": "svp-181", "proposed_similarity": 72.4,
              "proposed_second_id": "sv10-099", "proposed_second_similarity": 71.4}
LEAD_LOSS = {"filed_as": "sv10-099", "proposed_id": "sv04-059", "proposed_similarity": 80.0,
             "proposed_second_id": "sv10-099", "proposed_second_similarity": 79.0}
OTHER_LEAD = {"filed_as": "sv02-074", "proposed_id": "sv04-059", "proposed_similarity": 80.0,
              "proposed_second_id": "dp7-80", "proposed_second_similarity": 79.0}
WRONG_CARD = {"filed_as": "sv03-046", "proposed_id": "sv10.5w-053", "proposed_similarity": 85.7,
              "proposed_second_id": "dp7-80", "proposed_second_similarity": 75.2}
LEGACY = {"filed_as": "sv05-022", "proposed_id": "svp-181", "proposed_similarity": 79.0}


def report(*labels):
    """Run the report over a temporary directory holding exactly these labels."""
    with tempfile.TemporaryDirectory() as tmp:
        for index, label in enumerate(labels):
            data = {"scan": f"scans/case{index}_blob", "crop": None, "match_source": "corrected",
                    "user_id": 30, "recorded_at": "2026-11-17T10:00:00+00:00", **label}
            Path(tmp, f"case{index}.json").write_text(json.dumps(data), encoding="utf-8")
        done = subprocess.run([sys.executable, str(SCRIPT), "--dir", tmp],
                              capture_output=True, text=True)
    return done.stdout


class CorrectedVerdicts(unittest.TestCase):
    def test_lost_to_the_floor(self):
        self.assertIn("lost to the floor (72.4 < 78)", report(FLOOR_LOSS))

    def test_lost_to_the_lead_when_runner_up_is_the_filed_card(self):
        self.assertIn("lost to the lead (1 < 2): runner-up sv10-099 is the filed card",
                      report(LEAD_LOSS))

    def test_rejected_on_another_cards_lead(self):
        """A small lead against some other card proves nothing about the filed one."""
        out = report(OTHER_LEAD)
        self.assertNotIn("lost to the lead (", out)
        self.assertIn("rejected on a lead of 1, but the runner-up dp7-80 is not the filed card", out)

    def test_wrong_card_accepted(self):
        self.assertIn("wrong card accepted (cleared floor and lead, lead 10.5)", report(WRONG_CARD))

    def test_legacy_label_without_runner_up(self):
        out = report(LEGACY)
        self.assertIn("wrong card accepted (no runner-up recorded)", out)
        self.assertIn("1 label(s) predate the runner-up score", out)

    def test_verdicts_are_tallied_separately(self):
        out = report(FLOOR_LOSS, LEAD_LOSS, OTHER_LEAD, WRONG_CARD, LEGACY)
        self.assertIn("of 5 corrected: 1 lost to the floor, 1 lost to the lead, "
                      "1 rejected on another card's lead, 2 wrong card accepted, "
                      "0 same card in another language", out)


if __name__ == "__main__":
    unittest.main()
