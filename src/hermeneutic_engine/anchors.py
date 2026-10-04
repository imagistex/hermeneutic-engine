"""Anchors: an exact quotation plus its position in a source.

Offsets are zero-based, half-open UTF-8 byte offsets into the stored source.
Nothing is normalized before anchoring.

Readers never write offsets. They return the words; `resolve` finds them inside
the unit the reader was looking at. Because the search is scoped to one unit,
the same words elsewhere in the corpus cannot block or misdirect it.

`exact`, `prefix` and `suffix` mirror the W3C Web Annotation text-quote
selector; `start` and `end` mirror the text-position selector (in bytes here).
"""

from __future__ import annotations

import re

_WS = re.compile(r"\s+")
CONTEXT_CHARS = 32


def _context(source: bytes, start: int, end: int) -> tuple[str, str]:
    reach = CONTEXT_CHARS * 4  # enough bytes for CONTEXT_CHARS of any UTF-8
    prefix = source[max(0, start - reach):start].decode("utf-8", errors="ignore")[-CONTEXT_CHARS:]
    suffix = source[end:end + reach].decode("utf-8", errors="ignore")[:CONTEXT_CHARS]
    return prefix, suffix


def _collapse(text: str) -> tuple[str, list[int]]:
    """Collapse whitespace runs to one space; keep a map back to `text`."""
    out: list[str] = []
    index: list[int] = []
    in_space = False
    for i, ch in enumerate(text):
        if ch.isspace():
            if out and not in_space:
                out.append(" ")
                index.append(i)
            in_space = True
        else:
            out.append(ch)
            index.append(i)
            in_space = False
    if out and out[-1] == " ":
        out.pop()
        index.pop()
    return "".join(out), index


def _count(haystack, needle) -> int:
    """How many times `needle` occurs, counting overlapping matches."""
    n, at = 0, haystack.find(needle)
    while at >= 0:
        n += 1
        at = haystack.find(needle, at + 1)
    return n


def resolve(source: bytes, unit_start: int, unit_end: int, quote: str) -> dict | None:
    """Find `quote` inside source[unit_start:unit_end].

    Returns an anchor (without the source ID), or None if the words are not
    there. `match` is "exact", or "ws-normalized" when only whitespace differed;
    in both cases `exact` holds the true source text, never the reader's copy.
    """
    if not quote or not quote.strip():
        return None
    unit = source[unit_start:unit_end]
    needle = quote.encode("utf-8")
    at = unit.find(needle)
    if at >= 0:
        start = unit_start + at
        end = start + len(needle)
        exact = quote
        occurrences = _count(unit, needle)
        match = "exact"
    else:
        text = unit.decode("utf-8")
        collapsed, index = _collapse(text)
        wanted = _WS.sub(" ", quote).strip()
        at = collapsed.find(wanted)
        if at < 0:
            return None
        c0 = index[at]
        c1 = index[at + len(wanted) - 1] + 1
        start = unit_start + len(text[:c0].encode("utf-8"))
        end = unit_start + len(text[:c1].encode("utf-8"))
        exact = text[c0:c1]
        occurrences = _count(collapsed, wanted)
        match = "ws-normalized"
    prefix, suffix = _context(source, start, end)
    return {
        "start": start,
        "end": end,
        "exact": exact,
        "prefix": prefix,
        "suffix": suffix,
        "match": match,
        "occurrences": occurrences,
    }


def check(source: bytes, anchor: dict) -> bool:
    """True if the anchor's bytes still say exactly what the anchor claims."""
    try:
        start, end = anchor["start"], anchor["end"]
        # Python would quietly accept negative or oversized slice bounds.
        if not (isinstance(start, int) and isinstance(end, int) and 0 <= start < end <= len(source)):
            return False
        return source[start:end].decode("utf-8") == anchor["exact"]
    except (UnicodeDecodeError, KeyError, TypeError):
        return False
