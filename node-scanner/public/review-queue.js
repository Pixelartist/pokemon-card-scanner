// Review queue shared by cloud app (/) and local scanner (/scanner/) —
// same origin, one localStorage key. Item: {id,name,meta,img,score,source,at}
const RQ_KEY = 'psc_review_queue';
const RQ = {
  load() { try { return JSON.parse(localStorage.getItem(RQ_KEY)) || []; } catch { return []; } },
  save(items) { localStorage.setItem(RQ_KEY, JSON.stringify(items.slice(0, 100))); },
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

async function postMisread(payload) {
  const token = window._getAuthToken ? window._getAuthToken() : null;
  const headers = {};
  if (token) headers.Authorization = `Bearer ${token}`;
  try {
    const r = await fetch('/api/misreads', { method: 'POST', headers, body: JSON.stringify(payload) });
    if (!r.ok) throw new Error(r.statusText);
    return await r.json();
  } catch (e) {
    console.error('Failed to log misread:', e);
    throw e;
  }
}

function _createCorrectionUI() {
  const btn = document.createElement('button');
  btn.className = 'correction';
  btn.textContent = '✗ Not this?';
  btn.title = 'Log a correction so we can train this hard case out of the model';
  btn.onclick = async (e) => {
    const row = e.target.closest('.queue-item');
    if (!row) return;
    const id = row.dataset.id;
    const item = RQ.load().find(q => q.id === id);
    if (!item) return;
    if (!window.confirm('Log that this card ID is wrong and store the scan+crop so we can re-embed / harden this case?')) return;
    const correctId = prompt('What card is this actually? (name or id)');
    if (!correctId) return;
    const status = row.querySelector('.qi-status') || row.querySelector('.qi-actions')?.children[1];
    const working = status ? (status.textContent = 'Logging...') : null;
    const payload = {
      wrong_id: item.id,
      wrong_name: item.name,
      correct_id: correctId,
      correct_name: correctId,
      score: item.score,
      source: item.source
    };
    if (item.scan) payload.scan_image_path = 'scans/' + item.scan;
    if (item.crop) payload.crop_image_path = 'scans/' + item.crop;
    try {
      await postMisread(payload);
      localStorage.setItem('last_misread_' + id, Date.now());
      if (working) working.textContent = 'Logged';
      window.showToast('Correction logged — thanks for improving the model!');
      RQ.remove(id);
    } catch (e) {
      if (working) working.textContent = 'Failed';
      alert('Could not log correction: ' + e.message + '\nRetrying...');
    }
  };
  return btn;
}
function mountReviewQueue(root, opts = {}) {
  function render() {
    const items = RQ.load();
    root.innerHTML = `
      <div class="queue-header">
        <span>Review Queue <span class="queue-count${items.length ? '' : ' zero'}">${items.length}</span></span>
      </div>
      <div class="queue-list">` + (items.length ? items.map(q => `
        <div class="queue-item" data-id="${rqEsc(q.id)}">
          ${q.img ? `<img src="${rqEsc(q.img)}" alt="">` : '<div class="qi-thumb-empty"></div>'}
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
    if (opts.onChange) opts.onChange(items.length);
  }
  if (window._createCorrectionUI) {
    const correctionHandler = window._createCorrectionUI();
    if (correctionHandler) root.appendChild(correctionHandler);
  }
  window.addEventListener('click', async (e) => {
    const row = e.target.closest('.queue-item');
    if (!row) return;
    const id = row.dataset.id;
    if (e.target.closest('.keep')) {
      const item = RQ.load().find(q => q.id === id);
      try { if (opts.add) await opts.add(item); RQ.remove(id); } catch (err) { e.target.closest('.qi-actions')?.insertAdjacentHTML('beforeend', `<span class="qi-status">${rqEsc(err.message)}</span>`); return; }
    } else if (e.target.closest('.correction')) {
      const item = RQ.load().find(q => q.id === id);
      if (!item) return;
      if (!window.confirm(`Log that ${item.name} is wrong and store the scan+crop so we can re-embed / harden this case?`)) return;
      const correctId = prompt('What card is this actually? (name or id)');
      if (!correctId) return;
      const status = row.querySelector('.qi-status') || row.querySelector('.qi-actions')?.children[1];
      const working = status ? (status.textContent = 'Logging...') : null;
      const payload = {
        wrong_id: item.id,
        wrong_name: item.name,
        correct_id: correctId,
        correct_name: correctId,
        score: item.score,
        source: item.source
      };
      if (item.scan) payload.scan_image_path = 'scans/' + item.scan;
      if (item.crop) payload.crop_image_path = 'scans/' + item.crop;
      try {
        await postMisread(payload);
        localStorage.setItem('last_misread_' + id, Date.now());
        if (working) working.textContent = 'Logged';
        window.showToast('Correction logged — thanks for improving the model!');
        RQ.remove(id);
      } catch (e) {
        if (working) working.textContent = 'Failed';
        alert('Could not log correction: ' + e.message + '\nRetrying...');
      }
    } else if (e.target.closest('.discard')) {
      RQ.remove(id);
    } else return;
    render();
  });
  render();
  return { refresh: render };
}

window.RQ = RQ;
window.mountReviewQueue = mountReviewQueue;