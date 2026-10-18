#!/bin/bash
# Detached re-embed runner — survives Hermes tool-call interruptions.
# Usage: bash /opt/data/pokemon-card-scanner/scripts/run_reembed.sh
cd /opt/data/pokemon-card-scanner || exit 1
exec .venv/bin/python -u scripts/reembed_english_index.py