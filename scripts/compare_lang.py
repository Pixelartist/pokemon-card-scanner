#!/usr/bin/env python3
"""Check if German and English cards share artwork (same set, different language)."""
import json
from pathlib import Path

BASE_DIR = Path("/opt/data/pokemon-card-scanner")
DATA_DIR = BASE_DIR / "data"

meta = json.loads(open(DATA_DIR / "quickdraw-meta.json").read())["cards"]

# Group German cards by set
german_by_set = {}
for c in meta:
    cid = c.get("id", "")
    if cid.startswith("de_"):
        set_id = cid.split("-")[0]  # de_me05-015 -> de_me05
        basename = cid  # de_me05-015
        # Find corresponding English (strip de_ prefix)
        eng_id = cid[3:] if cid.startswith("de_") else None
        if eng_id:
            for c2 in meta:
                if c2.get("id") == eng_id:
                    print(f"German {cid} <-> English {eng_id} (same artwork)")
                    break