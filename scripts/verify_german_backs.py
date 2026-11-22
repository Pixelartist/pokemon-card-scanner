#!/usr/bin/env python3
"""Fingerprint every installed German card image against the Bisafans card back.

Bisafans answers HTTP 200 with a generic card back for files it does not have,
and that back was installed as artwork for some cards before the importer
learned to reject it.  This walks the whole installed store and compares each
image with the back itself, so a back that arrived any other way (older import
runs, relinks, manual copies) is caught too.  An earlier check compared each
card with a re-fetch of its own manifest URL, which for those cards was the
back, so it reported them as fine.

Entries flagged "reconstructed": true were rebuilt from the installed file
after the fact: the legacy art predates the manifest, so their px is the file's
own size, at is its mtime, and there is no url or page.  Generated, not
observed; recorded entries carry no flag.  The report counts them and flags any
whose px no longer matches the installed file.  A repair records a real fetch,
so --apply drops the flag.

    python scripts/verify_german_backs.py                 # report only
    python scripts/verify_german_backs.py --all-de        # every de_*.png on disk
    python scripts/verify_german_backs.py --apply         # replace the backs
    python scripts/verify_german_backs.py --limit 50      # sample

Every card found showing the back is re-sourced from TCGdex / PokéWiki (and from
the pre-upgrade record in the manifest backups), installed, and recorded in
data/german_art_sources.json.  Cards with no German alternative are left alone
and listed in data/german_back_audit.json.
"""
import argparse
import concurrent.futures as cf
import hashlib
import io
import json
import os
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

import fetch_german_art as F                                   # noqa: E402
from services.quickdraw_matcher import (FINE_DIM, _fingerprint,  # noqa: E402
                                        _rgba, _weighted_sq_rows)

MANIFEST = F.MANIFEST
BACKUPS = [ROOT / "data" / "german_art_sources.pre-upgrade.json"]
REPORT = ROOT / "data" / "german_back_audit.json"
IMG_DIR = F.IMG_DIR

BACK_URL = F.BACK_URL  # the site's generic card back
BACK_TOL = F.BACK_TOL  # at or below this the image *is* the back
SUSPECT_TOL = 0.12     # worth a human look, but not replaced
GOOD_TOL = 0.20        # a replacement must be at least this far from the back


def fingerprint(data: bytes):
    return _fingerprint(_rgba(Image.open(io.BytesIO(data))))[1]


def distance(a, b) -> float:
    return float(_weighted_sq_rows(np.stack([a]), np.stack([b]), FINE_DIM)[0])


def md5(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def installed_paths(all_de: bool) -> dict:
    """{card_id: path} for the manifest, or for every de_ image on disk."""
    if all_de:
        return {p.stem: p for p in sorted(IMG_DIR.glob("de_*.png"))}
    cards = json.loads(MANIFEST.read_text(encoding="utf-8"))["cards"]
    return {cid: IMG_DIR / f"{cid}.png" for cid in cards}


def scan(paths: dict, back, back_md5: str, workers: int) -> dict:
    out = {}

    def one(item):
        cid, path = item
        if not path.exists():
            return cid, {"error": "missing"}
        raw = path.read_bytes()
        try:
            with Image.open(io.BytesIO(raw)) as im:
                px = list(im.size)
            fp = fingerprint(raw)
        except Exception as exc:                       # noqa: BLE001
            return cid, {"error": str(exc)[:120]}
        digest = md5(raw)
        return cid, {"d": distance(fp, back), "bytes": len(raw), "px": px,
                     "md5": digest, "same_png": digest == back_md5}

    with cf.ThreadPoolExecutor(max_workers=workers) as pool:
        for i, (cid, rec) in enumerate(pool.map(one, paths.items()), 1):
            out[cid] = rec
            if i % 1000 == 0:
                print(f"  fingerprinted {i}/{len(paths)}", flush=True)
    return out


def source_tcgdex_en(cid: str, code: str, num: str):
    """Last resort: the English scan beats showing the card back."""
    got = F.tcgdex_image(F.TGDEX_EN, code, num)
    return {**got, "page": ""} if got else None


def replacement(cid: str, backups: list):
    """Every non-Bisafans scan for this card, German sources first.

    PokéWiki's category and title lookups, and the pre-upgrade backup, often
    resolve to the same file, so candidates are deduped by url: the first hit
    keeps its source label and the duplicates are not validated or fingerprinted
    again.  The backup is skipped before it is fetched when its url is known
    already.
    """
    code, _, num = cid[3:].partition("-")
    found, seen = [], set()

    def add(cand):
        if cand and cand["url"] not in seen:
            seen.add(cand["url"])
            found.append(cand)

    for name, fetch in ((n, f) for n, f in F.SOURCES if n != "bisafans"):
        got = fetch(cid, code, num)
        if got:
            add({"source": name, "bytes": got["bytes"], "url": got["url"],
                 "page": got.get("page", "")})
    for backup in backups:
        rec = backup.get(cid) or {}
        if rec.get("url") and rec.get("source") != "bisafans":
            if rec["url"] in seen:
                continue
            r = F.get(rec["url"])
            if r is not None:
                add({"source": rec["source"], "bytes": r.content,
                     "url": rec["url"], "page": rec.get("page", "")})
    got = source_tcgdex_en(cid, code, num)
    if got:
        add({"source": "tcgdex-en", "bytes": got["bytes"],
             "url": got["url"], "page": ""})
    return found


def best_candidate(cid: str, backups: list, back):
    """Widest candidate that is not the back; German beats English."""
    german, english = [], []
    for cand in replacement(cid, backups):
        png, size = F.validate(cand["bytes"])
        if png is None:
            continue
        dist = distance(fingerprint(png), back)
        if dist <= GOOD_TOL:
            continue                    # that candidate is a back too
        cand = {**cand, "png": png, "px": size, "d": dist}
        (english if cand["source"] == "tcgdex-en" else german).append(cand)
    pool = german or english
    return max(pool, key=lambda c: c["px"][0]) if pool else None


def fix(cid: str, backups: list, back, fixed: dict):
    best = best_candidate(cid, backups, back)
    if best is None:
        return False
    F.install(cid, best["png"])
    # an observed fetch replaces the entry, so no "reconstructed" flag is written
    fixed[cid] = {"source": best["source"], "url": best["url"],
                  "page": best["page"], "px": best["px"], "d": best["d"]}
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--all-de", action="store_true",
                    help="scan every de_*.png, not only the imported ones")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--from-report", action="store_true",
                    help="repair the cards listed in an earlier report, no rescan")
    ap.add_argument("--probe", action="store_true",
                    help="show what each back would be replaced with, no writes")
    args = ap.parse_args()

    paths = installed_paths(args.all_de)
    if args.limit:
        paths = dict(list(paths.items())[:args.limit])
    print(f"images to fingerprint: {len(paths)} "
          f"({'every de_ image' if args.all_de else 'imported German art'})")

    back_resp = F.get(BACK_URL)
    if back_resp is None:
        raise SystemExit("could not download the Bisafans card back")
    back = fingerprint(back_resp.content)
    back_png, _ = F.validate(back_resp.content)
    back_md5 = md5(back_png)
    print(f"card back: {len(back_resp.content)} B, normalised md5 {back_md5}")

    if args.from_report and REPORT.exists():
        prior = json.loads(REPORT.read_text(encoding="utf-8"))
        results = {b["card"]: {"d": b["d"], "bytes": b["bytes"],
                                "md5": "", "same_png": False}
                   for b in prior.get("backs", [])}
        print(f"reusing {len(results)} backs from the previous report")
    else:
        results = scan(paths, back, back_md5, args.workers)
    errs = {c: r for c, r in results.items() if "error" in r}
    ok = {c: r["d"] for c, r in results.items() if "d" in r}
    backs = sorted([c for c, d in ok.items() if d <= BACK_TOL])
    suspects = sorted([c for c, d in ok.items()
                       if BACK_TOL < d <= SUSPECT_TOL], key=lambda c: ok[c])

    ds = np.array(sorted(ok.values())) if ok else np.array([0.0])
    print(f"\nchecked {len(ok)} | unreadable {len(errs)}")
    print(f"distance to back: min {ds.min():.3f}  p01 {np.percentile(ds, 1):.3f}  "
          f"p50 {np.percentile(ds, 50):.3f}  max {ds.max():.3f}")
    cards = json.loads(MANIFEST.read_text(encoding="utf-8"))["cards"]
    m_checked = [c for c in ok if c in cards]
    m_backs = [c for c in backs if c in cards]
    recon = [c for c in m_checked if cards[c].get("reconstructed") is True]
    # a reconstructed px is the file's own size, so it must still match the file
    # (--from-report reuses old results, which carry no px)
    comparable = [c for c in recon if results[c].get("px")]
    drift = [c for c in comparable
             if list(results[c]["px"]) != list(cards[c].get("px") or [])]
    print(f"imported German art: {len(m_checked)} images, {len(m_backs)} are backs, "
          f"{len(recon)} reconstructed provenance")
    print(f"  reconstructed px: {len(comparable) - len(drift)} match the installed "
          f"file, {len(drift)} differ")
    for c in drift[:10]:
        print(f"   px drift {c} manifest {cards[c].get('px')} "
              f"file {results[c]['px']}")
    print(f"IS THE CARD BACK (<= {BACK_TOL}): {len(backs)}")
    print(f"suspect ({BACK_TOL}-{SUSPECT_TOL}): {len(suspects)}")
    exact = [c for c in backs if results[c]["same_png"]]
    print(f"  of the backs, byte-identical to the normalised back: {len(exact)}")
    for c in backs[:20]:
        print(f"   {c} d={ok[c]:.4f} {results[c]['bytes']} B")
    for c in suspects[:10]:
        print(f"   suspect {c} d={ok[c]:.3f}")

    backups = []
    for p in BACKUPS:
        if p.exists():
            backups.append(json.loads(p.read_text(encoding="utf-8")).get("cards", {}))

    fixed, stuck = {}, []
    if args.probe and backs:
        for cid in backs:
            cands = replacement(cid, backups)
            print(f"\n{cid}: {len(cands)} candidates")
            for cand in cands:
                png, size = F.validate(cand["bytes"])
                d = (distance(fingerprint(png), back) if png else -1)
                print(f"   {cand['source']:<15} {str(size):<14} d={d:.3f} "
                      f"{cand['url'][-60:]}")
    if args.apply and backs:
        print(f"\nre-sourcing {len(backs)} cards ...")
        for i, cid in enumerate(backs, 1):
            okfix = fix(cid, backups, back, fixed)
            if not okfix:
                stuck.append(cid)
            if i % 25 == 0:
                print(f"  {i}/{len(backs)} fixed={len(fixed)} stuck={len(stuck)}",
                      flush=True)
        if fixed:
            prior = json.loads(MANIFEST.read_text(encoding="utf-8"))
            manifest = {**prior.get("cards", {})}
            for cid, rec in fixed.items():
                manifest[cid] = {"source": rec["source"], "url": rec["url"],
                                 "page": rec["page"], "px": rec["px"],
                                 "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
            F.save_manifest(manifest)
            print(f"manifest updated for {len(fixed)} cards")

    REPORT.write_text(json.dumps({
        "checked": len(ok),
        "unreadable": errs,
        "back_threshold": BACK_TOL,
        "backs": [{"card": c, "d": ok[c], "bytes": results[c]["bytes"]}
                  for c in backs],
        "suspects": [{"card": c, "d": ok[c]} for c in suspects],
        "fixed": fixed,
        "unfixable": stuck,
        "scope": "all-de" if args.all_de else "manifest",
        "manifest_checked": len(m_checked),
        "manifest_backs": m_backs,
        "manifest_reconstructed": len(recon),
        "reconstructed_px_mismatch": drift,
        "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }, indent=1), encoding="utf-8")
    print(f"\nreport: {REPORT.relative_to(ROOT)}")
    if args.apply:
        print(f"fixed {len(fixed)} | unfixable {len(stuck)}")


if __name__ == "__main__":
    main()
