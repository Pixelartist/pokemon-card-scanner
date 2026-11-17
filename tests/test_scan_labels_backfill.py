"""Repairs and reports in scripts/scan_labels_backfill.py.

The script runs over a corpus that decides gate tuning, so two things it touches
matter: an id it rewrites has to name the same card the app will show, and a
runner-up it cannot know must stay visibly absent instead of turning into a
guess. The card database is built here rather than taken from data/ so the test
holds whatever the live corpus contains.
"""
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "scan_labels_backfill.py"


class Backfill(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.dir = root / "scans"
        self.dir.mkdir()
        self.db = root / "cards.db"
        conn = sqlite3.connect(self.db)
        conn.execute("CREATE TABLE cards (id INTEGER PRIMARY KEY, ptcg_id TEXT)")
        conn.executemany("INSERT INTO cards (id, ptcg_id) VALUES (?, ?)",
                         [(15032, "sv10-099"), (40, "base1-4")])
        conn.commit()
        conn.close()

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, name, **label):
        Path(self.dir, name).write_text(json.dumps(label), encoding="utf-8")

    def run_backfill(self, *args):
        done = subprocess.run([sys.executable, str(SCRIPT), "--dir", str(self.dir),
                               "--db", str(self.db), *args], capture_output=True, text=True)
        return done.stdout

    def read(self, name):
        return json.loads((self.dir / name).read_text(encoding="utf-8"))

    def test_numeric_ids_become_ptcg_ids(self):
        self.write("a.json", scan="scans/a_blob", filed_as=15032, proposed_id=40,
                   proposed_similarity=79.0,
                   proposed_second_id="sv02-074", proposed_second_similarity=77.2)
        out = self.run_backfill()
        data = self.read("a.json")
        self.assertEqual(data["filed_as"], "sv10-099")
        self.assertEqual(data["proposed_id"], "base1-4")
        self.assertEqual(data["proposed_second_id"], "sv02-074", "a ptcg_id must be left alone")
        self.assertIn("15032 -> sv10-099", out)
        self.assertIn("40 -> base1-4", out)

    def test_unresolvable_id_is_left_as_posted(self):
        self.write("b.json", scan="scans/b_blob", filed_as="sv01-034", proposed_id="sv04-059",
                   proposed_similarity=80.0,
                   proposed_second_id=999999, proposed_second_similarity=79.0)
        out = self.run_backfill()
        self.assertEqual(self.read("b.json")["proposed_second_id"], 999999)
        self.assertIn("1 numeric id(s) left as posted", out)

    def test_labels_without_a_runner_up_are_reported_not_filled(self):
        self.write("c.json", scan="scans/c_blob", filed_as="sv02-074", proposed_id="sv02-074",
                   proposed_similarity=80.0, proposed_second_similarity=73.3)
        out = self.run_backfill()
        data = self.read("c.json")
        self.assertNotIn("proposed_second_id", data, "the id is unknowable, so it must not be invented")
        self.assertIn("1 label(s) with no runner-up recorded", out)
        self.assertIn("missing proposed_second_id", out)

    def test_dry_run_reports_without_writing(self):
        self.write("d.json", scan="scans/d_blob", filed_as=15032, proposed_id="sv10-099",
                   proposed_similarity=79.0,
                   proposed_second_id="sv02-074", proposed_second_similarity=77.2)
        out = self.run_backfill("--dry-run")
        self.assertEqual(self.read("d.json")["filed_as"], 15032)
        self.assertIn("Would resolve 1 numeric id(s) to ptcg_id", out)


if __name__ == "__main__":
    unittest.main()
