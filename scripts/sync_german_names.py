#!/usr/bin/env python3
"""Give German card rows their German names.

The German catalogue was imported with English identities: cards.name says
"Meditite" while the print (and TCGdex de) says "Meditie", and sets still carry
placeholders like "DE ex14". Names come from TCGdex `de` first; sets TCGdex has
no German record for fall back to the PokéWiki card pages, whose titles read
"<German name> (<set> <number>)".

Only rows whose ptcg_id starts with `de_` are ever touched.

  python scripts/sync_german_names.py --dry-run
  python scripts/sync_german_names.py
"""
import argparse
import concurrent.futures as cf
import json
import re
import sqlite3
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "pokemon_cards.db"
API_DE = "https://api.tcgdex.net/v2/de"
API_EN = "https://api.tcgdex.net/v2/en"
WIKI = "https://www.pokewiki.de/api.php"

S = requests.Session()
S.headers["User-Agent"] = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0 Safari/537.36")


def get(url, **params):
    for attempt in range(3):
        try:
            r = S.get(url, params=params or None, timeout=30)
            if r.status_code == 200:
                return r
            if r.status_code == 404:
                return None
        except requests.RequestException:
            pass
        time.sleep(1.5 * (attempt + 1))
    return None


def norm_num(token) -> str:
    """SM01 / SM1 / 001 / 1 / A-060 -> comparable keys."""
    t = re.sub(r"\s+", "", str(token)).upper()
    t = re.sub(r"^A-", "", t)
    return re.sub(r"\d+", lambda m: str(int(m.group(0))), t)


def split_id(ptcg_id: str):
    """'de_P-A-060' is ambiguous: set P + 'A-060', or set P-A + '060'."""
    body = ptcg_id[3:]
    return [(body[:i], body[i + 1:]) for i, ch in enumerate(body) if ch == "-"]


def fetch_set(api, code):
    r = get(f"{api}/sets/{code}")
    return code, (r.json() if r else None)


def wiki_cards(label: str) -> dict:
    """{normalised number: German name} from a PokéWiki set category."""
    mapping, cont = {}, {}
    while True:
        params = {"action": "query", "generator": "categorymembers",
                  "gcmtitle": f"Kategorie:Sammelkarte aus {label}",
                  "gcmnamespace": 0, "gcmlimit": 500,
                  "format": "json", "formatversion": 2}
        params.update(cont)
        r = get(WIKI, **params)
        if r is None:
            break
        d = r.json()
        for page in (d.get("query") or {}).get("pages", []):
            m = re.match(r"^(.+) \((.+) (\S+)\)$", page["title"])
            if not m:
                continue                 # trainer pages have no set number
            name, set_label, num = m.groups()
            if set_label.strip().lower() != label.lower():
                continue
            mapping.setdefault(norm_num(num), name)
        if "continue" not in d:
            break
        cont = d["continue"]
    return mapping


def wiki_search_name(labels, number) -> str | None:
    """Last resort: the page exists but is not filed in its set category."""
    for label in labels:
        r = get(WIKI, action="query", list="search",
                srsearch=f'"{label} {number}"', srlimit=5,
                format="json", formatversion=2)
        if r is None:
            continue
        for hit in (r.json().get("query") or {}).get("search", []):
            m = re.match(r"^(.+) \((.+) (\S+)\)$", hit["title"])
            if not m:
                continue
            name, set_label, num = m.groups()
            if (set_label.strip().lower() == label.lower()
                    and norm_num(num) == norm_num(number)):
                return name
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "select id, ptcg_id, name, number, set_name, set_id from cards "
        "where ptcg_id like 'de\\_%' escape '\\'").fetchall()
    print(f"german cards: {len(rows)}")

    codes = sorted({split_id(r["ptcg_id"])[0][0] for r in rows})
    # TCGdex calls the Pocket promo set "P-A"; the catalogue stores it as "P"
    candidates = sorted({c for code in codes for c in (code, f"{code}-A")})
    print(f"german set codes: {len(codes)}")

    de_sets, en_sets = {}, {}
    with cf.ThreadPoolExecutor(max_workers=6) as ex:
        for code, data in ex.map(lambda c: fetch_set(API_DE, c), candidates):
            if data:
                de_sets[code] = data
        for code, data in ex.map(lambda c: fetch_set(API_EN, c), codes):
            if data:
                en_sets[code] = data
    print("de sets found:", len(de_sets), "| en sets found:", len(en_sets))

    # name index: (set id, normalised local id) -> German name
    names = {}
    for code, data in de_sets.items():
        for card in data.get("cards", []):
            if card.get("name"):
                names[(code, norm_num(card.get("localId")))] = card["name"]

    def set_label(code):
        """German set label, preferring TCGdex de, then an EX-prefixed en name."""
        data = de_sets.get(code) or de_sets.get(f"{code}-A")
        if data and data.get("name"):
            return data["name"]
        en = (en_sets.get(code) or {}).get("name")
        if not en:
            return None
        return f"EX {en}" if code.startswith("ex") and not en.lower().startswith("ex") else en

    labels = {code: set_label(code) for code in codes}
    print("sets without any name source:", [c for c, v in labels.items() if not v])

    def category_labels(code):
        """Wiki categories are not always spelled like TCGdex (SVP vs SV)."""
        out = [labels.get(code)]
        en = (en_sets.get(code) or {}).get("name")
        if en:
            out += [en, f"EX {en}"] if code.startswith("ex") else [en]
        out = [x for x in out if x]
        return list(dict.fromkeys(out + [x.replace("SVP", "SV") for x in out]))

    resolved, wiki_source, no_source = [], [], []
    wiki_cache = {}
    for r in rows:
        found = None
        for set_id, num in split_id(r["ptcg_id"]):
            if (set_id, norm_num(num)) in names:
                found = names[(set_id, norm_num(num))]
                break
        if not found:
            code = split_id(r["ptcg_id"])[0][0]
            for label in category_labels(code):
                if label not in wiki_cache:
                    wiki_cache[label] = wiki_cards(label)
                found = wiki_cache[label].get(norm_num(r["number"]))
                if found:
                    wiki_source.append(r["ptcg_id"])
                    break
            if not found:
                found = wiki_search_name(category_labels(code), r["number"])
                if found:
                    wiki_source.append(r["ptcg_id"])
        if not found:
            no_source.append(r["ptcg_id"])
            continue
        resolved.append((r["id"], r["ptcg_id"], found, r["name"], r["set_name"]))

    card_updates, set_updates = [], {}
    for row_id, ptcg_id, name, old_name, old_set in resolved:
        code = split_id(ptcg_id)[0][0]
        display = f"DE {labels[code]}" if labels.get(code) else old_set
        if name != old_name or (display and display != old_set):
            card_updates.append((name, display, row_id))

    for code, label in labels.items():
        if not label:
            continue
        row = con.execute("select id, name from sets where ptcg_id = ?",
                          (f"de_{code}",)).fetchone()
        if row and row["name"] != f"DE {label}":
            set_updates[row["id"]] = (f"DE {label}", row["name"])

    print(f"names from TCGdex de: {len(resolved) - len(wiki_source)}")
    print(f"names from PokéWiki:  {len(wiki_source)}")
    print(f"cards still unnamed:  {len(no_source)} {no_source[:5]}")
    print(f"card rows to update:  {len(card_updates)}")
    for new, disp, cid in card_updates[:8]:
        print(f"   {cid}: {new!r} set={disp!r}")
    print(f"set names to change: {len(set_updates)}")
    for sid, (new, old) in list(set_updates.items())[:10]:
        print(f"   {old!r} -> {new!r}")

    if args.dry_run:
        return

    con.executemany("update cards set name = ?, set_name = ? where id = ?",
                    [(n, s, i) for n, s, i in card_updates])
    con.executemany("update sets set name = ? where id = ?",
                    [(new, i) for i, (new, _old) in set_updates.items()])
    con.commit()
    print(f"updated {len(card_updates)} card rows and {len(set_updates)} set names")


if __name__ == "__main__":
    sys.exit(main())
