#!/bin/bash
# Run quickdraw matcher test suite
# Usage: ./test.sh [--daemon]

set -e

cd /opt/data/pokemon-card-scanner

# Activate venv and run tests
.venv/bin/python3 tests/test_quickdraw.py "$@"