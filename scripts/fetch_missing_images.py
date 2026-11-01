#!/usr/bin/env python3
"""Download the card images that cards_meta.json references but the disk lacks.

Driven by src/static/cards_meta.json (the browser index's source of truth):
every meta entry whose `image` does not resolve under data/images/ or
data/german_cards/images/ is fetched from TCGdex assets and written to the
exact path the mount already serves, so no other script needs to change.

Usage:
  python scripts/fetch_missing_images.py            # all missing
  python scripts/fetch_missing_images.py --sets sv02,sv04
  python scripts/fetch_missing_images.py --dry-run
"""
import argparse, asyncio, json, sys
from collections import defaultdict
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
META = ROOT / "src" / "static" / "cards_meta.json"
MOUNTS = {
    "/static/card_images/": ROOT / "data" / "images",
    "/static/german_cards/images/": ROOT / "data" / "german_cards" / "images",
}
API = "https://api.tcgdex.net/v2"
CONCURRENCY = 12


def real_path(url: str):
    for prefix, base in MOUNTS.items():
        if url.startswith(prefix):
            return base / url[len(prefix):]
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sets", help="comma-separated set codes (default: all missing)")
    ap.add_argument("--lang", default="en", choices=["en", "de"])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, help="cap downloads (smoke test)")
    ap.add_argument("--fill-urls", action="store_true",
                    help="give entries with an empty `image` the conventional TCGdex path")
    args = ap.parse_args()

    meta = json.loads(META.read_text())
    want = {s.strip() for s in args.sets.split(",")} if args.sets else None

    missing = defaultdict(list)  # set code -> [meta entry]
    relist = []                   # entries whose empty `image` we can synthesise
    for m in meta:
        code = str(m.get("set") or "").strip()
        if not code or (want and code not in want):
            continue
        img = m.get("image") or ""
        if not img and args.fill_urls:
            # meta rows for these sets were written without an image URL; the
            # convention the rest of the index follows is deterministic, so
            # reconstruct it and let the download decide if it exists.
            m["image"] = f"/static/card_images/tcgdex/{args.lang}/{code}-{m['number']}.png"
            relist.append(m)
            img = m["image"]
        p = real_path(img) if img else None
        if p is not None and p.exists() and p.stat().st_size > 1000:
            continue
        missing[code].append(m)

    total = sum(len(v) for v in missing.values())
    print(f"{total} missing images across {len(missing)} sets")
    if args.dry_run:
        for code, rows in sorted(missing.items(), key=lambda kv: -len(kv[1]))[:40]:
            print(f"  {code:<10} {len(rows)}")
        return

    async def run():
        limits = httpx.Limits(max_connections=CONCURRENCY)
        async with httpx.AsyncClient(limits=limits, timeout=30.0,
                                     headers={"User-Agent": "card-scanner-index/1.0"}) as c:

            async def get(url, tries=3):
                for a in range(tries):
                    try:
                        r = await c.get(url)
                        if r.status_code == 200 and len(r.content) > 1000:
                            return r.content
                        if r.status_code in (403, 404):
                            return None          # permanent
                    except httpx.HTTPError:
                        pass
                    await asyncio.sleep(1.5 * (a + 1))
                return None

            sem = asyncio.Semaphore(CONCURRENCY)
            stats = {"ok": 0, "fail": 0, "skip": 0, "written": []}

            async def one(m, setserie):
                code, num = str(m["set"]), str(m["number"])
                p = real_path(m.get("image") or "")
                if p is None:
                    stats["fail"] += 1
                    return
                if p.exists() and p.stat().st_size > 1000:
                    stats["skip"] += 1
                    return
                serie = setserie.get(code, code)
                # meta has a few uppercase set codes (SV10) while TCGdex uses
                # lowercase (sv10); the on-disk filename keeps meta's spelling.
                cands = [(serie, code)]
                if code != code.lower():
                    s2 = setserie.get(code.lower(), code.lower())
                    cands.append((s2, code.lower()))
                async with sem:
                    data = None
                    for ser, cd in cands:
                        for ext in ("high.png", "normal.png"):
                            data = await get(f"https://assets.tcgdex.net/{args.lang}/{ser}/{cd}/{num}/{ext}")
                            if data is not None:
                                break
                        if data is not None:
                            break
                    if data is None:
                        stats["fail"] += 1
                        return
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_bytes(data)
                    stats["ok"] += 1
                    stats["written"].append(p)

            # serie segment of the asset URL comes from the set detail
            # (TCGdex returns serie as an object: {"id": "sv", ...})
            setserie = {}
            async def lookup(cd):
                d = await c.get(f"{API}/{args.lang}/sets/{cd}")
                if d.status_code != 200:
                    return cd
                s = d.json().get("serie")
                return s.get("id") if isinstance(s, dict) else (s or cd)

            for code in missing:
                setserie[code] = await lookup(code)
                # meta has a few uppercase codes (SV10) TCGdex stores lowercase;
                # the asset URL needs the *lowercase* code with its real serie.
                if code != code.lower():
                    setserie.setdefault(code.lower(), await lookup(code.lower()))

            for code, rows in sorted(missing.items()):
                if args.limit:
                    rows = rows[:max(0, args.limit - stats["ok"] - stats["fail"])]
                    if not rows:
                        break
                await asyncio.gather(*(one(m, setserie) for m in rows))
                print(f"  {code:<10} {len(rows):>4}  ok={stats['ok']} fail={stats['fail']}", flush=True)

            print(f"\ndownloaded={stats['ok']}  failed={stats['fail']}  skipped(existing)={stats['skip']}")
            return stats

    stats = asyncio.run(run())
    if stats and relist:
        done = {id(m) for m in relist
                if (rp := real_path(m["image"])) is not None and rp.exists()}
        for m in relist:                       # keep `image` empty if nothing landed
            if id(m) not in done:
                m["image"] = ""
        if done:
            META.write_text(json.dumps(meta, ensure_ascii=False))
            print(f"re-populated `image` on {len(done)} meta rows")


if __name__ == "__main__":
    main()
