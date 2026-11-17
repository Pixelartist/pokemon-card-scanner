#!/usr/bin/env python3
"""Repair the scan labels stored beside the frames in data/scans.

A card filed from a scan writes <frame>.json next to the image (see
_write_scan_label in src/api/main.py). A label can hold the numeric database id
of a card instead of its ptcg_id - a correction opened from the card detail
modal posts cards.id - and every label has to name cards the same way before the
corpus can be compared against the card index. Those ids are rewritten here, and
labels that never recorded a runner-up are listed.

Missing runner-up fields are reported, never invented: the candidate list behind
an old capture is not stored anywhere, so a similarity without an id (or
neither) is all the evidence there is, and writing nulls over the absence would
erase the difference between a gate that saw one candidate and a label written
before the field existed.
"""
import argparse
import json
import sqlite3
import sys
from pathlib import Path

ID_FIELDS = ("filed_as", "proposed_id", "proposed_second_id")
RUNNER_UP_FIELDS = ("proposed_second_id", "proposed_second_similarity")


def resolve(ids, db_path):
    """Map database ids to ptcg_ids; ids the database does not know are absent."""
    if not ids:
        return {}
    if not db_path.exists():
        print(f"! {db_path} not found", file=sys.stderr)
        return {}
    try:
        conn = sqlite3.connect(db_path)
        try:
            # query_only rather than a file: URI: the project can live on a UNC
            # share, and the app is serving scans from this database right now.
            conn.execute("PRAGMA query_only = ON")
            marks = ",".join("?" * len(ids))
            rows = conn.execute(f"SELECT id, ptcg_id FROM cards WHERE id IN ({marks})",
                                sorted(ids))
            return {card_id: ptcg_id for card_id, ptcg_id in rows if ptcg_id}
        finally:
            conn.close()
    except sqlite3.Error as exc:
        print(f"! cannot read cards from {db_path}: {exc}", file=sys.stderr)
        return {}


def main():
    root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description="Resolve numeric card ids in the stored scan labels.")
    parser.add_argument("--dir", default=str(root / "data" / "scans"),
                        help="directory holding the label files (default: data/scans)")
    parser.add_argument("--db", default=str(root / "data" / "pokemon_cards.db"),
                        help="card database used to resolve ids (default: data/pokemon_cards.db)")
    parser.add_argument("--dry-run", action="store_true",
                        help="report the changes without writing the labels")
    args = parser.parse_args()

    files = sorted(Path(args.dir).glob("*.json"))
    if not files:
        print(f"No scan labels in {args.dir}.")
        print("One is written each time a card is filed from a scan; file a few and run this again.")
        return 1

    labels, numeric, unreadable = {}, [], 0
    for path in files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"  ! skipped {path.name}: {exc}", file=sys.stderr)
            unreadable += 1
            continue
        if not isinstance(data, dict):
            print(f"  ! skipped {path.name}: not a JSON object", file=sys.stderr)
            unreadable += 1
            continue
        labels[path] = data
        for field in ID_FIELDS:
            posted = data.get(field)
            if posted and str(posted).isdigit():
                numeric.append((path, field, posted, int(posted)))

    known = resolve({card_id for *_, card_id in numeric}, Path(args.db))
    resolved, unresolved = [], []
    for path, field, posted, card_id in numeric:
        if card_id in known:
            labels[path][field] = known[card_id]
            resolved.append((path, field, posted, known[card_id]))
        else:
            unresolved.append((path, field, posted))

    changed = list(dict.fromkeys(path for path, *_ in resolved))
    if changed and not args.dry_run:
        for path in changed:
            path.write_text(json.dumps(labels[path], ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Scan labels in {args.dir} - {len(files)} files"
          + (f", {unreadable} unreadable" if unreadable else "") + "\n")

    if resolved:
        print(f"{'Would resolve' if args.dry_run else 'Resolved'} "
              f"{len(resolved)} numeric id(s) to ptcg_id:")
        for path, field, posted, ptcg_id in resolved:
            print(f"  {path.name:<44} {field:<20} {posted} -> {ptcg_id}")
    else:
        print("No numeric ids left to resolve.")

    if unresolved:
        print(f"\n{len(unresolved)} numeric id(s) left as posted:")
        for path, field, posted in unresolved:
            print(f"  {path.name:<44} {field:<20} {posted}")

    no_runner_up = [(path, data) for path, data in labels.items()
                    if any(field not in data for field in RUNNER_UP_FIELDS)]
    if no_runner_up:
        print(f"\n{len(no_runner_up)} label(s) with no runner-up recorded - their lead is unknown:")
        for path, data in no_runner_up:
            missing = ", ".join(field for field in RUNNER_UP_FIELDS if field not in data)
            print(f"  {path.name:<44} {data.get('match_source') or '?':<10} "
                  f"{data.get('proposed_id') or '?':<14} -> {data.get('filed_as') or '?':<14} "
                  f"missing {missing}")

    print(f"\n{'Would update' if args.dry_run else 'Updated'} {len(changed)} label(s), "
          f"{len(labels) - len(changed)} unchanged.")
    if args.dry_run and changed:
        print("Rerun without --dry-run to write them.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
