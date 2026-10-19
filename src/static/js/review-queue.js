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
  return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

// Mount a queue panel into `root` (element). opts.add(item) -> Promise|void, opts.onDone.
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
            <button class="discard">✕</button>
          </div>
        </div>`).join('') : '<div class="empty">Nothing waiting for review.</div>')
      + '</div>';
    if (opts.onChange) opts.onChange(items.length);
  }
  root.addEventListener('click', async (e) => {
    const row = e.target.closest('.queue-item');
    if (!row) return;
    const id = row.dataset.id;
    if (e.target.closest('.keep')) {
      const item = RQ.load().find(q => q.id === id);
      try { if (opts.add) await opts.add(item); RQ.remove(id); }
      catch (err) { e.target.closest('.qi-actions').insertAdjacentHTML('beforeend', `<span class="qi-status">${rqEsc(err.message)}</span>`); return; }
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
