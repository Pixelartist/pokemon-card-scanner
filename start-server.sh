#!/bin/bash
# Start the Pokemon Card Scanner server detached from any terminal session.
# Usage: /opt/data/pokemon-card-scanner/start-server.sh
cd /opt/data/pokemon-card-scanner
/opt/data/pokemon-card-scanner/.venv/bin/python daemon.py > /opt/data/pokemon-card-scanner/server.log 2>&1 &
disown
echo "Server started (PID $!)"