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
todo = [(i, m["image"]) for i, m in zip(ids, meta) if m.get("image")]
print(f"{len(ids)} card ids, {len(todo)} with image urls")

with sync_playwright() as p:
    b = p.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])
    page = b.new_page()
    page.goto(PAGE, wait_until="load")
    page.wait_for_function("window.__cardScanner && window.__cardScanner.ready === true", timeout=60000)
    print("browser scanner ready")

    # Pre-register batch hash function (parenthesized IIFE so Playwright
    # registers it instead of auto-invoking it).
    page.evaluate("""(function() {
        window.__buildHashes = async function(entries) {
            const m = window.__cardScanner.matcher;
            const out = {};
            for (const [id, url] of entries) {
                try {
                    const img = new Image();
                    img.crossOrigin = 'anonymous';
                    img.src = url;
                    await img.decode();
                    out[id] = await m.calculateHash(img);
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

    hashes = {}
    if PARTIAL.exists():
        hashes = json.loads(PARTIAL.read_text())
        print(f"resuming: {len(hashes)} hashes from {PARTIAL.name}")
    BATCH = 32
    pending = [e for e in todo if e[0] not in hashes]
    t0 = time.time()
    for start in range(0, len(pending), BATCH):
        batch = pending[start:start + BATCH]
        hashes.update(page.evaluate("entries => window.__buildHashes(entries)", batch))
        done = start + len(batch)
        if done % 2048 < BATCH or done == len(pending):
            PARTIAL.write_text(json.dumps(hashes))  # checkpoint for resume
            rate = done / (time.time() - t0)
            eta = (len(pending) - done) / rate / 60
            print(f"  {done}/{len(pending)} hashes done (rate {rate:.1f}/s, {eta:.0f} min left)", flush=True)

    b.close()

OUT.write_text(json.dumps(hashes))
PARTIAL.unlink(missing_ok=True)
print(f"Done: {len(hashes)} hashes -> {OUT}")
