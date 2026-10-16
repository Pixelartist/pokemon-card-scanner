(function() {
'use strict';

// ============================================================
// Electric Border Glow - ported from ReactBits WebGL shader
// to vanilla Canvas2D noise. Works on any element via overlay.
// ============================================================

const MODULE_VERSION = '1.0.0';
const PAD = 10; // canvas margin around the card for the glow to bleed into

/** Tier → color mapping (matching value tiers) */
const TIER_COLORS = {
    gold:   { stroke: '#FFD700', glow: 'rgba(255,215,0,',  core: [1, 0.84, 0] },
    silver: { stroke: '#C0C0C0', glow: 'rgba(192,192,192,', core: [0.75, 0.75, 0.78] },
    bronze: { stroke: '#CD7F32', glow: 'rgba(205,127,50,',  core: [0.8, 0.5, 0.2] },
};

/** Shared noise functions */
function hash(x, y, seed) {
    return (Math.sin((x * 0x8da6b343 + y * 0xd8163841 + seed) * 12.9898) * 43758.5453) % 1;
}

function noise2D(x, y, seed) {
    const i = Math.floor(x), j = Math.floor(y);
    const fx = x - i, fy = y - j;
    const u = fx * fx * (3.0 - 2.0 * fx);
    const v = fy * fy * (3.0 - 2.0 * fy);
    const a = hash(i, j, seed);
    const b = hash(i+1, j, seed);
    const c = hash(i, j+1, seed);
    const d = hash(i+1, j+1, seed);
    return a + (b-a)*u + (c-a)*v + (a-b-c+d)*u*v;
}

function fbm(x, octaves, lacunarity, gain, t, seed, flatness) {
    let y = 0, amp = 1, freq = 1, maxAmp = 0;
    for (let k = 0; k < octaves; k++) {
        const a = amp * (k === 0 ? flatness : 1);
        y += a * noise2D(freq * x, t * freq * 0.25, seed + k * 31);
        maxAmp += a;
        freq *= lacunarity;
        amp *= gain;
    }
    return y / maxAmp;
}

/** Sample a point along a rounded rect perimeter. */
function getRoundedRectPoint(t, left, top, w, h, radius) {
    const sw = w - 2*radius, sh = h - 2*radius;
    const ca = Math.PI * radius / 2;
    const P = 2*(sw+sh) + 4*ca;
    const d = t * P;
    let acc = 0;
    if (d <= acc+sw) { const p=(d-acc)/sw; return {x:left+radius+p*sw, y:top}; }
    acc+=sw;
    if (d <= acc+ca) { const p=(d-acc)/ca; return cornerPt(left+w-radius, top+radius, radius, -Math.PI/2, Math.PI/2, p); }
    acc+=ca;
    if (d <= acc+sh) { const p=(d-acc)/sh; return {x:left+w, y:top+radius+p*sh}; }
    acc+=sh;
    if (d <= acc+ca) { const p=(d-acc)/ca; return cornerPt(left+w-radius, top+h-radius, radius, 0, Math.PI/2, p); }
    acc+=ca;
    if (d <= acc+sw) { const p=(d-acc)/sw; return {x:left+w-radius-p*sw, y:top+h}; }
    acc+=sw;
    if (d <= acc+ca) { const p=(d-acc)/ca; return cornerPt(left+radius, top+h-radius, radius, Math.PI/2, Math.PI/2, p); }
    acc+=ca;
    if (d <= acc+sh) { const p=(d-acc)/sh; return {x:left, y:top+h-radius-p*sh}; }
    acc+=sh;
    const p=(d-acc)/ca;
    return cornerPt(left+radius, top+radius, radius, Math.PI, Math.PI/2, p);
}

function cornerPt(cx, cy, r, start, arc, p) {
    const a = start + p * arc;
    return { x: cx + r*Math.cos(a), y: cy + r*Math.sin(a) };
}

// ============================================================
// ElectricGlow - attaches a canvas overlay to a DOM element
// ============================================================

class ElectricGlow {
    constructor(element, tier) {
        this.el = element;
        this.tier = tier;
        this.active = false;
        this.canvas = null;
        this.ctx = null;
        this.width = 0;
        this.height = 0;
        this.dpr = Math.min(window.devicePixelRatio || 1, 2);
        this.time = 0;
        this.lastTs = 0;
        this.frameId = null;
    }

    attach() {
        const tier = this.tier;
        const cfg = TIER_COLORS[tier];
        if (!cfg) return false;

        // Build overlay structure
        const wrapper = document.createElement('div');
        wrapper.className = 'electric-glow-wrapper';
        wrapper.style.cssText = `position:absolute;inset:-${PAD}px;border-radius:inherit;overflow:visible;pointer-events:none;`;

        this.canvas = document.createElement('canvas');
        this.canvas.className = 'electric-glow-canvas';

        wrapper.appendChild(this.canvas);
        if (getComputedStyle(this.el).position === 'static') {
            this.el.style.position = 'relative';
        }
        // Ensure wrapper is visible inside the card
        if (getComputedStyle(this.el).overflow !== 'visible') {
            this.el.style.overflow = 'visible';
        }
        this.el.appendChild(wrapper);
        this.wrapper = wrapper;

        this.color = cfg.stroke;
        this.glowBase = cfg.glow;
        this.coreColor = cfg.core;
        this.pixelRatio = this.dpr;
        this._retryResize();
        return true;
    }

    _retryResize() {
        if (this.resize()) return;
        // Element may not be laid out yet (hidden tab, display:none)
        let tries = 0;
        const attempt = () => {
            if (this.resize()) return;
            if (++tries < 20) requestAnimationFrame(attempt);
        };
        requestAnimationFrame(attempt);
    }

    resize() {
        const w = this.el.offsetWidth || this.el.getBoundingClientRect().width;
        const h = this.el.offsetHeight || this.el.getBoundingClientRect().height;
        this.width = w;
        this.height = h;
        if (this.width < 40 || this.height < 50) return;

        // Canvas = card box + PAD on every side; buffer matches exactly so
        // the drawn border rectangle and the clear region stay in sync.
        const c = this.canvas;
        this.canvasW = this.width + PAD * 2;
        this.canvasH = this.height + PAD * 2;
        c.width = Math.round(this.canvasW * this.pixelRatio);
        c.height = Math.round(this.canvasH * this.pixelRatio);
        const ctx = c.getContext('2d');
        ctx.setTransform(1, 0, 0, 1, 0, 0);
        ctx.scale(this.pixelRatio, this.pixelRatio);
        this.ctx = ctx;
        this._updateGeometry();
    }

    _updateGeometry() {
        const w = this.width, h = this.height;
        // Border sits ON the card edge; strands wobble ±few px around it.
        this.borderLeft = PAD;
        this.borderTop = PAD;
        this.borderWidth = w;
        this.borderHeight = h;
        this.radius = Math.min(w, h) * 0.06;

        const straightW = this.borderWidth - 2*this.radius;
        const straightH = this.borderHeight - 2*this.radius;
        const cornerArc = Math.PI * this.radius / 2;
        this.perimeter = 2*(straightW+straightH) + 4*cornerArc;
        this.sampleCount = Math.max(200, Math.floor(this.perimeter / 1.8));
    }

    start() {
        if (this.active) return;
        this.active = true;
        this.lastTs = performance.now();
        this._tick(this.lastTs);
    }

    stop() {
        this.active = false;
        if (this.frameId) { cancelAnimationFrame(this.frameId); this.frameId = null; }
    }

    destroy() {
        this.stop();
        if (this.wrapper && this.wrapper.parentNode) {
            this.wrapper.parentNode.removeChild(this.wrapper);
        }
    }

    _tick(ts) {
        if (!this.active) return;
        const dt = Math.min((ts - this.lastTs) / 1000, 0.05);
        this.lastTs = ts;
        this.time += dt * 1.8; // same speed as test tab
        this._render();
        this.frameId = requestAnimationFrame(this._tick.bind(this));
    }

    _render() {
        const ctx = this.ctx;
        if (!ctx) return;
        const w = this.canvasW, h = this.canvasH;
        ctx.clearRect(0, 0, w, h);
        if (this.width < 40 || this.height < 50) return;

        const { time, color } = this;
        const displacement = 4; // strands wobble only a few px around the card edge
        const scale = displacement * (0.85 + 0.15 * Math.sin(time * 1.8));

        ctx.lineCap = 'round';
        ctx.lineJoin = 'round';

        const octaves = 9, lacunarity = 1.6, gain = 0.68;

        // ---- Soft halo ----
        ctx.globalAlpha = 0.35;
        ctx.strokeStyle = color;
        ctx.lineWidth = 6;
        ctx.filter = 'blur(4px)';
        this._strokeBorderPath(ctx, scale, displacement, octaves, lacunarity, gain, time, 0);
        ctx.stroke();

        // ---- Medium glow ----
        ctx.globalAlpha = 0.5;
        ctx.lineWidth = 3;
        ctx.filter = 'blur(1.5px)';
        this._strokeBorderPath(ctx, scale, displacement, octaves, lacunarity, gain, time, 0);
        ctx.stroke();

        ctx.filter = 'none';

        // ---- Thin bright strands ----
        for (let strand = 0; strand < 2; strand++) {
            ctx.globalAlpha = 0.8 - strand * 0.3;
            ctx.strokeStyle = '#FFFFFF';
            ctx.lineWidth = 1;
            ctx.shadowColor = color;
            ctx.shadowBlur = 6;
            this._strokeBorderPath(ctx, scale, displacement, octaves, lacunarity, gain, time, strand * 0.5);
            ctx.stroke();
        }
        ctx.shadowBlur = 0;
        ctx.globalAlpha = 1;
    }

    _strokeBorderPath(ctx, scale, displacement, octaves, lacunarity, gain, t, strandOffset) {
        ctx.beginPath();
        for (let i = 0; i <= this.sampleCount; i++) {
            const prog = i / this.sampleCount;
            const pt = getRoundedRectPoint(prog, this.borderLeft, this.borderTop, this.borderWidth, this.borderHeight, this.radius);

            const xN = fbm(prog * 8 + strandOffset, octaves, lacunarity, gain, t + strandOffset, 0, 0.02);
            const yN = fbm(prog * 8 + strandOffset, octaves, lacunarity, gain, t + strandOffset, 1, 0.02);

            const dx = pt.x + xN * scale;
            const dy = pt.y + yN * scale;
            if (i === 0) ctx.moveTo(dx, dy); else ctx.lineTo(dx, dy);
        }
    }
}

// ============================================================
// Public API + init
// ============================================================

const _instances = new Map(); // el -> ElectricGlow
let _io = null;

/** Initialize: find all [data-glow-tier] elements and attach canvases */
function initElectricGlow() {
    if (_instances.size > 0) return; // already initialized

    const selectors = [
        '[data-glow-tier="gold"]',
        '[data-glow-tier="silver"]',
        '[data-glow-tier="bronze"]',
    ];

    document.querySelectorAll(selectors.join(',')).forEach(el => {
        const tier = el.getAttribute('data-glow-tier');
        if (!tier || !TIER_COLORS[tier]) return;
        if (_instances.has(el)) return;
        const eg = new ElectricGlow(el, tier);
        if (eg.attach()) {
            _instances.set(el, eg);
            eg.start();
        }
    });

    // Observe scroll / resize to reposition canvases
    if ('ResizeObserver' in window) {
        const ro = new ResizeObserver(() => {
            _instances.forEach((eg) => { eg.resize(); });
        });
        document.querySelectorAll('.electric-glow-wrapper').forEach(w => ro.observe(w.parentElement));
    }

    // Pause offscreen (IntersectionObserver)
    if ('IntersectionObserver' in window) {
        _io = new IntersectionObserver((entries) => {
            entries.forEach(e => {
                const el = e.target;
                const eg = _instances.get(el);
                if (!eg) return;
                if (e.isIntersecting) eg.start(); else eg.stop();
            });
        }, { rootMargin: '200px' });
        document.querySelectorAll('[data-glow-tier]').forEach(el => _io.observe(el));
    }
}

/** Called after new cards are added to the gallery (e.g., after loadGalleryPage) */
function scanForNewGlowElements() {
    const selectors = [
        '[data-glow-tier="gold"]',
        '[data-glow-tier="silver"]',
        '[data-glow-tier="bronze"]',
    ];
    document.querySelectorAll(selectors.join(',')).forEach(el => {
        if (_instances.has(el)) return;
        const tier = el.getAttribute('data-glow-tier');
        if (!tier || !TIER_COLORS[tier]) return;
        const eg = new ElectricGlow(el, tier);
        if (eg.attach()) {
            _instances.set(el, eg);
            eg.start();
            if (_io) _io.observe(el);
        }
    });
}

// Expose for main.js
window.ElectricGlow = ElectricGlow;
window.initElectricGlow = initElectricGlow;
window.scanForNewGlowElements = scanForNewGlowElements;

})();
