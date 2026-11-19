"""Download set logos and symbols from TCGdex and record them on the set rows.

TCGdex publishes one logo (the wide set banner) and one symbol (the small round
badge printed on every card of the set) per set under assets.tcgdex.net. They
are the same artwork the physical cards carry, so they read as "the set" at a
glance in a way a name or a code never does.

Files land in data/images/sets/ and are served at /static/sets/; the row's
images column stores those public paths as {"logo": ..., "symbol": ...}.

    python scripts/fetch_set_images.py
    python scripts/fetch_set_images.py --force          # re-download everything
    python scripts/fetch_set_images.py --limit 20       # smoke test
    python scripts/fetch_set_images.py --db path/to.db
"""
import argparse
import json
import sqlite3
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "data" / "pokemon_cards.db"
IMAGE_DIR = ROOT / "data" / "images" / "sets"
CATALOG = ROOT / "data" / "cache" / "tcgdex" / "sets_en.json"
PUBLIC_PREFIX = "/static/sets"
API_SET = "https://api.tcgdex.net/v2/en/sets/{}"
HEADERS = {"User-Agent": "Mozilla/5.0 (pokemon-card-scanner)"}

# Logos are published as webp (png for a handful of pocket sets); symbols as png.
LOGO_EXTS = (".webp", ".png", ".jpg")
SYMBOL_EXTS = (".png", ".webp")
WORKERS = 12


def _get(url: str, timeout: int = 25) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=timeout) as resp:
        return resp.read()


def load_catalog() -> dict:
    """tcgdex set id -> {logo, symbol} base urls (no file extension)."""
    catalog = {}
    if CATALOG.exists():
        with open(CATALOG, encoding="utf-8") as f:
            for entry in json.load(f).get("sets", []):
                sid = (entry.get("id") or "").lower()
                if sid:
                    catalog[sid] = {
                        "logo": entry.get("logo") or "",
                        "symbol": entry.get("symbol") or "",
                    }
    return catalog


def catalog_for(set_id: str, catalog: dict) -> dict:
    """Look a set up locally; hit the TCGdex API when the cache has nothing."""
    meta = catalog.get(set_id)
    if meta and (meta.get("logo") or meta.get("symbol")):
        return meta
    try:
        detail = json.loads(_get(API_SET.format(set_id)))
    except Exception as exc:
        print(f"  ! {set_id}: no catalog entry ({exc})")
        return meta or {}
    meta = {"logo": detail.get("logo") or "", "symbol": detail.get("symbol") or ""}
    catalog[set_id] = meta
    return meta


def _strip_ext(url: str) -> str:
    for ext in (".webp", ".png", ".jpg", ".jpeg", ".svg"):
        if url.lower().endswith(ext):
            return url[: -len(ext)]
    return url


def fetch_one(base: str, exts) -> tuple:
    """Return (save path, bytes) for the first extension that exists, else (None, None)."""
    if not base:
        return None, None
    base = _strip_ext(base)
    for ext in exts:
        try:
            data = _get(base + ext)
        except Exception:
            continue
        if data:
            return ext, data
    return None, None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(DEFAULT_DB), help="SQLite database path")
    parser.add_argument("--limit", type=int, default=0, help="only the first N sets")
    parser.add_argument("--force", action="store_true", help="re-download existing files")
    parser.add_argument("--workers", type=int, default=WORKERS)
    args = parser.parse_args()

    IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT ptcg_id FROM sets ORDER BY id").fetchall()
    codes = [r["ptcg_id"] for r in rows if r["ptcg_id"]]
    if args.limit:
        codes = codes[: args.limit]

    catalog = load_catalog()
    print(f"{len(codes)} sets, {len(catalog)} TCGdex set entries cached")

    # Resolve the TCGdex id once per DB set (de_ rows point at the same artwork).
    wanted = {}
    for code in codes:
        tcgdex_id = code.lower()
        if tcgdex_id.startswith("de_"):
            tcgdex_id = tcgdex_id[3:]
        wanted.setdefault(tcgdex_id, []).append(code)

    def job(item):
        tcgdex_id = item
        meta = catalog_for(tcgdex_id, catalog)
        logo_ext, logo = fetch_one(meta.get("logo"), LOGO_EXTS)
        sym_ext, sym = fetch_one(meta.get("symbol"), SYMBOL_EXTS)
        written = {}
        if logo:
            name = f"{tcgdex_id}_logo{logo_ext}"
            path = IMAGE_DIR / name
            if args.force or not path.exists():
                path.write_bytes(logo)
            written["logo"] = f"{PUBLIC_PREFIX}/{name}"
        if sym:
            name = f"{tcgdex_id}_symbol{sym_ext}"
            path = IMAGE_DIR / name
            if args.force or not path.exists():
                path.write_bytes(sym)
            written["symbol"] = f"{PUBLIC_PREFIX}/{name}"
        return tcgdex_id, written

    results = {}
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for i, (tcgdex_id, images) in enumerate(pool.map(job, wanted), 1):
            results[tcgdex_id] = images
            if i % 25 == 0 or i == len(wanted):
                print(f"  {i}/{len(wanted)} sets fetched")

    updated = missing = 0
    for tcgdex_id, codes_for_id in wanted.items():
        images = results.get(tcgdex_id) or {}
        if not images:
            missing += len(codes_for_id)
            continue
        payload = json.dumps(images)
        for code in codes_for_id:
            conn.execute("UPDATE sets SET images = ? WHERE ptcg_id = ?", (payload, code))
            updated += 1
    conn.commit()
    conn.close()

    logos = sum(1 for images in results.values() if images.get("logo"))
    symbols = sum(1 for images in results.values() if images.get("symbol"))
    print(
        f"Updated {updated} set rows ({logos} logos, {symbols} symbols, "
        f"{missing} sets without artwork) -> {IMAGE_DIR}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
