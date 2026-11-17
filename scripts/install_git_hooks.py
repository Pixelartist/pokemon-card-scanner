#!/usr/bin/env python3
"""Install the git hooks that snapshot the live database.

Hooks live in .git/hooks and are not versioned, so every clone has to run this
once:  python scripts/install_git_hooks.py

Each hook runs scripts/backup_live_db.py, which is a no-op unless the database
changed since the newest copy. A hook that already exists (Git LFS installs
several) gets the block appended instead of being replaced, and a hook that
already carries the marker is left as it is.
"""
import subprocess
import sys
from pathlib import Path

MARKER = "# live-db-backup"
HOOKS = ("pre-commit", "pre-merge-commit", "pre-rebase", "post-checkout", "post-merge")
BLOCK = f"""{MARKER}: snapshot data/pokemon_cards.db before git changes the working tree.
# Added by scripts/install_git_hooks.py; delete this block to remove it.
root=$(git rev-parse --show-toplevel) || exit 0
python "$root/scripts/backup_live_db.py" || echo "warning: live database backup failed" >&2
exit 0
"""


def hooks_dir():
    done = subprocess.run(["git", "rev-parse", "--git-path", "hooks"],
                          capture_output=True, text=True)
    return Path(done.stdout.strip() or ".git/hooks")


def main():
    if not Path("scripts/backup_live_db.py").exists():
        print("run this from the project root", file=sys.stderr)
        return 1
    target = hooks_dir()
    target.mkdir(parents=True, exist_ok=True)
    written = appended = updated = 0
    for name in HOOKS:
        path = target / name
        if not path.exists():
            path.write_bytes(("#!/bin/sh\n" + BLOCK).encode("utf-8"))
            written += 1
        else:
            # Our block is always last, so cutting at the marker puts the block
            # back without touching a hook's own code (Git LFS installs several).
            text = path.read_text(encoding="utf-8", errors="replace")
            index = text.find(MARKER)
            if index < 0:
                path.write_bytes((text.rstrip("\n") + "\n" + BLOCK).encode("utf-8"))
                appended += 1
            else:
                head = text[:index].rstrip("\n")
                path.write_bytes((head + "\n" + BLOCK if head.strip() else "#!/bin/sh\n" + BLOCK)
                                 .encode("utf-8"))
                updated += 1
        path.chmod(0o755)  # LF is written above, or sh on Windows refuses to run it
    print(f"hooks in {target}: {written} new, {appended} appended to, {updated} refreshed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
