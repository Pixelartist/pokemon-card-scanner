"""Turn "Not this" corrections into pinned confuser pairs for the matcher.

Every row in ``misreads`` is one user correction: the crop of the card they
actually held, the id the scanner guessed, and the id they filed instead. The
matcher keeps confusing a few kinds of prints, and the corrections say which:

``alias``
    The guessed id is not a catalogued print at all — the cloud reader answers
    ``ex14:56`` where the index has ``ex14-56`` Meditite, or a near-miss queue
    key carries its score (``unknown:de_sv04-212:30``).
``twin``
    Two real prints sharing one illustration, e.g. Kirlia ``me01-059`` and
    ``me02.5-088``. Their artwork is identical (NCC 0.998), so no embedding or
    template can separate them; only the user's correction can say which print
    to prefer.
``confuser``
    Two real prints with different artwork. Here the stored crop decides
    whether the correction is trustworthy: the crop must look more like the
    corrected print than like the guess.

``build_confuser_index`` collapses the rows to one pair per
``(wrong_id, correct_id)`` and writes ``data/confuser_pairs.json``.
``CLIPCardMatcher`` reads that file on every scan and promotes the corrected
print when its guess is a pinned wrong id, so the same mistake is not made
twice.

Run it after correcting cards in the review queue::

    cd src && python -m services.misread_training
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Honours DATA_DIR like api.main and api.models.database do, so the rebuild
# reads the same database the app serves and writes its pair file beside it.
DATA_DIR = Path(os.environ.get("DATA_DIR", Path(__file__).resolve().parent.parent.parent / "data"))
DEFAULT_DB = DATA_DIR / "pokemon_cards.db"
CONFUSER_PATH = DATA_DIR / "confuser_pairs.json"

# Grayscale template shape used by the matcher's own template index.
_CMP_SIZE = (96, 132)
# Identical-artwork threshold. Measured: me01-059 vs me02.5-088 Kirlia = 0.998.
TWIN_NCC = 0.97
# A different-artwork pair is only pinned when the crop backs the correction.
SUPPORT_MIN = 0.30
SUPPORT_MARGIN = 0.02
# Score gap the corrected print may sit below the guess and still be promoted.
TWIN_MARGIN = 0.05
CONFUSER_MARGIN = 0.02

_KIND_PRIORITY = {"twin": 3, "confuser": 2, "alias": 1}


# --------------------------------------------------------------------- helpers


def canonical_card_id(card_id: str) -> str:
    """Normalise the id shapes the reader and review queue produce.

    ``unknown:de_sv04-212:30`` -> ``de_sv04-212`` (queue key with score tag)
    ``de_sv04-212:30``         -> ``de_sv04-212`` (score tag on a real id)
    ``ex14:56``                -> ``ex14-56``    (set:number instead of set-number)
    """
    s = (card_id or "").strip()
    if s.lower().startswith("unknown:"):
        s = s.split(":", 1)[1]
    parts = [p for p in s.split(":") if p]
    if len(parts) >= 3:
        parts = parts[:2]
    if len(parts) == 2:
        head, tail = parts
        if "-" in head and tail.isdigit() and len(tail) <= 2:
            parts = [head]  # trailing near-miss score, not part of the id
    return "-".join(parts)


def artwork_path(card_id: str) -> Optional[Path]:
    """Local artwork for a card id, mirroring resolve_card_image_url's search."""
    if not card_id:
        return None
    cid = card_id.replace("/", "_").replace("!", "")
    images = DATA_DIR / "images"
    for rel in (f"tcgdex/en/{cid}.png", f"official/en/{cid}.png",
                f"pkmncards/{cid}.png", f"german_cards/images/de_{cid}.png"):
        p = images / rel
        if p.is_file():
            return p
    for sub in ("tcgdex/en", "official/en", "pkmncards"):
        d = images / sub
        if not d.is_dir():
            continue
        try:
            for entry in d.iterdir():
                if entry.name.lower() == f"{cid.lower()}.png":
                    return entry
        except OSError:
            pass
    return None


def _gray(path: Path):
    import numpy as np
    from PIL import Image

    with Image.open(path) as im:
        return np.asarray(im.convert("L").resize(_CMP_SIZE), dtype=np.float32)


def _ncc(a_path: Path, b_path: Path, shift: int = 6) -> Optional[float]:
    """Best normalised cross-correlation over small shifts (~±10% of the card).

    Photos are never pixel-aligned with the artwork, so a plain resize compare
    underrates them; the shift search is what makes the crop evidence usable.
    """
    try:
        import numpy as np

        a = _gray(a_path)
        b = _gray(b_path)
    except Exception as exc:  # missing/broken image, no PIL, ...
        logger.debug("ncc failed for %s vs %s: %s", a_path, b_path, exc)
        return None
    a = (a - a.mean()) / (np.linalg.norm(a - a.mean()) + 1e-9)
    best = -1.0
    for dy in range(-shift, shift + 1, 2):
        for dx in range(-shift, shift + 1, 2):
            b_shift = np.roll(np.roll(b, dy, axis=0), dx, axis=1)
            b_shift = (b_shift - b_shift.mean()) / (np.linalg.norm(b_shift - b_shift.mean()) + 1e-9)
            best = max(best, float((a * b_shift).sum()))
    return round(best, 4)


# ----------------------------------------------------------------- corrections


def load_corrections(db_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """One record per ``(wrong_id, correct_id)`` with every crop that backs it.

    The Meditite pair was corrected five times; training on five copies of the
    same mistake would weight it five times, so rows are collapsed here and
    only counted (``occurrences``).
    """
    db = Path(db_path or DEFAULT_DB)
    if not db.is_file():
        return []
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT * FROM misreads ORDER BY created_at ASC, id ASC"
        ).fetchall()
    except sqlite3.Error as exc:
        logger.warning("misreads unreadable in %s: %s", db, exc)
        return []
    finally:
        conn.close()

    # A correction the admin rejected is not ground truth: it must not pin a
    # confuser pair. Older databases have no review_status column at all.
    has_status = bool(rows) and "review_status" in rows[0].keys()

    groups: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for r in rows:
        if has_status and (r["review_status"] or "pending") == "rejected":
            continue
        wrong = (r["wrong_id"] or "").strip()
        correct = (r["correct_id"] or "").strip()
        if not wrong or not correct or wrong == correct:
            continue
        g = groups.setdefault((wrong, correct), {
            "wrong_id": wrong,
            "wrong_name": "",
            "correct_id": correct,
            "correct_name": "",
            "occurrences": 0,
            "score": 0.0,
            "source": "",
            "crops": [],
            "last_seen": "",
        })
        g["occurrences"] += 1
        g["wrong_name"] = r["wrong_name"] or g["wrong_name"]
        g["correct_name"] = r["correct_name"] or g["correct_name"]
        g["source"] = r["source"] or g["source"]
        g["last_seen"] = r["created_at"] or g["last_seen"]
        try:
            g["score"] = max(g["score"], float(r["score"] or 0))
        except (TypeError, ValueError):
            pass
        crop = (r["crop_image_path"] or "").strip()
        if crop and crop not in g["crops"] and (DATA_DIR / crop).is_file():
            g["crops"].append(crop)
    return list(groups.values())


def _crop_support(crops: List[str], wrong_art: Path, correct_art: Path):
    """Does the stored photo look more like the corrected print than the guess?"""
    if not crops:
        return None, False
    vs_correct: List[float] = []
    vs_wrong: List[float] = []
    for rel in crops:
        crop = DATA_DIR / rel
        c = _ncc(crop, correct_art)
        w = _ncc(crop, wrong_art)
        if c is None or w is None:
            continue
        vs_correct.append(c)
        vs_wrong.append(w)
    if not vs_correct:
        return None, False
    mean_c = sum(vs_correct) / len(vs_correct)
    mean_w = sum(vs_wrong) / len(vs_wrong)
    support = {
        "crop_vs_correct": round(mean_c, 4),
        "crop_vs_wrong": round(mean_w, 4),
        "crops_used": len(vs_correct),
    }
    pinned = mean_c >= SUPPORT_MIN and (mean_c - mean_w) >= SUPPORT_MARGIN
    return support, pinned


def _classify(correction: Dict[str, Any]) -> Dict[str, Any]:
    canon = canonical_card_id(correction["wrong_id"])
    correct_id = correction["correct_id"]
    wrong_art = artwork_path(canon)
    correct_art = artwork_path(correct_id)
    kind = "alias"
    art_sim: Optional[float] = None
    support: Optional[Dict[str, Any]] = None
    pinned = True

    if canon != correct_id and wrong_art and correct_art:
        art_sim = _ncc(wrong_art, correct_art)
        if art_sim is not None and art_sim >= TWIN_NCC:
            kind = "twin"
            support, _ = _crop_support(correction["crops"], wrong_art, correct_art)
        else:
            kind = "confuser"
            support, pinned = _crop_support(correction["crops"], wrong_art, correct_art)

    return {
        "wrong_id": correction["wrong_id"],
        "wrong_name": correction["wrong_name"],
        "correct_id": correct_id,
        "correct_name": correction["correct_name"],
        "kind": kind,
        "pinned": pinned,
        "margin": TWIN_MARGIN if kind == "twin" else CONFUSER_MARGIN,
        "occurrences": correction["occurrences"],
        "score": round(correction["score"], 4),
        "source": correction["source"],
        "last_seen": correction["last_seen"],
        "crops": correction["crops"],
        "artwork_similarity": art_sim,
        "support": support,
        "reason": _reason(kind, pinned, art_sim, support),
    }


def _reason(kind: str, pinned: bool, art_sim: Optional[float],
            support: Optional[Dict[str, Any]]) -> str:
    if kind == "alias":
        return "guessed id is not a catalogued print; correction normalises it"
    if kind == "twin":
        return f"identical artwork (ncc {art_sim:.3f}); pinned by user correction"
    if not pinned:
        return "crop does not back the correction; kept as alternative only"
    return (f"crop matches corrected print ({support['crop_vs_correct']:.2f}) "
            f"better than guess ({support['crop_vs_wrong']:.2f})")


def build_confuser_index(db_path: Optional[Path] = None,
                         out_path: Optional[Path] = None) -> Dict[str, Any]:
    """Collapse corrections into ``confuser_pairs.json``; returns the document."""
    out = Path(out_path or CONFUSER_PATH)
    corrections = load_corrections(db_path)
    pairs = [_classify(c) for c in corrections]
    pairs.sort(key=lambda p: (-_KIND_PRIORITY.get(p["kind"], 0), -p["occurrences"]))
    doc = {
        "version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "pairs": pairs,
        "counts": {
            "pairs": len(pairs),
            "pinned": sum(1 for p in pairs if p["pinned"]),
            "correction_rows": sum(p["occurrences"] for p in pairs),
            "by_kind": {k: sum(1 for p in pairs if p["kind"] == k)
                        for k in ("twin", "confuser", "alias")},
        },
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(out)
    logger.info("confuser pairs: %d from %d rows -> %s",
                len(pairs), doc["counts"]["correction_rows"], out)
    return doc


# ------------------------------------------------------------ rebuild scheduling

# An admin vetting a queue works through corrections in bursts: confirm, reject,
# re-point, next row. Each edit used to start its own thread, so a session of
# twenty edits ran twenty rebuilds at once, all reading the table and writing
# the same file. Now one worker owns the rebuild: the first request starts it
# after a short quiet period that folds a burst into a single run, and anything
# that arrives while it works queues exactly one follow-up run.
REBUILD_DEBOUNCE_SECONDS = 2.0

_REBUILD_LOCK = threading.Lock()
_REBUILD_RUNNING = False
_REBUILD_PENDING = False


def schedule_confuser_rebuild() -> None:
    """Queue a rebuild; every request until the run starts folds into it."""
    global _REBUILD_RUNNING, _REBUILD_PENDING
    with _REBUILD_LOCK:
        _REBUILD_PENDING = True
        if _REBUILD_RUNNING:
            return
        _REBUILD_RUNNING = True
    threading.Thread(target=_rebuild_loop, daemon=True, name="confuser-rebuild").start()


def _rebuild_loop() -> None:
    """One quiet period per run; an edit during a run queues one follow-up."""
    global _REBUILD_RUNNING, _REBUILD_PENDING
    while True:
        time.sleep(REBUILD_DEBOUNCE_SECONDS)
        with _REBUILD_LOCK:
            _REBUILD_PENDING = False          # this run covers everything so far
        try:
            build_confuser_index()
        except Exception as exc:  # a bad rebuild must not stop later ones
            logger.warning("confuser rebuild failed: %s", exc)
        with _REBUILD_LOCK:
            if not _REBUILD_PENDING:
                _REBUILD_RUNNING = False
                return


def wait_for_confuser_rebuild(timeout: float = 60.0) -> bool:
    """Block until no rebuild is queued or running (tests and scripts)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with _REBUILD_LOCK:
            if not _REBUILD_RUNNING:
                return True
        time.sleep(0.05)
    return False


# ---------------------------------------------------------------- runtime view

_CACHE: Dict[str, Any] = {"key": None, "index": None}


class ConfuserIndex:
    """Lookup used on every scan: which guess should have been which print."""

    def __init__(self, data: Dict[str, Any]):
        self.generated_at = data.get("generated_at")
        self.counts = data.get("counts", {})
        self.pairs = [p for p in data.get("pairs", [])
                      if p.get("pinned") and p.get("correct_id")]
        self._by_wrong: Dict[str, List[Dict[str, Any]]] = {}
        for p in sorted(self.pairs,
                        key=lambda x: _KIND_PRIORITY.get(x.get("kind"), 0),
                        reverse=True):
            self._by_wrong.setdefault(p["wrong_id"], []).append(p)
        self._by_canonical: Dict[str, Dict[str, Any]] = {}
        for p in self.pairs:
            key = canonical_card_id(p["wrong_id"])
            self._by_canonical.setdefault(key, p)

    def __bool__(self) -> bool:
        return bool(self.pairs)

    def pin(self, card_id: str) -> Optional[Dict[str, Any]]:
        """The pinned pair whose guess is ``card_id``, if any."""
        if not card_id:
            return None
        for p in self._by_wrong.get(card_id, ()):
            if p["correct_id"] != card_id:
                return p
        p = self._by_canonical.get(canonical_card_id(card_id))
        if p and p["correct_id"] != card_id:
            return p
        return None

    def twin(self, card_id: str) -> Optional[Dict[str, Any]]:
        """Only identical-artwork pairs: safe to override a template match."""
        p = self.pin(card_id)
        return p if p and p["kind"] == "twin" else None


def load_confusers(path: Optional[Path] = None) -> Optional[ConfuserIndex]:
    """Cached read of the pair file; reloads when the builder rewrites it."""
    p = Path(path or CONFUSER_PATH)
    try:
        st = p.stat()
    except OSError:
        _CACHE.update(key=None, index=None)
        return None
    key = (str(p), st.st_mtime_ns, st.st_size)
    if _CACHE["key"] == key:
        return _CACHE["index"]
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("confuser pairs unreadable (%s): %s", p, exc)
        data = None
    index = ConfuserIndex(data) if data else None
    _CACHE.update(key=key, index=index)
    return index


# ------------------------------------------------------------------------- cli


def _main() -> int:
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=None, help="scanner database (default: data/pokemon_cards.db)")
    ap.add_argument("--out", default=None, help="pair index path (default: data/confuser_pairs.json)")
    ap.add_argument("--show", action="store_true", help="print every pair")
    args = ap.parse_args()

    doc = build_confuser_index(args.db, args.out)
    counts = doc["counts"]
    print(f"{counts['correction_rows']} correction rows -> {counts['pairs']} pairs "
          f"({counts['pinned']} pinned)  {args.out or CONFUSER_PATH}")
    for kind, n in counts["by_kind"].items():
        print(f"  {kind:9} {n}")
    if args.show:
        for p in doc["pairs"]:
            mark = "PIN " if p["pinned"] else "    "
            print(f"{mark}{p['kind']:9} {p['wrong_id']} -> {p['correct_id']} "
                  f"x{p['occurrences']}  {p['reason']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
