import { PokemonCardDetector } from './card-detector-tfjs.js';
import { PerceptualHashMatcher } from './perceptual-hash.js';
import './review-queue.js';

import { escapeHTML } from './escape-html.js';

const $ = id => document.getElementById(id);
const statusDot = $('status-dot'), statusText = $('status-text');
const resultsList = $('results-list'), resultsTitle = $('results-title');
const captureBtn = $('capture-btn'), progressEl = $('progress');
const progressBar = progressEl.firstElementChild;

let video = $('camera'), stream = null, facing = 'environment';
let detector = new PokemonCardDetector(), hashMatcher = new PerceptualHashMatcher(), cardIndex = null;
let busy = false;
let lastTopScore = 0;
const queue = [];
const reviewed = new Set();
const versionBadge = $('version-badge');
versionBadge.classList.add('local');

function setStatus(text, on) {
  statusText.textContent = text;
  statusDot.className = 'dot' + (on ? ' on' : '');
}

async function loadIndex() {
  setStatus('Loading card index…');
  const base = '/scanner/';
  const [ids, m] = await Promise.all([
    fetch(base + 'card-ids.json').then(r => r.json()),
    fetch(base + 'cards_meta.json').then(r => r.json()),
  ]);
  window.cardIds = ids; window.meta = m;
  try {
    const hashResp = await fetch(base + 'card-hashes.json');
    if (hashResp.ok) {
      const hashes = await hashResp.json();
      const cardsWithHashes = ids.map((id, i) => ({
        id,
        name: meta[i]?.name || id,
        set: meta[i]?.set,
        number: meta[i]?.number,
        rarity: meta[i]?.rarity,
        image: meta[i]?.image,
        hash: hashes[id] || null,
      }));
      hashMatcher.loadIndex(cardsWithHashes);
    }
  } catch (e) {
    console.warn('[PerceptualHash] No precomputed hashes found, will compute on demand');
  }
}

async function loadModel() {
  setStatus('Loading detection model…');
  progressEl.classList.add('show');
  await detector.loadModel();
  progressEl.classList.remove('show');
  setStatus('Ready', true);
}

// Expose to window for debugging
window.detector = detector;
window.hashMatcher = hashMatcher;

async function startCamera() {
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: facing, width: { ideal: 640 }, height: { ideal: 800 } },
      audio: false,
    });
    video.srcObject = stream;
    await video.play();
    // Fit the video element to the stream's real aspect ratio. Forcing a
    // 3:4 box with object-fit:cover cropped a 16:9 webcam feed and looked
    // heavily zoomed in on laptops; phones were unaffected because their
    // rear camera already reports ~3:4.
    const s = stream.getVideoTracks()[0]?.getSettings();
    if (s?.width && s?.height) video.style.aspectRatio = `${s.width} / ${s.height}`;
    else if (video.videoWidth) video.style.aspectRatio = `${video.videoWidth} / ${video.videoHeight}`;
    setStatus('Ready', true);
    captureBtn.disabled = false;
  } catch (e) {
    setStatus('Camera error: ' + e.message, false);
  }
}

async function flip() {
  facing = facing === 'environment' ? 'user' : 'environment';
  if (stream) stream.getTracks().forEach(t => t.stop());
  await startCamera();
}

function dataURLtoBlob(url) {
  const [head, body] = url.split(',');
  const mime = head.match(/:(.*?);/)[1];
  const bin = atob(body);
  const arr = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i);
  return new Blob([arr], { type: mime });
}

function blobToDataURL(blob) {
  return new Promise((resolve) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.readAsDataURL(blob);
  });
}

async function capture() {
  if (busy) return false;
  busy = true;
  captureBtn.disabled = true;
  resultsList.innerHTML = '<div class="empty">Detecting card…</div>';
  const canvas = document.createElement('canvas');
  canvas.width = video.videoWidth || 640;
  canvas.height = video.videoHeight || 800;
  canvas.getContext('2d').drawImage(video, 0, 0, canvas.width, canvas.height);

  // Detect card using YOLO11 OBB
  const detections = await detector.detect(canvas);
  if (!detections || detections.length === 0) {
    setStatus('No card in frame — hold a card up');
    busy = false;
    captureBtn.disabled = false;
    return false;
  }

  // Use the highest-confidence detection
  const best = detections[0];
  const { points } = best;

  // Create axis-aligned bounding box from OBB corners for cropping
  const xs = points.map(p => p[0]);
  const ys = points.map(p => p[1]);
  const x = Math.max(0, Math.min(...xs));
  const y = Math.max(0, Math.min(...ys));
  const w = Math.min(canvas.width - x, Math.max(...xs) - x);
  const h = Math.min(canvas.height - y, Math.max(...ys) - y);

  const cropCanvas = document.createElement('canvas');
  cropCanvas.width = w;
  cropCanvas.height = h;
  const cropCtx = cropCanvas.getContext('2d');
  cropCtx.drawImage(canvas, x, y, w, h, 0, 0, w, h);
  setStatus('Card detected, matching…');

  const matched = await runMatch(cropCanvas);
  busy = false;
  captureBtn.disabled = false;
  return matched;
}

async function runMatch(imageElement) {
  try {
    const matches = await hashMatcher.match(imageElement, 85, 5);
    if (!matches || matches.length === 0) {
      resultsList.innerHTML = '<div class="empty">No match found</div>';
      return false;
    }
    resultsTitle.textContent = 'Top matches';
    resultsList.innerHTML = matches.map(({ id, name, set, number, rarity, image, similarity }) => {
      const cls = similarity > 85 ? '' : similarity > 70 ? 'silver' : 'bronze';
      return `
        <div class="match">
          ${image ? `<img src="${image}" alt="" loading="lazy">` : `<div style="width:40px;height:56px;background:var(--bg-2);border-radius:6px"></div>`}
          <div class="info">
            <div class="name">${escapeHTML(name || id || '?')}</div>
            <div class="meta">${[set, number, rarity].filter(Boolean).join(' · ') || '—'}</div>
          </div>
          <div class="score ${cls}">${similarity.toFixed(1)}%</div>
        </div>`;
    }).join('');

    lastTopScore = matches[0].similarity / 100;
    if (lastTopScore >= 0.88) setStatus(`Auto-captured: ${Math.round(lastTopScore * 100)}% match`);

    // Auto-enqueue top match into shared review queue
    const top = matches[0];
    if (top.id && window.RQ.add({
      id: top.id,
      name: top.name || top.id,
      meta: [top.set, top.number].filter(Boolean).join(' · '),
      img: top.image || '',
      score: top.similarity / 100,
      source: 'local',
    })) {
      if (window.__localQueue) window.__localQueue.refresh();
    }
    return true;
  } catch (e) {
    resultsList.innerHTML = `<div class="err">Error: ${e.message}</div>`;
    return false;
  }
}

$('file-input').addEventListener('change', async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  busy = true; captureBtn.disabled = true;
  resultsList.innerHTML = '<div class="empty">Detecting card…</div>';
  
  const img = new Image();
  img.src = URL.createObjectURL(file);
  await new Promise(r => { img.onload = r; img.onerror = r; });
  
  // Detect card using YOLO11 OBB
  const detections = await detector.detect(img);
  if (!detections || detections.length === 0) {
    setStatus('No card detected in image');
    busy = false; captureBtn.disabled = false;
    return;
  }
  
  // Use the highest-confidence detection
  const best = detections[0];
  const { points } = best;
  
  // Create axis-aligned bounding box from OBB corners for cropping
  const xs = points.map(p => p[0]);
  const ys = points.map(p => p[1]);
  const x = Math.max(0, Math.min(...xs));
  const y = Math.max(0, Math.min(...ys));
  const w = Math.min(img.width - x, Math.max(...xs) - x);
  const h = Math.min(img.height - y, Math.max(...ys) - y);
  
  const cropCanvas = document.createElement('canvas');
  cropCanvas.width = w;
  cropCanvas.height = h;
  const cropCtx = cropCanvas.getContext('2d');
  cropCtx.drawImage(img, x, y, w, h, 0, 0, w, h);
  setStatus('Card detected, matching…');
  
  await runMatch(cropCanvas);
  busy = false; captureBtn.disabled = false;
});

$('capture-btn').addEventListener('click', capture);
$('auto-scan-btn').addEventListener('click', toggleAutoScan);
window.toggleAutoScan = toggleAutoScan;

async function init() {
  console.log('[SCANNER] init() called');
  await loadIndex();
  await loadModel();
  await startCamera();
  // Mount shared review queue; "Keep" posts straight to the collection API
  window.__localQueue = window.mountReviewQueue($('review-queue'), {
    async add(item) {
      const token = sessionStorage.getItem('access_token');
      if (!token) throw new Error('Not logged in');
      const fd = new FormData();
      fd.append('card_id', item.id);
      const r = await fetch('/api/collection/add', {
        method: 'POST',
        headers: token ? { Authorization: `Bearer ${token}` } : {},
        body: fd,
      });
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
    },
  });
  console.log('[SCANNER] init() complete');
}
init();

// Auto-scan loop: keep capturing; on a high-confidence match, toast,
// pause ~1.5s so the card can be flipped, then scan the next one.
window.isAutoScanning = false;
function toggleAutoScan() {
  window.isAutoScanning = !window.isAutoScanning;
  const btn = $('auto-scan-btn');
  btn.textContent = window.isAutoScanning ? '⏹ Auto-Scan: ON' : '▶ Auto-Scan: OFF';
  if (window.isAutoScanning) autoScanLoop();
}
async function autoScanLoop() {
  while (window.isAutoScanning) {
    if (!busy && detector && hashMatcher && stream) {
      const matched = await capture();
      if (matched) {
        // Give the user time to flip/remove the card before the next scan.
        // A short pause here causes the loop to re-detect the same card
        // (or, once the card is gone, to spam "no card detected").
        await new Promise(r => setTimeout(r, 3000));
        continue;
      }
    }
    // No match or no card — wait before retrying so the loop doesn't spin.
    await new Promise(r => setTimeout(r, 1200));
  }
}