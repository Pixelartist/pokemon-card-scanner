"""Background identification: the OCR -> LLM chain and its attempt accounting.

The providers are stubbed, so these run without tesseract or an API key; what is
under test is the queue's behaviour (who runs, when it is exhausted, what gets
written back), not the OCR itself.
"""
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from services import card_text, identify_jobs


class IdentifyJobs(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = str(Path(self._tmp.name) / "test.db")
        conn = sqlite3.connect(self.db)
        conn.execute("CREATE TABLE cards (ptcg_id TEXT, name TEXT, number TEXT)")
        conn.executemany(
            "INSERT INTO cards (ptcg_id, name, number) VALUES (?,?,?)",
            [("sv07-077", "Meditite", "077")],
        )
        conn.commit()
        conn.close()
        self._stop_worker()

    def tearDown(self):
        self._stop_worker()
        self._tmp.cleanup()

    def _stop_worker(self):
        # Each test owns its own database; the module-level worker would keep
        # polling the previous one otherwise.
        with identify_jobs._lock:
            identify_jobs._worker = None
            identify_jobs._worker_db = None

    def run_job(self, ocr=None, llm=None, llm_configured=None):
        # `llm=None` means the provider is absent; `llm_configured=True` with
        # `llm=None` means it ran and came back with nothing.
        configured = llm is not None if llm_configured is None else llm_configured
        with mock.patch.object(card_text, "ocr_available", lambda: ocr is not None), \
             mock.patch.object(card_text, "llm_available", lambda: configured), \
             mock.patch.object(card_text, "ocr_text", lambda path: ocr), \
             mock.patch.object(card_text, "vision_llm", lambda path: llm):
            job_id = identify_jobs.enqueue(self.db, "scans/crops/x.jpg")
            self.assertTrue(identify_jobs.wait_for_idle(self.db, timeout=10))
            return identify_jobs.statuses(self.db, [job_id])[0]

    def test_ocr_reading_the_number_resolves_without_asking_the_llm(self):
        calls = []

        def llm(_):
            calls.append("llm")
            return {"set_code": "sv07", "number": "077", "name": "Meditite"}

        job = self.run_job(ocr="SV07 077/198\nMeditite", llm=llm)
        self.assertEqual(job["status"], "done")
        self.assertEqual(job["provider"], "ocr")
        self.assertEqual(job["result_id"], "sv07-077")
        self.assertEqual(job["attempts"], 1)
        self.assertEqual(calls, [], "a resolved OCR read must not spend the LLM attempt")

    def test_clipped_crop_falls_back_to_the_scan_frame(self):
        # The stored crop is cut above the collector line; the frame it came
        # from still has the number, so the job must read that instead.
        def ocr_only_on_the_scan(path):
            return "077/142 Meditie" if str(path).endswith("scan.png") else "~~~~"

        with mock.patch.object(card_text, "ocr_available", lambda: True), \
             mock.patch.object(card_text, "llm_available", lambda: False), \
             mock.patch.object(card_text, "ocr_text", ocr_only_on_the_scan):
            job_id = identify_jobs.enqueue(self.db, "scans/crops/clipped.jpg", "scans/scan.png")
            self.assertTrue(identify_jobs.wait_for_idle(self.db, timeout=10))
            job = identify_jobs.statuses(self.db, [job_id])[0]

        self.assertEqual(job["status"], "done")
        self.assertEqual(job["result_id"], "sv07-077")
        self.assertIn("from the scan", job["detail"])

    def test_crop_alone_that_has_no_number_is_exhausted(self):
        def ocr_only_on_the_scan(path):
            return "077/142 Meditie" if str(path).endswith("scan.png") else "~~~~"

        with mock.patch.object(card_text, "ocr_available", lambda: True), \
             mock.patch.object(card_text, "llm_available", lambda: False), \
             mock.patch.object(card_text, "ocr_text", ocr_only_on_the_scan):
            job_id = identify_jobs.enqueue(self.db, "scans/crops/clipped.jpg")
            self.assertTrue(identify_jobs.wait_for_idle(self.db, timeout=10))
            job = identify_jobs.statuses(self.db, [job_id])[0]

        self.assertEqual(job["status"], "exhausted")

    def test_inconclusive_ocr_falls_through_to_the_llm(self):
        job = self.run_job(ocr="~~~~", llm={"set_code": "sv07", "number": "077", "name": None})
        self.assertEqual(job["status"], "done")
        self.assertEqual(job["provider"], "llm")
        self.assertEqual(job["attempts"], 2)

    def test_both_providers_inconclusive_is_exhausted(self):
        job = self.run_job(ocr="~~~~", llm=None, llm_configured=True)
        self.assertEqual(job["status"], "exhausted")
        self.assertEqual(job["attempts"], 2, "each provider that ran costs one attempt")
        self.assertEqual(job["max_attempts"], 2)
        self.assertIn("ocr: inconclusive", job["detail"])

    def test_no_provider_configured_is_reported_as_unavailable(self):
        job = self.run_job(ocr=None, llm=None)
        self.assertEqual(job["status"], "unavailable")
        self.assertEqual(job["attempts"], 0, "nothing ran, so nothing was spent")

    def test_a_number_without_a_set_code_is_left_alone(self):
        # Only one card carries 077 *and* the name, so it resolves; two matches
        # would be ambiguous and must not be guessed.
        conn = sqlite3.connect(self.db)
        conn.execute("INSERT INTO cards (ptcg_id, name, number) VALUES ('other-077', 'Pikachu', '077')")
        conn.commit()
        resolved = card_text.resolve_card(conn, {"number": "77", "set_code": None}, name="Meditite")
        conn.close()
        self.assertEqual(resolved["ptcg_id"], "sv07-077")


if __name__ == "__main__":
    unittest.main()
