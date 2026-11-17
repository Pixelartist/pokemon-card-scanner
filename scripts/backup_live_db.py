#!/usr/bin/env python3
"""Verify the live database, then snapshot it, around git operations.

data/pokemon_cards.db is untracked but deliberately not ignored (see .gitignore),
so git refuses to overwrite it. This sits underneath that guard in two layers.

Detect and undo a replacement. A hook cannot run before git rewrites the working
tree: a merge writes the incoming blob during checkout and only then runs its
hooks, and a merge left in conflict runs none at all. So this does not trust the
order. It compares the live file against the blobs git holds for that path
(MERGE_HEAD, CHERRY_PICK_HEAD, REVERT_HEAD, HEAD): if the file *is* one of those
blobs and differs from the newest snapshot, it was replaced, and the newest
snapshot is copied back. That check runs before the snapshot step, so a clobbered
file is restored instead of being archived as the newest good copy.

Keep timestamped copies. data/backups/pokemon_cards.db.<UTC yyyymmdd-hhmmss>,
pruned to the newest --keep, skipped when the database is unchanged. The copy
goes through SQLite's backup API so rows a running app still has in its WAL land
in it.

Which hook covers which case:

    hook                fires                           protects
    ------------------  ------------------------------  --------------------------------
    pre-commit          any commit, including the one    a merge that clobbered the file
                        that concludes a conflicted      and left a conflict: no other
                        merge                            hook runs for it, so this is
                                                         what recovers that case
    pre-merge-commit    merge succeeded, before its      verify + snapshot before the
                        commit is written                merge result is recorded
    post-checkout       checkout or branch switch       a blob arriving via checkout
    post-merge          successful merge                the tree after a merge moved
    pre-rebase          rebase starts                    a copy before the series rewrites

A fast-forward merge runs post-checkout, not post-merge, which is why both are
installed. Restore by hand, with the app stopped:
    cp data/backups/pokemon_cards.db.20261117-130515 data/pokemon_cards.db
"""
import argparse
import hashlib
import os
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

PREFIX = "pokemon_cards.db."
REFS = ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "HEAD")


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def same_content(blob_bytes, file_bytes):
    """Equal, allowing for the line-ending conversion checkout applies to text.

    A binary file like the database comes through untouched, but a small text
    fixture (or a converted path) would otherwise never match its own blob.
    """
    if blob_bytes == file_bytes:
        return True
    return blob_bytes.replace(b"\r\n", b"\n") == file_bytes.replace(b"\r\n", b"\n")


def newest_snapshot(out):
    # By mtime, not name: a copy made by hand may carry a local-time name that
    # sorts above the UTC ones and would be mistaken for the newest.
    copies = sorted(out.glob(PREFIX + "*"), key=lambda p: p.stat().st_mtime)
    return copies[-1] if copies else None


def git(*args):
    done = subprocess.run(["git", *args], capture_output=True, text=True)
    return done.stdout.strip() if done.returncode == 0 else None


def git_bytes(*args):
    done = subprocess.run(["git", *args], capture_output=True)
    return done.stdout if done.returncode == 0 else None


def restore_if_replaced(db, out):
    """Put the newest snapshot back when git has written one of its own blobs over it."""
    copy = newest_snapshot(out)
    if copy is None:
        return False
    live = db.read_bytes()
    if same_content(copy.read_bytes(), live):
        return False  # already the newest snapshot, nothing was replaced
    toplevel = git("-C", str(db.parent), "rev-parse", "--show-toplevel")
    if not toplevel:
        return False
    repo = Path(toplevel)
    try:
        relative = db.resolve().relative_to(repo.resolve()).as_posix()
    except ValueError:
        return False
    for ref in REFS:
        incoming = git_bytes("-C", str(repo), "cat-file", "-p", f"{ref}:{relative}")
        if incoming is not None and same_content(incoming, live):
            shutil.copy2(copy, db)
            print(f"! {db.name} had been replaced by the {ref} blob; restored {copy.name}",
                  file=sys.stderr)
            return True
    return False


def snapshot(db, target):
    """Consistent copy through SQLite, so rows still in a running app's WAL land in it."""
    source = sqlite3.connect(db)
    try:
        copy = sqlite3.connect(target)
        try:
            source.backup(copy)
        finally:
            copy.close()
    finally:
        source.close()


def main():
    root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description="Verify and snapshot the live card database.")
    parser.add_argument("--db", default=str(root / "data" / "pokemon_cards.db"),
                        help="database to protect (default: data/pokemon_cards.db)")
    parser.add_argument("--dir", default=str(root / "data" / "backups"),
                        help="where the copies go (default: data/backups)")
    parser.add_argument("--keep", type=int, default=10,
                        help="copies to keep, newest first (default: 10)")
    args = parser.parse_args()

    db, out = Path(args.db), Path(args.dir)
    if not db.exists():
        return 0  # a fresh clone has no live database to protect
    out.mkdir(parents=True, exist_ok=True)

    # Before the copy below: a replacement must be undone, never archived.
    restore_if_replaced(db, out)

    existing = sorted(out.glob(PREFIX + "*"), key=lambda p: p.stat().st_mtime)
    temp = out / f".snapshot-{os.getpid()}"
    try:
        snapshot(db, temp)
    except sqlite3.Error as exc:
        print(f"! sqlite copy failed ({exc}); copying the file as-is", file=sys.stderr)
        temp.write_bytes(db.read_bytes())

    if existing and sha256(existing[-1]) == sha256(temp):
        temp.unlink()
        return 0  # unchanged since the last copy, nothing to add
    target = out / (PREFIX + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S"))
    temp.replace(target)
    for stale in existing[:max(0, len(existing) + 1 - args.keep)]:
        stale.unlink()
    print(f"live database backed up: {target} ({target.stat().st_size // 1024} KiB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
