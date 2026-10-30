// Browser-side card detector: Quickdraw's corner-point model over ONNX Runtime Web.
// Replaces the TF.js YOLO11 detector — a corner model answers "where are the 4
// corners?" instead of "is this a card?", so rectangular clutter (shoulders, table
// edges, hands) cannot score as a card. Same output shape as the old detector, so
// main.js and the viewfinder overlay are unchanged.
// Port of quickdraw.cards neural-scanner.js (detector recipe + decoder), verified
// server-side in services/card_detector.py against the same weights.

const ORT_BASE = '/static/ort/';
const MODEL_URL = ORT_BASE + 'quickdraw_detector.onnx';

// Detector input geometry and decoder constants — must match the server port.
const DW = 192, DH = 320;          // portrait input; landscape frames swap them
const STRIDE = 8, CORNER_NORM = 64; // cornerNorm 64 => one grid cell is 8px
const DET_LOGIT = -1.0, MIN_AREA = 600;
const MEAN = [0.485, 0.456, 0.406], STD = [0.229, 0.224, 0.225];

let _ort = null, _session = null, _loading = null;

async function _load() {
  if (_session) return { ort: _ort, session: _session };
  if (_loading) return _loading;
  _loading = (async () => {
    _ort = await import(ORT_BASE + 'ort.mjs');
    _ort.env.wasm.wasmPaths = {
      mjs: ORT_BASE + 'ort-wasm-simd-threaded.mjs',
      wasm: ORT_BASE + 'ort-wasm-simd-threaded.wasm',
    };
    // SharedArrayBuffer needs cross-origin isolation, which the QNAP proxy does
    // not send; single-threaded WASM is the portable path.
    _ort.env.wasm.numThreads = globalThis.crossOriginIsolated ? 2 : 1;
    const buf = await (await fetch(MODEL_URL)).arrayBuffer();
    _session = await _ort.InferenceSession.create(new Uint8Array(buf),
      { executionProviders: ['wasm'], graphOptimizationLevel: 'all' });
    console.log('[Quickdraw] corner detector loaded');
    return { ort: _ort, session: _session };
  })().catch(e => { _loading = null; throw e; });
  return _loading;
}

/** Box-filter downscale of RGBA into a normalised CHW float tensor. */
function resizeToTensor(rgba, width, height, ow, oh, out, rect) {
  const sx = rect.w / ow, sy = rect.h / oh, plane = ow * oh;
  const x0 = new Int32Array(ow), x1 = new Int32Array(ow);
  for (let x = 0; x < ow; x++) {
    x0[x] = Math.min(width - 1, Math.max(0, Math.floor(rect.x + x * sx)));
    x1[x] = Math.max(x0[x] + 1, Math.min(width, Math.floor(rect.x + (x + 1) * sx)));
  }
  const rowR = new Float32Array(ow), rowG = new Float32Array(ow), rowB = new Float32Array(ow);
  for (let y = 0; y < oh; y++) {
    const ya = Math.min(height - 1, Math.max(0, Math.floor(rect.y + y * sy)));
    const yb = Math.max(ya + 1, Math.min(height, Math.floor(rect.y + (y + 1) * sy)));
    rowR.fill(0); rowG.fill(0); rowB.fill(0);
    for (let yy = ya; yy < yb; yy++) {
      const base = yy * width * 4;
      for (let x = 0; x < ow; x++) {
        let r = 0, g = 0, b = 0;
        for (let xx = x0[x]; xx < x1[x]; xx++) {
          const p = base + xx * 4; r += rgba[p]; g += rgba[p + 1]; b += rgba[p + 2];
        }
        rowR[x] += r; rowG[x] += g; rowB[x] += b;
      }
    }
    const o = y * ow;
    for (let x = 0; x < ow; x++) {
      const n = 255 * (yb - ya) * (x1[x] - x0[x]);
      out[o + x] = (rowR[x] / n - MEAN[0]) / STD[0];
      out[plane + o + x] = (rowG[x] / n - MEAN[1]) / STD[1];
      out[2 * plane + o + x] = (rowB[x] / n - MEAN[2]) / STD[2];
    }
  }
  return out;
}

/** dst ~ H src for 4 correspondences; returns 3x3 row-major (h33 = 1). */
function solveHomography(src, dst) {
  const A = [], b = [];
  for (let i = 0; i < 4; i++) {
    const [x, y] = src[i], [u, v] = dst[i];
    A.push([x, y, 1, 0, 0, 0, -u * x, -u * y]); b.push(u);
    A.push([0, 0, 0, x, y, 1, -v * x, -v * y]); b.push(v);
  }
  for (let c = 0; c < 8; c++) {
    let p = c;
    for (let r = c + 1; r < 8; r++) if (Math.abs(A[r][c]) > Math.abs(A[p][c])) p = r;
    [A[c], A[p]] = [A[p], A[c]]; [b[c], b[p]] = [b[p], b[c]];
    const d = A[c][c];
    if (Math.abs(d) < 1e-12) return null;
    for (let r = 0; r < 8; r++) if (r !== c) {
      const f = A[r][c] / d; if (!f) continue;
      for (let k = c; k < 8; k++) A[r][k] -= f * A[c][k];
      b[r] -= f * b[c];
    }
  }
  const h = b.map((v, i) => v / A[i][i]); h.push(1); return h;
}

const quadArea = q => {
  let a = 0;
  for (let i = 0; i < 4; i++) { const p = q[i], n = q[(i + 1) % 4]; a += p[0] * n[1] - n[0] * p[1]; }
  return Math.abs(a) / 2;
};
const isConvex = q => {
  for (let i = 0; i < 4; i++) {
    const a = q[i], b = q[(i + 1) % 4], c = q[(i + 2) % 4];
    if ((b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0]) <= 0) return false;
  }
  return true;
};

// Reused across frames: source RGBA canvas, tensor buffer and detection list.
let _srcCanvas = null, _srcCtx = null, _rgba = null, _rgbaW = 0, _rgbaH = 0, _buf = null;

function readRGBA(el) {
  const w = el.videoWidth || el.naturalWidth || el.width;
  const h = el.videoHeight || el.naturalHeight || el.height;
  if (!w || !h) return null;
  if (!_srcCanvas) { _srcCanvas = document.createElement('canvas'); _srcCtx = _srcCanvas.getContext('2d', { willReadFrequently: true }); }
  if (_srcCanvas.width !== w || _srcCanvas.height !== h) {
    _srcCanvas.width = w; _srcCanvas.height = h; _rgba = null;
  }
  _srcCtx.drawImage(el, 0, 0);
  _rgba = _srcCtx.getImageData(0, 0, w, h).data;
  _rgbaW = w; _rgbaH = h;
  return _rgba;
}

class PokemonCardDetector {
  constructor() { this.model = null; }

  async loadModel() {
    this.model = await _load();
    return this.model;
  }

  isModelLoaded() { return !!this.model; }

  /** Run the corner model on one rect of the frame; returns score-sorted quads. */
  async _runRGBA(rgba, width, height, rect) {
    const portrait = rect.h >= rect.w;
    const iw = portrait ? DW : DH, ih = portrait ? DH : DW;
    const need = 3 * iw * ih;
    if (!_buf || _buf.length !== need) _buf = new Float32Array(need);
    resizeToTensor(rgba, width, height, iw, ih, _buf, rect);
    const feeds = { image: new this.model.ort.Tensor('float32', _buf, [1, 3, ih, iw]) };
    const maps = (await this.model.session.run(feeds)).maps;
    const gh = maps.dims[2], gw = maps.dims[3], d = maps.data, plane = gh * gw;
    const sx = rect.w / iw, sy = rect.h / ih;
    const sig = v => 1 / (1 + Math.exp(-v));
    const out = [];
    for (let y = 0; y < gh; y++) for (let x = 0; x < gw; x++) {
      const v = d[y * gw + x];
      if (v < DET_LOGIT) continue;
      let peak = true;
      for (let dy = -1; dy <= 1 && peak; dy++) for (let dx = -1; dx <= 1; dx++) {
        if (!dx && !dy) continue;
        const yy = y + dy, xx = x + dx;
        if (yy >= 0 && yy < gh && xx >= 0 && xx < gw && d[yy * gw + xx] > v) { peak = false; break; }
      }
      if (!peak) continue;
      const q = [];
      for (let k = 0; k < 4; k++) q.push([
        rect.x + (d[(1 + 2 * k) * plane + y * gw + x] * CORNER_NORM + (x + 0.5) * STRIDE) * sx,
        rect.y + (d[(2 + 2 * k) * plane + y * gw + x] * CORNER_NORM + (y + 0.5) * STRIDE) * sy,
      ]);
      if (q.some(p => !isFinite(p[0]) || !isFinite(p[1]))) continue;
      // flip > .5 means the network saw the card upside down: swap the corner pairs.
      const flip = sig(d[9 * plane + y * gw + x]);
      out.push({ score: sig(v), quad: flip > 0.5 ? [q[2], q[3], q[0], q[1]] : q });
    }
    return out.sort((a, b) => b.score - a.score);
  }

  async detect(imageElement) {
    if (!this.model) await this.loadModel();
    const rgba = readRGBA(imageElement);
    if (!rgba) return [];
    const width = _rgbaW, height = _rgbaH;
    let dets = await this._runRGBA(rgba, width, height, { x: 0, y: 0, w: width, h: height });
    dets = dets.filter(t => isConvex(t.quad) && quadArea(t.quad) > MIN_AREA).slice(0, 3);
    // Refine: re-detect at the card's own scale. Corners land roughly twice as
    // precisely, which is what makes the straightened crop edges land on the card.
    const iw = height >= width ? DW : DH, ih = height >= width ? DH : DW;
    const refined = [];
    for (const det of dets) {
      const xs = det.quad.map(p => p[0]), ys = det.quad.map(p => p[1]);
      const cx = (Math.min(...xs) + Math.max(...xs)) / 2, cy = (Math.min(...ys) + Math.max(...ys)) / 2;
      let w = (Math.max(...xs) - Math.min(...xs)) * 1.45, h = (Math.max(...ys) - Math.min(...ys)) * 1.45;
      if (w / h > iw / ih) h = w * ih / iw; else w = h * iw / ih;
      if (w >= width * 0.8 && h >= height * 0.8) { refined.push(det); continue; }
      const rect = { x: cx - w / 2, y: cy - h / 2, w, h };
      const near = (await this._runRGBA(rgba, width, height, rect)).filter(t => isConvex(t.quad));
      let best = null, bd = Infinity;
      for (const t of near) {
        const dd = Math.hypot(t.quad.reduce((s, p) => s + p[0], 0) / 4 - cx,
                              t.quad.reduce((s, p) => s + p[1], 0) / 4 - cy);
        if (dd < bd) { bd = dd; best = t; }
      }
      refined.push(best && bd < Math.min(w, h) * 0.25
        ? { ...best, score: Math.max(best.score, det.score) } : det);
    }
    return refined.filter(t => quadArea(t.quad) > MIN_AREA).map(t => {
      const xs = t.quad.map(p => p[0]), ys = t.quad.map(p => p[1]);
      const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys);
      return {
        points: t.quad, quad: t.quad, confidence: t.score * 100,
        centerX: (x0 + x1) / 2, centerY: (y0 + y1) / 2,
        width: x1 - x0, height: y1 - y0, angle: 0,
      };
    });
  }
}

/**
 * Straighten a detected card into an upright image. Uses the quad's homography
 * rather than the old OBB angle+rotate, so perspective (card tilted away from
 * the camera) is corrected too, not just in-plane rotation.
 */
export function cropCardStraight(imageEl, detection) {
  const canvas = document.createElement('canvas');
  const quad = (detection && (detection.points || detection.quad) || []).map(
    p => (Array.isArray(p) ? [p[0], p[1]] : [p.x, p.y]));
  const rgba = readRGBA(imageEl);
  if (quad.length !== 4 || !rgba || quad.some(p => !isFinite(p[0]) || !isFinite(p[1]))) {
    canvas.width = canvas.height = 1;
    return canvas;
  }
  const w = _rgbaW, h = _rgbaH;
  // Keep the card's own proportions, capped at 352px on the long edge.
  const ew = Math.hypot(quad[1][0] - quad[0][0], quad[1][1] - quad[0][1]);
  const eh = Math.hypot(quad[3][0] - quad[0][0], quad[3][1] - quad[0][1]);
  if (ew < 2 || eh < 2) { canvas.width = canvas.height = 1; return canvas; }
  let outW, outH;
  if (eh >= ew) { outH = 352; outW = Math.max(32, Math.round(352 * ew / eh)); }
  else { outW = 352; outH = Math.max(32, Math.round(352 * eh / ew)); }
  const H = solveHomography([[0, 0], [1, 0], [1, 1], [0, 1]], quad);
  if (!H) { canvas.width = canvas.height = 1; return canvas; }
  canvas.width = outW; canvas.height = outH;
  const ctx = canvas.getContext('2d');
  const img = ctx.createImageData(outW, outH), o = img.data;
  const wm = w - 1, hm = h - 1;
  for (let y = 0; y < outH; y++) for (let x = 0; x < outW; x++) {
    const u = (x + 0.5) / outW, v = (y + 0.5) / outH, hw = H[6] * u + H[7] * v + H[8];
    const px = (H[0] * u + H[1] * v + H[2]) / hw - 0.5;
    const py = (H[3] * u + H[4] * v + H[5]) / hw - 0.5;
    const ix = Math.floor(px), iy = Math.floor(py), fx = px - ix, fy = py - iy;
    const cx0 = Math.min(wm, Math.max(0, ix)), cx1 = Math.min(wm, Math.max(0, ix + 1));
    const cy0 = Math.min(hm, Math.max(0, iy)), cy1 = Math.min(hm, Math.max(0, iy + 1));
    const p00 = (cy0 * w + cx0) * 4, p10 = (cy0 * w + cx1) * 4;
    const p01 = (cy1 * w + cx0) * 4, p11 = (cy1 * w + cx1) * 4;
    const w00 = (1 - fx) * (1 - fy), w10 = fx * (1 - fy), w01 = (1 - fx) * fy, w11 = fx * fy;
    const q = (y * outW + x) * 4;
    for (let c = 0; c < 3; c++) {
      o[q + c] = rgba[p00 + c] * w00 + rgba[p10 + c] * w10 + rgba[p01 + c] * w01 + rgba[p11 + c] * w11;
    }
    o[q + 3] = 255;
  }
  ctx.putImageData(img, 0, 0);
  return canvas;
}

export const detector = new PokemonCardDetector();
export { PokemonCardDetector };
window.PokemonCardDetector = PokemonCardDetector;
window.cropCardStraight = cropCardStraight;
