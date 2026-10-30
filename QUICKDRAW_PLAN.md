# Quickdraw Scanner Reverse-Engineering Plan

**Goal**: Match Quickdraw's detection UX — live rectangle showing exactly what the scanner sees, no false positives, reliable auto-capture.

**Source**: `https://www.quickdraw.cards/` (app.js bundle, vision-worker.js, neural-scanner.js, scanner-state.js, CSS)
**Our repo**: `/opt/data/pokemon-card-scanner/src/` (TF.js YOLO detector + Quickdraw matcher + FastAPI backend)

---

## Quickdraw Architecture (what we need)

| Layer | Quickdraw | Our status |
|-------|-----------|------------|
| **Detector** | ONNX Runtime (WASM SIMD) — 2 CNNs: detector + embedder | TF.js YOLO, NMS=0.1 → **noisy** (fixed: 50% display gate in `detectCardInFrame`) |
| **Input** | 640 maxEdge, aspect-preserved, ImageNet norm | 300×300 fixed, TF.js norm |
| **Peak find** | 3×3 local max on grid logits + 8 corner offsets + flip | YOLO boxes → quad via `detectCardInFrame` |
| **Refinement** | Zoom 1.45× per card, re-detect, pick nearest center | Not implemented |
| **Tracking** | 60% crop around last quad (`TRACK_SCALE=0.6`), `detAccept` gate | Not implemented |
| **Embedding** | Perspective-correct crop → cosine similarity vs 14k fingerprints | ✅ Quickdraw matcher (`/api/quickdraw/match`) |
| **Early outline** | Worker posts `live-boxes` (quad of artwork region) **before** embedding | ✅ `updateOverlayDetection(det)` calls on every tick |
| **Consensus gate** | `FrameConsensus(required=2, supportRequired=3)` + `supportConsensus(required=3)` + `ScanLogGate(absenceMs=1200)` | ❌ Not implemented — auto-capture fires on single frame |
| **Hint ladder** | brightness/glare/sharpness/edge-length → text hints | ⚠️ Partial (`updateOverlayDetection` has 3 hints) |
| **Glide animation** | `requestAnimationFrame` easing 70ms between quads | ❌ Not implemented |
| **Worker pipeline** | `MediaStreamTrackProcessor` → worker → `live-boxes`/`live-result` | ❌ Single-threaded timer loop |

---

## Phased Implementation (smallest complete changes)

### Phase 1: Visual polish (overlay already wired — finish it)
| # | Task | File(s) | Size |
|---|------|---------|------|
| 1.1 | Add **glide easing** to `#detectionPolygon` (70ms ease, corner correspondence) | `main.js` + `main.css` | S |
| 1.2 | Implement full **hint ladder** (brightness, glare, sharpness, edge-length, "Reading…") | `main.js` (`qualityHint`) | S |
| 1.3 | Add **confirmed/weak** visual states (green fill, dashed for code-read) | `main.css` (`.confirmed`, `.code-reading`) | XS |
| 1.4 | Add static **viewfinder corner brackets** (the 4 `<i>` elements at 15% inset) | `index.html` + `main.css` | XS |

### Phase 2: Consensus gate (stop false auto-captures)
| # | Task | File(s) | Size |
|---|------|---------|------|
| 2.1 | Implement `FrameConsensus` class (strong/weak votes, maxAge, maxGap) | `main.js` (new class) | S |
| 2.2 | Wire `visionConsensus` + `supportConsensus` in `detectCardInFrame` / auto-scan loop | `main.js` | S |
| 2.3 | Add `ScanLogGate` (card must leave before re-log) | `main.js` | S |
| 2.4 | Auto-capture only fires on `consensus.confirmed || supported.confirmed` | `main.js` | XS |

### Phase 3: Temporal tracking (stabilize detector)
| # | Task | File(s) | Size |
|---|------|---------|------|
| 3.1 | Add `track` state: last quad + timestamp, `TRACK_SCALE=0.6`, `TRACK_MAX_GAP=400ms` | `main.js` | S |
| 3.2 | On frame: if tracking valid → run detector on crop around last quad | `card-detector-tfjs.js` (new `detectCropped`) | M |
| 3.3 | Fallback to full-frame every 4th frame (`TRACK_FULL_EVERY=4`) | `main.js` | XS |

### Phase 4: Detector quality (reduce false positives at source)
| # | Task | File(s) | Size |
|---|------|---------|------|
| 4.1 | Raise TF.js NMS `scoreThreshold` from 0.1 → 0.3 | `card-detector-tfjs.js` | XS |
| 4.2 | Add **refinement pass** (zoom 1.45×, re-detect) like Quickdraw | `card-detector-tfjs.js` | M |
| 4.3 | (Optional) Replace TF.js with **ONNX Runtime + exported Quickdraw models** | New worker + models | L |

### Phase 5: Worker pipeline (optional, for parity)
| # | Task | File(s) | Size |
|---|------|---------|------|
| 5.1 | Move detection to Web Worker (`vision-worker.js` pattern) | New worker, `main.js` | M |
| 5.2 | Use `MediaStreamTrackProcessor` for low-latency frames | `main.js` | M |

---

## Immediate Next Step (user asked: one by one)

**Start with Phase 1.1–1.4** — the overlay is already hooked (`updateOverlayDetection`), just needs:
- Glide animation (70ms ease)
- Full hint ladder
- Confirmed/weak/code-read visual states
- Static viewfinder brackets

These are **CSS + ~30 lines JS**, zero detector changes, and immediately visible.

---

## Risk / What I Skipped

- **ONNX models not public** — Quickdraw's neural meta returns 404. Phase 4.3 needs exported models or training our own.
- **WebWorker + MediaStreamTrackProcessor** — requires `cross-origin-isolated` headers (COOP/COEP). Our FastAPI server may not serve them; Phase 5 needs server config.
- **Multi-card** — Quickdraw handles up to 3 overlapping cards. Our pipeline assumes 1 card; tracking/refinement logic would need adaptation.
- **Threshold values** (`th.detLogit`, `th.detAccept`, `th.accept`, `th.margin`) — not in public meta; will need tuning by eye or from Quickdraw's `docs/PERF-*.md` if found.

---

## File Map (what we'll touch)

```
/opt/data/pokemon-card-scanner/src/
├── static/
│   ├── js/
│   │   ├── main.js              # Phases 1–3 (overlay, consensus, tracking)
│   │   ├── card-detector-tfjs.js # Phase 4 (NMS threshold, refinement)
│   │   └── review-queue.js      # (unchanged)
│   └── css/
│       └── main.css             # Phase 1 (overlay styles, glide, viewfinder)
└── templates/
    └── index.html               # Phase 1.4 (viewfinder SVG markup)
```

---

## Commands to Start Phase 1

```bash
cd /opt/data/pokemon-card-scanner
# 1. Add glide easing to updateOverlayDetection / showBox equivalent
# 2. Add qualityHint function (copy from Quickdraw)
# 3. Add .confirmed, .code-reading, .viewfinder CSS
# 4. Add <div class="viewfinder"><i></i>×4</div> to index.html scan-overlay
# 5. Hard reload (?nocache=) and verify live rectangle glides + hints update
```
---

## Status (2026-10-30)
- Phase 1 ✅ viewfinder corners, SVG polygon + 70ms glide, confirmed state, quality-hint ladder
- Phase 2 ✅ consensus gate (2 stable frames, wobble/staleness resets, consumed by check())
- Phase 3 ✅ temporal tracking: detectTracked() crops 1.6x around last quad, remaps to
  video coords, 400ms staleness, full-frame probe every 4th tick; fixed _quadsSimilar
  .x vs [0] bug and wobble _ready reset
- Phase 4 ✅ refinement zoom: _refineQuad() re-detects at 1.45x on full-frame probes
  when card height < 60% of frame; refined quad kept only if confidence >= full-frame
- Phase 5 skipped (embedding/matching parity) — our CLIP + Quickdraw fingerprint
  matcher already outperforms; ONNX model swap = L, models not downloadable
- Phase 6 skipped — Quickdraw neural-meta endpoint 404s; nothing to integrate
