#!/usr/bin/env python3
"""Verify the live database, then snapshot it, around git operations.

data/pokemon_cards.db is untracked but deliberately not ignored (see .gitignore),
so git refuses to overwrite it. This sits underneath that guard in two layers.

Detect and undo a replacement. A hook cannot run before git rewrites the working
tree: a merge writes the incoming blob during checkout and only then runs its
hooks, and a merge left in conflict runs none at all. So this does not trust the
order. The live file is compared with the blobs git holds for that path
(MERGE_HEAD, CHERRY_PICK_HEAD, REVERT_HEAD, HEAD): if it *is* one of those blobs
and differs from what was last recorded, it was replaced and the newest snapshot
is copied back. That check runs before the snapshot step, so a clobbered file is
restored instead of being archived as the newest good copy.

Compare identities, not content. The database is ~17 MB on a network share, so a
hook must not read it twice per commit. live-identity.json in the backups
directory records the size, mtime, sha256 and git blob id of the live file as of
the newest snapshot:

  - size and mtime match  -> nothing is read at all;
  - sha256 matches        -> only metadata moved, nothing is stored;
  - git refs are asked for object ids (no blob content), so deciding whether the
    file was replaced costs one read of the file and no read of the snapshot;
  - content is compared only for text files, where checkout converts line
    endings and the ids cannot match, and once on an install with no recorded
    identity (where it avoids storing a duplicate).

Keep timestamped copies. data/backups/pokemon_cards.db.<UTC yyyymmdd-hhmmss>,
pruned to the newest --keep. The copy goes through SQLite's backup API so rows a
running app still has in its WAL land in it.

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
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

PREFIX = "pokemon_cards.db."
STATE = "live-identity.json"
REFS = ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "HEAD")


def identity(data):
    """Comparable identities for one block of bytes: content hash and git object id."""
    return {"sha256": hashlib.sha256(data).hexdigest(),
            "blob": hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest(),
            "size": len(data)}


def same_content(blob_bytes, file_bytes):
    """Equal, allowing for the line-ending conversion checkout applies to text."""
    if blob_bytes == file_bytes:
        return True
    return blob_bytes.replace(b"\r\n", b"\n") == file_bytes.replace(b"\r\n", b"\n")


def snapshots(out):
    """Existing copies, oldest first, by mtime.

    Not by name: a copy made by hand may carry a local-time name that sorts above
    the UTC ones and would be mistaken for the newest.
    """
    return sorted(out.glob(PREFIX + "*"), key=lambda p: p.stat().st_mtime)


def git(*args):
    done = subprocess.run(["git", *args], capture_output=True, text=True)
    return done.stdout.strip() if done.returncode == 0 else None


def git_bytes(*args):
    done = subprocess.run(["git", *args], capture_output=True)
    return done.stdout if done.returncode == 0 else None


def read_state(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def record(path, current, mtime_ns):
    try:
        path.write_text(json.dumps(dict(current, mtime_ns=mtime_ns)), encoding="utf-8")
    except OSError as exc:
        print(f"! cannot record {path.name}: {exc}", file=sys.stderr)


def restore_if_replaced(db, newest, live, current):
    """Copy the newest snapshot back when the live file is a blob git wrote there."""
    # Text is the only case where ids disagree with the file checkout produced.
    text = b"\0" not in live[:8192]
    toplevel = git("-C", str(db.parent), "rev-parse", "--show-toplevel")
    if not toplevel:
        return None
    repo = Path(toplevel)
    try:
        relative = db.resolve().relative_to(repo.resolve()).as_posix()
    except ValueError:
        return None
    for ref in REFS:
        incoming = git("-C", str(repo), "rev-parse", f"{ref}:{relative}")
        if incoming is None:
            continue
        matched = incoming == current["blob"]
        if not matched and text:
            content = git_bytes("-C", str(repo), "cat-file", "-p", f"{ref}:{relative}")
            matched = content is not None and same_content(content, live)
        if not matched:
            continue
        try:
            restored = newest.read_bytes()
            db.write_bytes(restored)
        except OSError as exc:
            print(f"! {db} is the {ref} blob but could not be restored: {exc}", file=sys.stderr)
            return None
        print(f"! {db.name} had been replaced by the {ref} blob; restored {newest.name}",
              file=sys.stderr)
        return restored
    return None


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
    try:
        out.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(f"! cannot use {out}: {exc}", file=sys.stderr)
        return 0

    state_path = out / STATE
    state = read_state(state_path)
    copies = snapshots(out)
    newest = copies[-1] if copies else None
    try:
        stat = db.stat()
        if (newest is not None and state and state.get("size") == stat.st_size
                and state.get("mtime_ns") == stat.st_mtime_ns):
            return 0  # untouched since the last snapshot: neither file is read
        live = db.read_bytes()
    except OSError as exc:
        print(f"! live database backup failed: {exc}", file=sys.stderr)
        return 0
    current = identity(live)

    if newest is not None and state and state.get("sha256") == current["sha256"]:
        record(state_path, current, stat.st_mtime_ns)  # metadata moved, bytes did not
        return 0

    if newest is not None:
        restored = restore_if_replaced(db, newest, live, current)
        if restored is not None:
            record(state_path, identity(restored), db.stat().st_mtime_ns)
            return 0

    temp = out / f".snapshot-{os.getpid()}"
    try:
        try:
            snapshot(db, temp)
        except (sqlite3.Error, OSError) as exc:
            print(f"! sqlite copy failed ({exc}); copying the file as-is", file=sys.stderr)
            temp.write_bytes(live)
        if newest is not None and state is None and temp.read_bytes() == newest.read_bytes():
            # First run on an install that predates live-identity.json: same
            # database, so record it instead of storing a duplicate (~34 MB read,
            # once).
            record(state_path, current, stat.st_mtime_ns)
            return 0
        # Second resolution is not enough on its own: a snapshot and the one a
        # hook takes moments later would collide and one would be lost.
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        target = out / (PREFIX + stamp)
        suffix = 1
        while target.exists():
            suffix += 1
            target = out / f"{PREFIX}{stamp}-{suffix}"
        temp.replace(target)
        for stale in copies[:max(0, len(copies) + 1 - args.keep)]:
            stale.unlink()
        record(state_path, current, stat.st_mtime_ns)
        print(f"live database backed up: {target} ({target.stat().st_size // 1024} KiB)")
        return 0
    except OSError as exc:
        # A hook must never fail the git operation that ran it, and a locked
        # database on Windows raises here rather than inside sqlite.
        print(f"! live database backup failed: {exc}", file=sys.stderr)
        return 0
    finally:
        temp.unlink(missing_ok=True)


if __name__ == "__main__":
    sys.exit(main())
