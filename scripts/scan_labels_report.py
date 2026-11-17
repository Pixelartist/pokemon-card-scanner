#!/usr/bin/env python3
"""Summarise the scan labels stored beside the frames in data/scans.

A card filed from a scan writes <frame>.json next to the image (see
_write_scan_label in src/api/main.py), recording what the match gate proposed
and what was actually filed. Reading them back shows where the gate loses real
captures: a "corrected" label whose proposal scored at or above the floor means
the gate confidently accepted the wrong card, while one below the floor means it
proposed nothing and the user had to search.

The labels carry the proposal's top similarity and the nearest card that is not
the same one, so a corrected row can say which threshold cost the capture: too
low for the floor, or leading the runner-up by less than the margin.
"""
import argparse
import json
import sys
from pathlib import Path

SOURCES = [
    ("accepted", "filed as proposed"),
    ("corrected", "gate's card was wrong, user fixed it"),
    ("picked", "user chose one of the scan's other candidates"),
    ("manual", "not from a scan, no gate proposal"),
]


def load(files):
    for path in files:
        try:
            yield path, json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"  ! skipped {path.name}: {exc}", file=sys.stderr)


def sim(value):
    return "  n/a" if value is None else f"{value:5.1f}%"


def base_id(card_id):
    """de_sv07-077 and sv07-077 are one card in two prints, not a correction."""
    text = str(card_id or "")
    return text[3:] if text.startswith("de_") else text


def main():
    root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description="Report the scan labels written beside stored frames.")
    parser.add_argument("--dir", default=str(root / "data" / "scans"),
                        help="directory holding the label files (default: data/scans)")
    parser.add_argument("--floor", type=float, default=78.0,
                        help="similarity floor the gate uses (default: 78)")
    parser.add_argument("--margin", type=float, default=2.0,
                        help="lead margin the gate uses (default: 2)")
    args = parser.parse_args()

    files = sorted(Path(args.dir).glob("*.json"))
    if not files:
        print(f"No scan labels in {args.dir}.")
        print("One is written each time a card is filed from a scan; label a few and run this again.")
        return 1

    labels = [data for _, data in load(files)]
    counts = {}
    for data in labels:
        key = data.get("match_source") or "unknown"
        counts[key] = counts.get(key, 0) + 1

    print(f"Scan labels in {args.dir} - {len(files)} files, floor {args.floor:g}, margin {args.margin:g}\n")
    for key, meaning in SOURCES:
        print(f"  {key:<10} {counts.pop(key, 0):>4}   {meaning}")
    for key, n in counts.items():
        print(f"  {key:<10} {n:>4}   (unrecognised match_source)")

    corrected = [d for d in labels if d.get("match_source") == "corrected"]
    if corrected:
        print("\nCorrected - what the gate proposed, and what was filed:")
        lost_floor = lost_lead = rejected_other = wrong_card = same_card = no_proposal = 0
        for data in corrected:
            proposed = data.get("proposed_similarity")
            filed = data.get("filed_as")
            proposed_id = data.get("proposed_id") or "?"
            second = data.get("proposed_second_similarity")
            second_id = data.get("proposed_second_id")
            lead = None if proposed is None or second is None else round(proposed - second, 1)
            runner_is_filed = second_id is not None and base_id(second_id) == base_id(filed)
            if proposed is None:
                no_proposal += 1
                verdict = "no proposal recorded"
            elif base_id(proposed_id) == base_id(filed):
                same_card += 1
                verdict = "same card, other language print"
            elif proposed < args.floor:
                lost_floor += 1
                verdict = f"lost to the floor ({proposed:g} < {args.floor:g})"
            elif lead is not None and lead < args.margin and runner_is_filed:
                lost_lead += 1
                verdict = (f"lost to the lead ({lead:g} < {args.margin:g}): "
                           f"runner-up {second_id} is the filed card")
            elif lead is not None and lead < args.margin:
                rejected_other += 1
                verdict = (f"rejected on a lead of {lead:g}, but the runner-up "
                           f"{second_id or 'unknown'} is not the filed card")
            elif lead is None:
                wrong_card += 1
                verdict = "wrong card accepted (no runner-up recorded)"
            else:
                wrong_card += 1
                verdict = f"wrong card accepted (cleared floor and lead, lead {lead:g})"
            print(f"  {sim(proposed)}  {proposed_id:<14} -> {filed or '?':<14} {verdict}")
        print(f"\n  of {len(corrected)} corrected: {lost_floor} lost to the floor, "
              f"{lost_lead} lost to the lead, {rejected_other} rejected on another card's lead, "
              f"{wrong_card} wrong card accepted, {same_card} same card in another language"
              + (f", {no_proposal} without a proposal" if no_proposal else ""))
    else:
        print("\nNo corrected labels yet - nothing to compare against the floor.")

    missing_second = sum(1 for d in labels if "proposed_second_similarity" not in d)
    if missing_second:
        print(f"\n{missing_second} label(s) predate the runner-up score, so their lead is unknown.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
