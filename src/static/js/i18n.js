/* Interface translation for the card scanner.
 *
 * Why this shape: the UI is a Jinja shell plus ~4k lines of vanilla JS that
 * render panels with innerHTML, so there is no single place to look strings up.
 * The catalogs in /static/i18n/<lang>.json are keyed by the English source text,
 * which lets the same catalog serve
 *   - static markup (the whole document is walked once on load),
 *   - anything the JS renders later (a MutationObserver translates new nodes),
 *   - explicit calls from code (`t('Add to Collection')`),
 * without renaming or extracting every string by hand.
 *
 * Language choice follows the user's own setting: the server seeds
 * <html data-ui-lang> from ?lang= -> ui_lang cookie -> Accept-Language, and the
 * picker in the user menu writes the cookie back, so the next page load is
 * already in that language. English is the source language and needs no catalog,
 * so switching back restores the original text (each translated node remembers
 * its English source).
 *
 * Adding a language: drop src/static/i18n/<code>.json next to de.json, add the
 * code to SUPPORTED and LABELS below, and add it to SUPPORTED in
 * src/services/i18n.py so the server can pick it from Accept-Language.
 */
(function (global) {
  'use strict';

  var SUPPORTED = ['en', 'de'];
  var LABELS = { en: 'English', de: 'Deutsch' };
  var DEFAULT_LANG = 'en';
  var COOKIE = 'ui_lang';
  var COOKIE_MAX_AGE = 60 * 60 * 24 * 365;

  // Attributes that carry UI copy. Card names live in `alt`/text of card tiles,
  // but those never match a catalog key, so they pass through untouched.
  var ATTRIBUTES = ['placeholder', 'title', 'aria-label', 'alt'];
  var SKIP_TAGS = { SCRIPT: 1, STYLE: 1, NOSCRIPT: 1, TEXTAREA: 1, CODE: 1, PRE: 1 };
  // Card names/values are data, not UI copy: never rewrite them.
  var SKIP_SELECTOR = '[data-i18n], [data-i18n-skip], .card-name, .candidate-name, .set-card-name, .so-name';

  var lang = DEFAULT_LANG;
  var catalog = {};
  var applying = false;
  var observer = null;
  var ready = false;

  // node/element -> { source, applied } so a later language switch can restore
  // the English text and a re-render can replace the remembered source.
  var nodeRecords = new WeakMap();
  var attrRecords = new WeakMap();
  var titleRecord = null;

  function normalize(code) {
    return String(code || '').trim().replace('_', '-').split('-')[0].toLowerCase();
  }

  function isSupported(code) {
    return SUPPORTED.indexOf(code) !== -1;
  }

  function readInjectedCatalog() {
    var el = document.getElementById('i18n-catalog');
    if (!el) return null;
    try {
      var data = JSON.parse(el.textContent || '{}');
      return data && typeof data === 'object' ? data : null;
    } catch (err) {
      return null;
    }
  }

  function loadCatalog(code) {
    if (code === DEFAULT_LANG) return Promise.resolve({});
    return fetch('/static/i18n/' + code + '.json', { cache: 'no-cache' })
      .then(function (response) {
        if (!response.ok) throw new Error('catalog ' + response.status);
        return response.json();
      })
      .catch(function (err) {
        console.warn('[i18n] could not load catalog for ' + code, err);
        return null;
      });
  }

  /** Translate a UI string. `{name}` placeholders are filled from params. */
  function t(text, params) {
    if (text == null) return '';
    var out = text;
    if (lang !== DEFAULT_LANG) {
      var hit = catalog[String(text)];
      if (typeof hit === 'string') out = hit;
    }
    if (params) {
      out = String(out).replace(/\{(\w+)\}/g, function (match, key) {
        return Object.prototype.hasOwnProperty.call(params, key) && params[key] != null
          ? String(params[key])
          : match;
      });
    }
    return out;
  }

  function lookup(english) {
    if (lang === DEFAULT_LANG) return english;
    var hit = catalog[english];
    return typeof hit === 'string' ? hit : english;
  }

  function skipped(node) {
    var el = node.nodeType === 1 ? node : node.parentElement;
    if (!el) return true;
    if (SKIP_TAGS[el.nodeName]) return true;
    return !!(el.closest && el.closest(SKIP_SELECTOR));
  }

  function translateTextNode(node) {
    if (!node || node.nodeType !== 3 || skipped(node)) return;
    var current = node.nodeValue;
    if (!current) return;
    var record = nodeRecords.get(node);
    // Something else rewrote this text (a re-render, a counter): that new text
    // is the source now, not whatever was here when we last translated.
    if (!record || current !== record.applied) {
      record = { source: current, applied: current };
      nodeRecords.set(node, record);
    }
    var source = record.source.trim();
    if (!source) return;
    var next = record.source.replace(source, lookup(source));
    if (next !== current) {
      node.nodeValue = next;
      record.applied = next;
    }
  }

  function translateAttribute(el, name) {
    if (!el || el.nodeType !== 1 || !el.hasAttribute(name) || skipped(el)) return;
    var current = el.getAttribute(name);
    var store = attrRecords.get(el);
    if (!store) {
      store = {};
      attrRecords.set(el, store);
    }
    var record = store[name];
    if (!record || current !== record.applied) {
      record = { source: current, applied: current };
      store[name] = record;
    }
    var source = String(record.source || '').trim();
    if (!source) return;
    var next = String(record.source).replace(source, lookup(source));
    if (next !== current) {
      el.setAttribute(name, next);
      record.applied = next;
    }
  }

  /** Translate an element (or a detached fragment from a render) in place. */
  function translate(root) {
    if (!root) return;
    if (root.nodeType === 3) {
      translateTextNode(root);
      return;
    }
    if (root.nodeType !== 1 && root.nodeType !== 9 && root.nodeType !== 11) return;

    var elements = [];
    if (root.nodeType === 1) elements.push(root);
    if (root.querySelectorAll) {
      var descendants = root.querySelectorAll('*');
      for (var i = 0; i < descendants.length; i += 1) elements.push(descendants[i]);
    }
    for (var e = 0; e < elements.length; e += 1) {
      var el = elements[e];
      if (SKIP_TAGS[el.nodeName]) continue;
      for (var a = 0; a < ATTRIBUTES.length; a += 1) translateAttribute(el, ATTRIBUTES[a]);
    }

    var walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, null, false);
    var node = walker.nextNode();
    while (node) {
      translateTextNode(node);
      node = walker.nextNode();
    }
  }

  function applyDocument() {
    if (!document.body) return;
    applying = true;
    try {
      var explicit = document.querySelectorAll('[data-i18n]');
      for (var i = 0; i < explicit.length; i += 1) {
        explicit[i].textContent = t(explicit[i].getAttribute('data-i18n'));
      }
      var titled = document.querySelectorAll('[data-i18n-title]');
      for (var j = 0; j < titled.length; j += 1) {
        titled[j].setAttribute('title', t(titled[j].getAttribute('data-i18n-title')));
      }
      var placeheld = document.querySelectorAll('[data-i18n-placeholder]');
      for (var k = 0; k < placeheld.length; k += 1) {
        placeheld[k].setAttribute('placeholder', t(placeheld[k].getAttribute('data-i18n-placeholder')));
      }

      translate(document.body);

      if (!titleRecord) titleRecord = { source: document.title, applied: document.title };
      var titleSource = titleRecord.source.trim();
      if (titleSource) {
        var nextTitle = titleRecord.source.replace(titleSource, lookup(titleSource));
        if (nextTitle !== document.title) {
          document.title = nextTitle;
          titleRecord.applied = nextTitle;
        }
      }
    } finally {
      applying = false;
    }
  }

  function startObserver() {
    if (observer || !document.body || typeof MutationObserver === 'undefined') return;
    observer = new MutationObserver(function (mutations) {
      if (applying) return;
      applying = true;
      try {
        for (var i = 0; i < mutations.length; i += 1) {
          var mutation = mutations[i];
          if (mutation.type === 'childList') {
            for (var n = 0; n < mutation.addedNodes.length; n += 1) translate(mutation.addedNodes[n]);
          } else if (mutation.type === 'characterData') {
            translateTextNode(mutation.target);
          } else if (mutation.type === 'attributes') {
            translateAttribute(mutation.target, mutation.attributeName);
          }
        }
      } finally {
        applying = false;
      }
    });
    observer.observe(document.body, {
      childList: true,
      subtree: true,
      characterData: true,
      attributes: true,
      attributeFilter: ATTRIBUTES
    });
  }

  function writeCookie(code) {
    try {
      document.cookie = COOKIE + '=' + code + '; path=/; max-age=' + COOKIE_MAX_AGE + '; samesite=lax';
    } catch (err) { /* cookies disabled: the choice lasts for this page only */ }
  }

  function syncPicker() {
    var picker = document.getElementById('ui-lang-pref');
    if (!picker) return;
    for (var i = 0; i < SUPPORTED.length; i += 1) {
      var code = SUPPORTED[i];
      if (!picker.querySelector('option[value="' + code + '"]')) {
        var option = document.createElement('option');
        option.value = code;
        option.textContent = LABELS[code] || code.toUpperCase();
        picker.appendChild(option);
      }
    }
    picker.value = lang;
  }

  function bindPicker() {
    var picker = document.getElementById('ui-lang-pref');
    if (!picker) return;
    syncPicker();
    if (picker.dataset.i18nBound) return;
    picker.dataset.i18nBound = '1';
    picker.addEventListener('change', function () {
      setLanguage(picker.value, { announce: true });
    });
  }

  /** Switch language: load the catalog, retranslate the page, remember the choice. */
  function setLanguage(code, options) {
    options = options || {};
    var next = normalize(code);
    if (!isSupported(next)) next = DEFAULT_LANG;
    return loadCatalog(next).then(function (loaded) {
      if (loaded === null && next !== DEFAULT_LANG) return lang; // fetch failed: stay put
      catalog = next === DEFAULT_LANG ? {} : loaded;
      lang = next;
      document.documentElement.setAttribute('lang', next);
      document.documentElement.setAttribute('data-ui-lang', next);
      writeCookie(next);
      syncPicker();
      applyDocument();
      if (options.announce && typeof global.showToast === 'function') {
        global.showToast(t('Interface language: {language}', { language: LABELS[next] || next }));
      }
      document.dispatchEvent(new CustomEvent('i18n:change', { detail: { lang: next } }));
      return lang;
    });
  }

  function detectInitialLanguage() {
    var fromServer = normalize(document.documentElement.getAttribute('data-ui-lang'));
    if (isSupported(fromServer)) return fromServer;
    var list = (global.navigator && navigator.languages && navigator.languages.length)
      ? navigator.languages
      : [(global.navigator && navigator.language) || DEFAULT_LANG];
    for (var i = 0; i < list.length; i += 1) {
      var code = normalize(list[i]);
      if (isSupported(code)) return code;
    }
    return DEFAULT_LANG;
  }

  function boot() {
    lang = detectInitialLanguage();
    var injected = readInjectedCatalog();
    if (lang !== DEFAULT_LANG && injected && Object.keys(injected).length) {
      catalog = injected;
    }
    var pending = lang === DEFAULT_LANG || Object.keys(catalog).length
      ? Promise.resolve()
      : loadCatalog(lang).then(function (loaded) { if (loaded) catalog = loaded; });
    pending.then(function () {
      ready = true;
      bindPicker();
      document.documentElement.setAttribute('lang', lang);
      applyDocument();
      startObserver();
      document.dispatchEvent(new CustomEvent('i18n:ready', { detail: { lang: lang } }));
    });
  }

  global.t = t;
  global.I18n = {
    t: t,
    apply: applyDocument,
    translate: translate,
    setLanguage: setLanguage,
    supported: SUPPORTED.slice(),
    labels: LABELS,
    get lang() { return lang; },
    get ready() { return ready; }
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})(window);
