#!/bin/bash
# Diagnostic script for the pokemon-card-scanner container
# Run this ON THE NAS to diagnose the "attempt to write a readonly database" error.
set -e

echo "=== 1. Container status ==="
docker inspect pokemon-card-scanner --format 'Status: {{.State.Status}} | Health: {{.State.Health.Status}}'
echo

echo "=== 2. Container user ==="
docker exec pokemon-card-scanner id
echo

echo "=== 3. Data dir contents in container ==="
docker exec pokemon-card-scanner ls -la /opt/data/pokemon-card-scanner/data/
echo

echo "=== 4. Source directory ownership (NAS-side) ==="
# Try both possible source paths
for SRC in \
  "/share/CACHEDEV4_DATA/dockerdata/container-station-data/lib/docker/volumes/hermes_hermes-data/_data/pokemon-card-scanner/data" \
  "/opt/data/pokemon-card-scanner/data" \
  "/volume1/dockerdata/pokemon-card-scanner/data"; do
  if [ -d "$SRC" ]; then
    echo "FOUND: $SRC"
    stat -c '  uid=%u gid=%g mode=%A owner=%U group=%G' "$SRC"
    stat -c '  DB file:' "$SRC/pokemon_cards.db" 2>/dev/null && \
      stat -c '    DB: uid=%u gid=%g size=%s bytes' "$SRC/pokemon_cards.db" || \
      echo "    (no DB)"
    break
  fi
done
echo

echo "=== 5. Docker bind-mount details ==="
docker inspect pokemon-card-scanner --format '{{range .Mounts}}{{printf "Source: %s\nDestination: %s\nType: %s\nRW: %t\nDriver: %s\n" .Source .Destination .Type .RW .Driver}}{{end}}'
echo

echo "=== 6. Container logs (last 50 lines) ==="
docker logs pokemon-card-scanner --tail 50 2>&1
echo

echo "=== 7. DB write test (from inside container) ==="
docker exec pokemon-card-scanner sh -c 'cd /opt/data/pokemon-card-scanner && python3 -c "import sqlite3; conn=sqlite3.connect(\"data/pokemon_cards.db\"); conn.execute(\"SELECT 1\"); print(\"DB read: OK\"); conn.close(); print(\"DB close: OK\")"' 2>&1
echo

echo "=== Done ==="
