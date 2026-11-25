// Timing telemetry for the browser scanner.
//
// The browser is the only place these numbers exist: the server receives an
// upload and cannot tell whether a tablet spent 80ms or 800ms finding,
// straightening and hashing the card, nor how long it spent loading the card
// index before it could scan at all. Each is posted as one row to
// /api/scan-metrics and read back per device by scripts/scan_metrics_report.py.
//
//   const capture = scanMetrics.begin();
//   capture.detect_ms = 120;                       // whatever was measured
//   scanMetrics.report(capture, { kind: 'capture', outcome: 'match' });
(function () {
  const URL = '/api/scan-metrics';
  let device = null;

  const round = value => Math.round(value * 10) / 10;

  // Who is scanning, from whatever the platform exposes: userAgentData where it
  // exists, the UA string elsewhere, plus the hardware hints that predict how a
  // capture performs — cores and memory decide the WASM inference cost far more
  // than the model name does.
  function deviceInfo() {
    if (device) return device;
    const ua = navigator.userAgent || '';
    const data = navigator.userAgentData || null;
    const brands = (data && data.brands) || [];
    const brandVersion = name => {
      const hit = brands.find(b => String(b.brand).toLowerCase().includes(name));
      return hit ? hit.version : '';
    };
    const part = re => (ua.match(re) || [])[1] || '';
    let browser = 'Unknown', version = '';
    if (/Edg\//.test(ua)) { browser = 'Edge'; version = part(/Edg\/([\d.]+)/); }
    else if (/OPR\//.test(ua)) { browser = 'Opera'; version = part(/OPR\/([\d.]+)/); }
    else if (/FxiOS\//.test(ua)) { browser = 'Firefox'; version = part(/FxiOS\/([\d.]+)/); }
    else if (/Firefox\//.test(ua)) { browser = 'Firefox'; version = part(/Firefox\/([\d.]+)/); }
    else if (/CriOS\//.test(ua)) { browser = 'Chrome'; version = part(/CriOS\/([\d.]+)/); }
    else if (/Chrome\//.test(ua)) { browser = 'Chrome'; version = brandVersion('chrome') || part(/Chrome\/([\d.]+)/); }
    else if (/Safari\//.test(ua)) { browser = 'Safari'; version = part(/Version\/([\d.]+)/); }
    // iPadOS 13+ sends a Macintosh UA; touch points give it away, since no
    // desktop Mac reports any.
    const touch = navigator.maxTouchPoints || 0;
    const ipad = /iPad/.test(ua) || (/Macintosh/.test(ua) && touch > 1);
    const iphone = /iPhone|iPod/.test(ua);
    const android = /Android/.test(ua);
    const minSide = Math.min(screen.width || 0, screen.height || 0);
    const deviceClass = ipad ? 'tablet'
      : iphone ? 'phone'
      : android ? (/Mobile/.test(ua) ? 'phone' : 'tablet')
      : (touch > 1 && minSide >= 700) ? 'tablet' : 'desktop';
    const model = ipad ? 'iPad' : iphone ? 'iPhone'
      : android ? (part(/Android [\d.]+; ?([^;)]+)/) || 'Android').trim() : '';
    const platform = data && data.platform ? data.platform
      : ipad || iphone ? 'iOS' : android ? 'Android'
      : /Windows/.test(ua) ? 'Windows' : /Macintosh/.test(ua) ? 'macOS'
      : /Linux/.test(ua) ? 'Linux' : 'Unknown';
    device = {
      device: [model, platform].filter(Boolean).join(' · ') || platform,
      device_class: deviceClass,
      browser: version ? `${browser} ${version.split('.')[0]}` : browser,
      user_agent: ua,
      hardware_concurrency: navigator.hardwareConcurrency || null,
      device_memory: navigator.deviceMemory || null,
    };
    return device;
  }

  window.scanMetrics = {
    // A handle for one capture or one page load. Everything measured is written
    // onto it; report() adds the device and the total.
    begin() {
      return { started: performance.now() };
    },

    report(handle, fields) {
      if (!handle || !fields) return;
      const payload = Object.assign({}, deviceInfo(), handle, fields);
      payload.total_ms = round(performance.now() - handle.started);
      // performance.now() counts from navigation start, so for a page load the
      // handle's start is exactly the wait before the scanner could begin.
      if (payload.kind === 'startup') payload.boot_ms = round(handle.started);
      delete payload.started;
      // Fire and forget. Telemetry that could delay, fail or block a scan would
      // cost more than the numbers are worth.
      try {
        fetch(URL, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
          keepalive: true,   // the row still lands if the tab closes right after
        }).catch(() => {});
      } catch (e) { /* never break a scan for a measurement */ }
    },
  };
})();
