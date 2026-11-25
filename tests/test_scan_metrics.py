"""Capture and startup timing telemetry: what a browser row becomes in the table.

The payload is untrusted browser input, so what is under test is the shaping —
range-checked durations, trimmed text — and that the summary groups by device
without averaging a page load into a capture.
"""
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from services import scan_metrics


class ScanMetrics(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = str(Path(self._tmp.name) / "test.db")

    def tearDown(self):
        self._tmp.cleanup()

    def row(self, **payload):
        """Record one payload and read back the row it became."""
        row_id = scan_metrics.record(self.db, payload)
        conn = sqlite3.connect(self.db)
        conn.row_factory = sqlite3.Row
        try:
            return dict(conn.execute(
                "SELECT * FROM scan_metrics WHERE id = ?", (row_id,)).fetchone())
        finally:
            conn.close()

    def test_a_capture_keeps_its_stages(self):
        row = self.row(device="iPad · iOS", device_class="tablet", browser="Safari 17",
                       total_ms=1840.4, detect_ms=520, crop_ms=260, hash_ms=90,
                       gate_ms=None, cloud_ms=None, frames=3, video="1280x720")
        self.assertEqual(row["device_class"], "tablet")
        self.assertEqual(row["total_ms"], 1840.4)
        self.assertEqual(row["detect_ms"], 520.0)
        self.assertEqual(row["frames"], 3)
        self.assertEqual(row["video"], "1280x720")
        self.assertIsNone(row["gate_ms"])

    def test_a_duration_that_is_not_a_measurement_is_dropped(self):
        # A wall-clock stamp, a negative, a NaN and a typo are not durations;
        # only the one real number survives.
        row = self.row(total_ms="not a number", detect_ms=-5, crop_ms=float("nan"),
                       hash_ms=10 ** 9, cloud_ms="120")
        self.assertIsNone(row["total_ms"])
        self.assertIsNone(row["detect_ms"])
        self.assertIsNone(row["crop_ms"])
        self.assertIsNone(row["hash_ms"])
        self.assertEqual(row["cloud_ms"], 120.0)

    def test_durations_are_rounded_to_a_tenth(self):
        self.assertEqual(self.row(detect_ms=12.34567)["detect_ms"], 12.3)

    def test_long_text_is_trimmed(self):
        row = self.row(device="x" * 500, user_agent="y" * 1000)
        self.assertEqual(len(row["device"]), 120)
        self.assertEqual(len(row["user_agent"]), 400)

    def test_summary_groups_by_device(self):
        for total in (100, 200, 300, 400):
            scan_metrics.record(self.db, {
                "device": "iPad · iOS", "device_class": "tablet",
                "browser": "Safari 17", "total_ms": total, "outcome": "match",
                "hardware_concurrency": 4, "device_memory": 4,
            })
        scan_metrics.record(self.db, {
            "device": "Laptop · Windows", "device_class": "desktop",
            "browser": "Chrome 128", "total_ms": 40, "outcome": "match",
        })

        data = scan_metrics.summary(self.db, days=7)
        self.assertEqual(data["captures"], 5)
        by_device = {d["device"]: d for d in data["devices"]}
        tablet = by_device["iPad · iOS"]
        self.assertEqual(tablet["rows"], 4)
        self.assertEqual(tablet["outcomes"], {"match": 4})
        self.assertEqual(tablet["cores_p50"], 4.0)
        self.assertEqual(tablet["memory_p50"], 4.0)
        self.assertEqual(tablet["stages"]["total_ms"]["p50"], 250.0)
        self.assertEqual(tablet["stages"]["total_ms"]["p90"], 370.0)
        self.assertEqual(tablet["stages"]["total_ms"]["max"], 400.0)
        self.assertEqual(by_device["Laptop · Windows"]["stages"]["total_ms"]["p50"], 40.0)
        self.assertEqual(data["slowest"][0]["total_ms"], 400.0)

    def test_stages_absent_from_a_capture_report_no_median(self):
        # A local match never calls the server, so `cloud_ms` has no value: the
        # summary must say so instead of averaging it in as zero.
        scan_metrics.record(self.db, {"device": "iPad · iOS", "total_ms": 200,
                                      "detect_ms": 50})
        stats = scan_metrics.summary(self.db)["devices"][0]["stages"]["cloud_ms"]
        self.assertIsNone(stats["p50"])
        self.assertEqual(stats["n"], 0)

    def test_startup_rows_are_kept_apart_from_captures(self):
        # A page load and a capture have no stages in common but total_ms, so
        # averaging them together would describe neither.
        scan_metrics.record(self.db, {
            "kind": "startup", "device": "iPad · iOS", "device_class": "tablet",
            "browser": "Safari 17", "outcome": "ready",
            "boot_ms": 900, "modules_ms": 120, "json_fetch_ms": 1500,
            "json_parse_ms": 620, "index_ms": 380, "model_ms": 2100,
            "total_ms": 5600, "bytes": 12 * 1024 * 1024,
        })
        scan_metrics.record(self.db, {
            "device": "iPad · iOS", "device_class": "tablet",
            "browser": "Safari 17", "total_ms": 300, "detect_ms": 120,
            "outcome": "match",
        })

        data = scan_metrics.summary(self.db)
        self.assertEqual(data["captures"], 1)
        self.assertEqual(data["startups"], 1)
        # The capture table never sees the 5600 ms page load.
        self.assertEqual(data["devices"][0]["stages"]["total_ms"]["p50"], 300.0)
        startup = data["startup_devices"][0]
        self.assertEqual(startup["rows"], 1)
        self.assertEqual(startup["stages"]["json_parse_ms"]["p50"], 620.0)
        self.assertEqual(startup["stages"]["model_ms"]["p50"], 2100.0)
        self.assertEqual(startup["stages"]["total_ms"]["p50"], 5600.0)
        self.assertEqual(startup["bytes_p50"], float(12 * 1024 * 1024))
        self.assertNotIn("detect_ms", startup["stages"])
        self.assertEqual(data["slowest_startups"][0]["total_ms"], 5600.0)

    def test_a_kind_it_does_not_know_is_a_capture(self):
        self.assertEqual(self.row(kind="nonsense", total_ms=10)["kind"], "capture")

    def test_a_database_from_before_startup_rows_is_migrated(self):
        # The table shipped a revision earlier; CREATE TABLE IF NOT EXISTS skips
        # the new columns, so the migration has to add them.
        conn = sqlite3.connect(self.db)
        conn.execute(
            "CREATE TABLE scan_metrics (id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, device TEXT, total_ms REAL)"
        )
        conn.commit()
        conn.close()

        row = self.row(kind="startup", device="iPad · iOS", boot_ms=900, total_ms=5600)
        self.assertEqual(row["kind"], "startup")
        self.assertEqual(row["boot_ms"], 900.0)
        self.assertEqual(row["device"], "iPad · iOS")

    def test_summary_of_an_empty_table_is_empty_not_an_error(self):
        data = scan_metrics.summary(self.db)
        self.assertEqual(data["captures"], 0)
        self.assertEqual(data["devices"], [])
        self.assertEqual(data["startups"], 0)
        self.assertEqual(data["startup_devices"], [])
        self.assertEqual(data["slowest"], [])
        self.assertEqual(data["slowest_startups"], [])

    def test_rows_outside_the_window_are_excluded(self):
        scan_metrics.record(self.db, {"device": "iPad · iOS", "total_ms": 200})
        conn = sqlite3.connect(self.db)
        try:
            conn.execute("UPDATE scan_metrics SET created_at = datetime('now', '-30 days')")
            conn.commit()
        finally:
            conn.close()
        self.assertEqual(scan_metrics.summary(self.db, days=7)["captures"], 0)
        self.assertEqual(scan_metrics.summary(self.db, days=60)["captures"], 1)


if __name__ == "__main__":
    unittest.main()
