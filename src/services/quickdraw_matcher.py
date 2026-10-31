"""
Quickdraw-style local card fingerprint matcher.

Port of Quickdraw's vision-core fingerprinting (integral images + cell means
+ contrast-normalised int8 features) with vectorised numpy search.
Builds/loads data/quickdraw-index.bin; recognize() runs in ~10-50ms.
"""
import io
import json
import logging
import struct
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DATA_DIR = BASE_DIR / "data"
INDEX_PATH = DATA_DIR / "quickdraw-index.bin"
META_PATH = DATA_DIR / "quickdraw-meta.json"
DETECTOR_PATH = DATA_DIR / "models" / "quickdraw-detector.onnx"

MAGIC = b"DLYGIDX1"
VERSION = 1
COARSE_DIM = 96
FINE_DIM = 384
LUMA = np.array([0.299, 0.587, 0.114])

# Art-window crops as fractions of the card (from Quickdraw vision-core.js)
ART_CROPS = {
    "standard": {"x": 0.115, "y": 0.182, "w": 0.77, "h": 0.53},
    "pendulum": {"x": 0.075, "y": 0.182, "w": 0.85, "h": 0.47},
}

# Detector constants (neural-scanner.js / neural-meta.json)
DET_W, DET_H, DET_STRIDE, DET_CORNER_NORM = 192, 320, 8, 64.0
DET_LOGIT, DET_FLIP = -1.0, 0.5
DET_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
DET_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)


def _row_weight(size: int) -> np.ndarray:
    y = np.arange(size)[:, None]
    return np.where((y >= size * 0.375) & (y < size * 0.625), 0.2, 1.0)


def _round(a: np.ndarray) -> np.ndarray:
    return np.floor(a + 0.5)


def _integral(rgba: np.ndarray):
    """Summed-area tables for RGB (alpha on white), like vision-core buildIntegral."""
    h, w = rgba.shape[:2]
    alpha = rgba[:, :, 3:4].astype(np.float64) / 255.0
    rgb = rgba[:, :, :3].astype(np.float64) * alpha + 255.0 * (1.0 - alpha)
    pad = np.zeros((h + 1, w + 1, 3))
    pad[1:, 1:] = rgb
    return pad.cumsum(0).cumsum(1), w, h  # (h+1, w+1, 3)


def _bilinear_plane(table, x, y, w, h):
    """Bilinear sample of a summed-area plane at fractional coords (vision-core integralAt)."""
    x = np.clip(x, 0.0, float(w))
    y = np.clip(y, 0.0, float(h))
    ix = np.minimum(np.floor(x), w - 1).astype(int)
    iy = np.minimum(np.floor(y), h - 1).astype(int)
    fx = x - ix
    fy = y - iy
    return (table[iy, ix] * (1 - fx) * (1 - fy) + table[iy, ix + 1] * fx * (1 - fy)
            + table[iy + 1, ix] * (1 - fx) * fy + table[iy + 1, ix + 1] * fx * fy)


def _cells(tables, w, h, region, size=16) -> np.ndarray:
    """Mean RGB of each cell in a size x size grid over the region -> (size,size,3)."""
    x0 = min(max(region["x"], 0.0), 0.99) * w
    y0 = min(max(region["y"], 0.0), 0.99) * h
    x1 = min(max(region["x"] + region["w"], region["x"] + 0.01), 1.0) * w
    y1 = min(max(region["y"] + region["h"], region["y"] + 0.01), 1.0) * h
    xs = x0 + np.arange(size + 1) * ((x1 - x0) / size)
    ys = y0 + np.arange(size + 1) * ((y1 - y0) / size)
    gx, gy = np.meshgrid(xs, ys)
    area = ((x1 - x0) / size) * ((y1 - y0) / size)
    out = np.empty((size, size, 3))
    for c in range(3):
        corners = _bilinear_plane(tables[:, :, c], gx, gy, w, h)
        out[:, :, c] = (corners[1:, 1:] - corners[1:, :-1] - corners[:-1, 1:] + corners[:-1, :-1]) / area
    return out


def _downsample(rgb: np.ndarray) -> np.ndarray:
    return (rgb[0::2, 0::2] + rgb[1::2, 0::2] + rgb[0::2, 1::2] + rgb[1::2, 1::2]) / 4.0


def _make_features(rgb: np.ndarray, colour_rgb: np.ndarray, size: int):
    """Contrast-normalised int8 features (vision-core makeFeatures)."""
    w_row = _row_weight(size)
    lum = rgb @ LUMA
    wsum = w_row.sum() * size
    mean = float((lum * w_row).sum() / wsum)
    var = float((((lum - mean) ** 2) * w_row).sum() / wsum)
    contrast = float(np.sqrt(max(var, 0.0)))
    scale = 40.0 / max(contrast, 4.0)
    lum_feat = _round(np.clip((lum - mean) * scale, -110, 110) * np.sqrt(w_row))

    cs = size // 2
    wc = _row_weight(cs)
    y_c = colour_rgb @ LUMA
    denom = mean + 40.0
    blue = _round(np.clip((colour_rgb[:, :, 2] - y_c) / denom * 90.0, -110, 110) * np.sqrt(wc))
    red = _round(np.clip((colour_rgb[:, :, 0] - y_c) / denom * 90.0, -110, 110) * np.sqrt(wc))
    feats = np.concatenate([lum_feat.reshape(-1), blue.reshape(-1), red.reshape(-1)]).astype(np.int8)
    return feats, mean, contrast


def _describe(art16: np.ndarray):
    """Given 16x16x3 cell means -> (coarse[96], fine[384])."""
    mid = _downsample(art16)      # 8x8
    small = _downsample(mid)      # 4x4
    fine, _, _ = _make_features(art16, mid, 16)
    coarse, _, _ = _make_features(mid, small, 8)
    return coarse, fine


def _region(base, dx=0.0, dy=0.0, scale=1.0):
    x, y, w, h = base["x"], base["y"], base["w"], base["h"]
    nw, nh = w * scale, h * scale
    return {"x": x + (w - nw) / 2 + dx, "y": y + (h - nh) / 2 + dy, "w": nw, "h": nh}


def _hypothesis(tables, w, h, region):
    return _describe(_cells(tables, w, h, region))


_JITTERS = [
    (0.0, 0.0, 1.0), (0.0, 0.0, 0.94), (0.0, 0.0, 0.90),
    (-0.02, 0.0, 1.0), (0.02, 0.0, 1.0), (0.0, -0.02, 1.0), (0.0, 0.02, 1.0),
]

_LUM_W = {d: 0.86 / ((d * 2 // 3) * 0.8 * 3200) for d in (COARSE_DIM, FINE_DIM)}
_CHR_W = {d: 0.14 / ((d // 3) * 0.8 * 1800) for d in (COARSE_DIM, FINE_DIM)}


def _weighted_sq(q: np.ndarray, mat: np.ndarray, dim: int) -> np.ndarray:
    """Weighted squared distance from int8 query q to every row of int8 matrix."""
    d = mat.astype(np.int32) - q.astype(np.int32)
    lum_n = dim * 2 // 3
    lum = np.einsum("ij,ij->i", d[:, :lum_n], d[:, :lum_n])
    chr_ = np.einsum("ij,ij->i", d[:, lum_n:], d[:, lum_n:])
    return lum * _LUM_W[dim] + chr_ * _CHR_W[dim]


def _weighted_sq_rows(qs: np.ndarray, mat: np.ndarray, dim: int) -> np.ndarray:
    """Min weighted squared distance from each of the queries (k,dim) to every row."""
    best = None
    lum_n = dim * 2 // 3
    qw_lum = _LUM_W[dim]
    qw_chr = _CHR_W[dim]
    mat_lum = mat[:, :lum_n].astype(np.int32)
    mat_chr = mat[:, lum_n:].astype(np.int32)
    for q in qs:
        d_l = mat_lum - q[:lum_n].astype(np.int32)
        d_c = mat_chr - q[lum_n:].astype(np.int32)
        dist = (np.einsum("ij,ij->i", d_l, d_l) * qw_lum
                + np.einsum("ij,ij->i", d_c, d_c) * qw_chr)
        best = dist if best is None else np.minimum(best, dist)
    return best


def _quad_matrix(quad):
    """Homography (3x3) mapping unit square -> quad (4x2, TL TR BR BL)."""
    A = np.zeros((8, 8))
    b = np.zeros(8)
    uv = [(0, 0), (1, 0), (1, 1), (0, 1)]
    for i, (u, v) in enumerate(uv):
        X, Y = quad[i]
        A[i] = [u, v, 1, 0, 0, 0, -X * u, -X * v]
        b[i] = X
        A[i + 4] = [0, 0, 0, u, v, 1, -Y * u, -Y * v]
        b[i + 4] = Y
    try:
        h = np.linalg.solve(A, b)
    except np.linalg.LinAlgError:
        return None
    return np.array([[h[0], h[1], h[2]], [h[3], h[4], h[5]], [h[6], h[7], 1.0]])


def _convex(quad) -> bool:
    s = []
    for i in range(4):
        ax, ay = quad[i]
        bx, by = quad[(i + 1) % 4]
        cx, cy = quad[(i + 2) % 4]
        s.append((bx - ax) * (cy - ay) - (by - ay) * (cx - ax))
    return all(v >= 0 for v in s) or all(v <= 0 for v in s)


def _quad_area(quad) -> float:
    x = quad[:, 0]
    y = quad[:, 1]
    return abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))) / 2.0


def _detect_quads(session, rgba: np.ndarray) -> List[np.ndarray]:
    """Port of neural-scanner runDetector: returns quads (4x2) in image coords, best first."""
    h0, w0 = rgba.shape[:2]
    portrait = h0 >= w0
    iw, ih = (DET_W, DET_H) if portrait else (DET_H, DET_W)
    arr = np.asarray(Image.fromarray(rgba).resize((iw, ih), Image.BILINEAR), dtype=np.float32)[..., :3] / 255.0
    arr = (arr - DET_MEAN) / DET_STD
    maps = session.run(None, {"image": np.ascontiguousarray(arr.transpose(2, 0, 1)[None])})[0][0]
    gh, gw = maps.shape[1], maps.shape[2]
    scores = maps[0]
    padded = np.pad(scores, 1, constant_values=-np.inf)
    peak = scores >= padded[:-2, :-2]
    for dy in range(3):
        for dx in range(3):
            if dy == 1 and dx == 1:
                continue
            peak &= scores >= padded[dy:dy + gh, dx:dx + gw]
    sel = np.argwhere((scores > DET_LOGIT) & peak)
    sig = lambda v: 1.0 / (1.0 + np.exp(-v))
    dets = []
    for y, x in sel:
        q = np.empty((4, 2))
        for k in range(4):
            q[k, 0] = (maps[1 + 2 * k, y, x] * DET_CORNER_NORM + (x + .5) * DET_STRIDE) / iw * w0
            q[k, 1] = (maps[2 + 2 * k, y, x] * DET_CORNER_NORM + (y + .5) * DET_STRIDE) / ih * h0
        if sig(maps[9, y, x]) > DET_FLIP:
            q = q[[2, 3, 0, 1]]
        dets.append((sig(scores[y, x]), q))
    dets = [(s, q) for s, q in dets if _convex(q) and _quad_area(q) > 600]
    dets.sort(key=lambda t: -t[0])
    return [q for _, q in dets]


def _warp_card(rgba: np.ndarray, quad: np.ndarray, ow=252, oh=352) -> np.ndarray:
    """Perspective-rectify the quad to an (oh, ow, 4) card image (vision-core rectifyQuad)."""
    M = _quad_matrix(quad)
    if M is None:
        return rgba
    v, u = np.meshgrid((np.arange(oh) + .5) / oh, (np.arange(ow) + .5) / ow, indexing="ij")
    den = M[2, 0] * u + M[2, 1] * v + 1.0
    sx = (M[0, 0] * u + M[0, 1] * v + M[0, 2]) / den
    sy = (M[1, 0] * u + M[1, 1] * v + M[1, 2]) / den
    H, W = rgba.shape[:2]
    sx = np.clip(sx, 0, W - 1.001)
    sy = np.clip(sy, 0, H - 1.001)
    x0 = sx.astype(np.int32)
    y0 = sy.astype(np.int32)
    fx = (sx - x0)[..., None]
    fy = (sy - y0)[..., None]
    src = rgba.astype(np.float32)
    top = src[y0, x0] * (1 - fx) + src[y0, x0 + 1] * fx
    bot = src[y0 + 1, x0] * (1 - fx) + src[y0 + 1, x0 + 1] * fx
    return np.clip(top * (1 - fy) + bot * fy, 0, 255).astype(np.uint8)


def _rgba(img: Image.Image) -> np.ndarray:
    img = img.convert("RGB")
    return np.dstack([np.asarray(img), np.full(img.size[::-1] + (1,), 255, np.uint8)])


def _fingerprint(rgba: np.ndarray, region: Optional[dict] = None):
    tables, w, h = _integral(rgba)
    return _hypothesis(tables, w, h, region or ART_CROPS["standard"])


class QuickdrawMatcher:
    """Local fingerprint matcher: int8 integral-image features + vectorised search."""

    def __init__(self):
        self.index_path = INDEX_PATH
        self.meta_path = META_PATH
        self.coarse: Optional[np.ndarray] = None
        self.fine: Optional[np.ndarray] = None
        self.card_idx: Optional[np.ndarray] = None
        self.cards: List[dict] = []
        self.count = 0
        self._building = False
        self._build_error: Optional[str] = None
        self._detector = None
        self._det_lock = threading.Lock()

    def status(self) -> Dict[str, Any]:
        return {"available": self.count > 0, "cards_indexed": self.count,
                "building": self._building, "build_error": self._build_error}

    def load_index(self, force: bool = False) -> bool:
        if self.count and not force:
            return True
        if not self.index_path.exists() or not self.meta_path.exists():
            return False
        try:
            self.cards = json.loads(self.meta_path.read_text())["cards"]
            buf = self.index_path.read_bytes()
            magic, version, count, cdim, fdim = struct.unpack_from("<8sIIII", buf, 0)
            if magic != MAGIC or version != VERSION or cdim != COARSE_DIM or fdim != FINE_DIM:
                logger.error("quickdraw index: bad header %r v%s", magic, version)
                return False
            if len(buf) < 24 + count * (6 + COARSE_DIM + FINE_DIM):
                logger.error("quickdraw index: truncated file")
                return False
            off = 24
            self.card_idx = np.frombuffer(buf, np.uint32, count, off)
            self.art_idx = np.frombuffer(buf, np.uint16, count, off + 4 * count)
            self.coarse = np.frombuffer(buf, np.int8, count * COARSE_DIM, 24 + 6 * count).reshape(count, COARSE_DIM)
            self.fine = np.frombuffer(buf, np.int8, count * FINE_DIM,
                                      24 + 6 * count + count * COARSE_DIM).reshape(count, FINE_DIM)
            self.count = count
            logger.info("quickdraw: loaded index of %d cards", count)
            return True
        except Exception:
            logger.exception("quickdraw: load failed")
            return False

    def _local_image(self, card: dict) -> Optional[Path]:
        rel = card.get("image", "").split("/static/card_images/")[-1].lstrip("/")
        p = DATA_DIR / "images" / rel
        return p if rel and p.exists() else None

    def build(self) -> bool:
        if not self.cards and self.meta_path.exists():
            self.cards = json.loads(self.meta_path.read_text())["cards"]
        logger.info("quickdraw: building index for %d catalogue cards", len(self.cards))
        t0 = time.time()
        card_indices, coarse_rows, fine_rows = [], [], []
        missing = 0
        for i, card in enumerate(self.cards):
            p = self._local_image(card)
            if p is None:
                missing += 1
                continue
            try:
                rgba = _rgba(Image.open(p))
                coarse, fine = _fingerprint(rgba)
            except Exception:
                missing += 1
                continue
            card_indices.append(i)
            coarse_rows.append(coarse)
            fine_rows.append(fine)
        count = len(card_indices)
        if not count:
            self._build_error = "no card images found"
            return False
        hdr = struct.pack("<8sIIII", MAGIC, VERSION, count, COARSE_DIM, FINE_DIM)
        tmp = self.index_path.with_suffix(".tmp")
        with open(tmp, "wb") as f:
            f.write(hdr)
            f.write(np.asarray(card_indices, np.uint32).tobytes())
            f.write(np.zeros(count, np.uint16).tobytes())
            f.write(np.stack(coarse_rows).tobytes())
            f.write(np.stack(fine_rows).tobytes())
        tmp.replace(self.index_path)
        self.count = 0
        self.load_index(force=True)
        logger.info("quickdraw: built index %d cards (%d missing) in %.1fs",
                    count, missing, time.time() - t0)
        return True

    def build_in_background(self) -> bool:
        if self._building:
            return False
        self._building = True
        self._build_error = None

        def _run():
            try:
                self.build()
            except Exception as e:
                self._build_error = str(e)
                logger.exception("quickdraw build failed")
            finally:
                self._building = False

        threading.Thread(target=_run, daemon=True).start()
        return True

    def _get_detector(self):
        if self._detector is None:
            with self._det_lock:
                if self._detector is None:
                    try:
                        import onnxruntime as ort
                        self._detector = ort.InferenceSession(
                            str(DETECTOR_PATH), providers=["CPUExecutionProvider"])
                    except Exception as e:
                        logger.warning("quickdraw detector unavailable: %s", e)
                        self._detector = False  # negative cache
        return self._detector or None

    def _query_hypotheses(self, image_bytes: bytes):
        rgba = _rgba(Image.open(io.BytesIO(image_bytes)))
        session = self._get_detector()
        if session is not None:
            try:
                quads = _detect_quads(session, rgba)
                # detector quads on already-upright full-frame cards are ~85% bias-small;
                # warping those crops the card and hurts, so only warp tilted/small cards
                if quads:
                    q = quads[0]
                    ang = max(abs(np.degrees(np.arctan2(*(q[1] - q[0])[::-1]))),
                              abs(np.degrees(np.arctan2(*(q[2] - q[3])[::-1]))))
                    if (ang > 6.0 or
                            _quad_area(q) < 0.7 * rgba.shape[0] * rgba.shape[1]):
                        rgba = _warp_card(rgba, q)
            except Exception as e:
                logger.debug("quickdraw detect/warp failed, raw frame: %s", e)
        tables, w, h = _integral(rgba)
        return [_hypothesis(tables, w, h, _region(ART_CROPS["standard"], dx, dy, s))
                for dx, dy, s in _JITTERS]

    # measured on index: exact self dist ~0, transformed queries ~0.05-0.15,
    # distinct-card nearest neighbours median 0.28 (min 0 = cross-set dup art)
    MATCH_DIST, WEAK_DIST = 0.12, 0.30

    def recognize(self, image_bytes: bytes, top_k: int = 5) -> Dict[str, Any]:
        if not self.count and not self.load_index():
            return {"decision": "no_match", "candidates": [], "source": "quickdraw_matcher"}
        t0 = time.time()
        hyps = self._query_hypotheses(image_bytes)
        c_dist = _weighted_sq_rows(np.stack([c for c, _ in hyps]), self.coarse, COARSE_DIM)
        k = min(max(top_k * 20, 100), self.count)
        cand = np.argpartition(c_dist, k - 1)[:k]
        f_dist = _weighted_sq_rows(np.stack([f for _, f in hyps]), self.fine[cand], FINE_DIM)
        order = np.argsort(f_dist)[:top_k]
        best = float(f_dist[order[0]])
        decision = ("match" if best < self.MATCH_DIST else
                    "possible_match" if best < self.WEAK_DIST else "no_match")
        candidates = []
        for o in order:
            card = self.cards[int(self.card_idx[cand[o]])]
            candidates.append({
                "id": card.get("ptcg_id", ""), "ptcg_id": card.get("ptcg_id", ""),
                "name": card.get("name", ""), "set": card.get("set", ""),
                "number": card.get("number", ""), "image": card.get("image", ""),
                "similarity": round(max(0.0, 1.0 - float(f_dist[o]) / self.WEAK_DIST), 4),
                "distance": round(float(f_dist[o]), 3),
                "source": "quickdraw_matcher",
            })
        return {"decision": decision, "candidates": candidates,
                "source": "quickdraw_matcher",
                "latency_ms": int((time.time() - t0) * 1000)}


_matcher: Optional[QuickdrawMatcher] = None
_matcher_lock = threading.Lock()


def get_quickdraw_matcher() -> QuickdrawMatcher:
    global _matcher
    if _matcher is None:
        with _matcher_lock:
            if _matcher is None:
                _matcher = QuickdrawMatcher()
    return _matcher
