#!/usr/bin/env python3
"""Report the timings the browser scanner posts to /api/scan-metrics.

Two sections, because they answer two different questions:

* capturing — one row per scan: grab, detect, crop, hash, server gate, cloud.
* startup   — one row per page load: modules, index transfer (JSON metadata
  and the binary hash index), JSON parse, index build, detector model load, and
  the total time to scanner-ready.

Both are grouped per device, because that is the comparison that matters: the
same pipeline takes very different times on a tablet with single-threaded WASM
than on a laptop. Reading them side by side says whether a device is slow
loading the scanner or slow using it.

    python scripts/scan_metrics_report.py                # last 7 days
    python scripts/scan_metrics_report.py --days 30
    python scripts/scan_metrics_report.py --json
"""
import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from services import scan_metrics  # noqa: E402

# Printed in the order a capture spends its time.
CAPTURE_COLUMNS = (
    ("grab_ms", "grab"),
    ("detect_ms", "detect"),
    ("crop_ms", "crop"),
    ("hash_ms", "hash"),
    ("gate_ms", "gate"),
    ("cloud_ms", "cloud"),
    ("total_ms", "total"),
)

# And in the order a page load spends it.
STARTUP_COLUMNS = (
    ("boot_ms", "boot"),
    ("modules_ms", "mods"),
    ("index_fetch_ms", "fetch"),
    ("json_parse_ms", "parse"),
    ("index_ms", "index"),
    ("model_ms", "model"),
    ("total_ms", "ready"),
)


def ms(value):
    return "-" if value is None else f"{value:.0f}"


def mb(value):
    return "-" if not value else f"{value / (1024 * 1024):.1f}M"


def hardware(device):
    """Cores and memory, when the browser reported them."""
    parts = []
    if device["cores_p50"]:
        parts.append(f"{device['cores_p50']:.0f} cores")
    if device["memory_p50"]:
        parts.append(f"{device['memory_p50']:g} GB")
    return " · ".join(parts)


def report(devices, columns, with_bytes=False):
    """One block per device: who it is, then median [p90] per stage.

    A block per device rather than one wide table, because the question is
    always "which device, and in which stage": this keeps the name, the
    hardware, the stages and the outcomes together. The medians used to be
    printed twice, once as a table and once as a per-device line.
    """
    for device in devices:
        label = f"{device['device_class']} · {device['device']}"
        line = f"{label[:32]:<34}{device['browser'][:13]:<14}{device['rows']:>4} rows"
        hw = hardware(device)
        if hw:
            line += f"   {hw}"
        if with_bytes and device["bytes_p50"]:
            line += f"   {mb(device['bytes_p50'])}"
        print(line)
        parts = []
        for stage, name in columns:
            stats = device["stages"][stage]
            if stats["p50"] is not None:
                parts.append(f"{name} {ms(stats['p50'])} [{ms(stats['p90'])}]")
        print("  " + "  ".join(parts))
        outcomes = ", ".join(f"{key} {count}" for key, count in device["outcomes"].items())
        print(f"  outcomes: {outcomes}")


def main():
    parser = argparse.ArgumentParser(description="Report browser capture and startup timings.")
    parser.add_argument("--days", type=int, default=7, help="window in days (default: 7)")
    parser.add_argument("--db", default=os.environ.get("DATA_DIR", str(ROOT / "data")))
    parser.add_argument("--json", action="store_true", help="print the raw summary as JSON")
    args = parser.parse_args()

    db_path = Path(args.db)
    if db_path.is_dir():
        db_path = db_path / "pokemon_cards.db"
    if not db_path.exists():
        print(f"No database at {db_path}.")
        return 1

    data = scan_metrics.summary(str(db_path), days=args.days)
    if args.json:
        print(json.dumps(data, indent=2))
        return 0

    print(f"Last {data['window_days']} day(s): {data['captures']} capture(s), "
          f"{data['startups']} page load(s)\n")
    if not data["captures"] and not data["startups"]:
        print("Nothing recorded yet. Open the scanner on a device: every page load posts its")
        print("time to ready, and every capture posts its stage timings.")
        return 0

    if data["captures"]:
        print("Capturing - median ms per stage, p90 in brackets")
        report(data["devices"], CAPTURE_COLUMNS)

        slowest = [row for row in data["slowest"] if row["total_ms"]]
        if slowest:
            print("\nSlowest captures:")
            for row in slowest:
                print(f"  {row['created_at']}  {row['total_ms']:>7.0f} ms  "
                      f"{row['device_class']}/{row['device']} {row['browser']}  "
                      f"{row['video'] or '-'}, {row['frames'] or '?'} frames  "
                      f"{row['outcome']}  "
                      f"(detect {ms(row['detect_ms'])}, crop {ms(row['crop_ms'])}, "
                      f"hash {ms(row['hash_ms'])}, cloud {ms(row['cloud_ms'])})")
    else:
        print("No captures in the window.\n")

    if data["startup_devices"]:
        print("\nLoading the scanner - median ms per stage, p90 in brackets")
        report(data["startup_devices"], STARTUP_COLUMNS, with_bytes=True)

        slowest = [row for row in data["slowest_startups"] if row["total_ms"]]
        if slowest:
            print("\nSlowest page loads:")
            for row in slowest:
                print(f"  {row['created_at']}  {row['total_ms']:>7.0f} ms to ready  "
                      f"{row['device_class']}/{row['device']} {row['browser']}  "
                      f"(modules {ms(row['modules_ms'])}, fetch {ms(row['index_fetch_ms'])}, "
                      f"parse {ms(row['json_parse_ms'])}, index {ms(row['index_ms'])}, "
                      f"model {ms(row['model_ms'])})")
    else:
        print("No page loads in the window.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
