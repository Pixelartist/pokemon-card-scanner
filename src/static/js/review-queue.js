// Review queue shared by cloud app (/) and local scanner (/scanner/) —
// same origin, one localStorage key. Item: {id,name,meta,img,score,source,at,scan,crop}
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
  return String(s).replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
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
  if (item.img) out.push(item.img);
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

function rqOpenCorrection(item) {
  if (document.getElementById(MODAL_ID)) return;
  const body = document.body;
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
        <div class="rqm-column">
          <div class="rqm-label">Your capture</div>
          <div class="rqm-capture">
            ${item.scan
              ? `<img src="/data/${rqEsc(item.scan)}" alt="Original scan">`
              : '<div class="rqm-missing">No capture</div>'}
          </div>
          ${item.crop
            ? `<div class="rqm-crop-wrap">
                <div class="rqm-label">Card crop</div>
                <div class="rqm-capture rqm-crop">
                  <img src="/data/${rqEsc(item.crop)}" alt="Card crop">
                </div>
               </div>`
            : ''}
        </div>
        <div class="rqm-column">
          <div class="rqm-label">Model guessed</div>
          <div class="rqm-guess">
            ${item.img
              ? `<img src="${rqEsc(item.img)}" alt="${rqEsc(item.name)}">`
              : '<div class="rqm-missing">No image</div>'}
            <div class="rqm-guess-name">${rqEsc(item.name)}</div>
            <div class="rqm-guess-meta">${rqEsc(item.meta || '')}${item.score ? ' · ' + Math.round(item.score * 100) + '%' : ''}</div>
          </div>
          <div class="rqm-search">
            <div class="rqm-label">What is it actually?</div>
            <input type="text" class="rqm-input" id="rqm-input"
                   placeholder="Type card name or set…" autocomplete="off">
            <div class="rqm-results" id="rqm-results"></div>
          </div>
        </div>
      </div>
      <div class="rqm-footer">
        <button class="rqm-btn-cancel" type="button">Cancel</button>
        <button class="rqm-btn-save" type="button" disabled>Save correction</button>
      </div>
    </div>`;
  body.appendChild(overlay);
  overlay.querySelector('.rqm-close').addEventListener('click', () => rqCloseCorrection());
  overlay.querySelector('.rqm-btn-cancel').addEventListener('click', () => rqCloseCorrection());
  overlay.addEventListener('click', e => { if (e.target === overlay) rqCloseCorrection(); });
  document.addEventListener('keydown', rqCloseOnEscape);
  const input = overlay.querySelector('#rqm-input');
  input.focus();
  const saveBtn = overlay.querySelector('.rqm-btn-save');
  let selected = null;
  input.addEventListener('input', () => rqSearchCorrect(input.value, overlay, saveBtn));
  input.addEventListener('keydown', e => {
    if (e.key === 'Enter' && selected) {
      e.preventDefault();
      saveBtn.click();
    } else if (e.key === 'Escape') {
      rqCloseCorrection();
    }
  });
  function rqCloseOnEscape(e) { if (e.key === 'Escape') rqCloseCorrection(); }
  function rqSearchCorrect(q, el, btn) {
    const results = el.querySelector('.rqm-results');
    if (!q || q.length < 2) { results.innerHTML = ''; btn.disabled = true; selected = null; return; }
    results.innerHTML = '<div class="rqm-loading">Searching…</div>';
    fetch(`/api/cards/search?query=${encodeURIComponent(q)}&limit=6`)
      .then(r => r.json())
      .then(data => {
        results.innerHTML = '';
        if (!data.results || data.results.length === 0) {
          results.innerHTML = '<div class="rqm-none">No matches found</div>';
          btn.disabled = true;
          return;
        }
        data.results.forEach(card => {
          const row = document.createElement('div');
          row.className = 'rqm-row';
          const img = card.images?.large || card.images?.small || '';
          row.innerHTML = img
            ? `<img src="${rqEsc(img)}" class="rqm-row-img" alt="">`
            : '<span class="rqm-row-noimg"></span>';
          row.innerHTML += `<div class="rqm-row-info"><span class="rqm-row-name">${rqEsc(card.name)}</span><span class="rqm-row-meta">${rqEsc(card.number)} · ${rqEsc(card.set_name || card.set || '')}</span></div>`;
          row.addEventListener('click', () => {
            selected = card;
            [...results.querySelectorAll('.rqm-row')].forEach(r => r.classList.remove('rqm-row-hl'));
            row.classList.add('rqm-row-hl');
            input.value = card.name + (card.set_name ? ' ' + card.set_name : '');
            btn.disabled = false;
          });
          results.appendChild(row);
        });
      })
      .catch(() => {
        results.innerHTML = '<div class="rqm-none">Search failed</div>';
        btn.disabled = true;
      });
  }
  saveBtn.addEventListener('click', async () => {
    if (!selected) return;
    saveBtn.disabled = true;
    saveBtn.textContent = 'Saving…';
    try {
      await postMisread({
        wrong_id: item.id,
        wrong_name: item.name,
        correct_id: selected.ptcg_id || selected.id || item.id,
        correct_name: selected.name,
        score: item.score,
        source: item.source,
        scan_image_path: item.scan,
        crop_image_path: item.crop,
      });
      localStorage.setItem('last_misread_' + item.id, Date.now());
      RQ.remove(item.id);
      rqCloseCorrection();
      window.showToast('Correction saved — card added to your collection');
      if (window.refreshReviewQueues) window.refreshReviewQueues();
      // Add the corrected card to the collection
      try {
        const correctId = selected.ptcg_id || selected.id || item.id;
        if (correctId && typeof window.addToCollectionById === 'function') {
          await window.addToCollectionById(correctId, selected.name);
        }
      } catch (addErr) {
        console.error('Could not add corrected card to collection:', addErr);
      }
    } catch (e) {
      saveBtn.disabled = false;
      alert('Could not log correction: ' + e.message);
    }
  });
}

function rqCloseCorrection() {
  const m = document.getElementById(MODAL_ID);
  if (!m) return;
  document.removeEventListener('keydown', rqCloseOnEscape);
  function rqCloseOnEscape() {}
  m.remove();
}

// --------------------------------------------------------------------------- Mount
function mountReviewQueue(root, opts = {}) {
  function render() {
    const items = RQ.load();
    const latest = items[0];
    // The tray collapses to a count pill when there is nothing to review, so it
    // never covers the camera framing box.
    root.classList.toggle('empty', items.length === 0);
    // Up to 3 chip previews for the collapsed camera mode bar
    const chips = items.slice(0, 3).map(q =>
      `<div class="queue-chip" data-id="${rqEsc(q.id)}" title="${rqEsc(q.name)}">` +
      `<img data-qid="${rqEsc(q.id)}" alt="">` +
      `<div class="chip-name">${rqEsc(q.name)}</div></div>`
    ).join('');
    root.innerHTML = `
      <div class="queue-header">
        <span>Review Queue <span class="queue-count${items.length ? '' : ' zero'}">${items.length}</span></span>
        ${latest ? `<span class="qi-latest"><img data-qid="${rqEsc(latest.id)}" alt=""><span class="qi-name">${rqEsc(latest.name)}</span></span>` : ''}
      </div>
      <div class="queue-chips">${chips}</div>
      <div class="queue-list">` + (items.length ? items.map(q => `\n        <div class="queue-item" data-id="${rqEsc(q.id)}">
          <img data-qid="${rqEsc(q.id)}" alt="">
          <div class="qi-info">
            <div class="qi-name">${rqEsc(q.name)}</div>
            <div class="qi-meta">${rqEsc(q.meta || '')}${q.score ? ' · ' + Math.round(q.score * 100) + '%' : ''} · ${rqEsc(q.source || '')}</div>
          </div>
          <div class="qi-actions">
            <button class="keep">Keep</button>
            <button class="correction">✗ Not this?</button>
            <button class="discard">✕</button>
          </div>
        </div>
      `).join('') : '<div class="empty">Nothing waiting for review.</div>') + `
      </div>
    `;
    rqPaintImages(root, items);
    if (opts.onChange) opts.onChange(items.length);
  }

  // In camera-fs collapsed mode, clicking a chip opens the overlay expanded
  // and scrolls to that card's row. Also prevents the header toggle.
  root.addEventListener('click', (e) => {
    const chip = e.target.closest('.queue-chip');
    if (chip) {
      e.stopPropagation();
      root.closest('#scan-overlay')?.classList.add('expanded');
      setTimeout(() => {
        const row = root.querySelector(`.queue-item[data-id="${chip.dataset.id}"]`);
        if (row) row.scrollIntoView({ behavior: 'smooth', block: 'center' });
      }, 50);
      return;
    }
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
      // Removal is deferred to onAdded — fires once the card is really in the collection
      try {
        if (opts.add) await opts.add(item, () => { RQ.remove(id); refreshAll(); });
        else { RQ.remove(id); }
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
