#!/usr/bin/env python3
"""Resolve image URLs for cards missing local images.

Reads /tmp/missing_cards.json (list of ptcg_ids), classifies each card by
its set prefix against the TCGdex JA/DE/EN set lists, fetches the per-set
card lists (cached to data/cache/tcgdex/sets_detail/) and writes a map
{ptcg_id: image_url} to data/image_url_map.json.
"""
import json
import re
import time
from pathlib import Path

import requests

PROJECT = Path("/opt/data/pokemon-card-scanner")
CACHE = PROJECT / "data/cache/tcgdex"
CACHE.mkdir(parents=True, exist_ok=True)
API = "https://api.tcgdex.net/v2"

sess = requests.Session()
sess.headers["User-Agent"] = "pokemon-card-scanner/1.0"


def _as_set_list(raw):
    if isinstance(raw, dict):
        return raw.get("sets", [])
    return raw


def fetch_sets(locale):
    p = CACHE / f"sets_{locale}.json"
    if p.exists():
        return _as_set_list(json.loads(p.read_text()))
    d = sess.get(f"{API}/{locale}/sets", timeout=30).json()
    p.write_text(json.dumps(d))
    return _as_set_list(d)


def fetch_set_detail(locale, set_id):
    p = CACHE / f"set_detail_{locale}_{set_id}.json"
    if p.exists():
        return json.loads(p.read_text())
    try:
        r = sess.get(f"{API}/{locale}/sets/{set_id}", timeout=30)
        if r.status_code != 200:
            return None
        d = r.json()
    except Exception:
        return None
    p.write_text(json.dumps(d))
    time.sleep(0.15)
    return d


def main():
    missing = json.load(open("/tmp/missing_cards.json"))
    prefixes = sorted({pid.rsplit("-", 1)[0] for pid in missing})

    ja = {s["id"] for s in fetch_sets("ja")}
    de = {s["id"] for s in fetch_sets("de")}
    en_raw = fetch_sets("en")
    en_list = en_raw if isinstance(en_raw, list) else en_raw.get("sets", [])
    en = {s["id"] for s in en_list}
    # case-insensitive lookups: lower -> real id
    ja_ci = {i.lower(): i for i in ja}
    de_ci = {i.lower(): i for i in de}
    en_ci = {i.lower(): i for i in en}

    # map prefix -> (locale, tcgdex_set_id)
    plan = {}
    unresolvable = []
    for pfx in prefixes:
        de_stripped = re.sub(r"^de_", "", pfx)
        low = pfx.lower()
        if pfx in ja:
            plan[pfx] = ("ja", pfx)
        elif low in ja_ci:
            plan[pfx] = ("ja", ja_ci[low])
        elif pfx in de:
            plan[pfx] = ("de", pfx)
        elif low in de_ci:
            plan[pfx] = ("de", de_ci[low])
        elif de_stripped in de:
            plan[pfx] = ("de", de_stripped)
        elif de_stripped.lower() in de_ci:
            plan[pfx] = ("de", de_ci[de_stripped.lower()])
        elif pfx in en:
            plan[pfx] = ("en", pfx)
        elif low in en_ci:
            plan[pfx] = ("en", en_ci[low])
        else:
            unresolvable.append(pfx)

    print(f"prefixes: {len(prefixes)} resolvable, {len(unresolvable)} unresolvable: {unresolvable}")

    url_map = {}
    # build per-prefix card url dicts
    needed_prefixes = sorted({pid.rsplit("-", 1)[0] for pid in missing if pid.rsplit("-", 1)[0] in plan})
    missing_set = set(missing)
    for idx, pfx in enumerate(needed_prefixes, 1):
        locale, set_id = plan[pfx]
        detail = fetch_set_detail(locale, set_id)
        if not detail or not detail.get("cards"):
            print(f"  [{idx}/{len(needed_prefixes)}] {pfx}: NO DATA ({locale}/{set_id})")
            continue
        n = 0
        # index by localId number so DB ids like "SV4a-1" match "SV4a-001"
        by_num = {}
        for c in detail["cards"]:
            img = c.get("image")
            if not img:
                continue
            by_num.setdefault(c.get("localId", ""), img)
            try:
                by_num.setdefault(str(int(c["localId"])), img)
            except (ValueError, KeyError, TypeError):
                pass
        for pid in missing_set:
            if not pid.startswith(pfx + "-"):
                continue
            num = pid[len(pfx) + 1:]
            img = by_num.get(num)
            if img is None:
                try:
                    img = by_num.get(str(int(num)))
                except (ValueError, TypeError):
                    img = None
            if img and pid not in url_map:
                url_map[pid] = img + ".png"
                n += 1
        if idx % 20 == 0 or idx == len(needed_prefixes):
            print(f"  [{idx}/{len(needed_prefixes)}] {pfx}: mapped {n} (total {len(url_map)})")

    out = PROJECT / "data/image_url_map.json"
    out.write_text(json.dumps(url_map))
    print(f"mapped {len(url_map)} of {len(missing)} missing cards -> {out}")
    still = len(missing) - len(url_map)
    print(f"still unmapped: {still}")


if __name__ == "__main__":
    main()
