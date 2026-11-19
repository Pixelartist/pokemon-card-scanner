"""Download booster pack art for the Sets tab from three public sources.

Sources, in priority order. Each one only fills sets that still have no art, so
a better modern scan is never replaced by an older one:

  1. pokesymbols.com         modern English sets, keyed by set name
  2. Bulbagarden Archives    older and Japanese scans, keyed by the set code
                             in the filename ("ADV1 Expansion Pack pack.jpg")
  3. packart.tech            Japanese expansions, paired by release date in
                             their structured catalogue (English names there,
                             so names cannot be matched)

Every image is trimmed to the pack, shrunk to card size and stored as WebP in
data/images/boosters/ (served at /static/boosters/). The mapping lives in
data/cache/booster_art.json as {set code: [public path, ...]} and is written
only by this script; /api/sets reads that manifest.

    python scripts/fetch_booster_art.py
    python scripts/fetch_booster_art.py --source packart     # one source
    python scripts/fetch_booster_art.py --force              # re-download
    python scripts/fetch_booster_art.py --limit 5            # smoke test
"""
import argparse
import collections
import html
import io
import json
import re
import unicodedata
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CATALOG = ROOT / "data" / "cache" / "tcgdex" / "sets_en.json"
IMAGE_DIR = ROOT / "data" / "images" / "boosters"
INDEX = ROOT / "data" / "cache" / "booster_art.json"
PUBLIC_PREFIX = "/static/boosters"
HEADERS = {"User-Agent": "Mozilla/5.0 (pokemon-card-scanner)"}
WORKERS = 8
MAX_HEIGHT = 340
MAX_PER_SET = 5

# Catalogue entries that never shipped booster packs: promos, trainer kits,
# McDonald's collections, gallery/shiny subsets, energy and sample sets.
NOT_BOOSTERS = {
    "miscp", "basep", "wp", "jumbo", "sp", "bog", "np", "dpp", "hgssp", "bwp",
    "smp", "svp", "swshp", "mep", "mee", "si1", "ru1", "exu", "ex5.5", "20th",
    "tk-ex-latia", "tk-ex-latio", "tk-ex-m", "tk-ex-p", "tk-dp-l", "tk-dp-m",
    "tk-hs-g", "tk-hs-r", "tk-bw-e", "tk-bw-z", "tk-xy-sy", "tk-xy-n",
    "tk-xy-b", "tk-xy-w", "tk-xy-latia", "tk-xy-latio", "tk-xy-su", "tk-xy-p",
    "tk-sm-l", "tk-sm-r", "xyp", "xya", "xy0", "rc", "sma", "swsh4.5sv",
    "cel25cc", "swsh9tg", "swsh10tg", "swsh11tg", "swsh12tg", "swsh12.5gg",
    "sve", "mfb", "fut2020", "p-a", "30th-c", "ex5.5",
    "2011bw", "2012bw", "2014xy", "2015xy", "2016xy", "2017sm", "2018sm",
    "2019sm", "2021swsh", "2022swsh", "2023sv", "2024sv",
}

POKESYMBOLS_INDEX = "https://pokesymbols.com/tcg/booster-pack-art"
POKESYMBOLS_BASE = "https://pokesymbols.com"
# Site names that TCGdex spells differently, mapped to the site slug.
POKESYMBOLS_ALIASES = {"base set": "base-set"}

WIKI_API = "https://archives.bulbagarden.net/w/api.php"
WIKI_ROOT = "Category:TCG booster packs"
WIKI_SKIP_WORDS = ("korean", "chinese", "thai", "indonesian", "french", "german",
                   "spanish", "italian", "portuguese", "russian", "back")

PACKART_CATALOG = "https://packart.tech/data/catalog.json"
PACKART_PACKS = "https://packart.tech/data/packs.json"
PACKART_IMAGE = "https://packart.tech/images/web/{}.webp"
# Same-date collisions where the pairing would be wrong.
PACKART_SKIP = {"pcg10"}


# --------------------------------------------------------------------------
# shared helpers
# --------------------------------------------------------------------------

def fetch_bytes(url: str, timeout: int = 60) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=timeout) as resp:
        return resp.read()


def fetch_json(url: str) -> dict:
    return json.loads(fetch_bytes(url).decode("utf-8"))


def save_webp(payload: bytes, dest: Path, max_height: int = MAX_HEIGHT) -> None:
    """Trim the transparent canvas, scale to card size and write a WebP."""
    from PIL import Image

    image = Image.open(io.BytesIO(payload))
    image.load()
    if image.mode in ("RGBA", "LA", "P"):
        image = image.convert("RGBA")
        box = image.getbbox()
        if box:
            image = image.crop(box)
    else:
        image = image.convert("RGB")
    if image.height > max_height:
        width = max(1, round(image.width * max_height / image.height))
        image = image.resize((width, max_height), Image.LANCZOS)
    image.save(dest, "WEBP", quality=82, method=6)


def store(jobs: list, workers: int, force: bool) -> dict:
    """jobs: [(code, [(url, filename), ...])] -> {code: [public path, ...]}.

    Files are downloaded once even when two codes map to the same set art.
    """
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    wanted, owners = {}, collections.defaultdict(list)
    for code, files in jobs:
        for url, name in files:
            wanted.setdefault(name, url)
            owners[name].append(code)

    def run(item):
        name, url = item
        dest = IMAGE_DIR / name
        if force or not dest.exists():
            try:
                save_webp(fetch_bytes(url), dest)
            except Exception as exc:
                print(f"  ! {name}: {exc}")
                return name, None
        return name, f"{PUBLIC_PREFIX}/{name}"

    stored = collections.defaultdict(list)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for i, (name, path) in enumerate(pool.map(run, wanted.items()), 1):
            if path:
                for code in owners[name]:
                    stored[code].append(path)
            if i % 25 == 0 or i == len(wanted):
                print(f"  {i}/{len(wanted)} images")
    return stored


def load_catalogue() -> dict:
    """tcgdex set id -> {name, releaseDate} for every set we list."""
    data = json.loads(CATALOG.read_text(encoding="utf-8")).get("sets", [])
    sets = {}
    for entry in data:
        set_id = (entry.get("id") or "").lower()
        if not set_id:
            continue
        base = set_id[3:] if set_id.startswith("de_") else set_id
        # German rows share the English set and would overwrite its name.
        sets.setdefault(base, entry)
    return sets


def norm(name: str) -> str:
    name = html.unescape(name or "").lower().replace("&", " and ")
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", " ", name).strip()


def tokens_of(name: str) -> tuple:
    return tuple(t for t in norm(name).split() if t not in ("and", "the"))


# --------------------------------------------------------------------------
# source 1: pokesymbols.com
# --------------------------------------------------------------------------

def pokesymbols_catalogue() -> dict:
    """site slug -> {"name": display name, "packs": [file names in order]}."""
    page = fetch_bytes(POKESYMBOLS_INDEX).decode("utf-8", "replace")
    slugs = {}
    for attrs in re.findall(r"<img([^>]+)>", page):
        src = re.search(r'src="([^"]*booster-pack-art/[^"]+)"', attrs)
        if not src:
            continue
        file_match = re.search(r"/([^/]+)-pack-(\d+)\.(png|jpe?g|webp)$", src.group(1))
        if not file_match:
            continue
        slug, order = file_match.group(1), int(file_match.group(2))
        name = file_match.group(1) + "-pack-" + file_match.group(2) + "." + file_match.group(3)
        alt = re.search(r'alt="([^"]*)"', attrs)
        entry = slugs.setdefault(slug, {"name": "", "packs": []})
        if alt and not entry["name"]:
            entry["name"] = html.unescape(alt.group(1))
        entry["packs"].append((order, name))
    for entry in slugs.values():
        entry["packs"] = [name for _, name in sorted(entry["packs"])]
    return slugs


def pokesymbols_matches(names: dict, slugs: dict) -> dict:
    """code -> slug. Site names are short ("Surging Sparks") while TCGdex adds
    the series ("Scarlet & Violet—Surging Sparks"), so exact names win and
    matches where one name ends with the other fill in the rest."""
    by_tokens = {}
    for slug, entry in slugs.items():
        by_tokens.setdefault(tokens_of(entry["name"]), slug)
    keys = [k for k in by_tokens if k]

    matched = {}
    for code, name in names.items():
        want = tokens_of(name)
        if not want:
            continue
        alias = POKESYMBOLS_ALIASES.get(" ".join(want))
        if alias in slugs:
            matched[code] = alias
            continue
        if want in by_tokens:
            matched[code] = by_tokens[want]
            continue
        suffix = [k for k in keys if len(k) < len(want) and want[-len(k):] == k]
        if suffix:
            matched[code] = by_tokens[max(suffix, key=len)]
            continue
        prefixed = [k for k in keys if len(k) > len(want) and k[-len(want):] == want]
        if prefixed:
            matched[code] = by_tokens[min(prefixed, key=len)]
    return matched


def from_pokesymbols(codes: set, catalogue: dict) -> list:
    slugs = pokesymbols_catalogue()
    matched = pokesymbols_matches({c: catalogue[c]["name"] for c in codes}, slugs)
    print(f"pokesymbols: {len(slugs)} sets, {len(matched)} matched")
    jobs = []
    for code, slug in matched.items():
        jobs.append((code, [
            (f"{POKESYMBOLS_BASE}/images/tcg/sets/booster-pack-art/{name}", name.rsplit(".", 1)[0] + ".webp")
            for name in slugs[slug]["packs"][:MAX_PER_SET]
        ]))
    return jobs


# --------------------------------------------------------------------------
# source 2: Bulbagarden Archives
# --------------------------------------------------------------------------

def wiki_api(params: dict) -> dict:
    params = {**params, "format": "json"}
    url = WIKI_API + "?" + urllib.parse.urlencode(params)
    return fetch_json(url)


def wiki_files(category: str = WIKI_ROOT, depth: int = 2, seen: set = None) -> list:
    """Every File: title under a category, following subcategories."""
    seen = seen if seen is not None else set()
    if category in seen or depth < 0:
        return []
    seen.add(category)
    files, subcats, cont = [], [], None
    while True:
        params = {"action": "query", "list": "categorymembers", "cmtitle": category, "cmlimit": "500"}
        if cont:
            params["cmcontinue"] = cont
        data = wiki_api(params)
        for member in data.get("query", {}).get("categorymembers", []):
            if member["ns"] == 14:
                subcats.append(member["title"])
            elif member["ns"] == 6:
                files.append(member["title"][5:])
        cont = data.get("continue", {}).get("cmcontinue")
        if not cont:
            break
    for sub in subcats:
        files += wiki_files(sub, depth - 1, seen)
    return files


def wiki_urls(titles: list) -> dict:
    urls = {}
    for i in range(0, len(titles), 40):
        chunk = titles[i:i + 40]
        data = wiki_api({
            "action": "query",
            "titles": "|".join("File:" + t for t in chunk),
            "prop": "imageinfo",
            "iiprop": "url",
        })
        for page in data.get("query", {}).get("pages", {}).values():
            info = page.get("imageinfo")
            if info:
                urls[page["title"][5:]] = info[0]["url"]
    return urls


def from_bulbagarden(codes: set, catalogue: dict) -> list:
    files = wiki_files()
    by_code = collections.defaultdict(list)
    for name in files:
        token = name.split(" ")[0].strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.]*", token):
            continue
        if any(word in name.lower() for word in WIKI_SKIP_WORDS):
            continue
        by_code[token.lower()].append(name)
    wanted = {code: sorted(by_code[code], key=lambda n: (len(n), n))[:MAX_PER_SET]
              for code in codes if code in by_code}
    print(f"bulbagarden: {len(files)} files, {len(wanted)} matched")
    urls = wiki_urls([name for names in wanted.values() for name in names])
    jobs = []
    for code, names in wanted.items():
        pairs = []
        for index, name in enumerate(names, 1):
            url = urls.get(name)
            if url:
                pairs.append((url, f"wiki_{code}-{index}.webp"))
        jobs.append((code, pairs))
    return jobs


# --------------------------------------------------------------------------
# source 3: packart.tech
# --------------------------------------------------------------------------

def looks_japanese(set_id: str, name: str) -> bool:
    return set_id.upper() == set_id or any(ord(ch) > 0x2E80 for ch in name or "")


def from_packart(codes: set, catalogue: dict) -> list:
    """Pair by release date: their Japanese names are English translations."""
    site = fetch_json(PACKART_CATALOG).get("sets", [])
    by_date = collections.defaultdict(list)
    for item in site:
        if item.get("language") == "ja":
            by_date[item.get("sortDate")].append(item)

    packs = collections.defaultdict(list)
    for pack in fetch_json(PACKART_PACKS).get("packs", []):
        packs[pack["setId"]].append(pack)
    for items in packs.values():
        items.sort(key=lambda p: p.get("displayOrder") or 0)

    jobs = []
    for code in codes:
        entry = catalogue[code]
        if code in PACKART_SKIP or not looks_japanese(code, entry.get("name") or ""):
            continue
        candidates = by_date.get((entry.get("releaseDate") or "")[:10], [])
        if len(candidates) != 1:
            continue
        set_id = candidates[0]["id"]
        pairs = [(PACKART_IMAGE.format(p["fileStem"]), f"pa_{code}-{i}.webp")
                 for i, p in enumerate(packs.get(set_id, [])[:MAX_PER_SET], 1)]
        if pairs:
            jobs.append((code, pairs))
    print(f"packart: {len(site)} sets, {len(jobs)} matched")
    return jobs


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------

SOURCES = (
    ("pokesymbols", from_pokesymbols),
    ("bulbagarden", from_bulbagarden),
    ("packart", from_packart),
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", action="append", choices=[name for name, _ in SOURCES],
                        help="only this source (repeatable); default all")
    parser.add_argument("--limit", type=int, default=0, help="only the first N sets per source")
    parser.add_argument("--force", action="store_true", help="re-download existing files")
    parser.add_argument("--workers", type=int, default=WORKERS)
    args = parser.parse_args()

    chosen = [(name, fn) for name, fn in SOURCES if not args.source or name in args.source]
    catalogue = load_catalogue()
    remaining = {code for code in catalogue if code not in NOT_BOOSTERS}
    print(f"{len(remaining)} sets need artwork")

    index = {}
    for name, fetch_jobs in chosen:
        if not remaining:
            break
        jobs = fetch_jobs(remaining, catalogue)
        if args.limit:
            jobs = jobs[: args.limit]
        stored = store(jobs, args.workers, args.force)
        for code, paths in stored.items():
            if paths:
                index[code] = paths
                remaining.discard(code)

    full_run = not args.limit and len(chosen) == len(SOURCES)
    if not full_run:
        print(f"partial run: {len(index)} sets, manifest left untouched")
        return 0

    INDEX.parent.mkdir(parents=True, exist_ok=True)
    INDEX.write_text(json.dumps(index, indent=2, sort_keys=True), encoding="utf-8")

    keep = {path.rsplit("/", 1)[-1] for paths in index.values() for path in paths}
    pruned = 0
    for path in IMAGE_DIR.glob("*.webp"):
        if path.name not in keep:
            path.unlink()
            pruned += 1

    print(f"{len(index)} sets with art, {len(keep)} images ({pruned} stale files pruned) -> {INDEX}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
