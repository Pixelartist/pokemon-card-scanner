#!/bin/bash
set -e
# The mounted data volume keeps host ownership across redeploys; warn if the
# app user cannot write the DB / scans directory.
if mkdir -p /opt/data/pokemon-card-scanner/data && touch /opt/data/pokemon-card-scanner/data/.writable 2>/dev/null; then
    rm -f /opt/data/pokemon-card-scanner/data/.writable
    echo "data volume writable"
else
    echo "WARNING: /opt/data/pokemon-card-scanner/data not writable by uid $(id -u) — set volume owner to 1000 on the host" >&2
fi
cd /opt/data/pokemon-card-scanner
exec python run.py
