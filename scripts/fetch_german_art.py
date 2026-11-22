#!/usr/bin/env python3
"""Fetch German-language card art from several sources and install it locally.

Background: TCGdex only carries German artwork for the sets it has scanned.
Vintage sets (neo*, ex*, dp*, pl*, eco*) and most promo sets have a metadata
record but no `de` image, so the index was filled with the English asset. This
script walks a source chain per card and stores the first real German scan:

  1. tcgdex    api.tcgdex.net/v2/de -> assets.tcgdex.net/de/... (official, clean)
  2. pokewiki  pokewiki.de MediaWiki file namespace  (German scans, clean)
  3. bisafans  media.bisafans.de  (German photos; needs --allow-photos)

Every installed file is recorded in data/german_art_sources.json so a card's
art can be traced back to the source and page it came from.  Entries rebuilt
from the installed file instead of a fetch carry "reconstructed": true; a
real fetch replaces the entry, and the flag, with an observed record.

Usage:
  python scripts/fetch_german_art.py --dry-run            # report coverage
  python scripts/fetch_german_art.py --sets ex14,dp1      # specific sets
  python scripts/fetch_german_art.py --cards de_ex14-56
  python scripts/fetch_german_art.py --all-missing --allow-photos
"""
import argparse
import concurrent.futures as cf
import hashlib
import html as html_lib
import json
import re
import sys
import threading
import time
from pathlib import Path

import numpy as np
import requests
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from services.quickdraw_matcher import (FINE_DIM, _fingerprint,  # noqa: E402
                                        _rgba, _weighted_sq_rows)

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "pokemon_cards.db"
# The app mounts this directory at /static/german_cards/images (src/api/main.py),
# so it is the only copy of the art.  A second directory under src/static used to
# be written alongside it, but the mount above always won, so those files were
# never served — and since the data volume does not allow hardlinks here, the
# "link" fell back to a full copy and doubled the store.
IMG_DIR = ROOT / "data" / "german_cards" / "images"
MANIFEST = ROOT / "data" / "german_art_sources.json"
CACHE = ROOT / "data" / "cache"

TGDEX = "https://api.tcgdex.net/v2/de"
TGDEX_EN = "https://api.tcgdex.net/v2/en"
POKEWIKI = "https://www.pokewiki.de/api.php"
BISAFANS_INDEX = "https://www.bisafans.de/sammelkarten/sets"
MIN_WIDTH = 500  # below this a scan is kept only if no source does better
MIN_BYTES = 20000  # smaller responses are placeholders or error pages
BACK_URL = ("https://media.bisafans.de/44646cd/sammelkarten/sets/"
            "ex-rubin-saphir/001.jpg")     # the site's generic card back
BACK_TOL = 0.05        # at or below this the image *is* the back

# TCGdex set code -> PokéWiki category for sets whose German TCGdex name does
# not match the wiki's category ("Sammelkarte aus <name>").
PROMO_CATEGORIES = {
    "smp": "SM Black Star Promos",
    "swshp": "SWSH Black Star Promos",
    "svp": "SV Black Star Promos",
    "dpp": "DP Black Star Promos",
    "bwp": "BW Black Star Promos",
    "xyp": "XY Black Star Promos",
}

# PokéWiki's WAF answers 403 to scripted user-agents, so present as a browser;
# keep concurrency low and the requests gentle.
SESSION = requests.Session()
SESSION.headers["User-Agent"] = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0 Safari/537.36")
LOCK = threading.Lock()
_cache = {}


def norm_num(token: str) -> str:
    """SM01 / SM1 / 001 / 1 all collapse to the same comparable key."""
    t = re.sub(r"\s+", "", str(token)).upper()
    return re.sub(r"\d+", lambda m: str(int(m.group(0))), t)


def norm_name(name: str) -> str:
    n = name.lower().replace("&", "und")
    n = re.sub(r"[^a-z0-9]+", " ", n)
    return re.sub(r"\s+", " ", n).strip()


def get(url, **kw):
    timeout = kw.pop("timeout", 40)
    for attempt in range(4):
        try:
            r = SESSION.get(url, timeout=timeout, **kw)
            if r.status_code == 200:
                return r
            if r.status_code in (403, 404):
                return None
            time.sleep(1.5 * (attempt + 1))
        except requests.RequestException:
            time.sleep(1.5 * (attempt + 1))
    return None


def fetch_image(url, min_bytes: int = MIN_BYTES):
    """An image URL's bytes, or None when it is missing or too small to be art."""
    r = get(url)
    if r is None or len(r.content) < min_bytes:
        return None
    return r.content


# --------------------------------------------------------------------------- #
# sources
# --------------------------------------------------------------------------- #
def set_meta(code: str) -> dict:
    with LOCK:
        if code in _cache:
            return _cache[code]
    d = {}
    r = get(f"{TGDEX}/sets/{code}")
    if r is not None:
        d = r.json()
    with LOCK:
        _cache[code] = d
    return d


def tcgdex_image(api: str, code: str, num: str):
    """The first asset TCGdex publishes for a card in that locale."""
    r = get(f"{api}/cards/{code}-{num}")
    if r is None:
        return None
    img = r.json().get("image")
    if not img:
        return None
    for ext in ("high.png", "high.webp", "normal.png"):
        data = fetch_image(f"{img}/{ext}")
        if data is not None:
            return {"bytes": data, "url": f"{img}/{ext}"}
    return None


def source_tcgdex(card_id: str, code: str, num: str):
    """Official German asset (only exists for sets TCGdex has German scans of)."""
    return tcgdex_image(TGDEX, code, num)


def _imageinfo(titles):
    """{title: (url, width, height)} via one batched API call per 50 titles."""
    out = {}
    for i in range(0, len(titles), 50):
        params = {"action": "query", "titles": "|".join(titles[i:i + 50]),
                  "prop": "imageinfo", "iiprop": "url|size",
                  "format": "json", "formatversion": 2}
        r = get(POKEWIKI, params=params)
        if r is None:
            continue
        for page in (r.json().get("query") or {}).get("pages", []):
            info = (page.get("imageinfo") or [{}])[0]
            if info.get("url"):
                out[page["title"]] = (info["url"], info.get("width", 0),
                                      info.get("height", 0))
    return out


def english_set_name(code: str) -> str:
    key = f"en:{code}"
    with LOCK:
        if key in _cache:
            return _cache[key]
    r = get(f"{TGDEX_EN}/sets/{code}")
    name = (r.json().get("name") if r is not None else "") or ""
    with LOCK:
        _cache[key] = name
    return name


def pokewiki_files(code: str) -> dict:
    """{normalised number: best file} for a set's PokéWiki category."""
    key = f"pw:{code}"
    with LOCK:
        if key in _cache:
            return _cache[key]
    meta = set_meta(code)
    cats = [PROMO_CATEGORIES[code]] if code in PROMO_CATEGORIES else []
    cats.append(meta.get("name") or code)
    # several vintage sets are only categorised under their English name
    # (EX Hidden Legends, EX Team Rocket Returns, Neo Discovery, ...)
    en = english_set_name(code)
    if en and en not in cats:
        cats.append(en)
        if code.startswith("ex") and not en.lower().startswith("ex"):
            cats.append("EX " + en)      # wiki: "EX Hidden Legends", not "Hidden Legends"
    mapping = {}
    for cat in cats:
        cont, titles = {}, []
        while True:
            params = {"action": "query", "generator": "categorymembers",
                      "gcmtitle": f"Kategorie:Sammelkarte aus {cat}",
                      "gcmnamespace": 6, "gcmlimit": 500,
                      "format": "json", "formatversion": 2}
            params.update(cont)
            r = get(POKEWIKI, params=params)
            if r is None:
                break
            d = r.json()
            titles += [p["title"] for p in (d.get("query") or {}).get("pages", [])]
            if "continue" not in d:
                break
            cont = d["continue"]
        for title in titles:
            m = re.match(r"^Datei:(.+) \(([^()]*)\)\.(jpe?g|png)$", title, re.I)
            if not m:
                continue
            n = re.match(r"^(.*) (\S+)$", m.group(2))
            if not n:
                continue
            mapping.setdefault(norm_num(n.group(2)), []).append(title)
        if mapping:
            break
    # note: several files can describe one card (re-uploads, German vs
    # English art); the artwork should be the largest rendition available.
    flat = sorted({t for titles in mapping.values() for t in titles})
    info = _imageinfo(flat)
    best = {}
    for num, titles in mapping.items():
        cands = [(info[t][1], t) for t in titles if t in info]
        if cands:
            w, title = max(cands)
            best[num] = {"title": title, "url": info[title][0], "w": w}
    with LOCK:
        _cache[key] = best
    return best


def source_pokewiki(card_id: str, code: str, num: str):
    hit = pokewiki_files(code).get(norm_num(num))
    data = fetch_image(hit["url"]) if hit else None
    if data is None:
        return None
    return {"bytes": data, "url": hit["url"], "page": hit["title"],
            "w": hit["w"]}


def source_pokewiki_title(card_id: str, code: str, num: str):
    """Last wiki fallback: guess the File: name from the German card name.

    Some sets are not filed under any category we can derive (the wiki has its
    own set label for them), but the file itself follows the pattern
    "<Name> (<Set> <number>).jpg".
    """
    r = get(f"{TGDEX}/cards/{code}-{num}")
    if r is None:
        return None
    name = r.json().get("name")
    if not name:
        return None
    sets = [set_meta(code).get("name") or "", english_set_name(code)]
    sets += ["EX " + s for s in sets if s and not s.lower().startswith("ex")]
    titles = []
    for sname in dict.fromkeys(s for s in sets if s):
        for n in {num, num.zfill(2), num.zfill(3)}:
            for ext in ("jpg", "png", "jpeg"):
                titles.append(f"Datei:{name} ({sname} {n}).{ext}")
    info = _imageinfo(titles)
    if not info:
        return None
    title = max(info, key=lambda t: info[t][1])
    url, w, _ = info[title]
    data = fetch_image(url)
    if data is None:
        return None
    return {"bytes": data, "url": url, "page": title, "w": w}


def bisafans_sets() -> dict:
    """{normalised set label: {normalised number: media url}} scraped once."""
    with LOCK:
        if "bf" in _cache:
            return _cache["bf"]
    cached = CACHE / "bisafans_sets.json"
    if cached.exists():
        data = json.loads(cached.read_text(encoding="utf-8"))
    else:
        data = {}
        idx = get(BISAFANS_INDEX)
        pages = sorted(set(re.findall(
            r"/sammelkarten/sets/[a-z0-9-]+/[a-z0-9.\-]+\.php", idx.text))) if idx else []
        for page in pages:
            r = get("https://www.bisafans.de" + page)
            if r is None:
                continue
            # the page sends no charset, and requests would guess latin-1,
            # turning "&amp;" into "undamp" and eating umlauts
            html = r.content.decode("utf-8", "replace")
            label = re.search(r"<title>([^<|]+)", html)
            if not label:
                continue
            label = label.group(1).split("\u2014")[0]
            for _ in range(2):       # some titles arrive double-encoded (&amp;amp;)
                label = html_lib.unescape(label)
            urls = re.findall(
                r"(https://media\.bisafans\.de/[^\"']+/sammelkarten/sets/[^/]+/(\d+)\.jpg)", html)
            cards = {norm_num(n): u for u, n in urls}
            if cards:
                data[norm_name(label)] = cards
        CACHE.mkdir(parents=True, exist_ok=True)
        cached.write_text(json.dumps(data), encoding="utf-8")
    with LOCK:
        _cache["bf"] = data
    return data


def source_bisafans(card_id: str, code: str, num: str):
    sets = bisafans_sets()
    name = set_meta(code).get("name") or code
    key = norm_name(name)
    cards = sets.get(key)
    if cards is None:
        # page titles read "<set>-Basisset \u2014 <cycle> \u2014 Sets", so the set's
        # own name is a prefix; never take a page that merely mentions it
        for sname, scards in sets.items():
            if key and sname.startswith(key):
                cards = scards
                break
    if cards is None:
        # tolerate "EX " prefixes between TCGdex and the site (Rubin & Saphir)
        for sname, scards in sets.items():
            if key and (key in sname or sname in key):
                cards = scards
                break
    if not cards:
        return None
    url = cards.get(norm_num(num))
    data = fetch_image(url) if url else None
    if data is None:
        return None
    if _is_card_back(data):
        return None      # the site answers 200 with a generic card back
    return {"bytes": data, "url": url}


def _is_card_back(data: bytes) -> bool:
    """Bisafans returns the set's card back for numbers it has no scan of."""
    back = _back_fingerprint()
    if back is None:
        return False
    try:
        from io import BytesIO
        _c, fine = _fingerprint(_rgba(Image.open(BytesIO(data))))
    except Exception:
        return False
    return float(_weighted_sq_rows(np.stack([fine]), np.stack([back]),
                                   FINE_DIM)[0]) < BACK_TOL


def _back_fingerprint():
    global _BACK_FP
    with LOCK:
        if _BACK_FP is not False:
            return _BACK_FP
    r = get(BACK_URL)
    value = None
    if r is not None:
        try:
            from io import BytesIO
            value = _fingerprint(_rgba(Image.open(BytesIO(r.content))))[1]
        except Exception:
            value = None
    with LOCK:
        _BACK_FP = value if value is not None else False
    return value


_BACK_FP = False

SOURCES = [("tcgdex", source_tcgdex), ("pokewiki", source_pokewiki),
           ("pokewiki-title", source_pokewiki_title),
           ("bisafans", source_bisafans)]


# --------------------------------------------------------------------------- #
# storage
# --------------------------------------------------------------------------- #
def validate(data: bytes):
    """Accept only a plausible full card face; return normalised PNG bytes."""
    try:
        from io import BytesIO
        im = Image.open(BytesIO(data))
        im.load()
        if im.mode in ("RGBA", "LA", "P"):
            # wiki PNGs often carry transparency; composite over white so the
            # matcher sees a card face rather than black padding
            im = im.convert("RGBA")
            bg = Image.new("RGB", im.size, "white")
            bg.paste(im, mask=im.split()[-1])
            im = bg
        else:
            im = im.convert("RGB")
    except Exception:
        return None, None
    w, h = im.size
    if w < 200 or h < 280:
        return None, None
    if not 1.15 <= h / w <= 1.65:
        return None, None
    from io import BytesIO
    out = BytesIO()
    im.save(out, "PNG")          # encode once; optimize=True doubles the CPU
    return out.getvalue(), (w, h)


def install(card_id: str, png: bytes):
    """Write the card art at the path the app serves."""
    dest = IMG_DIR / f"{card_id}.png"
    dest.parent.mkdir(parents=True, exist_ok=True)
    # compare the bytes, not the size: different art can be the same length
    if not (dest.exists() and dest.read_bytes() == png):
        dest.write_bytes(png)


def save_manifest(cards: dict):
    """Write the manifest; the format lives here, not in the callers."""
    MANIFEST.write_text(json.dumps(
        {"updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "cards": cards},
        ensure_ascii=False, indent=1), encoding="utf-8")


def targets_from_db(sets=None, cards=None):
    import sqlite3
    con = sqlite3.connect(DB)
    rows = con.execute(
        "select ptcg_id, name, set_name from cards "
        "where ptcg_id like 'de\\_%' escape '\\'").fetchall()
    out = []
    for cid, name, set_name in rows:
        code, _, num = cid[3:].partition("-")
        if sets and code not in sets:
            continue
        if cards and cid not in cards:
            continue
        out.append({"card_id": cid, "code": code, "num": num, "name": name})
    return out


def missing_de_art(cards):
    """Keep cards whose TCGdex record has no German image (our art is English)."""
    by_set = {}
    for c in cards:
        by_set.setdefault(c["code"], []).append(c)
    keep = []
    for code, group in by_set.items():
        meta = set_meta(code)
        have = {norm_num(c.get("localId")) for c in meta.get("cards", [])
                if c.get("image")}
        if not meta:
            keep += group
            continue
        for c in group:
            if norm_num(c["num"]) not in have:
                keep.append(c)
    return keep


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sets", help="comma separated TCGdex set codes")
    ap.add_argument("--cards", help="comma separated card ids (de_ex14-56)")
    ap.add_argument("--all-missing", action="store_true",
                    help="every German card whose TCGdex record has no de art")
    ap.add_argument("--retry-failed", action="store_true",
                    help="retry the ids listed in data/german_art_failed.json")
    ap.add_argument("--allow-photos", action="store_true",
                    help="allow Bisafans photos (angled, with background)")
    ap.add_argument("--force", action="store_true", help="overwrite existing files")
    ap.add_argument("--upgrade-below", type=int, metavar="WIDTH",
                    help="re-fetch cards whose installed art is narrower than WIDTH; "
                         "every source is consulted and a file is replaced only by a "
                         "wider scan")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--skip-newer-than", type=float, metavar="EPOCH",
                    help="treat files modified after EPOCH as already imported")
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()

    manifest = {}
    if MANIFEST.exists():
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8")).get("cards", {})

    card_filter = {s.strip() for s in args.cards.split(",")} if args.cards else None
    if args.retry_failed:
        failed_path = ROOT / "data" / "german_art_failed.json"
        card_filter = set(json.loads(failed_path.read_text(encoding="utf-8")))
    if args.upgrade_below:
        card_filter = {cid for cid, rec in manifest.items()
                       if (rec.get("px") or [0])[0] < args.upgrade_below}
    cards = targets_from_db(
        {s.strip() for s in args.sets.split(",")} if args.sets else None,
        card_filter)
    if args.all_missing:
        cards = missing_de_art(cards)
    if args.limit:
        cards = cards[:args.limit]

    upgrade = bool(args.upgrade_below)
    # upgrading means a bigger German rendition may live on Bisafans, so the
    # third source is always consulted in that mode
    active = [(n, f) for n, f in SOURCES
              if n != "bisafans" or args.allow_photos or upgrade]
    print(f"{len(cards)} cards, sources: {', '.join(n for n, _ in active)}")

    stats = {n: 0 for n, _ in active}
    stats["exists"] = 0
    stats["failed"] = 0
    stats["upgraded"] = 0
    stats["kept"] = 0
    failed = []

    def one(card):
        cid = card["card_id"]
        dest = IMG_DIR / f"{cid}.png"
        current_w = (manifest.get(cid) or {}).get("px", [0])[0]
        if dest.exists() and not args.force and not upgrade:
            if cid in manifest:
                return ("exists", None)
            if args.skip_newer_than and dest.stat().st_mtime > args.skip_newer_than:
                return ("exists", None)
        candidates = []
        for name, fn in active:
            try:
                got = fn(cid, card["code"], card["num"])
            except Exception as exc:  # a broken source must not kill the run
                print(f"  ! {cid} {name}: {exc}")
                got = None
            if not got:
                continue
            png, size = validate(got["bytes"])
            if png is None:
                continue
            record = {"card": cid, "source": name, "url": got["url"],
                      "page": got.get("page", ""), "px": size,
                      "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
            if not upgrade and size[0] >= MIN_WIDTH:
                if args.dry_run:
                    return (name, {"card": cid, "url": got["url"], "size": size})
                install(cid, png)
                return (name, record)
            candidates.append((size[0], png, size, record, name))
        if not candidates:
            return ("failed", cid)
        # widest rendition wins: on an upgrade this is where Bisafans can beat
        # the small PokéWiki file, and nothing is replaced by a narrower scan
        width, png, size, record, name = max(candidates, key=lambda c: c[0])
        if upgrade and width <= current_w:
            return ("kept", None)
        if args.dry_run:
            return (name, {"card": cid, "url": record["url"], "size": size})
        install(cid, png)
        return (name, record)

    with cf.ThreadPoolExecutor(max_workers=args.workers) as ex:
        for i, (status, info) in enumerate(ex.map(one, cards), 1):
            if status == "exists":
                stats["exists"] += 1
            elif status == "kept":
                stats["kept"] += 1
            elif status == "failed":
                stats["failed"] += 1
                failed.append(info)
            else:
                stats[status] = stats.get(status, 0) + 1
                if not args.dry_run and info:
                    manifest[info["card"]] = {k: v for k, v in info.items()
                                              if k != "card"}
            if not args.dry_run and i % 25 == 0:
                save_manifest(manifest)      # survive an interrupted run
            if i % 100 == 0:
                print(f"  {i}/{len(cards)}  " +
                      "  ".join(f"{k}={v}" for k, v in stats.items()), flush=True)

    if not args.dry_run:
        save_manifest(manifest)
    print("\n" + "  ".join(f"{k}={v}" for k, v in stats.items()))
    if failed:
        (ROOT / "data" / "german_art_failed.json").write_text(
            json.dumps(failed, indent=1), encoding="utf-8")
        print(f"{len(failed)} cards had no usable art; see data/german_art_failed.json")


if __name__ == "__main__":
    sys.exit(main())
