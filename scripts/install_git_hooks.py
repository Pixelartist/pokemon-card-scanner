#!/usr/bin/env python3
"""Install the git hooks that snapshot the live database.

Hooks are not versioned, so every clone runs this once:
    python scripts/install_git_hooks.py

Each hook gets a small block that calls scripts/backup_live_db.py, which does
nothing unless the database changed. The block is appended to hooks that already
exist - Git LFS writes post-checkout, post-merge and post-commit - and refreshed
in place on later runs; it is always the last thing in the file.

The repository and its hooks directory are resolved from this file, not from the
shell's directory, and core.hooksPath is honoured: git only runs hooks from that
path, so writing to .git/hooks while it is set would leave every hook dormant.
"""
import subprocess
import sys
from pathlib import Path

MARKER = "# live-db-backup"
ROOT = Path(__file__).resolve().parent.parent
HOOKS = ("pre-commit", "pre-merge-commit", "pre-rebase", "post-checkout", "post-merge")
BLOCK = f"""{MARKER}: snapshot data/pokemon_cards.db before git changes the working tree.
# Added by scripts/install_git_hooks.py; delete this block to remove it.
root=$(git rev-parse --show-toplevel) || exit 0
python "$root/scripts/backup_live_db.py" || echo "warning: live database backup failed" >&2
exit 0
"""


def git(*args):
    done = subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True)
    return done.stdout.strip() if done.returncode == 0 else None


def hooks_dir():
    configured = git("config", "--get", "core.hooksPath")
    if configured:
        path = Path(configured).expanduser()
    else:
        path = Path(git("rev-parse", "--git-path", "hooks") or ".git/hooks")
    return path if path.is_absolute() else ROOT / path


def install(path):
    """Write the block into one hook, keeping whatever is already there."""
    if not path.exists():
        path.write_bytes(b"#!/bin/sh\n" + BLOCK.encode())
        return "new"
    text = path.read_text(encoding="utf-8", errors="replace")
    cut = text.find(MARKER)
    head = (text if cut < 0 else text[:cut]).rstrip("\n")
    if not head.strip():
        head = "#!/bin/sh"
    path.write_bytes((head + "\n" + BLOCK).encode())
    return "appended to" if cut < 0 else "refreshed"


def main():
    if not (ROOT / "scripts" / "backup_live_db.py").exists():
        print("scripts/backup_live_db.py is missing next to this file", file=sys.stderr)
        return 1
    if git("rev-parse", "--git-dir") is None:
        print(f"{ROOT} is not a git repository", file=sys.stderr)
        return 1
    target = hooks_dir()
    target.mkdir(parents=True, exist_ok=True)
    outcomes = ("new", "appended to", "refreshed")
    tally = dict.fromkeys(outcomes, 0)
    for name in HOOKS:
        path = target / name
        tally[install(path)] += 1
        path.chmod(0o755)  # LF is written above, or sh on Windows refuses to run it
    print(f"hooks in {target}: " + ", ".join(f"{tally[o]} {o}" for o in outcomes))
    return 0


if __name__ == "__main__":
    sys.exit(main())
