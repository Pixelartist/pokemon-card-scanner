#!/bin/sh
# shortcut: bare restart loop, no supervisor daemon on QNAP; upgrade to docker restart=unless-stopped if the container gets its own service
cd /opt/data/pokemon-card-scanner
while true; do
  .venv/bin/python daemon.py >> data/server.log 2>&1
  echo "[supervisor] server exited code=$? at $(date -Iseconds) — restarting in 3s" >> data/server.log
  sleep 3
done