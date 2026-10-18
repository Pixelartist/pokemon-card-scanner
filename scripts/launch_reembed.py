#!/usr/bin/env python3
"""Launch reembed_english_index.py in a new session so it survives
Hermes tool-call teardowns (os.setsid + double-fork pattern)."""
import os
import subprocess
import sys

WORKDIR = "/opt/data/pokemon-card-scanner"
PY = f"{WORKDIR}/.venv/bin/python"
SCRIPT = f"{WORKDIR}/scripts/reembed_english_index.py"

pid = os.fork()
if pid > 0:
    print(f"parent: child pid {pid}")
    sys.exit(0)

os.setsid()
pid = os.fork()
if pid > 0:
    sys.exit(0)

os.chdir(WORKDIR)
os.umask(0o022)
with open("/tmp/reembed.log", "w") as log:
    subprocess.run([PY, "-u", SCRIPT], stdout=log, stderr=log, check=False)
os._exit(0)