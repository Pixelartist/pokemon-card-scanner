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
    // Only a row the matcher could not name is worth a background attempt; a
    // card we already identified would spend OCR (and possibly an LLM call)
    // re-reading what the hash just told us.
    if (item.source === 'near_miss') rqEnqueueIdentify(item.id);
    return true;
  },
  remove(id) { this.save(this.load().filter(q => q.id !== id)); },
};

function rqEsc(s) {
  return String(s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
}

// ------------------------------------------------- Background identification
// A capture the matcher could not name is also handed to the server, which reads
// the printed set code and number with OCR and, when that is inconclusive, asks
// a vision LLM. The row reports the outcome, and once every attempt is spent it
// says so instead of looking like it is still being worked on.
const RQ_JOB_LABEL = {
  queued: 'OCR queued',
  running: 'reading text…',
  exhausted: 'OCR + AI exhausted',
  unavailable: 'no OCR/AI configured',
  error: 'background attempt failed',
  // The read happened, but this session cannot ask the server about it any more.
  unverified: 'sign in to check the read',
};
const RQ_JOB_FINAL = ['done', 'exhausted', 'unavailable', 'error', 'unverified'];

// A row whose read the server still has: pressing OCR/AI again would queue a
// second job for the same crop, and the button has to stay down when the dialog
// is closed and reopened, because the row — not the dialog — owns that state.
function rqJobPending(q) {
  return !!(q && q.jobId && !RQ_JOB_FINAL.includes(q.jobStatus));
}

// A row the user believes is wrong can ask for the OCR/AI read by hand, even
// though the matcher named it: that is exactly when it is worth paying for.
function rqAssistButton(q) {
  const ready = !!rqQueueableCrop(q);
  const busy = rqJobPending(q);
  const title = !ready ? 'No stored crop to read'
    : busy ? 'This crop is already being read'
    : 'Read this card again with OCR, then AI';
  return `<button class="assist" type="button"${ready && !busy ? '' : ' disabled'}`
    + ` title="${title}">OCR / AI</button>`;
}

function rqJobBadge(q) {
  const label = RQ_JOB_LABEL[q.jobStatus];
  if (!label) return '';
  const spent = ['exhausted', 'unavailable', 'error', 'unverified'].includes(q.jobStatus);
  return `<div class="qi-job${spent ? ' spent' : ''}">${rqEsc(label)}</div>`;
}

function rqAuthHeaders() {
  const token = window._getAuthToken || window.getAuthToken;
  const headers = { 'Content-Type': 'application/json' };
  if (token) headers.Authorization = `Bearer ${token()}`;
  return headers;
}

// What the worker needs to read the crop later: a path it can open, or the
// inline data URL the browser-side scanner keeps (the server persists that).
// Remote or same-origin URLs are not addressable by the worker, so those rows
// simply get no background attempt.
function rqQueueableCrop(item) {
  const crop = item && item.crop;
  if (!crop) return null;
  if (/^data:image\//.test(crop)) return { crop_data_url: crop };
  if (/^(data:|https?:|\/)/.test(crop)) return null;
  return { crop_image_path: crop };
}

async function rqEnqueueIdentify(id, opts = {}) {
  const item = RQ.load().find(q => q.id === id);
  const crop = rqQueueableCrop(item);
  if (!item || !crop) return false;
  // Unidentified rows queue themselves. A row the matcher named is only re-read
  // when the user asks for it — `Not this` may have told us the name is wrong,
  // or they may simply disagree with it.
  if (item.source !== 'near_miss' && !opts.userAsk) return false;
  // One read per row at a time. A pending job means the answer is already on its
  // way; a finished one can be asked again.
  if (rqJobPending(item)) return false;
  if (item.jobId && !opts.userAsk) return false;
  try {
    const r = await fetch('/api/identify/enqueue', {
      method: 'POST',
      headers: rqAuthHeaders(),
      body: JSON.stringify({ ...crop, scan_image_path: item.scan || null, card_key: item.id }),
    });
    if (!r.ok) return false;
    const { job_id: jobId } = await r.json();
    const items = RQ.load();
    const target = items.find(q => q.id === id);
    if (!target) return false;
    target.jobId = jobId;
    target.jobStatus = 'queued';
    if (opts.userAsk) {
      // The previous verdict is the thing being questioned: drop the flag that
      // froze it, and the suggestion text it wrote, or the next answer would
      // land behind `!q.resolved` and the row could never change.
      delete target.resolved;
      if (target.metaFromJob) { delete target.meta; delete target.metaFromJob; }
    }
    RQ.save(items);
    if (window.refreshReviewQueues) window.refreshReviewQueues();
    return true;
  } catch { return false; /* the row still works without a background attempt */ }
}

async function rqPollJobs() {
  let items = RQ.load();
  const pending = items.filter(rqJobPending);
  if (!pending.length) return;
  try {
    const r = await fetch('/api/identify/jobs?ids=' + pending.map(q => q.jobId).join(','),
                          { headers: rqAuthHeaders() });
    if (r.status === 401) {
      // Signed out. The server has an answer we are not allowed to read, and a
      // row left at "OCR queued" would look like it is still being worked on
      // forever (and, since a pending row keeps its button down, never be
      // readable again). Say so and let the button go.
      let stale = false;
      for (const q of items) {
        if (rqJobPending(q)) { q.jobStatus = 'unverified'; stale = true; }
      }
      if (stale) {
        RQ.save(items);
        if (window.refreshReviewQueues) window.refreshReviewQueues();
      }
      return;
    }
    if (!r.ok) return;
    const jobs = (await r.json()).jobs || [];
    const byId = new Map(jobs.map(j => [j.id, j]));
    let changed = false;
    for (const q of items) {
      const job = byId.get(q.jobId);
      if (!job) continue;
      if (job.status !== q.jobStatus) { q.jobStatus = job.status; changed = true; }
      if (job.status === 'done' && job.result_id && !q.resolved) {
        // The read is the better identity, so the row becomes that card: Keep
        // has to add what the row shows, not what the hash guessed before it.
        q.id = job.result_id;
        q.name = job.result_name || q.name;
        q.meta = `OCR/AI via ${job.provider || 'worker'}`;
        q.metaFromJob = true;
        q.img = '';
        q.resolved = true;
        changed = true;
      }
    }
    if (changed) {
      // A re-check can land on a card that is already queued; one row per card.
      const byCard = new Map();
      for (const q of items) {
        const kept = byCard.get(q.id);
        if (!kept || (q.resolved && !kept.resolved)) byCard.set(q.id, q);
      }
      items = [...byCard.values()];
      RQ.save(items);
      if (window.refreshReviewQueues) window.refreshReviewQueues();
    }
  } catch { /* polling is best-effort */ }
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

// A background read can take a few seconds (OCR, then a vision LLM). The modal
// polls its own job so the answer lands in the comparison instead of only in
// the queue row behind it.
async function rqWaitForJob(jobId, timeout = 90000) {
  const end = Date.now() + timeout;
  while (Date.now() < end) {
    try {
      const r = await fetch('/api/identify/jobs?ids=' + encodeURIComponent(jobId),
                            { headers: rqAuthHeaders() });
      if (r.status === 401) return { status: 'unverified' };
      if (r.ok) {
        const job = ((await r.json()).jobs || [])[0];
        if (job && RQ_JOB_FINAL.includes(job.status)) return job;
      }
    } catch { /* keep waiting until the deadline */ }
    await new Promise(res => setTimeout(res, 1500));
  }
  return null;
}

// "Not this" — the capture stays on the left no matter what is picked on the
// right, so you always compare the photo you took against the card you file.
// That comparison owns the top of the dialog at the largest size the viewport
// allows; the pickers, the manual search and the OCR/AI read sit below it.
function rqOpenCorrection(item) {
  if (document.getElementById(MODAL_ID)) return;

  const original = {
    id: item.id,
    name: item.name,
    meta: item.meta || '',
    score: item.score || 0,
    // The row's own thumbnail. Without it the dialog had no picture for the card
    // the matcher actually named, so the selected frame fell back to the
    // placeholder even when the row beside it showed the card.
    img: item.img || '',
  };
  // The current guess is the first choice, then every other match the scan
  // offered. Corrections therefore never need a re-scan.
  const choices = [original, ...(item.candidates || [])
    .filter(c => c && c.id && c.id !== item.id)
    .map(c => ({ id: c.id, name: c.name || c.id, number: c.number || '', set: c.set || '', meta: '', img: c.img || '', score: c.score || 0 }))];
  let selected = original;
  // The queue row this dialog will clear. A completed OCR/AI read renames the
  // row to the card it found, so this has to follow it.
  let rowId = item.id;

  const captureSrc = rqImgSrc(item.crop) || rqImgSrc(item.scan) || RQ_PLACEHOLDER;
  const captureLabel = item.crop ? 'Your capture' : (item.scan ? 'Your frame' : 'No capture');
  const canAssist = !!rqQueueableCrop(item);

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
      <div class="rqm-compare">
        <div class="rqm-shot">
          <div class="rqm-label">${rqEsc(captureLabel)}</div>
          <div class="rqm-photo"><img src="${rqEsc(captureSrc)}" alt="Your capture"></div>
        </div>
        <div class="rqm-shot rqm-shot-selected">
          <div class="rqm-label">Selected card</div>
          <div class="rqm-photo"><img id="rqm-selected-img" alt=""></div>
        </div>
      </div>
      <div class="rqm-details">
        <div class="rqm-selected-name" id="rqm-selected-name"></div>
        <div class="rqm-selected-meta" id="rqm-selected-meta"></div>
        <div class="rqm-assist">
          <button class="rqm-assist-btn" id="rqm-assist" type="button">OCR / AI</button>
          <span class="rqm-assist-status" id="rqm-assist-status" role="status" aria-live="polite"></span>
        </div>
        <div class="rqm-label rqm-label-gap">Other options</div>
        <div class="rqm-options" id="rqm-options"></div>
        <div class="rqm-label rqm-label-gap">Or search for the right card</div>
        <input type="text" class="rqm-input" id="rqm-input" placeholder="Type card name or set…" autocomplete="off">
        <div class="rqm-results" id="rqm-results"></div>
      </div>
      <div class="rqm-footer">
        <button class="rqm-btn-cancel" type="button">Cancel</button>
        <button class="rqm-btn-save" type="button">Add to gallery</button>
      </div>
    </div>`;
  document.body.appendChild(overlay);

  const selectedImg = overlay.querySelector('#rqm-selected-img');
  const selectedName = overlay.querySelector('#rqm-selected-name');
  const selectedMeta = overlay.querySelector('#rqm-selected-meta');
  const optionsBox = overlay.querySelector('#rqm-options');
  const resultsBox = overlay.querySelector('#rqm-results');
  const input = overlay.querySelector('#rqm-input');
  const saveBtn = overlay.querySelector('.rqm-btn-save');
  const assistBtn = overlay.querySelector('#rqm-assist');
  const assistStatus = overlay.querySelector('#rqm-assist-status');

  function guessMeta(c) {
    return [c.number, c.set, c.meta].filter(Boolean).join(' · ');
  }

  // The right-hand frame is the card this dialog will file. Everything that can
  // change that answer goes through here, so the comparison never goes stale
  // behind the controls.
  function renderSelected() {
    selectedImg.alt = selected.name || '';
    // Same cascade as the queue rows: the row's image, then the in-page index or
    // the details endpoint, then the placeholder. A candidate the index does not
    // carry (German/Pocket prints) still resolves instead of showing a gap.
    rqSetImage(selectedImg, { id: selected.id, img: selected.img || '' });
    selectedName.textContent = selected.name || selected.id;
    const meta = guessMeta(selected);
    const score = selected.score ? Math.round(selected.score * 100) + '%' : '';
    selectedMeta.textContent = [meta, score].filter(Boolean).join(' · ');
  }

  function renderOptions() {
    optionsBox.innerHTML = '';
    choices.forEach(c => {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'rqm-option' + (c.id === selected.id ? ' active' : '');
      btn.innerHTML = `<img alt=""><span>${rqEsc(c.name)}</span>`;
      const thumb = btn.querySelector('img');
      if (thumb) rqSetImage(thumb, { id: c.id, img: c.img || '' });
      btn.addEventListener('click', () => pick(c));
      optionsBox.appendChild(btn);
    });
  }

  function pick(c) {
    selected = c;
    renderSelected();
    renderOptions();
  }

  function setAssistStatus(text, tone = '') {
    assistStatus.textContent = text || '';
    assistStatus.className = 'rqm-assist-status' + (tone ? ' ' + tone : '');
  }

  if (!canAssist) {
    assistBtn.disabled = true;
    assistBtn.title = 'This row has no stored crop to read';
    setAssistStatus('No stored crop to read');
  } else if (rqJobPending(item)) {
    // Reopening the dialog must not hand back a button the server is already
    // working for; the row remembers the job, so the button starts pressed.
    assistBtn.disabled = true;
    assistBtn.title = 'This crop is already being read';
    setAssistStatus(item.jobStatus === 'running'
      ? 'Reading the card…'
      : 'Queued — waiting for the worker');
  }

  // Read the card's printed name and number (OCR first, then a vision LLM) and
  // put the answer straight into the comparison, without leaving the dialog.
  assistBtn.addEventListener('click', async () => {
    if (!canAssist || assistBtn.disabled) return;
    assistBtn.disabled = true;
    setAssistStatus('Queuing OCR…');
    if (!await rqEnqueueIdentify(item.id, { userAsk: true })) {
      setAssistStatus('Could not queue the read', 'bad');
      assistBtn.disabled = false;
      return;
    }
    const stored = RQ.load().find(q => q.id === item.id);
    const jobId = stored && stored.jobId;
    if (!jobId) {
      setAssistStatus('Queued — watch the row for the result');
      assistBtn.disabled = false;
      return;
    }
    setAssistStatus('Reading the card…');
    const job = await rqWaitForJob(jobId);
    if (!job) {
      // The wait gave up, not the job: stay pressed while the server still has it.
      setAssistStatus('Still reading — check the row in a moment');
      assistBtn.disabled = rqJobPending(RQ.load().find(q => q.jobId === jobId));
      return;
    }
    if (job.status === 'done' && job.result_id) {
      // rqPollJobs turns the queue row into the card that was read, so the row
      // this dialog clears moves with it.
      await rqPollJobs();
      const rows = RQ.load();
      rowId = rows.some(q => q.id === job.result_id) ? job.result_id
            : (rows.some(q => q.id === item.id) ? item.id : rowId);
      const img = await rqResolveLocalImage(job.result_id);
      const found = {
        id: job.result_id,
        name: job.result_name || job.result_id,
        number: '', set: '',
        meta: `OCR/AI via ${job.provider || 'worker'}`,
        img: img || '',
        score: 0,
      };
      const at = choices.findIndex(c => c.id === found.id);
      if (at >= 0) choices[at] = found; else choices.unshift(found);
      pick(found);
      setAssistStatus(`Read: ${found.name}`, 'ok');
    } else if (job.status === 'unverified') {
      // A lapsed session, not a failed read: say which, so signing in again is
      // the obvious next step instead of pressing the button once more.
      const rows = RQ.load();
      const row = rows.find(q => q.jobId === jobId);
      if (row) {
        row.jobStatus = 'unverified';
        RQ.save(rows);
        if (window.refreshReviewQueues) window.refreshReviewQueues();
      }
      setAssistStatus('Session expired — sign in, then read again', 'bad');
    } else if (job.status === 'unavailable') {
      setAssistStatus('No OCR/AI configured on the server', 'bad');
    } else if (job.status === 'exhausted') {
      setAssistStatus('OCR + AI could not read it', 'bad');
    } else {
      setAssistStatus('Read failed', 'bad');
    }
    assistBtn.disabled = false;
  });

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
            [...resultsBox.querySelectorAll('.rqm-row')].forEach(r => r.classList.remove('rqm-row-hl'));
            row.classList.add('rqm-row-hl');
            input.value = card.name;
            pick({
              id: card.ptcg_id || card.id || card.name,
              name: card.name,
              number: card.number || '',
              set: card.set_name || card.set || '',
              meta: '',
              img,
              score: 0,
            });
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
    // A correction is training data; a failed log must not block the add. The
    // wrong id is always the guess the row arrived with, even when the OCR/AI
    // read has since renamed the row.
    if (selected.id !== original.id) {
      postMisread({
        wrong_id: original.id,
        wrong_name: original.name,
        correct_id: selected.id,
        correct_name: selected.name,
        score: item.score,
        source: item.source,
        scan_image_path: item.scan,
        crop_image_path: item.crop,
      }).catch(() => {});
      try { localStorage.setItem('last_misread_' + original.id, Date.now()); } catch { /* quota */ }
    }
    close();
    try {
      if (typeof window.addToCollectionById === 'function') {
        // Same contract as "Keep": the row id rides along with the add form and
        // is cleared only when that form is submitted, so cancelling or adding
        // something else cannot remove this entry.
        await window.addToCollectionById(selected.id, selected.name, rowId, 'corrected',
          window.queueScanFrame ? window.queueScanFrame(item) : null);
      } else {
        RQ.remove(rowId);
        if (window.refreshReviewQueues) window.refreshReviewQueues();
      }
    } catch (e) {
      saveBtn.disabled = false;
      saveBtn.textContent = 'Add to gallery';
      alert(t('Could not open the add form') + ': ' + e.message);
    }
  });

  renderSelected();
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
    // One compact row per card: thumbnail, name, and the three choices, plus the
    // OCR/AI read for a row the user thinks was identified wrong.
    const rows = items.map(q => `
        <div class="queue-item" data-id="${rqEsc(q.id)}">
          <img data-qid="${rqEsc(q.id)}" alt="">
          <div class="qi-info">
            <div class="qi-name">${rqEsc(q.name)}</div>
            <div class="qi-meta">${rqEsc(q.meta || '')}${q.score ? ' · ' + Math.round(q.score * 100) + '%' : ''}</div>
            ${rqJobBadge(q)}
          </div>
          <div class="qi-actions">
            ${rqAssistButton(q)}
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
    rqPollJobs();
  }

  // A background job finishes on the server, after the row was drawn, so the
  // badge needs a poll. Every mounted queue would start its own timer, so one
  // shared interval drives them all, and it only ticks while a job is pending.
  if (!mountReviewQueue.__poll) {
    mountReviewQueue.__poll = setInterval(() => {
      const waiting = RQ.load().some(q => q.jobId &&
        !['done', 'exhausted', 'unavailable', 'error'].includes(q.jobStatus));
      if (waiting) rqPollJobs();
    }, 15000);
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
    } else if (e.target.closest('.assist')) {
      // The user says this name is wrong: spend the OCR/AI read on it now.
      const queued = await rqEnqueueIdentify(id, { userAsk: true });
      if (!queued) {
        e.target.closest('.qi-actions')?.insertAdjacentHTML('beforeend',
          '<span class="qi-status">could not queue</span>');
      }
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
