// Review queue shared by cloud app (/) and local scanner (/scanner/) —
// same origin, one localStorage key. Item: {id,name,meta,img,score,source,at,scan,crop,candidates}
const RQ_KEY = 'psc_review_queue';
const RQ = {
  load() { try { return JSON.parse(localStorage.getItem(RQ_KEY)) || []; } catch { return []; } },
  save(items) {
    // A near-miss entry can carry a crop data URL (~40 KB), so 100 entries can
    // blow the ~5 MB quota. Degrade by dropping the oldest half instead of
    // throwing out of the scan flow.
    for (;;) {
      try { localStorage.setItem(RQ_KEY, JSON.stringify(items.slice(0, 100))); return; }
      catch (e) {
        if (items.length <= 1) return;
        items = items.slice(0, Math.max(1, Math.ceil(items.length / 2)));
      }
    }
  },
  add(item) {
    const items = RQ.load();
    if (item.id && items.some(q => q.id === item.id)) return false;
    items.unshift({ ...item, at: Date.now() });
    this.save(items);
    return true;
  },
  remove(id) { this.save(this.load().filter(q => q.id !== id)); },
};

function rqEsc(s) {
  return String(s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
}

// Queue items hold either a server path (relative to /data/) or an inline data
// URL from the browser-side scanner; both have to survive into an <img src>.
function rqImgSrc(path) {
  if (!path) return '';
  return /^(data:|https?:|\/)/.test(path) ? path : '/data/' + path;
}

// ----------------------------------------------------------------- Card images
// Ported from quickdraw.cards `setCardImage()` (app.js): build a de-duplicated
// list of candidate URLs, point the <img> at the first, and step to the next on
// every `error` until a placeholder lands — a queue row can then never show a
// broken-image icon. A token per element guards late async resolutions, the same
// way their `cardImageToken` does, because a re-render replaces the element but
// not the fetch already in flight for it.
const RQ_PLACEHOLDER = '/static/images/placeholder.png';
const rqImageCache = new Map(); // card id -> local image url ('' = known missing)

function rqImageSources(item) {
  const out = [];
  if (item.img) out.push(rqImgSrc(item.img));
  const local = rqImageCache.get(item.id);
  if (local) out.push(local);
  out.push(RQ_PLACEHOLDER);
  return out.filter((u, i, all) => u && all.indexOf(u) === i);
}

// Local-first resolution: prefer the in-page card index (the scanner already
// holds every card's image path after loadIndex), then fall back to
// /api/cards/{id}/details, which checks our own SQLite rows and cards_meta.json
// before any upstream API. That ordering is what makes thumbnails resolve for
// Pocket/German/promo cards the upstream APIs don't list at all.
async function rqResolveLocalImage(id) {
  if (rqImageCache.has(id)) return rqImageCache.get(id);
  let url = '';
  if (typeof window.rqCardImageLookup === 'function') {
    try {
      const fromIndex = window.rqCardImageLookup(id);
      if (fromIndex && fromIndex.startsWith('/')) url = fromIndex;
    } catch { /* index lookup is best-effort */ }
  }
  if (!url) {
    try {
      const r = await fetch(`/api/cards/${encodeURIComponent(id)}/details`);
      if (r.ok) {
        const imgs = (await r.json())?.basic_info?.images || {};
        const found = imgs.large || imgs.small || '';
        // Only accept a path we serve ourselves; an upstream absolute URL is
        // exactly what goes stale, so it must not displace the local one.
        if (found && found.startsWith('/')) url = found;
      }
    } catch { /* offline — the cascade still ends on the placeholder */ }
  }
  rqImageCache.set(id, url);
  return url;
}

function rqSetImage(imgEl, item) {
  if (!imgEl || !item) return;
  const token = {};
  imgEl.__rqToken = token;
  const apply = (list) => {
    let next = 0;
    imgEl.onerror = () => {
      if (next < list.length) imgEl.src = list[next++];
      else imgEl.onerror = null;
    };
    imgEl.src = list[next++];
  };
  apply(rqImageSources(item));
  if (rqImageCache.has(item.id)) return; // already resolved, or known-missing
  rqResolveLocalImage(item.id).then((local) => {
    if (!local || imgEl.__rqToken !== token) return;
    if (imgEl.getAttribute('src') === local) return;
    apply(rqImageSources({ ...item, img: local }));
  });
}

// Point every queue image we just rendered at its resolved source.
function rqPaintImages(root, items) {
  const byId = new Map(items.map(q => [q.id, q]));
  root.querySelectorAll('img[data-qid]').forEach(el => rqSetImage(el, byId.get(el.dataset.qid)));
}

async function postMisread(payload) {
  const token = window._getAuthToken || window.getAuthToken;
  const headers = token ? { Authorization: `Bearer ${token()}`, 'Content-Type': 'application/json' } : { 'Content-Type': 'application/json' };
  try {
    const r = await fetch('/api/misreads', { method: 'POST', headers, body: JSON.stringify(payload) });
    if (!r.ok) throw new Error(r.statusText);
    return await r.json();
  } catch (e) {
    console.error('Failed to log misread:', e);
    throw e;
  }
}

// --------------------------------------------------------------------------- Modal
const MODAL_ID = 'rq-modal';

// "Not this" — the capture stays on the left no matter what is picked on the
// right, so you always compare the photo you took against the card you file.
function rqOpenCorrection(item) {
  if (document.getElementById(MODAL_ID)) return;

  const original = {
    id: item.id,
    name: item.name,
    number: '',
    set: '',
    meta: item.meta || '',
    img: item.img || '',
    score: item.score || 0,
  };
  // The current guess is the first choice, then every other match the scan
  // offered. Corrections therefore never need a re-scan.
  const choices = [original, ...(item.candidates || [])
    .filter(c => c && c.id && c.id !== item.id)
    .map(c => ({ id: c.id, name: c.name || c.id, number: c.number || '', set: c.set || '', meta: '', img: c.img || '', score: c.score || 0 }))];
  let selected = original;

  const captureSrc = rqImgSrc(item.crop) || rqImgSrc(item.scan) || RQ_PLACEHOLDER;
  const captureLabel = item.crop ? 'Your card crop' : (item.scan ? 'Your capture' : 'No capture');

  const overlay = document.createElement('div');
  overlay.className = 'rq-modal-backdrop';
  overlay.id = MODAL_ID;
  overlay.setAttribute('role', 'dialog');
  overlay.setAttribute('aria-modal', 'true');
  overlay.innerHTML = `
    <div class="rqm-panel">
      <div class="rqm-header">
        <div class="rqm-title">Not this card?</div>
        <button class="rqm-close" type="button" aria-label="Close">✕</button>
      </div>
      <div class="rqm-body">
        <div class="rqm-column rqm-column-capture">
          <div class="rqm-label">${rqEsc(captureLabel)}</div>
          <div class="rqm-capture"><img src="${rqEsc(captureSrc)}" alt="Your capture"></div>
        </div>
        <div class="rqm-column">
          <div class="rqm-label">Identified as</div>
          <div class="rqm-guess" id="rqm-guess"></div>
          <div class="rqm-label rqm-label-gap">Other options</div>
          <div class="rqm-options" id="rqm-options"></div>
          <div class="rqm-label rqm-label-gap">Or search for the right card</div>
          <input type="text" class="rqm-input" id="rqm-input" placeholder="Type card name or set…" autocomplete="off">
          <div class="rqm-results" id="rqm-results"></div>
        </div>
      </div>
      <div class="rqm-footer">
        <button class="rqm-btn-cancel" type="button">Cancel</button>
        <button class="rqm-btn-save" type="button">Add to gallery</button>
      </div>
    </div>`;
  document.body.appendChild(overlay);

  const guessBox = overlay.querySelector('#rqm-guess');
  const optionsBox = overlay.querySelector('#rqm-options');
  const resultsBox = overlay.querySelector('#rqm-results');
  const input = overlay.querySelector('#rqm-input');
  const saveBtn = overlay.querySelector('.rqm-btn-save');

  function guessMeta(c) {
    return [c.number, c.set, c.meta].filter(Boolean).join(' · ');
  }

  function renderGuess() {
    const meta = guessMeta(selected);
    const score = selected.score ? Math.round(selected.score * 100) + '%' : '';
    guessBox.innerHTML = `
      <img src="${rqEsc(rqImgSrc(selected.img) || RQ_PLACEHOLDER)}" alt="${rqEsc(selected.name || '')}">
      <div class="rqm-guess-text">
        <div class="rqm-guess-name">${rqEsc(selected.name || selected.id)}</div>
        <div class="rqm-guess-meta">${rqEsc(meta)}${score ? (meta ? ' · ' : '') + score : ''}</div>
      </div>`;
  }

  function renderOptions() {
    optionsBox.innerHTML = '';
    choices.forEach(c => {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'rqm-option' + (c.id === selected.id ? ' active' : '');
      btn.innerHTML = `<img src="${rqEsc(rqImgSrc(c.img) || RQ_PLACEHOLDER)}" alt=""><span>${rqEsc(c.name)}</span>`;
      btn.addEventListener('click', () => {
        selected = c;
        renderGuess();
        renderOptions();
      });
      optionsBox.appendChild(btn);
    });
  }

  function close() { rqCloseCorrection(); }
  function onKey(e) { if (e.key === 'Escape') close(); }
  overlay.__rqKeyHandler = onKey;
  document.addEventListener('keydown', onKey);
  overlay.querySelector('.rqm-close').addEventListener('click', close);
  overlay.querySelector('.rqm-btn-cancel').addEventListener('click', close);
  overlay.addEventListener('click', e => { if (e.target === overlay) close(); });

  input.addEventListener('input', () => {
    const q = input.value.trim();
    if (q.length < 2) { resultsBox.innerHTML = ''; return; }
    resultsBox.innerHTML = '<div class="rqm-loading">Searching…</div>';
    fetch(`/api/cards/search?query=${encodeURIComponent(q)}&limit=6`)
      .then(r => r.json())
      .then(data => {
        const results = data.results || [];
        resultsBox.innerHTML = '';
        if (results.length === 0) {
          resultsBox.innerHTML = '<div class="rqm-none">No matches found</div>';
          return;
        }
        results.forEach(card => {
          const row = document.createElement('div');
          row.className = 'rqm-row';
          const img = card.images?.large || card.images?.small || '';
          row.innerHTML = (img ? `<img src="${rqEsc(img)}" class="rqm-row-img" alt="">` : '')
            + `<div class="rqm-row-info"><span class="rqm-row-name">${rqEsc(card.name)}</span>`
            + `<span class="rqm-row-meta">${rqEsc(card.number)} · ${rqEsc(card.set_name || card.set || '')}</span></div>`;
          row.addEventListener('click', () => {
            selected = {
              id: card.ptcg_id || card.id || card.name,
              name: card.name,
              number: card.number || '',
              set: card.set_name || card.set || '',
              meta: '',
              img,
              score: 0,
            };
            [...resultsBox.querySelectorAll('.rqm-row')].forEach(r => r.classList.remove('rqm-row-hl'));
            row.classList.add('rqm-row-hl');
            input.value = card.name;
            renderGuess();
            renderOptions();
          });
          resultsBox.appendChild(row);
        });
      })
      .catch(() => { resultsBox.innerHTML = '<div class="rqm-none">Search failed</div>'; });
  });

  saveBtn.addEventListener('click', async () => {
    if (saveBtn.disabled) return;
    saveBtn.disabled = true;
    saveBtn.textContent = 'Opening…';
    // A correction is training data; a failed log must not block the add.
    if (selected.id !== item.id) {
      postMisread({
        wrong_id: item.id,
        wrong_name: item.name,
        correct_id: selected.id,
        correct_name: selected.name,
        score: item.score,
        source: item.source,
        scan_image_path: item.scan,
        crop_image_path: item.crop,
      }).catch(() => {});
      try { localStorage.setItem('last_misread_' + item.id, Date.now()); } catch { /* quota */ }
    }
    close();
    try {
      if (typeof window.addToCollectionById === 'function') {
        // Same contract as "Keep": the row id rides along with the add form and
        // is cleared only when that form is submitted, so cancelling or adding
        // something else cannot remove this entry.
        await window.addToCollectionById(selected.id, selected.name, item.id, 'corrected',
          item.scan ? { scan: item.scan, crop: item.crop,
                        proposal: { id: item.id, similarity: +((item.score || 0) * 100).toFixed(1) } }
                    : null);
      } else {
        RQ.remove(item.id);
        if (window.refreshReviewQueues) window.refreshReviewQueues();
      }
    } catch (e) {
      saveBtn.disabled = false;
      saveBtn.textContent = 'Add to gallery';
      alert('Could not open the add form: ' + e.message);
    }
  });

  renderGuess();
  renderOptions();
}

function rqCloseCorrection() {
  const m = document.getElementById(MODAL_ID);
  if (!m) return;
  if (m.__rqKeyHandler) document.removeEventListener('keydown', m.__rqKeyHandler);
  m.remove();
}

// --------------------------------------------------------------------------- Mount
function mountReviewQueue(root, opts = {}) {
  function render() {
    const items = RQ.load();
    // One compact row per card: thumbnail, name, and exactly three choices.
    const rows = items.map(q => `
        <div class="queue-item" data-id="${rqEsc(q.id)}">
          <img data-qid="${rqEsc(q.id)}" alt="">
          <div class="qi-info">
            <div class="qi-name">${rqEsc(q.name)}</div>
            <div class="qi-meta">${rqEsc(q.meta || '')}${q.score ? ' · ' + Math.round(q.score * 100) + '%' : ''}</div>
          </div>
          <div class="qi-actions">
            <button class="keep" type="button">Keep</button>
            <button class="correction" type="button">Not this</button>
            <button class="discard" type="button">Remove</button>
          </div>
        </div>
      `).join('');
    root.innerHTML = `
      <div class="queue-header">
        <span>Review Queue <span class="queue-count${items.length ? '' : ' zero'}">${items.length}</span></span>
      </div>
      <div class="queue-list">${items.length ? rows : '<div class="empty">Nothing waiting for review.</div>'}</div>
    `;
    rqPaintImages(root, items);
  }

  // Collapsed camera mode expands the tray when the header is tapped.
  root.addEventListener('click', (e) => {
    if (e.target === root || e.target.closest('.queue-header')) {
      if (!e.target.closest('.queue-item')) {
        root.closest('#scan-overlay')?.classList.toggle('expanded');
      }
    }
  });
  // Refresh every mounted queue (sidebar + camera overlay) so no instance
  // keeps a stale row after a mutation performed through another one.
  const refreshAll = () => (window.refreshReviewQueues ? window.refreshReviewQueues() : render());
  window.addEventListener('click', async (e) => {
    const row = e.target.closest('.queue-item');
    // This mount only handles rows inside its own container; otherwise the
    // first-mounted queue would swallow clicks meant for the other one and
    // only re-render itself, leaving the clicked queue visually unchanged.
    if (!row || !root.contains(row)) return;
    const id = row.dataset.id;
    const item = RQ.load().find(q => q.id === id);
    if (!item) return;
    if (e.target.closest('.keep')) {
      // Removal is deferred to the add form — fires once the card is really in
      // the collection (opts.add records the row id for submitAddCollection).
      try {
        // opts.add opens the add form; the row leaves the queue once the card
        // is really in the collection, so there is nothing to remove here.
        if (opts.add) await opts.add(item);
        else RQ.remove(id);
      }
      catch (err) { e.target.closest('.qi-actions')?.insertAdjacentHTML('beforeend', `<span class="qi-status">${rqEsc(err.message)}</span>`); return; }
    } else if (e.target.closest('.correction')) {
      rqOpenCorrection(item);
      return;
    } else if (e.target.closest('.discard')) {
      RQ.remove(id);
    } else return;
    refreshAll();
  });
  render();
  return { refresh: render };
}

window.RQ = RQ;
window.rqOpenCorrection = rqOpenCorrection;
window.rqCloseCorrection = rqCloseCorrection;
window.mountReviewQueue = mountReviewQueue;
