"""Deterministic text matching shared by dataset tools and verifier stages.

``find_span`` is a multi-stage quote matcher (after rag-facts-check):
exact -> whitespace/punctuation-insensitive -> fuzzy alignment. PDF text extraction mangles
whitespace, ligatures and minus signs, so every comparison goes through ``normalize`` first.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass

from rapidfuzz import fuzz

_CHAR_MAP = str.maketrans(
    {
        "−": "-",  # minus
        "–": "-",
        "—": "-",
        "‐": "-",
        "µ": "u",  # micro sign
        "μ": "u",  # greek mu
        "Ω": "ohm",  # ohm sign
        "Ω": "ohm",
        "°": "deg",
        "±": "+/-",
        "≤": "<=",
        "≥": ">=",
        "×": "x",
        "‘": "'",
        "’": "'",
        "“": '"',
        "”": '"',
        "ﬁ": "fi",
        "ﬂ": "fl",
    }
)

_WS = re.compile(r"\s+")
_NON_ALNUM = re.compile(r"[^0-9a-z.+\-/<=>%]+")
_NUMBER = re.compile(r"(?<![\w.])[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?")


def normalize(text: str) -> str:
    """Lowercase, unify unicode symbols, collapse whitespace."""
    text = text.replace("º", "°")  # NFKC would fold the ordinal sign into "o"
    text = unicodedata.normalize("NFKC", text).translate(_CHAR_MAP).lower()
    return _WS.sub(" ", text).strip()


def squash(text: str) -> str:
    """Aggressive normalization: drop everything but alnum and a few math symbols."""
    return _NON_ALNUM.sub("", normalize(text))


_GROUPED = re.compile(r"(?<![\d.,])-?\d{1,3}(?:,\d{3})+(?:\.\d+)?(?![\d,])")


def numbers_in(text: str) -> list[float]:
    """All numeric literals in text (after normalization). Thousands-grouped literals
    ("4,351") are returned both whole and as their comma-separated parts, since a comma can
    also separate list items."""
    out = []
    norm = normalize(text)
    for m in _NUMBER.finditer(norm):
        try:
            out.append(float(m.group()))
        except ValueError:
            continue
    out += [float(m.group().replace(",", "")) for m in _GROUPED.finditer(norm)]
    return out


def contains_number(text: str, value: float, rel_tol: float = 1e-6) -> bool:
    """True if ``value`` appears as a numeric literal in ``text`` (sign-insensitive)."""
    for n in numbers_in(text):
        if abs(abs(n) - abs(value)) <= rel_tol * max(1.0, abs(value)):
            return True
    return False


@dataclass(frozen=True)
class SpanMatch:
    stage: str  # exact | normalized | squashed | fuzzy
    score: float  # 0..100
    start: int | None = None  # char offsets in the *original* haystack when known
    end: int | None = None


def find_span(quote: str, haystack: str, fuzzy_threshold: float = 90.0) -> SpanMatch | None:
    """Locate ``quote`` in ``haystack``. Returns the first stage that matches, or None."""
    if not quote.strip():
        return None
    idx = haystack.find(quote)
    if idx >= 0:
        return SpanMatch("exact", 100.0, idx, idx + len(quote))
    nq, nh = normalize(quote), normalize(haystack)
    if nq and nq in nh:
        return SpanMatch("normalized", 100.0)
    sq, sh = squash(quote), squash(haystack)
    if len(sq) >= 3 and sq in sh:
        return SpanMatch("squashed", 100.0)
    if len(nq) < 8:  # fuzzy on very short strings is meaningless
        return None
    al = fuzz.partial_ratio_alignment(nq, nh)
    if al is not None and al.score >= fuzzy_threshold:
        return SpanMatch("fuzzy", float(al.score))
    return None


def contains_phrase(phrase: str, haystack: str) -> bool:
    """Whitespace/punctuation-insensitive containment, for keyword presence checks."""
    sp = squash(phrase)
    return bool(sp) and sp in squash(haystack)


_TOKEN = re.compile(r"[a-z0-9]+(?:\.[0-9]+)?")


def find_tokens(quote: str, haystack: str, slack: float = 3.0, min_width: int = 0) -> bool:
    """Order-free alignment: every token of ``quote`` occurs within one window of the haystack
    of at most ``slack`` x the quote's token count. Tolerates table rows that PDF extraction
    split across lines or columns, but still rejects quotes with values that are not there."""
    q = _TOKEN.findall(normalize(quote))
    if len(q) < 3:
        return False
    t = _TOKEN.findall(normalize(haystack))
    need = Counter(q)
    width = max(int(len(q) * slack) + 2, min_width)
    for start in range(len(t)):
        if t[start] not in need:
            continue
        window = Counter(t[start : start + width])
        if all(window[k] >= v for k, v in need.items()):
            return True
    return False


def align_quote(quote: str, haystack: str, loose: bool = True) -> SpanMatch | None:
    """``find_span``, falling back (when ``loose``) to the order-free token window, so table
    rows that PDF extraction reordered still count. Values must still be present."""
    m = find_span(quote, haystack)
    if m is None and loose and find_tokens(quote, haystack):
        m = SpanMatch("tokens", 80.0)
    return m
