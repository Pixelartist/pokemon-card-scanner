#!/opt/data/projects/finn-house-scraper/.venv/bin/python
"""Build mapping: DB ptcg_id -> artofpkm set id, verified by card count."""
import requests, re, json, sqlite3, html as htmlmod

HEADERS = {"User-Agent": "Mozilla/5.0"}
ART = "https://www.artofpkm.com"
DB = "/opt/data/pokemon-card-scanner/data/pokemon_cards.db"


def parse_listing():
    r = requests.get(ART + "/cards", headers=HEADERS, timeout=60)
    r.raise_for_status()
    html = r.text
    sets = {}
    for m in re.finditer(r'href="/sets/(\d+)"', html):
        sid = int(m.group(1))
        end = html.find('href="/sets/', m.end())
        ctx = html[m.start():end if end != -1 else m.end() + 1500]
        texts = [htmlmod.unescape(t).strip()
                 for t in re.findall(r'>([^<>]{2,80})<', ctx)]
        if len(texts) >= 4:
            # name, jp name, code, count
            code, count = texts[2], texts[3]
            if re.fullmatch(r"\d+", count):
                sets[sid] = {"name": texts[0], "code": code, "cards": int(count)}
    return sets


def main():
    art_sets = parse_listing()
    print(f"artofpkm sets parsed: {len(art_sets)}")

    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    # DB sets that have cards but no real images
    cur.execute("""
        SELECT s.ptcg_id, s.name, s.total_cards, COUNT(c.id),
               SUM(CASE WHEN c.images LIKE '%static/%' THEN 1 ELSE 0 END)
        FROM sets s JOIN cards c ON c.set_id = s.id
        GROUP BY s.id
        HAVING COALESCE(SUM(CASE WHEN c.images LIKE '%static/%' THEN 1 ELSE 0 END), 0) = 0
        ORDER BY s.ptcg_id
    """)
    db_sets = cur.fetchall()
    conn.close()

    print(f"DB sets with zero images: {len(db_sets)}")
    art_by_code = {}
    for sid, s in art_sets.items():
        art_by_code.setdefault(s["code"].lower(), []).append((sid, s))

    mapping, unmatched = {}, []
    for ptcg_id, name, total, cards, _ in db_sets:
        cands = art_by_code.get(ptcg_id.lower(), [])
        if not cands:
            unmatched.append((ptcg_id, cards))
            continue
        # pick candidate with count closest to our card count
        sid, s = min(cands, key=lambda c: abs(c[1]["cards"] - cards))
        mapping[ptcg_id] = {"art_id": sid, "art_code": s["code"],
                            "art_name": s["name"], "art_cards": s["cards"],
                            "db_cards": cards}
        print(f"  {ptcg_id:8} <-> art:{sid:4} {s['code']:12} {s['name'][:40]:40} "
              f"art={s['cards']} db={cards}")

    print("\nUnmatched DB sets (no artofpkm code):")
    for ptcg_id, cards in unmatched:
        print(f"  {ptcg_id} ({cards} cards)")

    json.dump(mapping, open(
        "/opt/data/pokemon-card-scanner/data/artofpkm_map.json", "w"),
        indent=1)
    print(f"\nSaved {len(mapping)} mappings to data/artofpkm_map.json")


if __name__ == "__main__":
    main()
