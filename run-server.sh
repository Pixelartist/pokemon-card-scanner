#!/bin/sh
# shortcut: bare restart loop, no supervisor daemon on QNAP; upgrade to docker restart=unless-stopped if the container gets its own service
# Resolve the project root from this script's own location so the supervisor
# runs from wherever the repo is checked out (not only the NAS path).
cd "$(dirname "$0")" || exit 1
while true; do
  .venv/bin/python daemon.py >> data/server.log 2>&1
  echo "[supervisor] server exited code=$? at $(date -Iseconds) — restarting in 3s" >> data/server.log
  sleep 3
done