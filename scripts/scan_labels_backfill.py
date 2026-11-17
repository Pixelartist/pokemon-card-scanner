#!/usr/bin/env python3
"""Repair the scan labels stored beside the frames in data/scans.

A card filed from a scan writes <frame>.json next to the image (see
_write_scan_label in src/api/main.py). Older labels can hold the numeric
database id of a card instead of its ptcg_id: a correction opened from the card
detail modal posts cards.id, and the writer did not always resolve it. Every
label has to name cards the same way before the corpus can be compared against
the card index, so this rewrites those ids and says which labels predate the
runner-up fields.

Only id values are rewritten. Missing runner-up fields are reported, never
invented: the candidate list behind an old capture is not stored anywhere, so a
similarity without an id (or neither) is all the evidence there is, and writing
nulls over the absence would erase the difference between a gate that saw one
candidate and a label written before the field existed.
"""
import argparse
import json
import sqlite3
import sys
from pathlib import Path

ID_FIELDS = ("filed_as", "proposed_id", "proposed_second_id")
RUNNER_UP_FIELDS = ("proposed_second_id", "proposed_second_similarity")


def load_cards(db_path):
    """Map of cards.id -> cards.ptcg_id, or None when the database is unreadable."""
    if not db_path.exists():
        print(f"! {db_path} not found - numeric ids cannot be resolved", file=sys.stderr)
        return None
    try:
        conn = sqlite3.connect(db_path)
        try:
            # The app serves scans from this file while it runs, and the project
            # can live on a UNC share where a file: URI is rejected, so read-only
            # is enforced by the pragma rather than by the connection string.
            conn.execute("PRAGMA query_only = ON")
            return {row[0]: row[1] for row in conn.execute("SELECT id, ptcg_id FROM cards")}
        finally:
            conn.close()
    except sqlite3.Error as exc:
        print(f"! cannot read cards from {db_path}: {exc}", file=sys.stderr)
        return None


def main():
    root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description="Resolve numeric card ids in the stored scan labels.")
    parser.add_argument("--dir", default=str(root / "data" / "scans"),
                        help="directory holding the label files (default: data/scans)")
    parser.add_argument("--db", default=str(root / "data" / "pokemon_cards.db"),
                        help="card database used to resolve ids (default: data/pokemon_cards.db)")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the changes without writing the labels")
    args = parser.parse_args()

    files = sorted(Path(args.dir).glob("*.json"))
    if not files:
        print(f"No scan labels in {args.dir}.")
        print("One is written each time a card is filed from a scan; file a few and run this again.")
        return 1

    cards = load_cards(Path(args.db))
    labels = {}
    resolved, unresolved, no_runner_up, unreadable = [], [], [], []
    for path in files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            unreadable.append(path.name)
            print(f"  ! skipped {path.name}: {exc}", file=sys.stderr)
            continue
        if not isinstance(data, dict):
            unreadable.append(path.name)
            print(f"  ! skipped {path.name}: not a JSON object", file=sys.stderr)
            continue
        labels[path] = data

        for field in ID_FIELDS:
            posted = data.get(field)
            if not posted or not str(posted).isdigit():
                continue
            ptcg_id = (cards or {}).get(int(posted))
            if ptcg_id:
                data[field] = ptcg_id
                resolved.append((path, field, posted, ptcg_id))
            else:
                unresolved.append((path, field, posted))

        missing = [field for field in RUNNER_UP_FIELDS if field not in data]
        if missing:
            no_runner_up.append((path, data, missing))

    changed = list(dict.fromkeys(path for path, *_ in resolved))
    if changed and not args.dry_run:
        for path in changed:
            path.write_text(json.dumps(labels[path], ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Scan labels in {args.dir} - {len(files)} files"
          + (f", {len(unreadable)} unreadable" if unreadable else "") + "\n")

    if resolved:
        verb = "Would resolve" if args.dry_run else "Resolved"
        print(f"{verb} {len(resolved)} numeric id(s) to ptcg_id:")
        for path, field, posted, ptcg_id in resolved:
            print(f"  {path.name:<44} {field:<20} {posted} -> {ptcg_id}")
    else:
        print("No numeric ids left to resolve.")

    if unresolved:
        why = "no card database to resolve them" if cards is None else "no matching card row"
        print(f"\n{len(unresolved)} numeric id(s) left as posted ({why}):")
        for path, field, posted in unresolved:
            print(f"  {path.name:<44} {field:<20} {posted}")

    if no_runner_up:
        print(f"\n{len(no_runner_up)} label(s) with no runner-up recorded - their lead is unknown:")
        for path, data, missing in no_runner_up:
            print(f"  {path.name:<44} {data.get('match_source') or '?':<10} "
                  f"{data.get('proposed_id') or '?':<14} -> {data.get('filed_as') or '?':<14} "
                  f"missing {', '.join(missing)}")

    updated = 0 if args.dry_run else len(changed)
    tail = f"{updated} label(s) updated, {len(labels) - updated} unchanged"
    if unreadable:
        tail += f", {len(unreadable)} unreadable"
    print(f"\n{tail}.")
    if args.dry_run and changed:
        print("Rerun without --dry-run to write them.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
