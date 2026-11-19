"""Backfill set release dates from TCGdex.

The cached set catalogue (data/cache/tcgdex/sets_en.json) lists every set but
leaves releaseDate empty: TCGdex only publishes the date on the per-set detail
endpoint. Without dates the Sets tab can neither sort nor filter by age, so
this script fetches each set once, keeps the detail response in the cache and
writes the date to both the catalogue and the sets table.

    python scripts/fetch_set_dates.py
    python scripts/fetch_set_dates.py --force        # re-fetch cached details
    python scripts/fetch_set_dates.py --limit 20     # smoke test
    python scripts/fetch_set_dates.py --db path/to.db
"""
import argparse
import json
import sqlite3
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "data" / "pokemon_cards.db"
CACHE_DIR = ROOT / "data" / "cache" / "tcgdex"
CATALOG = CACHE_DIR / "sets_en.json"
API_SET = "https://api.tcgdex.net/v2/{locale}/sets/{set_id}"
# Several catalogue ids are Japanese-only sets; the English endpoint 404s there.
LOCALES = ("en", "ja")
HEADERS = {"User-Agent": "Mozilla/5.0 (pokemon-card-scanner)"}
WORKERS = 12


def _get(url: str, timeout: int = 25) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=timeout) as resp:
        return resp.read()


def base_id(code: str) -> str:
    """TCGdex id for a DB code; localized rows (de_sv01) point at the English set."""
    sid = (code or "").lower()
    return sid[3:] if sid.startswith("de_") else sid


def load_detail(set_id: str, force: bool = False) -> dict:
    for locale in LOCALES:
        path = CACHE_DIR / f"set_detail_{locale}_{set_id}.json"
        if path.exists() and not force:
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                pass
        try:
            detail = json.loads(_get(API_SET.format(locale=locale, set_id=set_id)))
        except Exception:
            continue
        path.write_text(json.dumps(detail), encoding="utf-8")
        return detail
    raise RuntimeError("no TCGdex detail in any locale")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(DEFAULT_DB), help="SQLite database path")
    parser.add_argument("--limit", type=int, default=0, help="only the first N sets")
    parser.add_argument("--force", action="store_true", help="re-fetch cached detail files")
    parser.add_argument("--workers", type=int, default=WORKERS)
    args = parser.parse_args()

    data = json.loads(CATALOG.read_text(encoding="utf-8"))
    entries = data.get("sets", [])
    ids = sorted({base_id(e.get("id")) for e in entries if e.get("id")})
    if args.limit:
        ids = ids[: args.limit]
    print(f"{len(entries)} catalogue sets, {len(ids)} TCGdex ids to resolve")

    def job(set_id):
        try:
            return set_id, load_detail(set_id, args.force)
        except Exception as exc:
            print(f"  ! {set_id}: {exc}")
            return set_id, None

    dates = {}
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for i, (set_id, detail) in enumerate(pool.map(job, ids), 1):
            release = ((detail or {}).get("releaseDate") or "")[:10]
            if release:
                dates[set_id] = release
            if i % 50 == 0 or i == len(ids):
                print(f"  {i}/{len(ids)} sets resolved ({len(dates)} dated)")

    written_catalog = 0
    for entry in entries:
        release = dates.get(base_id(entry.get("id")))
        if release and entry.get("releaseDate") != release:
            entry["releaseDate"] = release
            written_catalog += 1
    CATALOG.write_text(json.dumps(data), encoding="utf-8")

    conn = sqlite3.connect(args.db, timeout=30)
    conn.row_factory = sqlite3.Row
    written_rows = 0
    for row in conn.execute("SELECT ptcg_id, release_date FROM sets").fetchall():
        release = dates.get(base_id(row["ptcg_id"]))
        if release and row["release_date"] != release:
            conn.execute("UPDATE sets SET release_date = ? WHERE ptcg_id = ?", (release, row["ptcg_id"]))
            written_rows += 1
    conn.commit()
    conn.close()

    print(
        f"{len(dates)} sets dated; updated {written_catalog} catalogue entries "
        f"and {written_rows} set rows -> {CATALOG}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
