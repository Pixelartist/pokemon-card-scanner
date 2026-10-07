#!/bin/bash
cd /opt/data/pokemon-card-scanner
while true; do
    echo "Starting server..."
    .venv/bin/python run.py > server.log 2>&1
    echo "Server exited with code $?, restarting in 2s..."
    sleep 2
done