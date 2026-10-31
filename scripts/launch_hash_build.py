#!/usr/bin/env python3
"""Launch build_browser_hashes.py detached (setsid double-fork) so it
survives Hermes session teardowns; auto-restarts up to 5x on crash
(the builder resumes from data/card-hashes.partial.json)."""
import os
import subprocess
import sys

WORKDIR = "/opt/data/pokemon-card-scanner"
PY = "/opt/data/projects/finn-house-scraper/.venv/bin/python"
SCRIPT = f"{WORKDIR}/scripts/build_browser_hashes.py"

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
with open("/tmp/build_hashes.log", "a") as log:
    for attempt in range(5):
        r = subprocess.run([PY, "-u", SCRIPT], stdout=log, stderr=log, check=False)
        if r.returncode == 0:
            break
        log.write(f"attempt {attempt + 1} exit {r.returncode}, restarting in 10s\n")
        log.flush()
        import time
        time.sleep(10)
os._exit(0)
