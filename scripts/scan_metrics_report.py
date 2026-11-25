#!/usr/bin/env python3
"""Report the timings the browser scanner posts to /api/scan-metrics.

Two tables, because they answer two different questions:

* capturing — one row per scan: grab, detect, crop, hash, server gate, cloud.
* startup   — one row per page load: modules, JSON transfer, JSON parse, index
  build, detector model load, and the total time to scanner-ready.

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
    ("json_fetch_ms", "json"),
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


def table(devices, columns, with_bytes=False):
    header = f"{'device':<34}{'browser':<14}{'n':>5}"
    if with_bytes:
        header += f"{'load':>9}"
    header += "".join(f"{label:>8}" for _, label in columns)
    print(header)
    print("-" * len(header))
    for device in devices:
        label = f"{device['device_class']} · {device['device']}"
        row = f"{label[:33]:<34}{device['browser'][:13]:<14}{device['rows']:>5}"
        if with_bytes:
            row += f"{mb(device['bytes_p50']):>9}"
        for stage, _ in columns:
            row += f"{ms(device['stages'][stage]['p50']):>8}"
        print(row)


def breakdown(devices, columns):
    for device in devices:
        label = f"{device['device_class']} · {device['device']} · {device['browser']}"
        hw = hardware(device)
        parts = []
        for stage, name in columns:
            stats = device["stages"][stage]
            if stats["p50"] is None:
                continue
            parts.append(f"{name} {ms(stats['p50'])} [{ms(stats['p90'])}]")
        outcomes = ", ".join(f"{key} {count}" for key, count in device["outcomes"].items())
        print(f"  {label}" + (f"  ({hw})" if hw else ""))
        print(f"    {'  '.join(parts)}")
        print(f"    outcomes: {outcomes}")


def main():
    parser = argparse.ArgumentParser(description="Report browser capture and startup timings.")
    parser.add_argument("--days", type=int, default=7, help="window in days (default: 7)")
    parser.add_argument("--limit", type=int, default=20000, help="rows to read (default: 20000)")
    parser.add_argument("--db", default=os.environ.get("DATA_DIR", str(ROOT / "data")))
    parser.add_argument("--json", action="store_true", help="print the raw summary as JSON")
    args = parser.parse_args()

    db_path = Path(args.db)
    if db_path.is_dir():
        db_path = db_path / "pokemon_cards.db"
    if not db_path.exists():
        print(f"No database at {db_path}.")
        return 1

    data = scan_metrics.summary(str(db_path), days=args.days, limit=args.limit)
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
        table(data["devices"], CAPTURE_COLUMNS)
        print("\nCapturing - median ms per stage, p90 in brackets")
        breakdown(data["devices"], CAPTURE_COLUMNS)

        slowest = [row for row in data["slowest"] if row["total_ms"]]
        if slowest:
            print("\nSlowest captures:")
            for row in slowest[:5]:
                print(f"  {row['created_at']}  {row['total_ms']:>7.0f} ms  "
                      f"{row['device_class']}/{row['device']} {row['browser']}  "
                      f"{row['video'] or '-'}, {row['frames'] or '?'} frames  "
                      f"{row['outcome']}  "
                      f"(detect {ms(row['detect_ms'])}, crop {ms(row['crop_ms'])}, "
                      f"hash {ms(row['hash_ms'])}, cloud {ms(row['cloud_ms'])})")
    else:
        print("No captures in the window.\n")

    if data["startup_devices"]:
        print("\nLoading the scanner - median ms to ready")
        table(data["startup_devices"], STARTUP_COLUMNS, with_bytes=True)
        print()
        breakdown(data["startup_devices"], STARTUP_COLUMNS)

        slowest = [row for row in data["slowest_startups"] if row["total_ms"]]
        if slowest:
            print("\nSlowest page loads:")
            for row in slowest[:5]:
                print(f"  {row['created_at']}  {row['total_ms']:>7.0f} ms to ready  "
                      f"{row['device_class']}/{row['device']} {row['browser']}  "
                      f"(modules {ms(row['modules_ms'])}, json {ms(row['json_fetch_ms'])}, "
                      f"parse {ms(row['json_parse_ms'])}, index {ms(row['index_ms'])}, "
                      f"model {ms(row['model_ms'])})")
    else:
        print("No page loads in the window.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
