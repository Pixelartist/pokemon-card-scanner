#!/usr/bin/env python3
"""Quickdraw matcher tests.

Runs under pytest (python -m pytest tests/ -q), under unittest discovery, or on
its own:

    python tests/test_quickdraw.py [--daemon]

The fingerprint index and the two Wailmer images are prerequisites; when they are
absent the tests skip with a reason instead of failing at import. That matters:
the module imports numpy and Pillow, so without the dev dependencies installed it
used to abort collection of the whole `tests` directory and hide real failures
behind an ImportError.
"""
import argparse
import subprocess
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

INDEX = ROOT / "data" / "quickdraw-index.bin"
ENGLISH = ROOT / "data" / "images" / "tcgdex" / "en" / "me05-015.png"
GERMAN = ROOT / "data" / "german_cards" / "images" / "de_me05-015.png"
SERVER = "http://localhost:5005"
EXPECTED_CARDS = 40291


def server_is_up(timeout=2):
    try:
        import requests
    except ImportError:
        return False
    try:
        return requests.get(f"{SERVER}/api/quickdraw/status", timeout=timeout).ok
    except Exception:
        return False


def start_server():
    print("Server not running, starting...")
    with open(ROOT / "daemon.log", "w") as log:
        subprocess.Popen([sys.executable, "daemon.py"], cwd=ROOT, stdout=log,
                         stderr=subprocess.STDOUT, start_new_session=True)
    time.sleep(5)


class QuickdrawMatcherTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        missing = [str(p) for p in (INDEX, ENGLISH, GERMAN) if not p.exists()]
        if missing:
            raise unittest.SkipTest("missing " + ", ".join(missing))
        from services.quickdraw_matcher import QuickdrawMatcher

        cls.matcher = QuickdrawMatcher()
        if not cls.matcher.load_index():
            raise unittest.SkipTest(f"{INDEX.name} did not load")

    def test_index_loads(self):
        """The index holds every card it was built from."""
        self.assertEqual(self.matcher.count, EXPECTED_CARDS)

    def test_english_self_match(self):
        result = self.matcher.recognize(ENGLISH.read_bytes(), lang="en")
        self.assertEqual(result["decision"], "match")
        self.assertTrue(result["candidates"])
        top = result["candidates"][0]
        self.assertEqual(top["ptcg_id"], "me05-015")
        self.assertLess(top["distance"], self.matcher.MATCH_DIST)

    def test_german_self_match(self):
        result = self.matcher.recognize(GERMAN.read_bytes(), lang="de")
        self.assertEqual(result["decision"], "match")
        self.assertTrue(result["candidates"])
        top = result["candidates"][0]
        self.assertEqual(top["ptcg_id"], "de_me05-015")
        self.assertLess(top["distance"], self.matcher.MATCH_DIST)

    def test_language_filter(self):
        """An English query never answers with the German variant."""
        result = self.matcher.recognize(ENGLISH.read_bytes(), lang="en")
        for candidate in result["candidates"]:
            self.assertFalse(candidate["ptcg_id"].startswith("de_"), candidate["ptcg_id"])

    def test_api_endpoints(self):
        try:
            import requests
        except ImportError:
            self.skipTest("requests is not installed")
        try:
            status = requests.get(f"{SERVER}/api/quickdraw/status", timeout=5)
        except requests.RequestException as exc:
            self.skipTest(f"no server on {SERVER} ({exc})")

        self.assertEqual(status.status_code, 200, status.text)
        self.assertEqual(status.json()["cards_indexed"], EXPECTED_CARDS)

        scan = requests.post(f"{SERVER}/api/scan", files={"image": ENGLISH.read_bytes()},
                             data={"lang": "en"}, timeout=120)
        self.assertEqual(scan.status_code, 200, scan.text)
        self.assertEqual(scan.json()["decision"], "match")

        scan = requests.post(f"{SERVER}/api/scan", files={"image": GERMAN.read_bytes()},
                             data={"lang": "de"}, timeout=120)
        self.assertEqual(scan.status_code, 200, scan.text)
        self.assertEqual(scan.json()["decision"], "match")


def main():
    parser = argparse.ArgumentParser(description="Quickdraw matcher tests")
    parser.add_argument("--daemon", action="store_true", help="start the server first if it is down")
    args, rest = parser.parse_known_args()
    if args.daemon and not server_is_up():
        start_server()
    unittest.main(argv=[sys.argv[0], *rest])


if __name__ == "__main__":
    main()
