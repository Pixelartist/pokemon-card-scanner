"""Read a card's printed identity: the set code and the number on the bottom strip.

Two providers, cheapest first:
  - OCR (pytesseract + the tesseract binary) runs locally and costs nothing;
  - a vision LLM reads the crop when OCR cannot.

Both are optional. A provider that is not installed or not configured returns
None instead of raising, so the job queue can skip it and move on.
"""
import base64
import difflib
import json
import logging
import os
import re
from collections import Counter
from pathlib import Path

logger = logging.getLogger(__name__)

# "077/198" (number/total), "SWSH057", "SV07-077", "ME05 015"
_NUMBER_TOTAL = re.compile(r"\b(\d{1,3})\s*/\s*(\d{1,3})\b")
# Anchored to the start of a line: a collector line begins with its set code,
# while prose produces the same shape mid-sentence (measured OCR noise "sO ae
# Vv - 4" became set "vv" + number 4). Spaces and hyphens only, so a newline
# cannot pair the last word of one line with the number on the next.
_SET_AND_NUMBER = re.compile(
    r"^[^A-Z]{0,3}([A-Z]{2,5}\d{0,2})[ \t]*[- ]?[ \t]*(\d{1,3})\b")
_ILLUSTRATOR_LINE = re.compile(r"illustr|illustrator|illus\.", re.IGNORECASE)
_NAME_WORD = re.compile(r"[^\W\d_]{3,}(?:[ '\-][^\W\d_]{2,})*", re.UNICODE)

# A number above the printed total is how a secret rare prints (measured:
# "148/086" for a card in an 86-card set), so it is not a misread on its own —
# but a total too small to be a set size makes the pair nonsense ("077/012").
SMALLEST_PLAUSIBLE_SET = 30

# A candidate name this close to the printed one is usable when it is also the
# clear winner among the cards sharing that number.
NAME_MATCH_MIN = 0.80

LLM_PROMPT = (
    "This is a cropped photo of a Pokemon trading card. Answer with JSON and "
    "nothing else: "
    '{"name": "<card name printed at the top, in the printed language>", '
    '"number": "<collector number: the digits left of the slash in the '
    'bottom line, e.g. 077 in 077/142>", "set_code": "<letters before that '
    'number, e.g. sv07; null unless you can read them clearly>"}. '
    "Use null for any field you cannot read, and never guess a set code: the "
    "number and the name are what matter."
)


def guess_name(text):
    """The printed card name: the first clean word sequence near the top.

    OCR of a card starts with artwork noise, so the first run of letters that is
    long enough is the best available guess ("...* Meditie" -> "Meditie").
    """
    if not text:
        return None
    for line in text.splitlines():
        if _ILLUSTRATOR_LINE.search(line):
            continue
        for match in _NAME_WORD.finditer(line):
            word = match.group(0).strip()
            if len(word) >= 4 and not word.isupper():
                return word
    return None


def parse_card_text(text):
    """Best-effort (set_code, number) from OCR text, or None when unreadable."""
    if not text:
        return None
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    # The collector line sits under the artwork; drop the illustrator credit so
    # its numbers cannot win.
    body = "\n".join(ln for ln in lines if not _ILLUSTRATOR_LINE.search(ln)).upper()
    # "077/142" first: it is the whole collector line, where a bare set code plus
    # digits can also come from artwork text or the dex number.
    # Several passes read the same strip, so a misread of the collector line
    # ("198/086" for "148/086") sits in the text beside the real one. Taking the
    # first match means a single bad pass decides; the reading most passes agree
    # on wins instead, and ties go to the one read first.
    readings = [(match.group(1), match.group(2))
                for match in _NUMBER_TOTAL.finditer(body)
                if _plausible(match.group(1), match.group(2))]
    if readings:
        number, total = Counter(readings).most_common(1)[0][0]
        # The printed total is kept for logging only: localised sets print their
        # own size, so it does not match our catalogue's count for the set.
        return {"set_code": None, "number": number, "total": total,
                "name": guess_name(text)}
    for line in body.splitlines():
        match = _SET_AND_NUMBER.search(line)
        if match and _plausible(match.group(2)):
            return {"set_code": match.group(1).lower(), "number": match.group(2)}
    return None


def _number_variants(number):
    """Collector numbers are stored zero-padded to three digits ("077")."""
    digits = re.sub(r"\D", "", str(number or ""))
    if not digits:
        return []
    return sorted({digits, digits.zfill(3)})


def _plausible(number, total=None):
    """A number out of range, or ahead of its own total, is a misread."""
    if not str(number or "").isdigit():
        return False
    if not 1 <= int(number) <= 999:
        return False
    if total is not None:
        if not str(total).isdigit() or not 1 <= int(total) <= 999:
            return False
        if int(total) < int(number) and int(total) < SMALLEST_PLAUSIBLE_SET:
            return False
    return True


def _normalise_name(name):
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def _as_card(row):
    """A plain dict, whatever row factory the caller's connection happens to use."""
    if row is None:
        return None
    return {"ptcg_id": row[0], "name": row[1], "number": row[2]}


def resolve_card(conn, parsed, name=None):
    """Turn a parsed (set_code, number) into a row from `cards`, or None.

    A set code pins the printing exactly; without one, the number plus the
    printed name is the best we can do, and only when it is unambiguous.
    """
    if not parsed or not parsed.get("number"):
        return None
    set_code = (parsed.get("set_code") or "").lower()
    numbers = _number_variants(parsed["number"])

    by_set = _by_set_code(conn, set_code, numbers) if set_code else None
    # A set code is believed only when the printed name back it up. Measured: a
    # vision model read "sv01" off a Stellar Crown card, and sv01-077 is a real
    # card (Wattrel) — trusting it would have identified the wrong card with
    # total confidence. The name is legible far more often than the set symbol.
    if by_set and (not name or _names_agree(by_set["name"], name)):
        return by_set
    if not name:
        return by_set
    return _by_name_and_number(conn, numbers, name)


def _by_set_code(conn, set_code, numbers):
    for number in numbers:
        for prefix in ("", "de_"):
            row = conn.execute(
                "SELECT ptcg_id, name, number FROM cards WHERE ptcg_id = ?",
                (f"{prefix}{set_code}-{number}",),
            ).fetchone()
            found = _as_card(row)
            if found:
                return found
    return None


def _names_agree(a, b):
    return _best_ratio(a, b) >= NAME_MATCH_MIN


def _best_ratio(printed, candidate):
    """Similarity between a printed name and a catalogue name.

    OCR of the header drags a stray word along with the card name ("easie
    Meditie"), so each word of the printed name is scored against the whole
    catalogue name and the best one wins; the number still has to match.
    """
    words = [w for w in re.split(r"[^a-z0-9]+", (printed or "").lower()) if len(w) >= 4]
    target = _normalise_name(candidate)
    if not words:
        words = [_normalise_name(printed)]
    return max(difflib.SequenceMatcher(None, w, target).ratio() for w in words)


def _by_name_and_number(conn, numbers, name):
    """Resolve by printed name + collector number, tolerating OCR errors."""
    if not name:
        return None
    rows = conn.execute(
        "SELECT ptcg_id, name, number FROM cards WHERE number IN (%s)"
        % ",".join("?" * len(numbers)),
        numbers,
    ).fetchall()
    wanted = _normalise_name(name)
    # OCR mangles names ("Meditie" for Meditite), so score every card carrying
    # that number instead of demanding an exact string.
    scored = sorted(
        ((_best_ratio(name, row[1]), row)
         for row in rows),
        key=lambda pair: pair[0], reverse=True,
    )
    if not scored or scored[0][0] < NAME_MATCH_MIN:
        return None
    winners = [row for ratio, row in scored if scored[0][0] - ratio < 0.05]
    # The same card in another printing is not a rival, so `sv07-077` and
    # `de_sv07-077` collapse to the catalogue's canonical (unprefixed) id.
    if len({_normalise_name(row[1]) for row in winners}) != 1:
        return None
    winners.sort(key=lambda row: len(row[0]))
    return _as_card(winners[0])


def ocr_available():
    """True when pytesseract and its library can be used in this process."""
    try:
        import pytesseract  # noqa: F401
        from PIL import Image  # noqa: F401
    except Exception:
        return False
    return True


def llm_available():
    return bool(os.environ.get("OPENAI_API_KEY"))


def _scaled(image, factor):
    from PIL import Image

    if factor == 1:
        return image
    return image.resize((image.width * factor, image.height * factor), Image.LANCZOS)


def ocr_text(image_path):
    """Raw text from the card, or None when OCR is not installed or fails.

    Several passes, because one does not do it: sparse text over the top band
    finds the name and over the whole card the collector line on a clean shot,
    while the bottom strip magnified finds "077/142" — measured on real cards,
    the default single pass returned nothing useful at any one size.
    """
    try:
        import pytesseract
        from PIL import Image, ImageOps
    except Exception:
        return None
    try:
        image = Image.open(image_path).convert("L")
    except Exception as exc:
        logger.info("OCR could not open %s: %s", image_path, exc)
        return None
    parts = []
    try:
        width, height = image.size
        # The name is printed on the top band over artwork, where a whole-card
        # pass dissolves it (measured "en piokpeko eX" for "Morpeko ex"); that
        # band at 4x with its contrast stretched reads it. Going first also keeps
        # guess_name() off the artwork noise the full pass leaves above it.
        banner = image.crop((0, 0, width, int(height * 0.20)))
        # Both readings of the band earn their place: block mode keeps the name
        # in one piece on a clean render ("BASIC Pidove:"), where sparse mode
        # cut it to "Pid", while sparse mode is what survives a dark printing
        # ("... Morpeko ex 180" on a black card).
        parts.append(pytesseract.image_to_string(_scaled(banner, 2), config="--psm 6"))
        parts.append(pytesseract.image_to_string(
            ImageOps.autocontrast(_scaled(banner, 4)), config="--psm 11"))
        parts.append(pytesseract.image_to_string(_scaled(image, 2), config="--psm 11"))
        # Two bands and two magnifications: the collector line survives a tighter
        # crop at 4x on a re-encoded JPEG but needs 8x on a soft one, and a 0.88
        # band clips it on a card whose bottom margin is small.
        for top in (0.86, 0.90):
            strip = image.crop((0, int(height * top), int(width * 0.62), height))
            for factor in (4, 8):
                big = _scaled(strip, factor)
                parts.append(pytesseract.image_to_string(big, config="--psm 6"))
                if factor == 4:
                    # Block mode needs dark print on a light background; a dark
                    # printing returns only the rule box there. Sparse mode reads
                    # the same strip at 4x (measured "055/084" off a German
                    # Morpeko ex that psm 6 never found).
                    parts.append(pytesseract.image_to_string(big, config="--psm 11"))
    except Exception as exc:
        logger.info("OCR failed for %s: %s", image_path, exc)
        return None
    text = "\n".join(part for part in parts if part.strip())
    return text or None


def vision_llm(image_path):
    """Ask a vision LLM for the printed identity, or None when unconfigured."""
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        return None
    base = os.environ.get("IDENTIFY_LLM_BASE", "https://api.openai.com/v1").rstrip("/")
    model = os.environ.get("IDENTIFY_LLM_MODEL", "gpt-4o-mini")
    try:
        import requests

        encoded = base64.b64encode(Path(image_path).read_bytes()).decode()
        response = requests.post(
            f"{base}/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={
                "model": model,
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": LLM_PROMPT},
                        {"type": "image_url",
                         "image_url": {"url": f"data:image/jpeg;base64,{encoded}"}},
                    ],
                }],
                "temperature": 0,
            },
            # Free router failover is slow: measured 19-39s for a real read, so
            # a 45s cap was cutting good answers off mid-request.
            timeout=int(os.environ.get("IDENTIFY_LLM_TIMEOUT", "90")),
        )
        if response.status_code != 200:
            logger.info("vision LLM answered %s", response.status_code)
            return None
        content = response.json()["choices"][0]["message"]["content"]
        # Models wrap JSON in fences often enough to be worth stripping.
        content = re.sub(r"^```(?:json)?|```$", "", content.strip(), flags=re.MULTILINE).strip()
        data = json.loads(content)
        if not data.get("number"):
            return None
        return {"set_code": (data.get("set_code") or "").lower() or None,
                "number": str(data["number"]), "name": data.get("name")}
    except Exception as exc:
        logger.info("vision LLM failed for %s: %s", image_path, exc)
        return None
