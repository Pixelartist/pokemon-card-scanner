"""Clobber detection in scripts/backup_live_db.py.

The database is untracked and un-ignored, so git refuses to overwrite it - but
that refusal is the outer guard, not this script. These tests put the ignore rule
back so a merge really does replace the file, which is the state the script has
to detect: a merge that writes a tracked blob over the live database and leaves
a modify/delete conflict behind. No hook runs for that case, so the recovery has
to be able to tell "this file is a git blob" from "the app wrote rows into it".

Each test builds its own repository in the system temp directory, so nothing
here touches the real database.
"""
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "backup_live_db.py"


class ClobberDetection(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.repo = self.root / "repo"
        (self.repo / "data").mkdir(parents=True)
        self.backups = self.root / "backups"
        self.db = self.repo / "data" / "live.db"
        self.git("init", "-q", ".")

    def tearDown(self):
        self._tmp.cleanup()

    # --- helpers -----------------------------------------------------------
    def git(self, *args):
        # safe.directory, because git refuses repositories it does not consider
        # owned, and this workspace lives on a network share.
        done = subprocess.run(["git", "-c", "safe.directory=*", "-C", str(self.repo), *args],
                              capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, f"git {' '.join(args)} failed: {done.stderr}")
        return done.stdout

    def make_db(self, marker):
        """A one-row database; the row says which side of the merge produced it."""
        self.db.unlink(missing_ok=True)
        conn = sqlite3.connect(self.db)
        conn.execute("CREATE TABLE cards (name TEXT)")
        conn.execute("INSERT INTO cards (name) VALUES (?)", (marker,))
        conn.commit()
        conn.close()

    def rows(self, path=None, sql="SELECT name FROM cards"):
        conn = sqlite3.connect(path or self.db)
        try:
            return [row[0] for row in conn.execute(sql)]
        finally:
            conn.close()

    def snapshots(self):
        return sorted(self.backups.glob("pokemon_cards.db.*"))

    def run_script(self):
        env = dict(os.environ, GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="safe.directory",
                   GIT_CONFIG_VALUE_0="*")
        return subprocess.run([sys.executable, str(SCRIPT), "--db", "data/live.db",
                               "--dir", str(self.backups)],
                              cwd=self.repo, capture_output=True, text=True, env=env)

    def prepare(self, live_marker="LIVE17NOV"):
        """Base branch tracks the database, the other branch modifies it, ours
        drops it but keeps *.db ignored so the merge can overwrite it."""
        self.make_db("NOV13")
        self.git("add", "-A")
        self.git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base")
        self.git("branch", "base")

        self.git("checkout", "-q", "-b", "remote")
        self.make_db("REMOTEBLOB")
        self.git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qam", "remote db")
        self.git("checkout", "-q", "base")

        self.git("checkout", "-q", "-b", "live")
        self.git("rm", "-q", "data/live.db")
        (self.repo / ".gitignore").write_text("data/*\n*.db\n", encoding="utf-8")
        self.git("add", ".gitignore")
        self.git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "stop tracking")

        (self.repo / "data").mkdir(exist_ok=True)
        self.make_db(live_marker)

    # --- tests -------------------------------------------------------------
    def test_merge_clobber_is_restored_and_not_archived(self):
        self.prepare()
        first = self.run_script()
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(len(self.snapshots()), 1)
        self.assertEqual(self.rows(), ["LIVE17NOV"])

        merged = subprocess.run(["git", "-c", "safe.directory=*", "-C", str(self.repo),
                                 "merge", "remote"], capture_output=True, text=True)
        self.assertNotEqual(merged.returncode, 0, "the merge should conflict")
        self.assertIn("modify/delete", merged.stdout + merged.stderr)
        self.assertEqual(self.rows(), ["REMOTEBLOB"], "the merge must have replaced the file")

        recovered = self.run_script()
        self.assertEqual(recovered.returncode, 0, recovered.stderr)
        self.assertIn("had been replaced by the MERGE_HEAD blob", recovered.stderr)
        self.assertEqual(self.rows(), ["LIVE17NOV"], "the snapshot must be back in place")

        copies = self.snapshots()
        self.assertEqual(len(copies), 1, "the clobbered state must not be archived")
        self.assertEqual(self.rows(copies[0]), ["LIVE17NOV"])

    def test_app_written_rows_are_kept_while_a_merge_is_pending(self):
        """MERGE_HEAD still resolves here, so this is the case that separates
        "this file is git's blob" from "the app wrote rows into it"."""
        self.prepare()
        self.assertEqual(self.run_script().returncode, 0)
        before = self.snapshots()

        subprocess.run(["git", "-c", "safe.directory=*", "-C", str(self.repo),
                        "merge", "remote"], capture_output=True, text=True)
        self.assertEqual(self.rows(), ["REMOTEBLOB"])
        recovered = self.run_script()
        self.assertIn("had been replaced by the MERGE_HEAD blob", recovered.stderr)
        self.assertEqual(self.rows(), ["LIVE17NOV"])

        # The app keeps writing while the merge is unresolved: these bytes are
        # nobody's blob, so they must be left alone and stored as they are.
        conn = sqlite3.connect(self.db)
        conn.execute("INSERT INTO cards (name) VALUES ('APPROW')")
        conn.commit()
        conn.close()

        second = self.run_script()
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertNotIn("had been replaced", second.stderr)
        self.assertEqual(self.rows(), ["LIVE17NOV", "APPROW"], "app rows must survive")
        self.assertEqual(len(self.snapshots()), len(before) + 1, "the new state should be stored")

    def test_unchanged_database_reads_nothing(self):
        self.prepare()
        self.assertEqual(self.run_script().returncode, 0)
        before = [p.name for p in self.snapshots()]

        again = self.run_script()
        self.assertEqual(again.returncode, 0)
        self.assertEqual(again.stdout, "", "an unchanged database should be silent")
        self.assertEqual([p.name for p in self.snapshots()], before)


if __name__ == "__main__":
    unittest.main()
