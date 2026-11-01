#!/opt/data/projects/finn-house-scraper/.venv/bin/python
"""Build card hashes in the browser to match the scanner exactly.

The scanner hashes images with Chromium's canvas resampling, which differs
from PIL's — so the index MUST be built with the same browser code.
Uses each card's `image` URL from cards_meta.json (English official sets +
German index + clip images; cards without an image URL are skipped).

Output: src/static/card-hashes.json  (id -> 105-bit hash string)
"""
import json
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

CHROME = "/opt/data/playwright/chromium-1234/chrome-linux64/chrome"
BASE = Path(__file__).resolve().parent.parent
OUT = BASE / "src" / "static" / "card-hashes.json"
PARTIAL = BASE / "data" / "card-hashes.partial.json"
IDS_FILE = BASE / "src" / "static" / "card-ids.json"
META_FILE = BASE / "src" / "static" / "cards_meta.json"
PAGE = "http://localhost:5005/"

ids = json.loads(IDS_FILE.read_text())
meta = json.loads(META_FILE.read_text())

# German set name -> set code mapping (from image URL patterns in meta)
GERMAN_SET_MAP = {
    "Reisegefährten": "de_sv09",
    "Farbenschock": "de_swsh4",
    "Silberne Sturmwinde": "de_swsh12",
    "Zenit der Könige": "de_swsh12.5",
    "Stunde der Wächter": "de_sm2",
    "Sturm Am Firmament": "de_sm7",
    "Welten im Wandel": "de_sm12",
    "Triumph": "de_hgss4",
    "Hoheit der Drachen": "de_bw6",
    "Dunkelnacht": "de_me05",
    "Epische Begegnungen": "de_dp4",
    "EX Rubin & Saphir": "de_ex1",
    "EX Legend Maker": "de_ex12",
    "EX Sandsturm": "de_ex2",
    "Ultimative Sieger": "de_pl3",
}

# Build a lookup: (set, number) -> image_url from meta
# Also normalize German set names to codes for matching card-ids.json
meta_by_set_num = {}
for m in meta:
    s, n = m.get("set", ""), m.get("number", "")
    if s and n:
        meta_by_set_num[f"{s}-{n}"] = m.get("image", "")
        # Also add normalized German set code -> image
        if s in GERMAN_SET_MAP:
            code = GERMAN_SET_MAP[s]
            meta_by_set_num[f"{code}-{n}"] = m.get("image", "")

# Also build card_id -> image for direct matches (some meta entries have card_id-like names)
meta_by_id = {m.get("name", ""): m.get("image", "") for m in meta}

# card id ("set-number") -> its own image URL. meta rows are keyed this way
# everywhere else in the app, so this is the unambiguous lookup.
meta_by_cardid = {}
for m in meta:
    code = str(m.get("set") or "").strip()
    if code and m.get("image"):
        meta_by_cardid.setdefault(f"{code}-{m['number']}", m["image"])

# German images are named after their card id (de_sv01-075.png -> "de_sv01-075"),
# so the filename is the join key. GERMAN_SET_MAP above never fires: cards_meta.json
# stores set *codes* ("sv01"), not the German set names it was keyed by.
for m in meta:
    img = m.get("image") or ""
    if "/german_cards/images/" in img:
        meta_by_set_num.setdefault(img.rsplit("/", 1)[-1].removesuffix(".png"), img)

def get_image_url(card_id: str) -> str | None:
    # Try set-number pattern first (handles German cards with different set names)
    # card_id format is usually "set-number"
    if card_id in meta_by_set_num:
        url = meta_by_set_num[card_id]
        if url:
            return url
    # Direct match in meta (rare, name-based)
    if card_id in meta_by_id:
        url = meta_by_id[card_id]
        if url:
            return url
    # meta's own image for this exact card id. Must come BEFORE the English
    # fallback: thousands of newer cards live under card_images/tcgdex/en/
    # (or pocket/, clip_images/), and falling through to official/en/{id}.png
    # hashed them from a file that does not exist.
    if card_id in meta_by_cardid:
        url = meta_by_cardid[card_id]
        if url:
            return url
    # Fallback: construct English official path
    # Most English cards follow /static/card_images/official/en/{card_id}.png
    if not card_id.startswith("de_") and "-" in card_id:
        return f"/static/card_images/official/en/{card_id}.png"
    return None

todo = []   # built below, once on_disk/probe_image exist


def old_get_image_url(card_id: str) -> str | None:
    """The resolver as it shipped before the meta_by_cardid fix: EN ids always
    fell through to official/en/{id}.png, ignoring meta's own image."""
    if card_id in meta_by_set_num and meta_by_set_num[card_id]:
        return meta_by_set_num[card_id]
    if card_id in meta_by_id and meta_by_id[card_id]:
        return meta_by_id[card_id]
    if not card_id.startswith("de_") and "-" in card_id:
        return f"/static/card_images/official/en/{card_id}.png"
    return None


MOUNTS = {
    "/static/card_images/": BASE / "data" / "images",
    "/static/german_cards/images/": BASE / "data" / "german_cards" / "images",
}

# Image roots the API serves, for ids that have no meta row at all (the TCG
# Pocket sets). Their art is already on disk under data/images/pocket/en-US/
# but nothing pointed the resolver there, so ~2k cards sat unhashable while
# their scans sat in the same data/images tree.
PROBE_ROOTS = [
    ("/static/pocket/en-US/", BASE / "data" / "images" / "pocket" / "en-US"),
    ("/static/card_images/pocket/en-US/", BASE / "data" / "images" / "pocket" / "en-US"),
    ("/static/card_images/tcgdex/en/", BASE / "data" / "images" / "tcgdex" / "en"),
    ("/static/card_images/official/en/", BASE / "data" / "images" / "official" / "en"),
]
PROBE_EXTS = (".webp", ".png", ".jpg")


def probe_image(card_id: str) -> str | None:
    """URL of an existing image file for an id that has no meta row."""
    for prefix, directory in PROBE_ROOTS:
        for ext in PROBE_EXTS:
            p = directory / f"{card_id}{ext}"
            if p.exists() and p.stat().st_size > 1000:
                return prefix + card_id + ext
    return None


def on_disk(url: str | None):
    for prefix, base in MOUNTS.items():
        if url and url.startswith(prefix):
            p = base / url[len(prefix):]
            return p if p.exists() and p.stat().st_size > 1000 else None
    return None


def resolve(card_id: str) -> str | None:
    """Best image URL for an id: meta's, if that file is actually there;
    otherwise any existing scan found under the served image roots."""
    u = get_image_url(card_id)
    if u and on_disk(u):
        return u
    return probe_image(card_id)


todo = [(i, u) for i in ids if (u := resolve(i))]
print(f"{len(ids)} card ids, {len(todo)} with an image file on disk")

with sync_playwright() as p:
    b = p.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])
    page = b.new_page()
    page.set_default_navigation_timeout(120000)
    page.goto(PAGE, wait_until="domcontentloaded")
    page.wait_for_function("window.__cardScanner && window.__cardScanner.ready === true", timeout=120000)
    print("browser scanner ready")

    # Pre-register batch hash function (parenthesized IIFE so Playwright
    # registers it instead of auto-invoking it).
    page.evaluate("""(function() {
        window.__buildHashes = async function(entries) {
            const m = window.__cardScanner.matcher;
            const out = {};
            const withTimeout = (p, id) => Promise.race([
                p,
                new Promise((_, rej) => setTimeout(() => rej(new Error('timeout ' + id)), 5000)),
            ]);
            for (const [id, url] of entries) {
                try {
                    const img = new Image();
                    img.crossOrigin = 'anonymous';
                    img.src = url;
                    // shortcut: 5s per image; a dead server request would hang decode() forever
                    await withTimeout(img.decode(), id);
                    out[id] = await withTimeout(m.calculateHash(img), id);
                } catch (e) {
                    console.warn('Failed to hash', id, e.message);
                }
            }
            return out;
        };
        return true;
    })()""")
    print("hash builder registered")

    # Self-check: build a hash, then re-hash the same image with a fresh
    # Image element — must be identical (guards against stale-page issues).
    check = page.evaluate("""(async () => {
        const tests = [['pop9-1','/static/card_images/official/en/pop9-1.png'],
                       ['xy1-1','/static/card_images/official/en/xy1-1.png'],
                       ['de_hgss2-1','/static/german_cards/images/de_hgss2-1.png']];
        const built = await window.__buildHashes(tests);
        const out = {};
        for (const [id, url] of tests) {
            if (!built[id]) { out[id] = { len: 0, match: false }; continue; }
            const img = new Image();
            img.crossOrigin = 'anonymous';
            img.src = url;
            await img.decode();
            const again = await window.__cardScanner.matcher.calculateHash(img);
            out[id] = { len: built[id].length, match: built[id] === again };
        }
        return out;
    })()""")
    print(f"self-check: {check}")
    if not all(v.get("match") for v in check.values()):
        raise SystemExit("ABORT: browser hash not deterministic between calls")

    # Seed from the committed index, then overlay the partial checkpoint so an
    # interrupted run continues where it stopped. Without the OUT seed every
    # re-run recomputes all ~40k hashes (2.7h) to add a few thousand.
    hashes = {}
    if OUT.exists():
        hashes.update(json.loads(OUT.read_text()))
        print(f"seeding: {len(hashes)} hashes from {OUT.name}")
    if PARTIAL.exists():
        part = json.loads(PARTIAL.read_text())
        if len(part) > len(hashes):        # partial from an older/smaller index
            hashes = part
        else:
            hashes.update(part)
        print(f"overlay: {len(part)} from {PARTIAL.name}")
    BATCH = 32
    # A seeded hash is only trustworthy if the fixed resolver still points at
    # the same file the old one did. The old resolver sent every English id to
    # official/en/{id}.png, so ids whose real scan lives under card_images/
    # tcgdex/en/ were hashed from a missing or wrong file — drop those so they
    # get rebuilt, and drop anything now unresolvable.
    stale = [i for i in list(hashes)
             if (new_u := resolve(i)) is None
             or (u := old_get_image_url(i)) is None or u != new_u]
    for i in stale:
        del hashes[i]
    if stale:
        print(f"purged {len(stale)} stale hashes (wrong/missing source image)")
    pending = [e for e in todo if e[0] not in hashes]
    t0 = time.time()
    for start in range(0, len(pending), BATCH):
        batch = pending[start:start + BATCH]
        print(f"Processing batch {start}/{len(pending)}: {batch[0][0]} to {batch[-1][0]}", flush=True)
        hashes.update(page.evaluate("entries => window.__buildHashes(entries)", batch))
        done = start + len(batch)
        if done % 256 < BATCH or done == len(pending):
            PARTIAL.write_text(json.dumps(hashes))  # checkpoint for resume
            rate = done / (time.time() - t0)
            eta = (len(pending) - done) / rate / 60
            print(f"  {done}/{len(pending)} hashes done (rate {rate:.1f}/s, {eta:.0f} min left)", flush=True)

    b.close()

bad = [i for i, h in hashes.items() if not h]
for i in bad:
    del hashes[i]
if bad:
    print(f"dropped {len(bad)} ids whose image failed to load (not written to index)")

OUT.write_text(json.dumps(hashes))
PARTIAL.unlink(missing_ok=True)
print(f"Done: {len(hashes)} hashes -> {OUT}")
