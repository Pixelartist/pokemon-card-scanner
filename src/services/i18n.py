"""Interface-language negotiation for the web UI.

The UI is translated in the browser by ``src/static/js/i18n.js`` from flat
English -> target JSON catalogs in ``src/static/i18n/``. The server only decides
which language a page *starts* in, so the first paint already carries the right
``<html lang>`` and the catalog inline (no extra round trip, and a language
switch is one fetch of the other catalog).

Resolution order, most explicit first:
    1. ``?lang=de`` on the URL (also written to the cookie by the route)
    2. the ``ui_lang`` cookie (what the in-app picker last chose)
    3. the browser's ``Accept-Language`` header
    4. English

Kept free of FastAPI imports so ``negotiate()`` can be unit-tested on its own.
"""
import json
import os
from pathlib import Path

I18N_DIR = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))) / "static" / "i18n"

# Languages with a catalog file in I18N_DIR. English is the source language, so
# it has no catalog: t() falls through to the string itself.
SUPPORTED = ("en", "de")
DEFAULT = "en"
COOKIE = "ui_lang"
COOKIE_MAX_AGE = 60 * 60 * 24 * 365

_cache = {}


def normalize(code):
    """'de-AT' / 'de_DE' -> 'de'; anything unparseable -> ''."""
    if not code:
        return ""
    base = str(code).strip().replace("_", "-").split("-")[0]
    return base.lower()


def load_catalog(lang):
    """Return the English->translation map for ``lang`` ({} for English/missing).

    Re-read when the file's mtime changes so editing a catalog does not need a
    server restart.
    """
    lang = normalize(lang)
    if lang not in SUPPORTED or lang == DEFAULT:
        return {}
    path = I18N_DIR / f"{lang}.json"
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return {}
    cached = _cache.get(lang)
    if cached and cached[0] == mtime:
        return cached[1]
    try:
        with path.open(encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    _cache[lang] = (mtime, data)
    return data


def catalog_json(lang):
    """Catalog as JSON safe to inline in a <script> block.

    '<' is escaped so a translation can never terminate the script element.
    """
    return json.dumps(load_catalog(lang), ensure_ascii=False).replace("<", "\\u003c")


def _accept_language_weights(header):
    """[(q, index, code)] best-first, ignoring malformed entries."""
    entries = []
    for index, part in enumerate((header or "").split(",")):
        part = part.strip()
        if not part:
            continue
        pieces = part.split(";")
        code = normalize(pieces[0])
        if not code:
            continue
        quality = 1.0
        for param in pieces[1:]:
            param = param.strip()
            if param.startswith("q="):
                try:
                    quality = float(param[2:])
                except ValueError:
                    quality = 0.0
        entries.append((-quality, index, code))
    entries.sort()
    return [code for _, _, code in entries]


def negotiate(query=None, cookie=None, accept_language=None):
    """Pick the UI language for a request. Returns one of SUPPORTED."""
    for candidate in (query, cookie):
        code = normalize(candidate)
        if code in SUPPORTED:
            return code
    for code in _accept_language_weights(accept_language):
        if code in SUPPORTED:
            return code
    return DEFAULT


def resolve_request(request):
    """negotiate() for a Starlette/FastAPI Request, without importing them."""
    return negotiate(
        query=request.query_params.get("lang"),
        cookie=request.cookies.get(COOKIE),
        accept_language=request.headers.get("accept-language"),
    )
