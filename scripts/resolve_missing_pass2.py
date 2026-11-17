#!/usr/bin/env python3
"""Second pass: resolve images for cards still missing after pass 1.

Sources, in order:
  1. TCGdex EN details (locale fallback if preferred locale has no images)
  2. PTCG-database JP official scans, with promo-number normalization
     (XY01 -> 1, BW01 -> 1, SV001 -> 1, ...)
Appends to data/image_url_map.json.
"""
import json
import re
import time
import urllib.request
from pathlib import Path

PROJECT = Path("/opt/data/pokemon-card-scanner")
CACHE = PROJECT / "data/cache/tcgdex"
API = "https://api.tcgdex.net/v2"
PTCG_ROOT = Path("/opt/data/cache/scratch/PTCG-database/data_jp")

import glob

def norm_num(n):
    n = re.sub(r'/(SET|UC|GR|R|U)\b.*', '', str(n).strip())
    if '/' in n:
        n = n.split('/')[0]
    m = re.search(r'(\d+)', n)
    return str(int(m.group(1))) if m else n


def fetch_detail(locale, set_id):
    p = CACHE / f"set_detail_{locale}_{set_id}.json"
    if p.exists():
        return json.loads(p.read_text())
    try:
        with urllib.request.urlopen(f"{API}/{locale}/sets/{set_id}", timeout=30) as r:
            d = json.loads(r.read())
        if d.get('type', '').startswith('https://tcgdex.dev/errors'):
            return None
        p.write_text(json.dumps(d))
        time.sleep(0.15)
        return d
    except Exception:
        return None


def main():
    missing = json.load(open('/tmp/final_missing.json'))
    url_map = json.load(open(PROJECT / 'data/image_url_map.json'))
    still = [p for p in missing if p not in url_map]
    prefixes = sorted({p.rsplit('-', 1)[0] for p in still})
    print(f'{len(still)} still missing across {len(prefixes)} prefixes')

    # --- source 1: tcgdex en detail with fallback ---
    # map prefix -> tcgdex en id (case-insensitive)
    en_raw = json.loads((CACHE / 'sets_en.json').read_text())
    en_list = en_raw if isinstance(en_raw, list) else en_raw.get('sets', [])
    en_ci = {s['id'].lower(): s['id'] for s in en_list}

    tcgdex_idx = {}  # prefix -> {num: url}
    for pfx in prefixes:
        sid = en_ci.get(pfx.lower())
        if not sid:
            continue
        detail = fetch_detail('en', sid)
        if not detail:
            continue
        by_num = {}
        for c in detail.get('cards', []):
            img = c.get('image')
            if img:
                by_num[norm_num(c.get('localId', ''))] = img
        if by_num:
            tcgdex_idx[pfx.lower()] = by_num

    hits1 = 0
    for pid in still:
        pfx, num = pid.rsplit('-', 1)
        by_num = tcgdex_idx.get(pfx.lower())
        if by_num:
            u = by_num.get(norm_num(num))
            if u:
                url_map[pid] = u + '/high.png'
                hits1 += 1
    print(f'tcgdex EN fallback hits: {hits1}')

    # --- source 2: PTCG-database with promo normalization ---
    ptcg_idx = {}
    for f in PTCG_ROOT.iterdir():
        if not f.is_dir():
            continue
        d = {}
        for fp in glob.glob(str(f / '*.json')):
            try:
                j = json.load(open(fp))
            except Exception:
                continue
            img = j.get('img')
            if img and j.get('number') is not None:
                d.setdefault(norm_num(j['number']), img)
        key = (j.get('set_name') or f.name).lower()
        ptcg_idx.setdefault(key, {}).update(d)

    hits2 = 0
    still2 = [p for p in still if p not in url_map]
    for pid in still2:
        pfx, num = pid.rsplit('-', 1)
        d = ptcg_idx.get(pfx.lower())
        if d:
            u = d.get(norm_num(num))
            if u:
                url_map[pid] = u
                hits2 += 1
    print(f'PTCG-database hits: {hits2}')

    (PROJECT / 'data/image_url_map.json').write_text(json.dumps(url_map))
    final_still = [p for p in missing if p not in url_map]
    print(f'map now covers {len(missing) - len(final_still)} of {len(missing)}; still unresolved: {len(final_still)}')
    json.dump(final_still, open('/tmp/final_missing2.json', 'w'))


if __name__ == '__main__':
    main()
