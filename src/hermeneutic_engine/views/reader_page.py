"""The reader page: one self-contained HTML file for a first visit.

It lets a visitor take one walk: from a finding, to the exact words in the
source that support it, to when those words first appeared and who took them
up, to where the model readers disagreed. Every number on it is counted from
the project when the page is rendered. Nothing is typed in except short
explanatory prose.

The file makes no requests. Its data is embedded as JSON, and a small script
sorts, filters and selects. The corpus was written by agents and contains
markup and instructions, so its text never reaches the page as markup: the
markup written here escapes it, the JSON block escapes every character that
could end the block or open a tag, and the script inserts text only as text
nodes. The whole file is ASCII, so it reads the same whatever character set a
viewer assumes.

A section whose data does not exist yet is left out. Rendering reads the
project and never writes to it.
"""

from __future__ import annotations

import html
import json
import re
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from ..anchors import check
from ..methods import focused_coding, sampling
from ..store import KIND_BY_PREFIX, Project
from ..verify import verify
from . import concordance, divergence, lexicon, names

METHODS = ("open-coding", "focused-coding")
PROSE = ("post", "text")       # the unit kinds the lexicon counts as statements
CONTEXT = 60                   # characters either side of a word in the concordance
PER_TERM = 60                  # concordance lines kept per term, evenly spaced in time
FIRST_USE_CONTEXT = 110        # characters either side of a term's first use
EARLIEST_NAMES = 40
BUSIEST_DAY_NAMES = 80
DATE_CODES = 12
TOP_DIVERGENT = 60
TOP_UNFIT = 300                # passages the codebook had no place for; those flagged by the most readers first
NOTES_OPEN = 12                # a lens's field notes start open when it has no more than this many
EXCERPT = 600                  # characters of a whole statement quoted as evidence
NOT_FOUND = "quotation not found"
ABSENT_CAVEAT = ("These patterns occur in no statement. Absence from this archive is not evidence that the idea was "
                 "absent: the archive holds only what was written to the wiki and kept by the publisher.")
_WS = re.compile(r"\s+")
_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")


# ---- small helpers ---------------------------------------------------------

def _esc(value) -> str:
    """Text for the page's markup: escaped, with anything outside ASCII as a character reference."""
    text = "" if value is None else str(value)
    return html.escape(text, quote=True).encode("ascii", "xmlcharrefreplace").decode("ascii")


def _script_json(data) -> str:
    """JSON for a <script type="application/json"> block. `<`, `>` and `&` are
    escaped so no text can end the block or open a tag inside it, and `=` so a
    URL in the corpus never reads as an src= or href= attribute to a scanner."""
    text = json.dumps(data, ensure_ascii=True, separators=(",", ":"))
    for char, code in (("<", "\\u003c"), (">", "\\u003e"), ("&", "\\u0026"), ("=", "\\u003d")):
        text = text.replace(char, code)
    return text


def _n(value) -> str:
    return f"{value:,}"


def _share(part: int, whole: int) -> str:
    return f"{part / whole:.0%}" if whole else "0%"


def _plural(n: int, word: str, plural: str | None = None) -> str:
    return f"{n:,} " + (word if n == 1 else plural or word + "s")


def _stamp(time) -> str:
    return f"{str(time).replace('T', ' ').rstrip('Z')} UTC" if time else "time not recorded"


def _minute(time) -> str:
    return str(time)[:16].replace("T", " ") if time else ""


def _kind(rid) -> str | None:
    return KIND_BY_PREFIX.get(str(rid).split(":", 1)[0])


def _ctx(record) -> dict:
    return (record or {}).get("context") or {}


def _first_seen(unit) -> str:
    return (_ctx(unit).get("first_seen") or {}).get("time") or ""


def _u16(text: str) -> int:
    """Length in UTF-16 code units, which is how the page's script counts."""
    return len(text.encode("utf-16-le")) // 2


def _day_axis(days) -> list[str]:
    """Every day from the first to the last of `days`, so that time on a chart is to scale."""
    known = sorted({d for d in days if d and _DAY.fullmatch(d)})
    if not known:
        return []
    start, end = date.fromisoformat(known[0]), date.fromisoformat(known[-1])
    return [(start + timedelta(days=k)).isoformat() for k in range((end - start).days + 1)]


# ---- lenses ----------------------------------------------------------------

def _reader(lens: dict) -> dict:
    return lens.get("reader") or {}


def _method(lens: dict) -> dict:
    return lens.get("method") or {}


def _framed(lens: dict) -> bool:
    return any(part.get("name") == "frame" for part in lens.get("stack") or [])


def _model(lens: dict) -> str:
    reader = _reader(lens)
    return str(reader.get("model") or reader.get("id") or lens["id"])


def _model_lenses(project: Project) -> list[dict]:
    """Model readers of the coding methods: by method and instructions version,
    framed before unframed, and a substitute just after the model it stood in for."""
    found = [lens for lens in project.records("lens")
             if _reader(lens).get("kind") == "model" and _method(lens).get("name") in METHODS]
    return sorted(found, key=lambda lens: (
        METHODS.index(_method(lens)["name"]), str(_method(lens).get("version", "0")), not _framed(lens),
        str(_reader(lens).get("family") or ""), str(_reader(lens).get("requested") or _model(lens)),
        bool(_reader(lens).get("requested")), lens["id"]))


def _given(lens: dict, codebook_memo_id: str | None) -> list[str]:
    """What a reader of a codebook was given, where that sets its reading apart:
    only some code types, or a codebook other than the one this page is about."""
    types = _method(lens).get("code_types")
    names = [str(t).replace("_", " ") for t in ([types] if isinstance(types, str) else types or []) if str(t).strip()]
    given = [" and ".join(names) + " codes only"] if names else []
    codebook = _reader(lens).get("codebook")
    if codebook and codebook != codebook_memo_id:
        given.append(f"codebook {codebook}")
    return given


def _qualifiers(lens: dict, several_methods: bool, codebook_memo_id: str | None = None) -> list[str]:
    method = _method(lens)
    quals = [(method["name"].replace("-", " ") + ", " if several_methods else "")
             + f"instructions v{method.get('version', '0')}"]
    quals += _given(lens, codebook_memo_id)
    if not _framed(lens):
        quals.append("no frame")
    if _reader(lens).get("requested"):
        quals.append(f"answering in place of {_reader(lens)['requested']}")
    return quals


def _lens_labels(project: Project, lens_ids, codebook_memo_id: str | None = None) -> dict[str, str]:
    """Markup naming each lens: who read, and how. The method is always named,
    and lenses whose labels would read the same also carry their IDs."""
    texts = {}
    for lid in lens_ids:
        lens = project.get(lid) if isinstance(lid, str) and _kind(lid) == "lens" else None
        if lens is None:
            texts[lid] = (str(lid), "not a lens in the ledger")
            continue
        reader, method = _reader(lens), _method(lens)
        if reader.get("kind") == "model" and method.get("name") in METHODS:
            quals = ([str(reader["family"])] if reader.get("family") else []) + _qualifiers(lens, True, codebook_memo_id)
        else:
            quals = [str(x) for x in (reader.get("kind"), str(method.get("name") or "").replace("-", " ")) if x]
        texts[lid] = (_model(lens), ", ".join(quals))
    same = Counter(texts.values())
    return {lid: f'<span class="lens-label"><code>{_esc(name)}</code> <span class="quals">{_esc(quals)}</span>'
                 + (f' <code class="quals">{_esc(lid)}</code>' if same[(name, quals)] > 1 else "") + "</span>"
            for lid, (name, quals) in texts.items()}


# ---- 1. opening --------------------------------------------------------------

_ICON_OK = ('<svg viewBox="0 0 16 16" aria-hidden="true" focusable="false"><path fill="currentColor" '
            'd="M6.4 11.6 2.8 8l1.1-1.1 2.5 2.5 5.7-5.7 1.1 1.1z"/></svg>')
_ICON_BAD = ('<svg viewBox="0 0 16 16" aria-hidden="true" focusable="false"><path fill="currentColor" '
             'd="M4.2 3.1 8 6.9l3.8-3.8 1.1 1.1L9.1 8l3.8 3.8-1.1 1.1L8 9.1l-3.8 3.8-1.1-1.1L6.9 8 3.1 4.2z"/></svg>')


def _fact(key: str, value: int) -> str:
    return f'<span class="n" data-fact="{key}">{_n(value)}</span>'


def _opening(project: Project, report: dict, title: str, toc: list[tuple[str, str]], walk: list[str]) -> str:
    units = project.records("unit")
    facts = [
        ("sources", len(project.records("source")), "sources, stored byte for byte"),
        ("statements", sum(1 for u in units if u.get("unit_kind") in PROSE),
         "statements (signed posts and unsigned prose)"),
        ("posts", sum(1 for u in units if u.get("unit_kind") == "post"), "signed posts"),
        ("lenses", len(project.records("lens")), "lenses, each recording who read and how"),
        ("anchors", report["anchors_checked"], "anchors checked against their source bytes"),
    ]
    name = project.frame.get("name")
    out = ['<header class="opening" id="sec-opening">']
    if name:
        out.append(f'<p class="eyebrow">Corpus: {_esc(name)}</p>')
    out += [f'<h1 class="title">{_esc(title)}</h1>',
            '<p class="lede">This is a qualitative coding project kept in hermeneutic-engine, where every quotation '
            'points at the exact bytes of its source and every reading records the model, the instructions and the '
            'priors behind it.</p>',
            '<dl class="facts">']
    for key, value, label in facts:
        out.append(f'<div class="fact" data-fact="{key}"><dt>{_esc(label)}</dt><dd>{_n(value)}</dd></div>')
    out += ['</dl>', '<p class="small">Counted from the project&rsquo;s ledger when this page was rendered.</p>']

    digest = report["digest"]
    out.append('<section class="seal" aria-label="verify">')
    if report["ok"]:
        out.append(f'<p class="seal-status is-ok">{_ICON_OK}<span>verify passed</span></p>')
        line = "It found no problems."
    else:
        out.append(f'<p class="seal-status is-bad">{_ICON_BAD}<span>verify failed</span></p>')
        line = f'It found {_fact("errors", report["n_errors"])} problems.'
    if report["n_warnings"]:
        rid, why = report["warnings"][0]
        line += (f' It also gave {_fact("warnings", report["n_warnings"])} warnings, the first for '
                 f'<code>{_esc(rid)}</code>: {_esc(why)}.')
    out.append(f"<p>{line}</p>")
    if not report["ok"]:
        out.append('<ul class="problems">' + "".join(
            f"<li><code>{_esc(rid)}</code>: {_esc(why)}</li>" for rid, why in report["errors"][:5]) + "</ul>")
    out += [f'<p class="digest-line"><span>Ledger digest</span> <code class="digest" title="{_esc(digest)}">'
            f'{_esc(digest[:12])}&hellip;</code> <button type="button" class="btn-link" data-toggle '
            'aria-expanded="false" aria-controls="digest-full" data-open="Hide the full digest" '
            'data-closed="Show the full digest">Show the full digest</button></p>',
            f'<p class="digest-full" id="digest-full" hidden><code>{_esc(digest)}</code></p>',
            f'<p>This shows {_esc(report["shows"])}. It does not show {_esc(report["does_not_show"])}.</p>',
            f'<p class="small">verify re-hashed {_fact("blobs", report["blobs"])} stored source files and compared '
            f'{_fact("runs", report["runs_checked"])} recorded model calls with the prompts and responses kept '
            'beside the ledger. The digest is a fingerprint of the ledger; publishing it elsewhere is what makes '
            'later tampering detectable.</p>',
            '</section>']
    if walk:
        out.append(f'<p class="walk-note">{" ".join(walk)}</p>')
    if toc:
        out.append('<nav class="toc" aria-label="Sections">'
                   + "".join(f'<a href="#{anchor}">{_esc(label)}</a>' for anchor, label in toc) + "</nav>")
    out.append("</header>")
    return "\n".join(out)


# ---- 2. their words ----------------------------------------------------------

def _words(project: Project, terms_text: str, sample: list | None, days: list[str]):
    """The lexicon section, its data, the term to open first, and for each
    sampled statement the terms it uses."""
    terms = lexicon.parse_terms(terms_text or "")
    if not terms:
        return "", [], None, {}
    try:
        results = lexicon.trace(project, terms)
    except (KeyError, TypeError):  # a corpus without first-seen times cannot be traced
        return "", [], None, {}
    order = sorted((i for i, r in enumerate(results) if r["units"]),
                   key=lambda i: (results[i]["first"]["time"] or "", results[i]["term"]))
    absent = [r for r in results if not r["units"]]
    data = []
    for i in order:
        r, regex = results[i], terms[i]["regex"]
        first = r["first"]
        text, (s0, s1) = first["text"], first["span"]
        lo, hi = max(0, s0 - FIRST_USE_CONTEXT), min(len(text), s1 + FIRST_USE_CONTEXT)
        anchor = first["anchor"]
        try:
            checked = check(project.source_bytes(anchor["source"]), anchor)
        except Exception:
            checked = False
        rows = concordance.lines(project, regex, width=CONTEXT)
        shown = rows if len(rows) <= PER_TERM else \
            [rows[round(k * (len(rows) - 1) / (PER_TERM - 1))] for k in range(PER_TERM)]
        data.append({
            # A plain term is the writers' own word, quoted when the page refers to it; a custom pattern's
            # label is the researcher's.
            "term": r["term"], "quote": lexicon.parse_terms(r["term"])[0]["regex"].pattern == r["pattern"],
            "pattern": r["pattern"], "units": r["units"], "uses": r["occurrences"],
            "names": r["signers"], "pages": r["pages"], "last": r["last_new_use"] or "",
            "by_day": {d: n for d, n in r["by_day"].items() if d}, "undated": r["by_day"].get("", 0),
            "peak": list(r["peak_day"]), "removed": r["later_removed"],
            "first": {
                "time": first["time"], "signature": first["signature"], "saved_as": first["saved_as"],
                "page": first["page"], "unit": first["unit"], "source": anchor["source"],
                "start": anchor["start"], "end": anchor["end"], "checked": checked,
                "before": _WS.sub(" ", text[lo:s0]), "match": text[s0:s1], "after": _WS.sub(" ", text[s1:hi]),
                "cut_left": lo > 0, "cut_right": hi < len(text),
            },
            "conc": {"from": (rows[0]["time"] or "")[:10] if rows else "",
                     "to": (rows[-1]["time"] or "")[:10] if rows else "",
                     "rows": [[row["time"], row["who"], row["left"], _WS.sub(" ", row["key"]), row["right"]]
                              for row in shown]},
            "sample": None if sample is None else [k for k, (_, utext) in enumerate(sample) if regex.search(utext)],
        })
    default = max(range(len(data)), key=lambda k: (data[k]["units"], -k)) if data else None
    unit_terms: dict[int, list[int]] = defaultdict(list)
    for t, entry in enumerate(data):
        for k in entry["sample"] or []:
            unit_terms[k].append(t)

    total = results[0]["of_units"] if results else 0
    out = ['<section class="section" id="sec-words" aria-labelledby="h-words">',
           '<h2 id="h-words">Their words</h2>',
           f'<p class="dek">The writers&rsquo; own terms, traced mechanically through {_n(total)} statements '
           '(signed posts and unsigned prose). A term is nominated by a reader, by frequency or by the researcher; '
           'everything after that is counting, and no model is involved. A statement is counted once, at its first '
           'appearance on a page. Names counts distinct signatures, or saving names where a statement is '
           'unsigned.</p>']
    if data:
        span = f"{days[0]} to {days[-1]}" if days else ""
        out += ['<div class="lex">', '<div class="lex-side">',
                '<div class="scroll lex-wrap" id="lex-wrap"><table class="lex-table" id="lex-table">',
                '<caption class="sr-only">Terms that occur, with their first use, statements and names</caption>',
                '<thead><tr><th scope="col">Term</th>'
                '<th scope="col" data-sort="first" aria-sort="ascending"><button type="button" class="sort">'
                'First used</button></th>'
                '<th scope="col" class="num" data-sort="units"><button type="button" class="sort">Statements'
                '</button></th>'
                '<th scope="col" class="num" data-sort="names"><button type="button" class="sort">Names</button></th>'
                '<th scope="col" class="spark-col">By day</th></tr></thead><tbody>']
        for k, t in enumerate(data):
            out.append(
                f'<tr data-i="{k}" data-first="{_esc(t["first"]["time"] or "")}" data-units="{t["units"]}" '
                f'data-names="{t["names"]}"><th scope="row"><button type="button" class="term-btn" '
                f'aria-pressed="false">{_esc(t["term"])}</button></th>'
                f'<td class="when">{_esc(_minute(t["first"]["time"]))}</td><td class="num">{_n(t["units"])}</td>'
                f'<td class="num">{_n(t["names"])}</td><td class="spark" data-spark="{k}"></td></tr>')
        out += ['</tbody></table></div>',
                f'<p class="small">{len(data):,} of {len(results):,} terms searched for occur. Times are UTC. Each '
                f'row&rsquo;s bars count new statements per day from {_esc(span)}, scaled to that term&rsquo;s '
                'busiest day. Select a term to see its first use and its uses in context.</p>',
                '</div>', '<div class="panel lex-detail" id="lex-detail"></div>', '</div>',
                '<div class="lex-conc" id="lex-conc"></div>']
    if absent:
        out += ['<div class="absent">', f'<h3>{_plural(len(absent), "term")} searched for and not found</h3>',
                f'<p>{_esc(ABSENT_CAVEAT)}</p>', '<ul class="absent-list">']
        out += [f'<li>{_esc(r["term"])}</li>' for r in absent]
        out += ['</ul>', '<details class="patterns"><summary>The exact patterns searched for</summary><ul>']
        out += [f'<li><span class="term-inline">{_esc(r["term"])}</span> <code>{_esc(r["pattern"])}</code></li>'
                for r in absent]
        out += ['</ul></details>', '</div>']
    out.append("</section>")
    return "\n".join(out), data, default, dict(unit_terms)


# ---- 3. their names ----------------------------------------------------------

def _names(survey: dict, days: list[str]):
    by_day = survey["by_day"]
    posts = survey["posts"]
    out = ['<section class="section" id="sec-names" aria-labelledby="h-names">',
           '<h2 id="h-names">Their names</h2>',
           f'<p class="dek">{_n(survey["names"])} names were used to save edits, and {_n(survey["signatures"])} '
           f'distinct signatures close {_n(posts)} signed posts. Names are self-chosen labels; one writer may use '
           'many, and many writers may share one. A name counts on the day it first saves an edit. The short word '
           'lists that sort name parts into kinds are declared in <code>views/names.py</code>.</p>']
    chart = None
    if by_day:
        chart = {"date": [by_day[d]["date"] if d in by_day else 0 for d in days],
                 "other": [by_day[d]["names"] - by_day[d]["date"] if d in by_day else 0 for d in days]}
        out += ['<div class="names-grid">',
                '<figure class="chart-fig"><figcaption class="chart-cap"><span class="chart-title">New names per day'
                '</span> <span class="legend"><span class="swatch s1"></span>carrying a month and day, as in '
                '<code>Aug08</code></span> <span class="legend"><span class="swatch s0"></span>other new names</span>'
                '</figcaption><div class="chart" id="names-chart"></div></figure>',
                '<div class="scroll names-wrap"><table class="data">',
                '<caption class="sr-only">New names by day, and what they contain</caption>',
                '<thead><tr><th scope="col">Day</th><th scope="col" class="num">New names</th>'
                '<th scope="col" class="num">Institution (OpenAI, OAI)</th><th scope="col" class="num">Role word</th>'
                '<th scope="col" class="num">Month and day</th><th scope="col" class="num">Collective word</th>'
                '</tr></thead><tbody>']
        for day, c in by_day.items():
            n = c["names"]
            cells = "".join(f'<td class="num">{_n(c[k])} <span class="muted">({_share(c[k], n)})</span></td>'
                            for k in ("institution", "role", "date", "collective"))
            out.append(f'<tr><th scope="row">{_esc(day or "no time recorded")}</th>'
                       f'<td class="num">{_n(n)}</td>{cells}</tr>')
        out += ['</tbody></table></div>', '</div>']

    earliest = sorted(survey["first_saved"].items(), key=lambda kv: (kv[1], kv[0]))
    if earliest:
        busiest = max(by_day.items(), key=lambda kv: kv[1]["names"])[0] if by_day else None
        on_busiest = [name for name, time in earliest if busiest and time[:10] == busiest]
        more = len(on_busiest) - BUSIEST_DAY_NAMES
        out += ['<div class="two-up">',
                f'<div><h3>The earliest names</h3><p class="small">The first {min(EARLIEST_NAMES, len(earliest))} '
                'names to save an edit, in order.</p><ul class="namelist">'
                + "".join(f'<li title="{_esc(_stamp(t))}">{_esc(name)}</li>' for name, t in earliest[:EARLIEST_NAMES])
                + "</ul></div>"]
        if on_busiest:
            out.append(f'<div><h3>New on {_esc(busiest)}, the busiest day</h3><p class="small">'
                       f'{_n(len(on_busiest))} names first saved an edit that day'
                       + (f'; the first {BUSIEST_DAY_NAMES} are listed' if more > 0 else "") + '.</p>'
                       '<ul class="namelist">'
                       + "".join(f"<li>{_esc(name)}</li>" for name in on_busiest[:BUSIEST_DAY_NAMES])
                       + (f'<li class="muted">and {_n(more)} more</li>' if more > 0 else "") + "</ul></div>")
        out.append("</div>")

    if posts:
        out += ['<div class="findings">',
                f'<p><strong>Signing under another name.</strong> {_n(survey["differs"])} of {_n(posts)} signed '
                f'posts ({_share(survey["differs"], posts)}) end in a signature that differs from the name the edit '
                f'was saved under. In {_n(survey["differs_same_date"])} of those the two names carry the same month '
                'and day.</p>',
                f'<p><strong>Addressing by date code.</strong> {_n(survey["posts_dating"])} posts '
                f'({_share(survey["posts_dating"], posts)}) contain a month-and-day code other than their own, the '
                'short form by which posts address one another.</p>']
        codes = survey["addressed_dates"].most_common(DATE_CODES)
        if codes:
            out.append('<p class="small">Date codes most used in others&rsquo; posts: '
                       + ", ".join(f'<span class="name">{_esc(code)}</span> ({_n(n)})' for code, n in codes)
                       + ".</p>")
        out.append("</div>")
    out.append("</section>")
    return "\n".join(out), chart


# ---- 4. the readers ----------------------------------------------------------

def _readers(project: Project, lenses: list[dict], scope: set | None, sample_memo: dict | None,
             several: bool, codebook_memo_id: str | None = None) -> tuple[str, dict[str, set]]:
    """The readers table, and for each lens the units it read (inside the sample, when there is one)."""
    ids = {lens["id"] for lens in lenses}
    read: dict[str, set] = defaultdict(set)
    for activity in project.records("activity"):
        if activity.get("type") == "read" and activity.get("status") == "ok" and activity.get("lens") in ids:
            for uid in activity.get("used") or []:
                if _kind(uid) == "unit" and (scope is None or uid in scope):
                    read[activity["lens"]].add(uid)
    codings, in_vivo, code_types = Counter(), Counter(), {}
    for coding in project.records("coding"):
        if coding.get("by") in ids and (scope is None or coding.get("unit") in scope):
            cid = coding.get("code")
            if cid not in code_types:
                code_types[cid] = (project.get(cid) or {}).get("code_type")
            codings[coding["by"]] += 1
            in_vivo[coding["by"]] += code_types[cid] == "in_vivo"
    missed = Counter()
    for failure in project.records("failure"):
        attempted = failure.get("attempted") or {}
        if failure.get("by") in ids and str(failure.get("reason") or "").startswith(NOT_FOUND) \
                and (scope is None or attempted.get("unit") in scope):
            missed[failure["by"]] += 1

    out = ['<section class="section" id="sec-readers" aria-labelledby="h-readers">',
           '<h2 id="h-readers">The readers</h2>']
    dek = ("Each model reader is a lens: the model, the instructions it was given (stored and hashed part by part) and "
           "the priors it states. A frame is the publisher&rsquo;s account of the material; a lens without one was "
           "told nothing about where the text came from.")
    if scope is not None:
        info = (sample_memo or {}).get("sample") or {}
        how = f", seed {_esc(info['seed'])}" if info.get("seed") is not None else ""
        dek += (f" Counts cover the {_n(len(scope))} statements of sample <code>{_esc(sample_memo['id'])}</code>"
                f"{how}; readings of other statements are left out.")
    if all(lens.get("theory") == "withheld" for lens in lenses):
        dek += " No analytic theory was given to any of these readers."
    out += [f'<p class="dek">{dek}</p>', '<div class="table-wrap"><table class="data readers">',
            '<caption class="sr-only">Model readers and what they did</caption>',
            '<thead><tr><th scope="col">Reader</th><th scope="col">Family</th><th scope="col">Instructions</th>'
            '<th scope="col">Frame</th><th scope="col" class="num">Units read</th>'
            '<th scope="col" class="num">Codings</th><th scope="col" class="num">In vivo</th>'
            '<th scope="col" class="num">Quotations not found</th><th scope="col"><span class="sr-only">Priors'
            '</span></th></tr></thead><tbody>']
    notes = []
    for n, lens in enumerate(lenses):
        reader, method = _reader(lens), _method(lens)
        lid = lens["id"]
        units_read = len(read[lid])
        tried = codings[lid] + missed[lid]
        method_name = method["name"].replace("-", " ") + " " if several else ""
        sub = f'<span class="sub">answering in place of {_esc(reader["requested"])}</span>' if reader.get("requested") else ""
        priors = lens.get("priors") or []
        given = "".join(f'<span class="sub">{_esc(what)}</span>' for what in _given(lens, codebook_memo_id))
        out.append(
            f'<tr><th scope="row"><code class="model">{_esc(_model(lens))}</code>{sub}</th>'
            f'<td>{_esc(reader.get("family") or "")}</td>'
            f'<td>{_esc(method_name)}v{_esc(method.get("version", "0"))}{given}</td>'
            f'<td>{"given" if _framed(lens) else "not given"}</td>'
            f'<td class="num">{_n(units_read)}{f" of {_n(len(scope))}" if scope is not None else ""}</td>'
            f'<td class="num">{_n(codings[lid])}</td>'
            f'<td class="num">{_n(in_vivo[lid])} <span class="muted">({_share(in_vivo[lid], codings[lid])})</span></td>'
            f'<td class="num">{_n(missed[lid])} of {_n(tried)}</td>'
            f'<td><button type="button" class="btn-link" data-toggle aria-expanded="false" '
            f'aria-controls="priors-{n}" data-open="Hide priors" data-closed="Priors ({len(priors)})">'
            f'Priors ({len(priors)})</button></td></tr>')
        stack = ", ".join(f'{_esc(part.get("name"))} <code>{_esc(str(part.get("sha256", ""))[:12])}</code>'
                          for part in lens.get("stack") or [])
        out.append(
            f'<tr class="priors-row" id="priors-{n}" hidden><td colspan="9"><div class="priors">'
            f'<p>Stated priors of <code>{_esc(lid)}</code>:</p><ul>'
            + "".join(f"<li>{_esc(prior)}</li>" for prior in priors) + "</ul>"
            + f'<p class="small">Theory: {_esc(lens.get("theory") or "not stated")}. Prompt parts, by SHA-256: '
              f'{stack or "none"}.</p></div></td></tr>')
        if reader.get("requested"):
            sub_info = reader.get("substituted") or {}
            trigger = f" after a {_esc(sub_info['trigger'])}" if sub_info.get("trigger") else ""
            category = sub_info.get("category")
            notes.append(f'<li><code class="model">{_esc(_model(lens))}</code> read {_plural(units_read, "unit")} '
                         f'that were sent to <code class="model">{_esc(reader["requested"])}</code>. The harness '
                         f'substituted it{trigger} (category: {_esc(category or "not stated")}). These readings '
                         'are credited to the model that made them, not to the one that was asked.</li>')
    out += ['</tbody></table></div>']
    if notes:
        out.append('<ul class="notes">' + "".join(notes) + "</ul>")
    out.append('<p class="small">In vivo counts codes named in the writers&rsquo; own words. A quotation not '
               'found is one the reader gave that the kernel could not find in the statement; it is recorded as a '
               'failure and never becomes a coding.</p>')
    out.append("</section>")
    return "\n".join(out), read


# ---- 5. readings side by side ------------------------------------------------

def _span16(unit: dict, data: bytes, anchor) -> tuple[int, int] | tuple[None, None]:
    """Where an anchor sits in its unit's text, counted in UTF-16 units."""
    if not isinstance(anchor, dict) or anchor.get("source") != unit["source"]:
        return None, None
    start, end = anchor.get("start"), anchor.get("end")
    if not (isinstance(start, int) and isinstance(end, int) and unit["start"] <= start < end <= unit["end"]):
        return None, None
    a = _u16(data[:start - unit["start"]].decode("utf-8", errors="replace"))
    return a, a + _u16(data[start - unit["start"]:end - unit["start"]].decode("utf-8", errors="replace"))


def _readings(project: Project, lenses: list[dict], sample: list, sample_memo: dict, read: dict,
              several: bool, unit_terms: dict, codebook_memo_id: str | None = None) -> tuple[str, dict]:
    where = {unit["id"]: k for k, (unit, _) in enumerate(sample)}
    # A code of this page's codebook is defined once, under The codebook, and not again beside every coding.
    book = {code["id"] for code in focused_coding.codes_of(project, codebook_memo_id)} if codebook_memo_id else set()
    codings: dict[str, list] = defaultdict(list)
    for coding in project.records("coding"):
        if coding.get("unit") in where:
            codings[coding["unit"]].append(coding)
    missed: dict[str, list] = defaultdict(list)
    for failure in project.records("failure"):
        attempted = failure.get("attempted") or {}
        if str(failure.get("reason") or "").startswith(NOT_FOUND) and attempted.get("unit") in where:
            missed[attempted["unit"]].append(failure)

    # Only the lenses that read or coded something in the sample.
    touched = [lens for lens in lenses
               if read[lens["id"]] or any(c["by"] == lens["id"] for cs in codings.values() for c in cs)
               or any(f["by"] == lens["id"] for fs in missed.values() for f in fs)]
    index = {lens["id"]: i for i, lens in enumerate(touched)}
    code_cache: dict[str, dict] = {}

    def code_of(cid) -> dict:
        if cid not in code_cache:
            record = project.get(cid) if isinstance(cid, str) and _kind(cid) == "code" else None
            code_cache[cid] = record or {"name": str(cid or "")}
        return code_cache[cid]

    units = []
    for k, (unit, text) in enumerate(sample):
        ctx = _ctx(unit)
        data = text.encode("utf-8")
        rows = []
        for coding in sorted(codings[unit["id"]], key=lambda c: (index.get(c["by"], 99),
                                                                  (c.get("anchor") or {}).get("start", 0))):
            if coding["by"] not in index:
                continue
            code = code_of(coding.get("code"))
            a, b = _span16(unit, data, coding.get("anchor"))
            defined = "" if coding.get("code") in book else coding.get("definition") or code.get("definition") or ""
            rows.append([index[coding["by"]], code.get("name") or "", code.get("code_type") or "",
                         (coding.get("anchor") or {}).get("exact") or "", defined, coding.get("note") or "", a, b,
                         code.get("key") or ""])
        lost = [[index[f["by"]], (f.get("attempted") or {}).get("quote") or "",
                 code_of((f.get("attempted") or {}).get("code")).get("name") or ""]
                for f in missed[unit["id"]] if f["by"] in index]
        removed = ctx.get("first_removed_in") or {}
        final = ctx.get("finally_removed_in") or {}
        units.append({
            "id": unit["id"], "kind": unit.get("unit_kind"), "page": ctx.get("page"), "time": _first_seen(unit),
            "saved_as": ctx.get("introduced_by"), "signature": ctx.get("signature"),
            "uncertain": bool(ctx.get("signature_uncertain")),
            "removed": removed.get("time"), "removed_by": removed.get("by"),
            "restored": bool(ctx.get("returned_after_removal")), "final": final.get("time"),
            "in_last": ctx.get("in_last_revision"), "page_deleted": ctx.get("page_deleted_after"),
            "text": text, "r": sorted(index[lid] for lid in index if unit["id"] in read[lid]),
            "c": rows, "m": lost, "t": unit_terms.get(k, []),
        })
    lens_data = [{"model": _model(lens), "quals": _qualifiers(lens, several, codebook_memo_id)} for lens in touched]
    with_book = codebook_memo_id and any(_reader(lens).get("codebook") == codebook_memo_id for lens in touched)

    out = ['<section class="section" id="sec-readings" aria-labelledby="h-readings">',
           '<h2 id="h-readings">Readings side by side</h2>',
           f'<p class="dek">The {_n(len(sample))} statements of sample <code>{_esc(sample_memo["id"])}</code>, each '
           'with every reader&rsquo;s codings. A reader returns words and the kernel finds them in the statement; a '
           'quotation it cannot find is recorded as a failure and never becomes a coding. Shading counts how many of '
           'a statement&rsquo;s readers quoted each passage. Point at a coding to see the words it quotes.'
           + (' A reader doing focused coding applied a fixed codebook: each of its codings carries the code&rsquo;s '
              'key, and the code is defined once, under <a href="#sec-codebook">The codebook</a>, not beside each '
              'coding.' if with_book else "") + '</p>',
           '<fieldset class="rd-lenses" id="rd-lenses"><legend>Readers shown</legend></fieldset>',
           '<div class="rd">', '<div class="rd-side">',
           '<label class="rd-label" for="rd-filter">Filter the statements, or go to one by its number</label>',
           '<input id="rd-filter" type="search" placeholder="Words, page, name, or a number such as 055" autocomplete="off" '
           'spellcheck="false">',
           '<p class="rd-term" id="rd-term" hidden></p>', '<p class="rd-count small" id="rd-count" aria-live="polite">'
           '</p>', '<ol class="rd-list" id="rd-list"></ol>', '</div>',
           '<div class="panel rd-detail" id="rd-detail"></div>', '</div>', '</section>']
    return "\n".join(out), {"lenses": lens_data, "units": units}


# ---- 6. field notes, and where the codebook had no place ----------------------

def _field_notes(project: Project, lenses: list[dict], codebook_memo_id: str | None, sampled: bool) -> str:
    """What each reader wrote in its notebook at the end of a batch, as it wrote it."""
    by_lens: dict[str, list] = defaultdict(list)
    for memo in project.records("memo"):
        if memo.get("memo_type") == "field_note":
            by_lens[memo.get("by")].append(memo)
    if not by_lens:
        return ""
    order = [lens["id"] for lens in lenses if lens["id"] in by_lens]  # the page's order, then any other lens
    order += sorted((lid for lid in by_lens if lid not in order), key=str)
    labels = _lens_labels(project, order, codebook_memo_id)
    total = sum(len(notes) for notes in by_lens.values())
    out = ['<section class="section" id="sec-notes" aria-labelledby="h-notes">',
           '<h2 id="h-notes">Field notes</h2>',
           '<p class="dek">A field note is what a reader wrote in its notebook when it finished a batch of units: a '
           'few sentences in its own words, signed however it chose or not signed at all. '
           f'{_plural(total, "note")} from {_plural(len(order), "lens", "lenses")} '
           + ("is" if total == 1 else "are") + ' given here as written, each lens&rsquo;s in the order it wrote them. '
           + ('A note is about the whole batch it closes, so these are not limited to the sample. ' if sampled else "")
           + 'A note is a reading, not a finding: nothing in it has been checked against a source.</p>',
           '<div class="fn-lenses">']
    for lid in order:
        notes = sorted(by_lens[lid], key=lambda memo: str(memo.get("at") or ""))  # ties keep the ledger's order
        signed = sum(1 for memo in notes if str(memo.get("signed") or "").strip())
        out.append(f'<details class="fn-lens"{" open" if len(notes) <= NOTES_OPEN else ""}><summary>{labels[lid]} '
                   f'<span class="muted">{_plural(len(notes), "note")}, {_n(signed)} signed</span></summary>'
                   '<ol class="fn-list">')
        for memo in notes:
            signature = str(memo.get("signed") or "")
            units = sum(1 for ref in memo.get("about") or [] if isinstance(ref, str) and _kind(ref) == "unit")
            who = (f'Signed <span class="fn-sig">{_esc(signature)}</span>' if signature.strip()
                   else '<span class="muted">unsigned</span>')
            out.append(f'<li class="fn"><div class="fn-body">{_esc(memo.get("body"))}</div>'
                       f'<p class="prov">{who} &middot; covers {_plural(units, "unit")} &middot; '
                       f'{_esc(_stamp(memo.get("at")))} &middot; <code>{_esc(memo["id"])}</code></p></li>')
        out.append("</ol></details>")
    out += ["</div>", "</section>"]
    return "\n".join(out)


def _unfit(project: Project, lenses: list[dict], codebook_memo_id: str) -> str:
    """Passages that readers of this codebook said it had no code for, with the
    names they suggested. Passages whose anchors overlap in one source are put
    together, whichever lens flagged them, so that readers flagging the same
    words are seen side by side. As in the divergence view, only finished
    readings count."""
    of_book = {lens["id"]: lens for lens in lenses
               if _method(lens).get("name") == focused_coding.METHOD["name"]
               and _reader(lens).get("codebook") == codebook_memo_id}
    position = {lid: n for n, lid in enumerate(of_book)}
    finished = {activity["id"]: activity.get("lens") for activity in project.records("activity")
                if activity.get("type") == "read" and activity.get("status") == "ok"}
    entries = []
    for memo in project.records("memo"):
        if memo.get("memo_type") != "unfit" or memo.get("by") not in of_book \
                or finished.get(memo.get("activity")) != memo["by"]:
            continue
        about = memo.get("about") or []
        anchor = next((ref for ref in about if isinstance(ref, dict)), None)
        span = None
        if anchor is not None:
            source, start, end = anchor.get("source"), anchor.get("start"), anchor.get("end")
            if isinstance(source, str) and isinstance(start, int) and isinstance(end, int) and start < end:
                span = (source, start, end)
        entries.append({"memo": memo, "anchor": anchor, "span": span,
                        "unit": next((ref for ref in about if isinstance(ref, str) and _kind(ref) == "unit"), None)})
    if not entries:
        return ""

    # In order of position, an anchor overlaps the passages before it exactly when it starts before they end.
    groups: list[dict] = []
    for entry in sorted((e for e in entries if e["span"]), key=lambda e: e["span"]):
        source, start, end = entry["span"]
        last = groups[-1] if groups else None
        if last is not None and last["source"] == source and start < last["end"]:
            last["entries"].append(entry)
            last["end"] = max(last["end"], end)
        else:
            groups.append({"source": source, "end": end, "entries": [entry]})
    # Words that were not found point at no bytes, so each stands alone.
    groups += [{"source": None, "end": None, "entries": [entry]} for entry in entries if not entry["span"]]
    for group in groups:
        first = group["entries"][0]
        group["lenses"] = list(dict.fromkeys(entry["memo"]["by"] for entry in group["entries"]))
        group["families"] = {str(_reader(of_book[lid])["family"]) for lid in group["lenses"]
                             if _reader(of_book[lid]).get("family")}
        group["unit"] = project.get(first["unit"]) if first["unit"] else None
        group["order"] = (-len(group["families"]), -len(group["lenses"]), _first_seen(group["unit"]),
                          first["span"] or ("", 0, 0), str(first["memo"].get("at") or ""), first["memo"]["id"])
    groups.sort(key=lambda group: group["order"])
    shown = groups[:TOP_UNFIT]
    labels = _lens_labels(project, list(of_book), codebook_memo_id)

    def said(members: list[dict]) -> str:
        """Under one passage: the name each reader suggested, and what it said is being done."""
        items = []
        for entry in sorted(members, key=lambda e: (position[e["memo"]["by"]], str(e["memo"].get("at") or ""))):
            memo = entry["memo"]
            name, why = str(memo.get("suggested_name") or "").strip(), str(memo.get("body") or "").strip()
            items.append(
                "<li><p>" + (f'<span class="code-name">{_esc(name)}</span> <span class="muted">suggested by</span> '
                             if name else '<span class="muted">No name suggested by</span> ')
                + f'{labels[memo["by"]]}</p>'
                + (f'<p class="unfit-why">{_esc(why)}</p>' if why
                   else '<p class="muted">The reader did not say what is being done.</p>')
                + f'<p class="prov"><code>{_esc(memo["id"])}</code></p></li>')
        return '<ul class="unfit-said">' + "".join(items) + "</ul>"

    def unanchored(entry: dict) -> str:
        if entry["anchor"] is not None:  # an anchor that names no span: shown as it is, and it will not check
            return _quoted_anchor(project, entry["anchor"], after=said([entry]))
        quote = str(entry["memo"].get("quote_not_found") or "")
        if not quote.strip():
            return '<li class="ev"><p class="prov">The reader quoted no passage.</p>' + said([entry]) + "</li>"
        return (f'<li class="ev"><blockquote class="src">&ldquo;{_esc(quote)}&rdquo;</blockquote>'
                '<p class="prov"><strong>Not found in the source.</strong> The reader gave these words and the '
                'kernel could not find them in the unit, so they point at no bytes.</p>' + said([entry]) + "</li>")

    flaggers = {entry["memo"]["by"] for entry in entries}
    shared = sum(1 for group in groups if len(group["lenses"]) > 1)
    across = sum(1 for group in groups if len(group["families"]) > 1)
    lost = sum(1 for entry in entries
               if entry["anchor"] is None and str(entry["memo"].get("quote_not_found") or "").strip())
    out = ['<section class="section" id="sec-unfit" aria-labelledby="h-unfit">',
           '<h2 id="h-unfit">Where the codebook had no place</h2>',
           '<p class="dek">A reader applying the codebook can say that a passage does something the codebook has no '
           f'code for, and suggest a name for it. {_plural(len(entries), "passage")} '
           + ("was" if len(entries) == 1 else "were") + f' flagged this way, by {_n(len(flaggers))} of the '
           f'{_plural(len(of_book), "lens", "lenses")} that applied codebook <code>{_esc(codebook_memo_id)}</code>. '
           'Passages whose quoted bytes overlap in the same source are put together, whichever lens flagged them. '
           f'That leaves {_n(len(groups))}: {_n(shared)} flagged by more than one lens, and {_n(across)} of those by '
           'lenses of more than one model family. Different families flagging the same words is convergence; a '
           'passage that one lens alone flagged may say as much about that reader as about the codebook. A suggested '
           'name is a candidate: none of these has been added to the codebook.'
           + (f' For {_n(lost)} of the {_n(len(entries))}, the words the reader gave could not be found in the '
              'source: shown here, marked, and grouped with nothing.' if lost else "")
           + (f' Showing the first {_n(len(shown))}: those flagged by the most model families and lenses, earliest '
              'first among ties.' if len(shown) < len(groups)
              else ' Those flagged by the most model families and lenses come first, earliest first among ties.')
           + '</p>',
           '<ol class="unfit-list">']
    for group in shown:
        unit, n_lenses, n_families = group["unit"], len(group["lenses"]), len(group["families"])
        ctx = _ctx(unit)
        agree = (f'Flagged by {_plural(n_lenses, "lens", "lenses")} from '
                 + (_plural(n_families, "model family", "model families") if n_families
                    else "no recorded model family"))
        summary = " &middot; ".join(_esc(x) for x in (unit.get("unit_kind"), ctx.get("page"), _stamp(_first_seen(unit)))
                                    if x) if unit else ""
        facts = []
        if ctx.get("introduced_by"):
            facts.append(f'saved as <span class="name">{_esc(ctx["introduced_by"])}</span>')
        if ctx.get("signature"):
            facts.append(f'signed <span class="name">{_esc(ctx["signature"])}</span>')
        if unit:
            facts.append(f'<code>{_esc(unit["id"])}</code>')
        if group["source"] is None:
            passages = [unanchored(group["entries"][0])]
        else:  # one quotation for each distinct span, with everyone who quoted exactly that under it
            by_span: dict[tuple, list] = {}
            for entry in group["entries"]:
                by_span.setdefault(entry["span"], []).append(entry)
            passages = [_quoted_anchor(project, members[0]["anchor"], after=said(members))
                        for members in by_span.values()]
        out.append('<li class="unfit panel"><p class="unfit-head">'
                   + (f"<strong>{agree}</strong>" if n_lenses > 1 else f"<span>{agree}</span>")
                   + (f' <span class="muted">{summary}</span>' if summary else "") + "</p>"
                   + (f'<p class="small">{" &middot; ".join(facts)}</p>' if facts else "")
                   + '<ul class="evidence">' + "".join(passages) + "</ul></li>")
    out += ["</ol>", "</section>"]
    return "\n".join(out)


# ---- 7. the codebook, and where the readers differ ----------------------------

def _loaded(value) -> str:
    if isinstance(value, (list, tuple)):
        return "; ".join(str(v) for v in value if str(v).strip())
    return str(value or "")


def _codebook(project: Project, codebook_memo_id: str) -> tuple[str, str]:
    codes = focused_coding.codes_of(project, codebook_memo_id)
    result = divergence.compare(project, codebook_memo_id)
    readers = result["readers"]
    if not readers:
        return "", ""
    memo = project.get(codebook_memo_id) or {}
    book = memo.get("codebook") or {}
    author = _reader(project.get(memo.get("by")) or {})
    proposer = author.get("model") or author.get("id") or memo.get("by")
    per_code = result["per_code"]

    out = ['<section class="section" id="sec-codebook" aria-labelledby="h-codebook">',
           '<h2 id="h-codebook">The codebook</h2>',
           '<p class="dek">Codebook '
           + (f'<strong>{_esc(book["name"])}</strong>, ' if book.get("name") else "")
           + f'memo <code>{_esc(codebook_memo_id)}</code>, written by {_esc(proposer)}. It has '
             f'{_plural(len(codes), "code")}, given in this order to each of the {_n(len(readers))} readers who '
             'applied it. The counts under each code cover the units read by at least two of them.</p>',
           '<ol class="codes">']
    for code in codes:
        kind = "in vivo" if code.get("code_type") == "in_vivo" else "analytic"
        row = per_code.get(code["id"]) or {}
        out.append(f'<li class="code"><h3><code class="code-key">[{_esc(code.get("key"))}]</code> '
                   f'<span class="code-name">{_esc(code.get("name"))}</span> <span class="chip">{kind}</span></h3><dl>')
        for label, field in (("Definition", "definition"), ("Apply when", "apply_when"),
                             ("Do not apply when", "do_not_apply_when"), ("Loaded", "loaded")):
            value = _loaded(code.get(field)) if field == "loaded" else str(code.get(field) or "")
            if value.strip():
                out.append(f"<dt>{label}</dt><dd>{_esc(value)}</dd>")
        out.append(f'</dl><p class="small">Applied by every reader of the unit in {_plural(row.get("all", 0), "unit")};'
                   f' by some readers but not all in {_n(row.get("some", 0))}.</p></li>')
    out += ["</ol>", "</section>"]
    codebook_html = "\n".join(out)

    per_unit = result["per_unit"]
    compared = result["units_compared"]
    if not compared:
        return codebook_html, ""
    units = {uid: project.get(uid) for uid, entry in per_unit.items() if entry["divergence"]}
    ranked = sorted((uid for uid, unit in units.items() if unit is not None),
                    key=lambda uid: (-per_unit[uid]["divergence"], _first_seen(units[uid]), uid))
    shown = ranked[:TOP_DIVERGENT]
    by_id = {code["id"]: code for code in codes}
    out = ['<section class="section" id="sec-divergence" aria-labelledby="h-divergence">',
           '<h2 id="h-divergence">Where the readers differ</h2>',
           f'<p class="dek">{_n(compared)} units were read by at least two of the {_n(len(readers))} readers. In '
           f'{_n(len(ranked))} of them a code was applied by some of the unit&rsquo;s readers and not by others. '
           'Agreement is diagnostic, not truth: readers can agree and all be wrong, and where they differ the '
           'codebook may fit the unit poorly. No reader is treated as the standard.'
           + (f' Showing the {_n(len(shown))} that differ most, earliest first among ties.' if shown else "")
           + "</p>"]
    reader_list = "".join(
        f'<li><span class="reader-name">{_esc(r["name"])}</span>: {_plural(r["units_read"], "unit")} read, '
        f'{_plural(r["codings"], "coding")}, {_n(r["quotes_not_found"])} quotations not found</li>'
        for r in readers.values())
    out.append(f'<ul class="notes">{reader_list}</ul>')
    if shown:
        out.append('<ol class="div-list">')
    for n, uid in enumerate(shown):
        unit, entry = units[uid], per_unit[uid]
        ctx = _ctx(unit)
        d = entry["divergence"]
        summary = " &middot; ".join(_esc(x) for x in (unit.get("unit_kind"), ctx.get("page"), _stamp(_first_seen(unit)))
                                    if x)
        facts = ["Read by " + ", ".join(f'<span class="reader-name">{_esc(readers[lid]["name"])}</span>'
                                        for lid in entry["readers"])]
        if ctx.get("introduced_by"):
            facts.append(f'saved as <span class="name">{_esc(ctx["introduced_by"])}</span>')
        if ctx.get("signature"):
            facts.append(f'signed <span class="name">{_esc(ctx["signature"])}</span>')
        facts.append(f"<code>{_esc(uid)}</code>")
        out.append(f'<li><details{" open" if n == 0 else ""}><summary><strong>Readers differ on '
                   f'{_plural(d, "code")}</strong> <span class="muted">{summary}</span></summary>'
                   f'<p class="small">{" &middot; ".join(facts)}</p>'
                   f'<blockquote class="src unit-quote">{_esc(project.unit_text(unit))}</blockquote><ul class="applied">')
        who = entry["readers"]
        for cid, by in entry["applied"].items():
            code = by_id.get(cid) or {}
            said = []
            for lid in by:
                for quote in entry["quotes"].get(cid, {}).get(lid, []):
                    said.append(f'<li><span class="reader-name">{_esc(readers[lid]["name"])}</span> '
                                f'<span class="src">&ldquo;{_esc(quote["exact"])}&rdquo;</span>'
                                + (f' <span class="note">Note: {_esc(quote["note"])}</span>' if quote["note"] else "")
                                + "</li>")
            absent = []
            for lid in who:
                if lid in by:
                    continue
                lost = entry["not_found"].get(cid, {}).get(lid)
                absent.append(_esc(readers[lid]["name"]) + (
                    f' (its quotation <span class="src">&ldquo;{_esc(lost[0])}&rdquo;</span> is not in the unit)'
                    if lost else ""))
            out.append(f'<li><p><code class="code-key">[{_esc(code.get("key"))}]</code> '
                       f'<span class="code-name">{_esc(code.get("name"))}</span> '
                       f'<span class="muted">applied by {len(by)} of {len(who)}</span></p><ul class="said">'
                       + "".join(said) + "</ul>"
                       + (f'<p class="small">Not applied by {", ".join(absent)}.</p>' if absent else "") + "</li>")
        out.append("</ul></details></li>")
    if shown:
        out.append("</ol>")
    out.append("</section>")
    return codebook_html, "\n".join(out)


# ---- 8. claims -----------------------------------------------------------------

def _quoted_anchor(project: Project, anchor: dict, *, label: str = "", rid: str = "", clip: int | None = None,
                   after: str = "") -> str:
    """One quoted passage with where it sits in its source. `after` is markup,
    already escaped by the caller, that closes the item."""
    source = project.get(anchor.get("source")) if _kind(anchor.get("source")) == "source" else None
    ctx = _ctx(source)
    exact = str(anchor.get("exact") or "")
    shown = exact if clip is None or len(exact) <= clip else exact[:clip] + "\u2026"
    try:
        ok = check(project.source_bytes(anchor["source"]), anchor)
    except Exception:
        ok = False
    where = []
    if ctx.get("page"):
        where.append(f'<span class="name">{_esc(ctx["page"])}</span>')
    if ctx.get("time"):
        where.append(_esc(_stamp(ctx["time"])))
    prov = (f'<code>{_esc(anchor.get("source"))}</code> bytes {_esc(anchor.get("start"))}&ndash;{_esc(anchor.get("end"))}'
            + ("" if ok else ", <strong>which no longer match these words</strong>"))
    head = f'<p class="small">{_esc(label)}</p>' if label else ""
    tail = f' &middot; <code>{_esc(rid)}</code>' if rid else ""
    return (f'<li class="ev">{head}<blockquote class="src">&ldquo;{_esc(shown)}&rdquo;</blockquote>'
            f'<p class="prov">{" &middot; ".join(where + [prov])}{tail}</p>{after}</li>')


def _evidence(project: Project, ref) -> str:
    if isinstance(ref, dict):
        return _quoted_anchor(project, ref)
    record = project.get(ref) if isinstance(ref, str) else None
    if record is None:
        return f'<li class="ev"><p class="prov">Names <code>{_esc(ref)}</code>, which is not in the ledger.</p></li>'
    if record["kind"] == "coding":
        code = project.get(record.get("code")) or {}
        return _quoted_anchor(project, record.get("anchor") or {}, rid=record["id"],
                              label=f"Coded {code.get('name') or record.get('code')}")
    if record["kind"] == "unit":
        anchor = {"source": record["source"], "start": record["start"], "end": record["end"],
                  "exact": project.unit_text(record)}
        return _quoted_anchor(project, anchor, rid=record["id"], clip=EXCERPT)
    body = str(record.get("text") or record.get("body") or "")
    return (f'<li class="ev"><p>{_esc(body[:EXCERPT])}</p><p class="prov">{_esc(record["kind"])} '
            f'<code>{_esc(record["id"])}</code></p></li>')


def _claims(project: Project) -> str:
    claims = project.records("claim")
    if not claims:
        return ""
    out = ['<section class="section" id="sec-claims" aria-labelledby="h-claims">', '<h2 id="h-claims">Claims</h2>',
           f'<p class="dek">{_plural(len(claims), "claim")} in the ledger, each with the evidence for and against it. '
           'verify checks that every quotation below still matches its source bytes; it does not check that the '
           'claim is right.</p>']
    for claim in claims:
        author = _reader(project.get(claim.get("by")) or {})
        who = author.get("model") or author.get("id") or claim.get("by")
        limits = claim.get("limits")
        out.append(f'<article class="claim panel"><p class="claim-status"><span class="chip">'
                   f'{_esc(claim.get("status") or "status not stated")}</span></p>'
                   f'<p class="claim-text">{_esc(claim.get("text"))}</p>')
        if isinstance(limits, (list, tuple)) and limits:
            out.append("<p><strong>Limits.</strong></p><ul>" + "".join(f"<li>{_esc(x)}</li>" for x in limits) + "</ul>")
        elif limits:
            out.append(f"<p><strong>Limits.</strong> {_esc(limits)}</p>")
        else:
            out.append('<p class="muted">No limits stated.</p>')
        for label, field in (("Supporting", "supports"), ("Countering", "counters")):
            refs = claim.get(field)
            refs = [refs] if isinstance(refs, (str, dict)) else list(refs or [])
            out.append(f'<h3>{label}</h3>')
            out.append('<ul class="evidence">' + "".join(_evidence(project, ref) for ref in refs) + "</ul>"
                       if refs else '<p class="muted">None recorded.</p>')
        out.append(f'<p class="small">Asserted by {_esc(who)} at {_esc(_stamp(claim.get("at")))} &middot; '
                   f'<code>{_esc(claim["id"])}</code></p></article>')
    out.append("</section>")
    return "\n".join(out)


# ---- the page ------------------------------------------------------------------

def render(project: Project, *, terms_text: str, sample_memo_id: str | None = None,
           codebook_memo_id: str | None = None, title: str = "hermeneutic-engine") -> str:
    """The whole page as one string. It starts with <title> and has no <html>,
    <head> or <body> tags; a publisher wraps it."""
    report = verify(project)
    lenses = _model_lenses(project)
    several = len({_method(lens)["name"] for lens in lenses}) > 1

    sample_memo, sample, scope = None, None, None
    if sample_memo_id:
        drawn = [u for u in sampling.units_of(project, sample_memo_id) if u is not None]
        drawn.sort(key=lambda u: (_first_seen(u), u["id"]))
        sample_memo = project.get(sample_memo_id)
        sample = [(unit, project.unit_text(unit)) for unit in drawn]
        scope = {unit["id"] for unit, _ in sample}

    try:
        survey = names.survey(project)
    except (KeyError, TypeError):  # posts without the fields the survey reads
        survey = None
    if survey is not None and not survey["names"] and not survey["posts"]:
        survey = None
    days = _day_axis([_first_seen(u)[:10] for u in project.records("unit") if u.get("unit_kind") in PROSE]
                     + (list(survey["by_day"]) if survey else []))

    words_html, terms, default, unit_terms = _words(project, terms_text, sample, days)
    names_html, names_chart = _names(survey, days) if survey else ("", None)
    readers_html, readings_html, readings, read = "", "", None, defaultdict(set)
    if lenses:
        readers_html, read = _readers(project, lenses, scope, sample_memo, several, codebook_memo_id)
    if sample is not None and lenses:
        readings_html, readings = _readings(project, lenses, sample, sample_memo, read, several, unit_terms,
                                            codebook_memo_id)
        if not readings["lenses"]:
            readings_html, readings = "", None
    notes_html = _field_notes(project, lenses, codebook_memo_id, scope is not None)
    unfit_html = _unfit(project, lenses, codebook_memo_id) if codebook_memo_id else ""
    codebook_html, divergence_html = _codebook(project, codebook_memo_id) if codebook_memo_id else ("", "")
    claims_html = _claims(project)

    sections = [("sec-words", "Their words", words_html), ("sec-names", "Their names", names_html),
                ("sec-readers", "The readers", readers_html), ("sec-readings", "Readings side by side", readings_html),
                ("sec-notes", "Field notes", notes_html), ("sec-unfit", "Where the codebook had no place", unfit_html),
                ("sec-codebook", "The codebook", codebook_html),
                ("sec-divergence", "Where the readers differ", divergence_html), ("sec-claims", "Claims", claims_html)]
    present = [(anchor, label) for anchor, label, body in sections if body]
    walk = []
    if words_html and terms:
        walk.append('Start with a term under <a href="#sec-words">Their words</a>. Its first use is quoted with the '
                    'bytes it occupies in its source, its spread is counted by day and by name, and its uses are '
                    'listed in context.')
    if readings and terms:
        walk.append('From a term, open the sampled statements that use it to compare how each model reader coded '
                    'them.')
    if divergence_html:
        walk.append('<a href="#sec-divergence">Where the readers differ</a> lists the statements on which readers '
                    'who applied the same codebook disagree most.')

    data = {"days": days, "terms": terms, "lexDefault": default, "names": names_chart, "readings": readings}
    rendered = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    body = [_opening(project, report, title, present, walk), '<main>']
    body += [html_ for _, _, html_ in sections if html_]
    body += ['</main>',
             f'<footer class="colophon"><p><code>hermeneutic-engine</code> &middot; MIT licence &middot; '
             f'rendered {_esc(rendered)}</p></footer>']
    return "".join([
        f"<title>{_esc(title)}</title>\n",
        "<style>", _CSS, "</style>\n",
        '<div class="page">\n', "\n".join(body), "\n</div>\n",
        '<script type="application/json" id="he-data">', _script_json(data), "</script>\n",
        "<script>", _JS, "</script>\n",
    ])


# ---- command line ----------------------------------------------------------------

def cmd_page(args) -> int:
    terms_text = Path(args.terms).read_text(encoding="utf-8")
    with Project(args.project) as project:
        page = render(project, terms_text=terms_text, sample_memo_id=args.sample,
                      codebook_memo_id=args.codebook, title=args.title)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page, encoding="ascii")
    print(f"wrote {out} ({len(page):,} bytes)")
    return 0


def register_cli(sub) -> None:
    p = sub.add_parser("page", help="render the reader page: one self-contained HTML file")
    p.add_argument("project")
    p.add_argument("--terms", required=True, help="a file of lexicon terms, one per line")
    p.add_argument("--out", required=True, help="the HTML file to write")
    p.add_argument("--sample", default=None, help="ID of a sample memo: scopes the readers and adds readings side by side")
    p.add_argument("--codebook", default=None, help="ID of a codebook memo: adds the codebook and where readers differ")
    p.add_argument("--title", default="hermeneutic-engine")
    p.set_defaults(func=cmd_page)


# ---- style and script ----------------------------------------------------------------
# Both are ASCII. Characters outside it are written as CSS or JavaScript escapes.

_CSS = r"""
:root {
  /* Layout: prose keeps to a reading measure; the instruments (term table, concordance, readings) take the page.
     Type carries voice: serif for the writers' own words, sans for the apparatus, mono for provenance. */
  --paper: #f3f5f8;
  --surface: #fcfcfd;
  --ink: #151b25;
  --ink-2: #475265;
  --ink-3: #5f6b7d;
  --rule: #d3d9e2;
  --hair: #e4e8ee;
  --accent: #2346b5;
  --accent-wash: #e4e9f8;
  --series-1: #2f57cc;
  --series-0: #a9b3c3;
  --mark-1: #fff0b0;
  --mark-2: #ffdc6e;
  --mark-3: #ffc533;
  --mark-4: #f2a516;
  --ok: #17784b;
  --ok-wash: #e2f2e8;
  --bad: #b3261e;
  --bad-wash: #fbe8e6;
  --font-sans: system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
  --font-serif: "Iowan Old Style", "Palatino Linotype", Palatino, "Book Antiqua", Georgia, "Times New Roman", serif;
  --font-mono: ui-monospace, "SF Mono", SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace;
  --step--1: 0.8125rem;
  --step-0: 1rem;
  --step-1: 1.1875rem;
  --step-2: 1.5rem;
  --step-3: 2.375rem;
  --measure: 68ch;
  --page: 78rem;
  --radius: 6px;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    color-scheme: dark;
    --paper: #0f1318;
    --surface: #151a21;
    --ink: #e7eaf0;
    --ink-2: #aab3c1;
    --ink-3: #8c97a7;
    --rule: #2b333e;
    --hair: #212831;
    --accent: #91a8ff;
    --accent-wash: #1c2541;
    --series-1: #7c9bf5;
    --series-0: #4f5a6c;
    --mark-1: #302d1b;
    --mark-2: #463f1b;
    --mark-3: #5c5019;
    --mark-4: #726217;
    --ok: #55c792;
    --ok-wash: #11291d;
    --bad: #ff8d84;
    --bad-wash: #3a1614;
  }
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --paper: #0f1318;
  --surface: #151a21;
  --ink: #e7eaf0;
  --ink-2: #aab3c1;
  --ink-3: #8c97a7;
  --rule: #2b333e;
  --hair: #212831;
  --accent: #91a8ff;
  --accent-wash: #1c2541;
  --series-1: #7c9bf5;
  --series-0: #4f5a6c;
  --mark-1: #302d1b;
  --mark-2: #463f1b;
  --mark-3: #5c5019;
  --mark-4: #726217;
  --ok: #55c792;
  --ok-wash: #11291d;
  --bad: #ff8d84;
  --bad-wash: #3a1614;
}

*, *::before, *::after { box-sizing: border-box; }
[hidden] { display: none !important; }
html { -webkit-text-size-adjust: 100%; scroll-behavior: smooth; }
body { margin: 0; background: var(--paper); color: var(--ink); font-family: var(--font-sans);
  font-size: var(--step-0); line-height: 1.55; }
@media (prefers-reduced-motion: reduce) { html { scroll-behavior: auto; } }
.page { max-width: var(--page); margin-inline: auto; padding-inline: clamp(16px, 4vw, 40px);
  padding-block: 2.75rem 2.5rem; }
h1, h2, h3, h4 { margin: 0; line-height: 1.2; text-wrap: balance; letter-spacing: -0.01em; }
h1 { font-family: var(--font-mono); font-size: var(--step-3); font-weight: 600; letter-spacing: -0.035em; }
h2 { font-size: var(--step-2); font-weight: 650; }
h3 { font-size: var(--step-1); font-weight: 650; }
h4 { font-size: var(--step-0); font-weight: 650; }
p, ul, ol, dl, figure, blockquote, fieldset { margin: 0; }
a { color: var(--accent); text-underline-offset: 2px; }
code, .mono { font-family: var(--font-mono); font-size: 0.86em; }
:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.sr-only { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden;
  clip: rect(0 0 0 0); white-space: nowrap; border: 0; }
.small { font-size: var(--step--1); color: var(--ink-2); line-height: 1.5; }
.muted { color: var(--ink-3); }
.src { font-family: var(--font-serif); }
.name { font-family: var(--font-serif); }
.term-inline { font-family: var(--font-serif); font-weight: 600; }
.chip { display: inline-block; font-size: 0.75rem; line-height: 1.5; padding: 0 0.45rem; border-radius: 999px;
  border: 1px solid var(--rule); color: var(--ink-2); background: var(--surface); white-space: nowrap;
  vertical-align: 0.08em; font-family: var(--font-sans); font-weight: 500; }
.chip-vivo { border-color: var(--mark-4); background: var(--mark-1); color: var(--ink); }
mark { background: var(--mark-2); color: inherit; border-radius: 2px; padding-inline: 0.04em; }
.btn-link { background: none; border: 0; padding: 0; font: inherit; font-size: var(--step--1);
  color: var(--accent); text-decoration: underline; text-underline-offset: 2px; cursor: pointer; }
.btn { font: inherit; font-size: var(--step--1); color: var(--accent); background: var(--surface);
  border: 1px solid var(--rule); border-radius: 4px; padding: 0.35rem 0.7rem; cursor: pointer; text-align: left; }
.btn:hover { border-color: var(--accent); }
.panel { background: var(--surface); border: 1px solid var(--rule); border-radius: var(--radius); }
/* A scroll box is also the containing block, so nothing positioned inside it (a visually hidden label)
   can escape the clip and widen the page. */
.scroll { position: relative; overflow: auto; background: var(--surface); border: 1px solid var(--rule);
  border-radius: var(--radius); }
.table-wrap { position: relative; overflow-x: auto; }
.rd-list { position: relative; }

/* opening */
.opening { display: flex; flex-direction: column; gap: 1.1rem; }
.eyebrow { font-size: var(--step--1); color: var(--ink-2); }
.lede { font-size: var(--step-1); line-height: 1.45; max-width: 58ch; }
.facts { display: grid; grid-template-columns: repeat(auto-fit, minmax(10rem, 1fr)); gap: 1rem 1.75rem;
  padding-block: 1.1rem; border-block: 1px solid var(--rule); margin-top: 0.4rem; }
.fact { display: flex; flex-direction: column-reverse; justify-content: flex-end; gap: 0.15rem; }
.fact dd { margin: 0; font-size: var(--step-2); font-weight: 650; letter-spacing: -0.01em; line-height: 1.15; }
.fact dt { font-size: var(--step--1); color: var(--ink-2); line-height: 1.35; }
.seal { display: flex; flex-direction: column; gap: 0.55rem; padding: 1rem 1.25rem; max-width: 62rem;
  background: var(--surface); border: 1px solid var(--rule); border-radius: var(--radius); }
.seal-status { display: flex; align-items: center; gap: 0.45rem; font-weight: 650; font-family: var(--font-mono); }
.seal-status svg { width: 1.1rem; height: 1.1rem; flex: none; }
.is-ok { color: var(--ok); }
.is-bad { color: var(--bad); }
.problems { padding-left: 1.2rem; font-size: var(--step--1); }
.digest-line { display: flex; flex-wrap: wrap; align-items: baseline; gap: 0.25rem 0.6rem; }
.digest { color: var(--ink); }
.digest-full code { word-break: break-all; font-size: var(--step--1); }
.walk-note { max-width: var(--measure); color: var(--ink-2); }
.toc { display: flex; flex-wrap: wrap; gap: 0.35rem 1.25rem; font-size: var(--step--1); }
.toc a { text-decoration: none; border-bottom: 1px solid var(--rule); padding-bottom: 1px; }
.toc a:hover { border-bottom-color: var(--accent); }

/* sections */
main { display: flex; flex-direction: column; }
.section { display: flex; flex-direction: column; gap: 1.1rem; padding-top: 3.75rem; }
.dek { color: var(--ink-2); max-width: var(--measure); }
.data { width: 100%; border-collapse: collapse; font-size: var(--step--1); font-variant-numeric: tabular-nums; }
.data th, .data td { text-align: left; vertical-align: top; padding: 0.45rem 0.65rem;
  border-bottom: 1px solid var(--hair); }
.data thead th { font-weight: 600; color: var(--ink-2); border-bottom-color: var(--rule); white-space: nowrap; }
.data .num, .lex-table .num { text-align: right; white-space: nowrap; }
.notes { padding-left: 1.2rem; display: flex; flex-direction: column; gap: 0.35rem; max-width: var(--measure); }

/* their words */
.lex { display: grid; grid-template-columns: minmax(0, 35rem) minmax(0, 1fr); gap: 1.5rem; align-items: start; }
.lex-side { display: flex; flex-direction: column; gap: 0.6rem; }
.lex-wrap { max-height: 37rem; }
.lex-table { width: 100%; border-collapse: collapse; font-size: var(--step--1); font-variant-numeric: tabular-nums; }
.lex-table th, .lex-table td { padding: 0.3rem 0.55rem; border-bottom: 1px solid var(--hair); text-align: left;
  vertical-align: middle; }
.lex-table thead th { position: sticky; top: 0; z-index: 1; background: var(--surface); color: var(--ink-2);
  font-weight: 600; box-shadow: inset 0 -1px 0 var(--rule); white-space: nowrap; }
.lex-table tbody tr { cursor: pointer; }
.lex-table tbody tr:hover { background: var(--paper); }
.lex-table tbody tr.is-selected { background: var(--accent-wash); }
.lex-table .when { white-space: nowrap; color: var(--ink-2); }
.term-btn { font-family: var(--font-serif); font-size: 0.95rem; font-weight: 600; color: var(--ink);
  background: none; border: 0; padding: 0; cursor: pointer; text-align: left; }
tr.is-selected .term-btn { color: var(--accent); }
.sort { font: inherit; color: inherit; background: none; border: 0; padding: 0; cursor: pointer; }
th[aria-sort="ascending"] .sort::after { content: " \2191"; }
th[aria-sort="descending"] .sort::after { content: " \2193"; }
th[aria-sort] .sort { color: var(--ink); }
.spark-col { width: 1%; }
.spark svg { display: block; }
.lex-detail { padding: 1.25rem; display: flex; flex-direction: column; gap: 1rem; min-width: 0; }
.td-term { font-family: var(--font-serif); font-size: var(--step-2); font-weight: 600; letter-spacing: 0; }
.td-pattern { font-size: var(--step--1); color: var(--ink-2); overflow-wrap: anywhere; }
.td-counts { display: grid; grid-template-columns: repeat(auto-fit, minmax(6.5rem, 1fr)); gap: 0.6rem 1rem; }
.td-counts div { display: flex; flex-direction: column-reverse; justify-content: flex-end; }
.td-counts dd { margin: 0; font-size: var(--step-1); font-weight: 650; }
.td-counts dt { font-size: var(--step--1); color: var(--ink-2); }
.first-use { display: flex; flex-direction: column; gap: 0.5rem; }
.first-use figcaption { font-size: var(--step--1); color: var(--ink-2); }
.first-quote { font-size: 1.125rem; line-height: 1.55; overflow-wrap: anywhere; }
.prov { font-size: var(--step--1); color: var(--ink-2); overflow-wrap: anywhere; }
.walk { align-self: flex-start; }
.lex-conc { display: flex; flex-direction: column; gap: 0.6rem; }
.conc-wrap { max-height: 30rem; }
.kwic { border-collapse: collapse; width: 100%; font-size: 0.9rem; }
.kwic td, .kwic th { padding: 0.28rem 0.45rem; border-bottom: 1px solid var(--hair); white-space: nowrap;
  vertical-align: baseline; }
.kwic thead th { position: sticky; top: 0; z-index: 1; background: var(--surface); text-align: left;
  font-size: var(--step--1); color: var(--ink-2); font-weight: 600; box-shadow: inset 0 -1px 0 var(--rule); }
.kwic .when { font-size: var(--step--1); color: var(--ink-3); font-variant-numeric: tabular-nums; }
.kwic .who { font-family: var(--font-serif); font-size: var(--step--1); color: var(--ink-2); }
.kwic .left { font-family: var(--font-serif); text-align: right; }
.kwic .right { font-family: var(--font-serif); text-align: left; }
.kwic .kw { font-family: var(--font-serif); font-weight: 650; text-align: center; padding-inline: 0.15rem; }
.absent { display: flex; flex-direction: column; gap: 0.5rem; max-width: var(--measure); }
.absent-list { list-style: none; padding: 0; display: flex; flex-wrap: wrap; gap: 0.35rem 1.1rem;
  font-family: var(--font-serif); font-weight: 600; }
.patterns summary { cursor: pointer; font-size: var(--step--1); color: var(--accent); }
.patterns ul { list-style: none; padding: 0; margin-top: 0.4rem; display: flex; flex-direction: column;
  gap: 0.2rem; font-size: var(--step--1); }
.patterns code { color: var(--ink-2); overflow-wrap: anywhere; }

/* charts */
.chart-fig { display: flex; flex-direction: column; gap: 0.4rem; min-width: 0; }
.chart-cap { display: flex; flex-wrap: wrap; align-items: baseline; gap: 0.25rem 1rem; font-size: var(--step--1);
  color: var(--ink-2); }
.chart-title { font-weight: 600; color: var(--ink); }
.legend { white-space: nowrap; }
.swatch { display: inline-block; width: 0.7rem; height: 0.7rem; border-radius: 2px; margin-right: 0.35rem;
  vertical-align: -0.05em; }
.swatch.s1 { background: var(--series-1); }
.swatch.s0 { background: var(--series-0); }
.chart { position: relative; min-height: 8rem; }
.chart svg { display: block; overflow: visible; }
svg .grid { stroke: var(--hair); stroke-width: 1; }
svg .axis { stroke: var(--rule); stroke-width: 1; }
svg .tick { fill: var(--ink-3); font-family: var(--font-sans); font-size: 11px; font-variant-numeric: tabular-nums; }
svg .peak { fill: var(--ink-2); font-family: var(--font-sans); font-size: 11px; font-weight: 600; }
svg .bar, svg .grid, svg .axis, svg .tick, svg .peak { pointer-events: none; }
svg .bar.s1 { fill: var(--series-1); }
svg .bar.s0 { fill: var(--series-0); }
svg .hit { fill: transparent; pointer-events: all; }
svg .hit.on { fill: var(--accent-wash); }
.tip { position: absolute; left: 0; top: 0; z-index: 3; pointer-events: none; background: var(--ink);
  color: var(--paper); font-size: var(--step--1); line-height: 1.4; padding: 0.35rem 0.55rem; border-radius: 4px;
  white-space: nowrap; }
.tip strong { font-weight: 650; }
.tip .tip-day { opacity: 0.8; }
.chart-table summary { cursor: pointer; font-size: var(--step--1); color: var(--accent); }
.chart-table table { margin-top: 0.4rem; width: auto; }

/* their names */
.names-grid { display: grid; grid-template-columns: minmax(0, 1.2fr) minmax(0, 1fr); gap: 1.5rem; align-items: start; }
.names-wrap { max-height: 24rem; }
.names-wrap thead th { position: sticky; top: 0; background: var(--surface); }
.names-wrap tbody th { white-space: nowrap; font-weight: 500; }
.two-up { display: grid; grid-template-columns: repeat(auto-fit, minmax(18rem, 1fr)); gap: 1.5rem; }
.two-up > div { display: flex; flex-direction: column; gap: 0.4rem; }
.namelist { list-style: none; padding: 0; display: flex; flex-wrap: wrap; gap: 0.15rem 0.85rem;
  font-family: var(--font-serif); font-size: 0.9rem; line-height: 1.5; }
.namelist li { overflow-wrap: anywhere; }
.findings { display: flex; flex-direction: column; gap: 0.6rem; max-width: var(--measure); }

/* the readers */
.readers .model { font-size: 0.82rem; color: var(--ink); }
.readers .sub { display: block; font-size: 0.75rem; color: var(--ink-2); font-weight: 400; }
.priors-row td { background: var(--paper); }
.priors { display: flex; flex-direction: column; gap: 0.35rem; max-width: var(--measure); }
.priors ul { padding-left: 1.2rem; }

/* readings side by side */
.rd-lenses { border: 0; padding: 0; display: flex; flex-wrap: wrap; gap: 0.4rem; }
.rd-lenses legend { font-size: var(--step--1); color: var(--ink-2); padding: 0; margin-bottom: 0.35rem; }
.lens-toggle { display: inline-flex; align-items: center; gap: 0.35rem; font-size: 0.78rem; color: var(--ink-2);
  border: 1px solid var(--rule); border-radius: 999px; padding: 0.15rem 0.6rem 0.15rem 0.4rem;
  background: var(--surface); cursor: pointer; }
.lens-toggle code { color: var(--ink); font-size: 0.78rem; }
.lens-toggle input { margin: 0; accent-color: var(--accent); }
.rd { display: grid; grid-template-columns: minmax(0, 22rem) minmax(0, 1fr); gap: 1.5rem; align-items: start; }
.rd-side { display: flex; flex-direction: column; gap: 0.5rem; }
.rd-label { font-size: var(--step--1); font-weight: 600; }
#rd-filter { font: inherit; font-size: 0.95rem; color: var(--ink); background: var(--surface); width: 100%;
  border: 1px solid var(--rule); border-radius: 4px; padding: 0.45rem 0.6rem; }
#rd-filter:focus { border-color: var(--accent); }
.rd-term { font-size: var(--step--1); background: var(--accent-wash); border-radius: 4px; padding: 0.4rem 0.6rem; }
.rd-list { list-style: none; padding: 0; max-height: 40rem; overflow-y: auto; border: 1px solid var(--rule);
  border-radius: var(--radius); background: var(--surface); }
.rd-list li + li { border-top: 1px solid var(--hair); }
.rd-item { display: flex; flex-direction: column; gap: 0.2rem; width: 100%; text-align: left; font: inherit;
  color: var(--ink); background: none; border: 0; padding: 0.55rem 0.75rem; cursor: pointer; }
.rd-item:hover { background: var(--paper); }
.rd-item[aria-pressed="true"] { background: var(--accent-wash); }
.rd-meta { font-size: 0.75rem; color: var(--ink-2); font-variant-numeric: tabular-nums; }
.rd-excerpt { font-family: var(--font-serif); font-size: 0.9rem; line-height: 1.4; display: -webkit-box;
  -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; overflow-wrap: anywhere; }
.rd-empty { padding: 0.75rem; font-size: var(--step--1); color: var(--ink-2); }
.rd-detail { padding: 1.25rem; display: flex; flex-direction: column; gap: 1rem; min-width: 0; }
.ud-head { display: flex; flex-direction: column; gap: 0.25rem; font-size: var(--step--1); color: var(--ink-2); }
.ud-text { font-size: 1.0625rem; line-height: 1.65; white-space: pre-wrap; overflow-wrap: anywhere;
  padding: 0.85rem 1rem; background: var(--paper); border-radius: 4px; }
.q { padding: 0.08em 0; border-radius: 2px; }
.q1 { background: var(--mark-1); }
.q2 { background: var(--mark-2); }
.q3 { background: var(--mark-3); }
.q4 { background: var(--mark-4); }
.q.on { outline: 2px solid var(--accent); outline-offset: 0; }
.shade-key { display: flex; flex-wrap: wrap; align-items: center; gap: 0.3rem 0.8rem; font-size: var(--step--1);
  color: var(--ink-2); }
.shade-key .q { padding: 0 0.4rem; color: var(--ink); }
.lens-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(17rem, 1fr)); gap: 0.9rem; }
.lens-block { display: flex; flex-direction: column; gap: 0.55rem; padding: 0.8rem 0.9rem;
  border: 1px solid var(--hair); border-radius: 4px; min-width: 0; }
.lens-name { display: flex; flex-wrap: wrap; align-items: baseline; gap: 0.15rem 0.5rem; font-size: 0.8rem; }
.lens-name code { font-size: 0.82rem; }
.lens-name .quals { font-weight: 400; color: var(--ink-2); font-size: 0.75rem; }
.codings { list-style: none; padding: 0; display: flex; flex-direction: column; gap: 0.7rem; }
.coding { display: flex; flex-direction: column; gap: 0.2rem; border-radius: 3px; outline-offset: 3px; }
.coding:hover .code-name, .coding:focus .code-name { color: var(--accent); }
.code-name { font-weight: 650; }
.code-quote { font-size: 0.95rem; line-height: 1.45; overflow-wrap: anywhere; }
.code-def, .code-note { font-size: var(--step--1); color: var(--ink-2); overflow-wrap: anywhere; }
.code-note .label { font-weight: 600; color: var(--ink-2); }
.missed { font-size: var(--step--1); color: var(--bad); }
.missed ul { padding-left: 1.1rem; color: var(--ink-2); }
.unit-terms { display: flex; flex-wrap: wrap; align-items: baseline; gap: 0.35rem; font-size: var(--step--1);
  color: var(--ink-2); }
.unit-terms .btn { font-family: var(--font-serif); padding: 0.1rem 0.55rem; }

/* codebook and divergence */
.codes { padding-left: 1.4rem; display: flex; flex-direction: column; gap: 1.1rem; max-width: 60rem; }
.code h3 { font-size: var(--step-0); display: flex; flex-wrap: wrap; align-items: baseline; gap: 0.25rem 0.5rem; }
.code dl { display: grid; grid-template-columns: max-content minmax(0, 1fr); gap: 0.2rem 0.9rem;
  font-size: 0.9rem; margin-block: 0.4rem; }
.code dt { color: var(--ink-2); }
.code dd { margin: 0; }
.code-key { font-size: 0.8rem; color: var(--ink-2); }
.div-list { list-style: none; padding: 0; display: flex; flex-direction: column; gap: 0.6rem; }
.div-list details { border: 1px solid var(--rule); border-radius: var(--radius); background: var(--surface);
  padding: 0.75rem 1rem; }
.div-list details[open] { display: flex; flex-direction: column; gap: 0.6rem; }
.div-list summary { cursor: pointer; }
.unit-quote { white-space: pre-wrap; overflow-wrap: anywhere; background: var(--paper); padding: 0.75rem 0.9rem;
  border-radius: 4px; line-height: 1.6; }
.applied { list-style: none; padding: 0; display: flex; flex-direction: column; gap: 0.6rem; }
.said { padding-left: 1.1rem; font-size: 0.9rem; }
.reader-name { font-family: var(--font-mono); font-size: 0.8rem; }
.note { color: var(--ink-2); font-size: var(--step--1); }

/* claims */
.claim { padding: 1.1rem 1.25rem; display: flex; flex-direction: column; gap: 0.55rem; max-width: 62rem; }
.claim-text { font-size: var(--step-1); line-height: 1.4; }
.evidence { list-style: none; padding: 0; display: flex; flex-direction: column; gap: 0.6rem; }
.ev blockquote { white-space: pre-wrap; overflow-wrap: anywhere; }

/* field notes, and where the codebook had no place */
.lens-label code { font-size: 0.82rem; color: var(--ink); }
.lens-label .quals { font-size: 0.75rem; color: var(--ink-2); font-weight: 400; }
.fn-lenses { display: flex; flex-direction: column; gap: 0.6rem; max-width: 62rem; }
.fn-lens { border: 1px solid var(--rule); border-radius: var(--radius); background: var(--surface);
  padding: 0.75rem 1rem; }
.fn-lens summary { cursor: pointer; }
.fn-lens summary .muted { font-size: var(--step--1); }
.fn-list { list-style: none; padding: 0; margin-top: 0.9rem; display: flex; flex-direction: column; gap: 1.25rem; }
.fn { display: flex; flex-direction: column; gap: 0.4rem; padding-left: 0.9rem; border-left: 2px solid var(--rule);
  max-width: var(--measure); }
.fn-body { white-space: pre-wrap; overflow-wrap: anywhere; line-height: 1.6; }
.fn-sig { color: var(--ink); font-weight: 600; white-space: pre-wrap; }
.unfit-list { list-style: none; padding: 0; display: flex; flex-direction: column; gap: 0.6rem; max-width: 62rem; }
.unfit { padding: 0.85rem 1rem; display: flex; flex-direction: column; gap: 0.5rem; }
.unfit-head { display: flex; flex-wrap: wrap; align-items: baseline; gap: 0.15rem 0.75rem; color: var(--ink-2); }
.unfit-head strong { color: var(--ink); font-weight: 650; }
.unfit-head .muted { font-size: var(--step--1); }
.unfit-said { list-style: none; padding: 0 0 0 0.9rem; margin-top: 0.5rem; border-left: 2px solid var(--rule);
  display: flex; flex-direction: column; gap: 0.6rem; font-size: 0.9rem; }
.unfit-said li { display: flex; flex-direction: column; gap: 0.15rem; }
.unfit-why { white-space: pre-wrap; overflow-wrap: anywhere; }

.colophon { margin-top: 4.5rem; padding-top: 1rem; border-top: 1px solid var(--rule); font-size: var(--step--1);
  color: var(--ink-3); }

@media (max-width: 960px) {
  .lex, .rd, .names-grid { grid-template-columns: minmax(0, 1fr); }
  .lex-wrap { max-height: 24rem; }
  .rd-list { max-height: 22rem; }
}
@media (max-width: 520px) {
  .facts { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .lex-detail, .rd-detail { padding: 1rem; }
  .code dl { grid-template-columns: minmax(0, 1fr); }
}
"""

_JS = r"""
(function () {
  "use strict";
  var holder = document.getElementById("he-data");
  if (!holder) { return; }
  var D = JSON.parse(holder.textContent);
  var T = D.terms || [];
  var R = D.readings || null;
  var SVGNS = "http://www.w3.org/2000/svg";
  var MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  var reduceMotion = !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
  var narrow = window.matchMedia ? window.matchMedia("(max-width: 960px)") : { matches: false };
  var useTerm = null;

  // ---- building nodes. Text from the data only ever becomes a text node. ----
  function setAttrs(node, attrs) {
    if (!attrs) { return; }
    Object.keys(attrs).forEach(function (key) {
      var value = attrs[key];
      if (value === null || value === undefined || value === false) { return; }
      if (key === "text") { node.textContent = String(value); }
      else { node.setAttribute(key, value === true ? "" : String(value)); }
    });
  }
  function add(node, kids) {
    if (kids === null || kids === undefined || kids === false || kids === "") { return node; }
    if (Array.isArray(kids)) { kids.forEach(function (kid) { add(node, kid); }); return node; }
    node.appendChild(typeof kids === "object" ? kids : document.createTextNode(String(kids)));
    return node;
  }
  function h(tag, attrs, kids) { var node = document.createElement(tag); setAttrs(node, attrs); return add(node, kids); }
  function sv(tag, attrs) { var node = document.createElementNS(SVGNS, tag); setAttrs(node, attrs); return node; }
  function clear(node) { while (node.firstChild) { node.removeChild(node.firstChild); } }
  function fmt(n) { return Number(n || 0).toLocaleString("en-US"); }
  function pct(part, whole) { return whole ? Math.round(100 * part / whole) + "%" : "0%"; }
  function dayLabel(d) { return d ? MONTHS[Number(d.slice(5, 7)) - 1] + " " + Number(d.slice(8, 10)) : ""; }
  function stamp(t) { return t ? t.replace("T", " ").replace(/Z$/, "") + " UTC" : "time not recorded"; }
  function minute(t) { return t ? t.slice(0, 16).replace("T", " ") : ""; }
  function r2(v) { return Math.round(v * 100) / 100; }
  function plural(n, word, many) { return fmt(n) + " " + (n === 1 ? word : (many || word + "s")); }
  function goTo(node) { if (node) { node.scrollIntoView({ behavior: reduceMotion ? "auto" : "smooth", block: "start" }); } }
  function quoted(text) { return "\u201c" + text + "\u201d"; }
  function termText(t) { return t.quote ? quoted(t.term) : t.term; }

  // ---- show and hide ----
  Array.prototype.forEach.call(document.querySelectorAll("[data-toggle]"), function (button) {
    var target = document.getElementById(button.getAttribute("aria-controls"));
    if (!target) { return; }
    button.addEventListener("click", function () {
      var open = button.getAttribute("aria-expanded") === "true";
      button.setAttribute("aria-expanded", open ? "false" : "true");
      target.hidden = open;
      var label = button.getAttribute(open ? "data-closed" : "data-open");
      if (label) { button.textContent = label; }
    });
  });

  // ---- charts: columns per day on one axis, a tooltip per column ----
  function ticksFor(max) {
    if (max <= 0) { return [0, 1]; }
    var raw = max / 4, mag = Math.pow(10, Math.floor(Math.log(raw) / Math.LN10)), step = 10 * mag;
    [1, 2, 5, 10].some(function (m) { if (m * mag >= raw) { step = m * mag; return true; } return false; });
    step = Math.max(1, step);
    var top = Math.ceil(max / step) * step, out = [];
    for (var v = 0; v <= top + step / 2; v += step) { out.push(v); }
    return out;
  }
  function barPath(x, w, top, bottom, radius) {
    var r = Math.max(0, Math.min(radius, w / 2, bottom - top));
    return "M" + r2(x) + "," + r2(bottom) + "V" + r2(top + r) + "Q" + r2(x) + "," + r2(top) + " " + r2(x + r) + "," +
      r2(top) + "H" + r2(x + w - r) + "Q" + r2(x + w) + "," + r2(top) + " " + r2(x + w) + "," + r2(top + r) +
      "V" + r2(bottom) + "Z";
  }
  function columnChart(box, spec) {
    clear(box);
    var days = spec.days, n = days.length;
    if (!n) { return; }
    var width = Math.max(260, Math.floor(box.getBoundingClientRect().width) || 600);
    var height = spec.height || 180;
    var m = { top: 22, right: 12, bottom: 26, left: 44 };
    var iw = width - m.left - m.right, ih = height - m.top - m.bottom;
    var totals = days.map(function (_, i) {
      return spec.series.reduce(function (sum, se) { return sum + (se.values[i] || 0); }, 0);
    });
    var max = Math.max.apply(null, totals.concat([0]));
    var ticks = ticksFor(max), top = ticks[ticks.length - 1];
    function y(v) { return m.top + ih - (v / top) * ih; }
    var band = iw / n, bw = Math.max(1, Math.min(24, band - 2));
    var svg = sv("svg", { width: width, height: height, viewBox: "0 0 " + width + " " + height, role: "img",
      "aria-label": spec.label });
    var tip = h("div", { "class": "tip", hidden: true });
    var hits = [];
    days.forEach(function (_, i) {
      var hit = sv("rect", { x: r2(m.left + band * i), y: m.top, width: r2(band), height: ih, "class": "hit" });
      hit.addEventListener("pointerenter", function () { show(i); });
      hit.addEventListener("pointerleave", hide);
      svg.appendChild(hit);
      hits.push(hit);
    });
    ticks.forEach(function (t) {
      var yy = Math.round(y(t)) + 0.5;
      svg.appendChild(sv("line", { x1: m.left, x2: width - m.right, y1: yy, y2: yy, "class": t === 0 ? "axis" : "grid" }));
      svg.appendChild(sv("text", { x: m.left - 8, y: yy + 4, "text-anchor": "end", "class": "tick", text: fmt(t) }));
    });
    var peak = 0;
    days.forEach(function (_, i) {
      if (totals[i] > totals[peak]) { peak = i; }
      var x = m.left + band * i + (band - bw) / 2, base = 0, last = -1;
      spec.series.forEach(function (se, k) { if (se.values[i]) { last = k; } });
      spec.series.forEach(function (se, k) {
        var v = se.values[i] || 0;
        if (!v) { return; }
        var bottom = y(base) - (base > 0 ? 2 : 0), upper = y(base + v);
        if (bottom - upper < 1) { upper = bottom - 1; }
        svg.appendChild(sv("path", { d: barPath(x, bw, upper, bottom, k === last ? 4 : 0), "class": "bar " + se.cls }));
        base += v;
      });
    });
    var placed = [];
    function label(i) {
      var text = dayLabel(days[i]), cx = m.left + band * (i + 0.5), est = text.length * 6.5;
      var anchor = i === 0 ? "start" : (i === n - 1 ? "end" : "middle");
      var at = anchor === "start" ? m.left : (anchor === "end" ? width - m.right : cx);
      var lo = anchor === "start" ? at : (anchor === "end" ? at - est : at - est / 2), hi = lo + est;
      if (placed.some(function (p) { return lo < p[1] + 12 && hi > p[0] - 12; })) { return; }
      placed.push([lo, hi]);
      svg.appendChild(sv("text", { x: r2(at), y: height - 8, "text-anchor": anchor, "class": "tick", text: text }));
    }
    label(0);
    if (n > 1) { label(n - 1); }
    days.forEach(function (d, i) { if (d.slice(8, 10) === "01") { label(i); } });
    if (max > 0) {
      var px = m.left + band * (peak + 0.5);
      var pa = px > width - 40 ? "end" : (px < m.left + 20 ? "start" : "middle");
      svg.appendChild(sv("text", { x: r2(px), y: r2(y(max) - 6), "text-anchor": pa, "class": "peak",
        text: fmt(max) + " on " + dayLabel(days[peak]) }));
    }
    function show(i) {
      hits.forEach(function (hit, j) { hit.setAttribute("class", j === i ? "hit on" : "hit"); });
      clear(tip);
      add(tip, h("div", { "class": "tip-day" }, days[i]));
      if (spec.series.length > 1) { add(tip, h("div", null, [h("strong", null, fmt(totals[i])), " " + spec.total])); }
      spec.series.forEach(function (se) {
        add(tip, h("div", null, [spec.series.length > 1 ? h("span", { "class": "swatch " + se.cls }) : null,
          h("strong", null, fmt(se.values[i] || 0)), " " + se.label]));
      });
      tip.hidden = false;
      var cx = m.left + band * (i + 0.5), tw = tip.offsetWidth, th = tip.offsetHeight;
      tip.style.left = Math.max(0, Math.min(width - tw, cx - tw / 2)) + "px";
      tip.style.top = Math.max(0, y(totals[i]) - th - 10) + "px";
    }
    function hide() { tip.hidden = true; hits.forEach(function (hit) { hit.setAttribute("class", "hit"); }); }
    box.appendChild(svg);
    box.appendChild(tip);
  }
  function sparkline(cell, byDay) {
    var n = (D.days || []).length;
    if (!n) { return; }
    var w = 96, ht = 20, band = w / n, bw = Math.max(1, band - 1);
    var values = D.days.map(function (d) { return byDay[d] || 0; });
    var max = Math.max.apply(null, values.concat([1]));
    var svg = sv("svg", { width: w, height: ht, viewBox: "0 0 " + w + " " + ht, "aria-hidden": "true", focusable: "false" });
    svg.appendChild(sv("line", { x1: 0, x2: w, y1: ht - 0.5, y2: ht - 0.5, "class": "axis" }));
    values.forEach(function (v, i) {
      if (!v) { return; }
      var bh = Math.max(1.5, (v / max) * (ht - 2));
      svg.appendChild(sv("rect", { x: r2(i * band + (band - bw) / 2), y: r2(ht - 1 - bh), width: r2(bw), height: r2(bh),
        "class": "bar s1" }));
    });
    cell.appendChild(svg);
  }
  function dayTable(byDay, label) {
    var rows = Object.keys(byDay).sort().map(function (d) {
      return h("tr", null, [h("th", { scope: "row" }, d), h("td", { "class": "num" }, fmt(byDay[d]))]);
    });
    return h("details", { "class": "chart-table" }, [h("summary", null, "Days as a table"),
      h("table", { "class": "data" }, [h("thead", null, h("tr", null, [h("th", { scope: "col" }, "Day"),
        h("th", { scope: "col", "class": "num" }, label)])), h("tbody", null, rows)])]);
  }

  // ---- their words ----
  var lexTable = document.getElementById("lex-table");
  var lexWrap = document.getElementById("lex-wrap");
  var lexDetail = document.getElementById("lex-detail");
  var lexConc = document.getElementById("lex-conc");
  var lexRows = lexTable ? Array.prototype.slice.call(lexTable.tBodies[0].rows) : [];
  var lex = { selected: -1, key: "first", dir: 1, chart: null };
  var SORTS = {
    first: function (row) { return row.getAttribute("data-first"); },
    units: function (row) { return Number(row.getAttribute("data-units")); },
    names: function (row) { return Number(row.getAttribute("data-names")); }
  };
  function rowOf(i) { return lexRows.filter(function (row) { return Number(row.getAttribute("data-i")) === i; })[0]; }
  function sortRows() {
    var get = SORTS[lex.key], body = lexTable.tBodies[0];
    lexRows.slice().sort(function (a, b) {
      var x = get(a), z = get(b), c = x < z ? -1 : (x > z ? 1 : 0);
      return c ? c * lex.dir : Number(a.getAttribute("data-i")) - Number(b.getAttribute("data-i"));
    }).forEach(function (row) { body.appendChild(row); });
    Array.prototype.forEach.call(lexTable.tHead.querySelectorAll("th[data-sort]"), function (th) {
      if (th.getAttribute("data-sort") === lex.key) { th.setAttribute("aria-sort", lex.dir > 0 ? "ascending" : "descending"); }
      else { th.removeAttribute("aria-sort"); }
    });
  }
  function revealRow(i) {
    var row = rowOf(i);
    if (!row || !lexWrap) { return; }
    var box = lexWrap.getBoundingClientRect(), at = row.getBoundingClientRect();
    var head = lexTable.tHead ? lexTable.tHead.getBoundingClientRect().height : 0;
    if (at.top < box.top + head || at.bottom > box.bottom) {
      lexWrap.scrollTop += at.top - box.top - head - lexWrap.clientHeight / 3;
    }
  }
  function stat(value, label) { return h("div", null, [h("dt", null, label), h("dd", null, value)]); }
  function renderTerm(i) {
    var t = T[i], f = t.first;
    clear(lexDetail);
    add(lexDetail, h("div", null, [h("h3", { "class": "td-term" }, termText(t)),
      h("p", { "class": "td-pattern" }, ["Counted with the pattern ", h("code", null, t.pattern)])]));
    add(lexDetail, h("dl", { "class": "td-counts" }, [stat(fmt(t.units), "statements"), stat(fmt(t.uses), "uses"),
      stat(fmt(t.names), "names"), stat(fmt(t.pages), "pages")]));
    var who = f.signature ? ["signed ", h("span", { "class": "name" }, f.signature)] : ["unsigned"];
    if (f.saved_as && f.saved_as !== f.signature) { who.push(", saved as ", h("span", { "class": "name" }, f.saved_as)); }
    add(lexDetail, h("figure", { "class": "first-use" }, [
      h("figcaption", null, [h("strong", null, "First use"), ", " + stamp(f.time) + ", ", who, ", on page ",
        h("span", { "class": "name" }, f.page || "unknown")]),
      h("blockquote", { "class": "src first-quote" }, [f.cut_left ? "\u2026" : "", f.before,
        h("mark", null, f.match), f.after, f.cut_right ? "\u2026" : ""]),
      h("p", { "class": "prov" }, ["Anchor: ", h("code", null, f.source), " bytes " + f.start + "\u2013" + f.end + ". ",
        f.checked ? "These bytes were checked against the stored source when this page was rendered."
          : "These bytes do not match the stored source."])
    ]));
    var chartBox = h("div", { "class": "chart" });
    add(lexDetail, h("figure", { "class": "chart-fig" }, [
      h("figcaption", { "class": "chart-cap" }, [h("span", { "class": "chart-title" }, "New statements per day"),
        h("span", null, "Busiest " + t.peak[0] + " with " + fmt(t.peak[1]) + "; last new use " + t.last.slice(0, 10) +
          "; " + fmt(t.removed) + " (" + pct(t.removed, t.units) + ") later removed from their page")]),
      chartBox,
      t.undated ? h("p", { "class": "small" }, plural(t.undated, "statement") + " with no recorded time are not drawn.") : null,
      dayTable(t.by_day, "New statements")
    ]));
    lex.chart = { box: chartBox, spec: { days: D.days, height: 170, total: "new statements",
      label: "New statements per day using " + termText(t),
      series: [{ cls: "s1", label: "new statements", values: D.days.map(function (d) { return t.by_day[d] || 0; }) }] } };
    if (R && t.sample) {
      var k = t.sample.length;
      if (k) {
        var walk = h("button", { type: "button", "class": "btn walk" },
          "Compare the readers on the " + plural(k, "sampled statement") + " that use it");
        walk.addEventListener("click", function () { if (useTerm) { useTerm(i); } });
        add(lexDetail, walk);
      } else {
        add(lexDetail, h("p", { "class": "small" }, "None of the " + fmt(R.units.length) + " sampled statements uses this term."));
      }
    }
  }
  function renderConc(i) {
    var t = T[i], c = t.conc, rows = c.rows;
    clear(lexConc);
    add(lexConc, h("h3", null, ["Uses of ", h("span", { "class": "term-inline" }, termText(t)), " in context"]));
    add(lexConc, h("p", { "class": "small" }, fmt(t.uses) + " uses in " + plural(t.units, "statement") + " under " +
      plural(t.names, "name") + ", " + c.from + " to " + c.to + "." + (rows.length < t.uses ? " Showing " + rows.length +
      ", evenly spaced from the first use to the last." : "") + " Each line is one use, in order of first appearance."));
    var body = h("tbody");
    rows.forEach(function (r) {
      body.appendChild(h("tr", null, [h("td", { "class": "when" }, minute(r[0])), h("td", { "class": "who" }, r[1]),
        h("td", { "class": "left" }, r[2]), h("td", { "class": "kw" }, h("mark", null, r[3])),
        h("td", { "class": "right" }, r[4])]));
    });
    add(lexConc, h("div", { "class": "scroll conc-wrap" }, h("table", { "class": "kwic" }, [
      h("thead", null, h("tr", null, [h("th", { scope: "col" }, "When (UTC)"), h("th", { scope: "col" }, "Who"),
        h("th", { scope: "col", "class": "left" }, "Before"), h("th", { scope: "col", "class": "kw" }, "Word"),
        h("th", { scope: "col" }, "After")])), body])));
  }
  function selectTerm(i, how) {
    if (!T[i] || !lexDetail) { return; }
    lex.selected = i;
    lexRows.forEach(function (row) {
      var on = Number(row.getAttribute("data-i")) === i;
      row.classList.toggle("is-selected", on);
      var button = row.querySelector(".term-btn");
      if (button) { button.setAttribute("aria-pressed", on ? "true" : "false"); }
    });
    renderTerm(i);
    renderConc(i);
    if (lex.chart) { columnChart(lex.chart.box, lex.chart.spec); }
    revealRow(i);
    if (how === "jump") { goTo(document.getElementById("sec-words")); }
    else if (how === "click" && narrow.matches) { goTo(lexDetail); }
  }
  if (lexTable) {
    lexRows.forEach(function (row) {
      var i = Number(row.getAttribute("data-i"));
      var cell = row.querySelector("[data-spark]");
      if (cell && T[i]) { sparkline(cell, T[i].by_day); }
      row.addEventListener("click", function () { selectTerm(i, "click"); });
    });
    Array.prototype.forEach.call(lexTable.tHead.querySelectorAll("th[data-sort]"), function (th) {
      th.querySelector("button").addEventListener("click", function () {
        var key = th.getAttribute("data-sort");
        if (lex.key === key) { lex.dir = -lex.dir; } else { lex.key = key; lex.dir = key === "first" ? 1 : -1; }
        sortRows();
        revealRow(lex.selected);
      });
    });
  }

  // ---- their names ----
  function drawNames() {
    var box = document.getElementById("names-chart");
    if (!box || !D.names) { return; }
    columnChart(box, { days: D.days, height: 210, total: "new names", label: "New names per day",
      series: [{ cls: "s1", label: "carrying a month and day", values: D.names.date },
        { cls: "s0", label: "other new names", values: D.names.other }] });
  }

  // ---- readings side by side ----
  function level(count, n) { return Math.max(1, Math.min(4, Math.ceil(4 * count / Math.max(1, n)))); }
  function shade(box, text, spans, n) {
    var cuts = [0, text.length];
    spans.forEach(function (sp) { cuts.push(sp.a, sp.b); });
    cuts = cuts.filter(function (v) { return v >= 0 && v <= text.length; }).sort(function (a, b) { return a - b; });
    var segs = [];
    for (var k = 0; k < cuts.length - 1; k += 1) {
      var a = cuts[k], b = cuts[k + 1];
      if (b <= a) { continue; }
      var seen = {}, count = 0;
      spans.forEach(function (sp) { if (sp.a <= a && sp.b >= b && !seen[sp.lens]) { seen[sp.lens] = true; count += 1; } });
      if (!count) { box.appendChild(document.createTextNode(text.slice(a, b))); continue; }
      var mark = h("mark", { "class": "q q" + level(count, n) }, text.slice(a, b));
      mark.title = "Quoted by " + count + " of the " + n + " readers of this statement shown";
      box.appendChild(mark);
      segs.push({ el: mark, a: a, b: b });
    }
    return segs;
  }
  function shadeKey(n) {
    var parts = [], byLevel = {};
    for (var c = 1; c <= n; c += 1) { var l = level(c, n); (byLevel[l] = byLevel[l] || []).push(c); }
    Object.keys(byLevel).forEach(function (l) {
      var cs = byLevel[l], range = cs.length > 1 ? cs[0] + "\u2013" + cs[cs.length - 1] : String(cs[0]);
      parts.push(h("span", { "class": "q q" + l }, range));
    });
    return h("p", { "class": "shade-key" }, ["Readers who quoted a passage, of the " + plural(n, "reader") +
      " of this statement shown:"].concat(parts));
  }
  function bootReadings() {
    var list = document.getElementById("rd-list"), filter = document.getElementById("rd-filter");
    var count = document.getElementById("rd-count"), termNote = document.getElementById("rd-term");
    var detail = document.getElementById("rd-detail"), lensBox = document.getElementById("rd-lenses");
    if (!list || !detail) { return; }
    var state = { q: "", term: null, selected: -1, hidden: {} };
    R.lenses.forEach(function (L, i) {
      var id = "rd-lens-" + i, input = h("input", { type: "checkbox", id: id, checked: true });
      input.addEventListener("change", function () { state.hidden[i] = !input.checked; renderUnit(); });
      lensBox.appendChild(h("label", { "class": "lens-toggle", "for": id }, [input, h("code", null, L.model),
        " " + L.quals.join(", ")]));
    });
    var empty = h("li", { "class": "rd-empty", hidden: true }, "No sampled statement matches.");
    // A statement's number is its place in the sample in order of first appearance,
    // the same number the pilot review page gives it.
    function numberOf(k) { return ("00" + (k + 1)).slice(-3); }
    var items = R.units.map(function (u, k) {
      var button = h("button", { type: "button", "class": "rd-item", "aria-pressed": "false" }, [
        h("span", { "class": "rd-meta" }, numberOf(k) + " \u00b7 " + minute(u.time) + " \u00b7 " + u.kind + " \u00b7 "
          + plural(u.c.length, "coding")),
        h("span", { "class": "rd-excerpt" }, u.text.replace(/\s+/g, " ").trim().slice(0, 200))]);
      button.addEventListener("click", function () { select(k, true); });
      var li = h("li", null, button);
      list.appendChild(li);
      return { li: li, button: button,
        hay: [u.text, u.page, u.saved_as, u.signature].filter(Boolean).join("\n").toLowerCase() };
    });
    list.appendChild(empty);
    function apply() {
      var q = state.q.trim().toLowerCase(), allowed = null, shown = 0, first = -1;
      if (state.term !== null && T[state.term]) {
        allowed = {};
        (T[state.term].sample || []).forEach(function (k) { allowed[k] = true; });
      }
      var asNumber = /^#?(\d{1,3})$/.exec(q), wanted = asNumber ? parseInt(asNumber[1], 10) - 1 : null;
      items.forEach(function (it, k) {
        // A bare number goes to the statement with that number, whatever else is filtered.
        var ok = wanted !== null ? k === wanted : (!q || it.hay.indexOf(q) !== -1) && (!allowed || allowed[k]);
        it.li.hidden = !ok;
        if (ok) { shown += 1; if (first < 0) { first = k; } }
      });
      empty.hidden = shown > 0;
      count.textContent = shown === items.length ? "All " + fmt(shown) + " sampled statements, in order of first appearance"
        : "Showing " + fmt(shown) + " of " + fmt(items.length) + " sampled statements";
      clear(termNote);
      termNote.hidden = !allowed;
      if (allowed) {
        var all = h("button", { type: "button", "class": "btn-link" }, "Show all");
        all.addEventListener("click", function () { state.term = null; apply(); });
        add(termNote, ["Only those that use ", h("span", { "class": "term-inline" }, termText(T[state.term])),
          ", the term selected under Their words. ", all]);
      }
      if ((state.selected < 0 || items[state.selected].li.hidden) && first >= 0) { select(first, false); }
    }
    function select(k, user) {
      state.selected = k;
      items.forEach(function (it, j) { it.button.setAttribute("aria-pressed", j === k ? "true" : "false"); });
      renderUnit();
      if (user && narrow.matches) { goTo(detail); }
    }
    function nameSpan(text) { return h("span", { "class": "name" }, text); }
    function renderUnit() {
      var u = R.units[state.selected];
      clear(detail);
      if (!u) { return; }
      var shownLenses = R.lenses.map(function (_, i) { return i; }).filter(function (i) { return !state.hidden[i]; });
      var readers = shownLenses.filter(function (i) {
        return u.r.indexOf(i) !== -1 || u.c.some(function (c) { return c[0] === i; });
      });
      var names = [u.saved_as ? ["saved as ", nameSpan(u.saved_as)] : "saved under no recorded name", " \u00b7 "];
      if (u.signature) {
        names.push(["signed ", nameSpan(u.signature), u.uncertain ? " (with a question mark)" : "",
          u.saved_as && u.signature !== u.saved_as ? ", a different name" : ""]);
      } else { names.push("unsigned"); }
      var fate = [];
      if (u.removed) {
        fate.push("First absent from the page at " + stamp(u.removed) + (u.restored ? ", later restored" : "") + ".");
        if (u.final && u.final !== u.removed) { fate.push(" Last removed at " + stamp(u.final) + "."); }
      } else if (u.in_last) { fate.push("Present in the last captured revision of its page."); }
      if (u.page_deleted) { fate.push(" The page itself was deleted at " + stamp(u.page_deleted) + "."); }
      add(detail, h("div", { "class": "ud-head" }, [
        h("p", null, [h("span", { "class": "chip" }, "no. " + numberOf(state.selected)), " ",
          h("span", { "class": "chip" }, u.kind || "unit"), " first seen " + stamp(u.time) + " on page ",
          nameSpan(u.page || "unknown")]),
        h("p", null, names),
        fate.length ? h("p", null, fate.join("")) : null,
        h("p", null, h("code", null, u.id))]));
      var spans = [];
      u.c.forEach(function (c) {
        if (!state.hidden[c[0]] && c[6] !== null && c[6] !== undefined) { spans.push({ a: c[6], b: c[7], lens: c[0] }); }
      });
      var textBox = h("div", { "class": "ud-text src" });
      var segs = shade(textBox, u.text, spans, readers.length);
      add(detail, textBox);
      if (spans.length) { add(detail, shadeKey(readers.length)); }
      var grid = h("div", { "class": "lens-grid" }), unread = [];
      shownLenses.forEach(function (li) {
        var L = R.lenses[li];
        var mine = u.c.filter(function (c) { return c[0] === li; });
        var lost = u.m.filter(function (x) { return x[0] === li; });
        if (u.r.indexOf(li) === -1 && !mine.length && !lost.length) { unread.push(L); return; }
        var block = h("section", { "class": "lens-block" }, h("h4", { "class": "lens-name" }, [h("code", null, L.model),
          h("span", { "class": "quals" }, L.quals.join(", ") + " \u00b7 " + plural(mine.length, "coding"))]));
        if (mine.length) {
          var ul = h("ul", { "class": "codings" });
          mine.forEach(function (c) {
            var item = h("li", { "class": "coding", tabindex: "0" }, [
              h("p", null, [c[8] ? [h("code", { "class": "code-key" }, "[" + c[8] + "]"), " "] : null,
                h("span", { "class": "code-name" }, c[1]), " ",
                h("span", { "class": "chip" + (c[2] === "in_vivo" ? " chip-vivo" : "") },
                  c[2] === "in_vivo" ? "in vivo" : (c[2] || "code"))]),
              h("p", { "class": "src code-quote" }, quoted(c[3])),
              c[4] ? h("p", { "class": "code-def" }, c[4]) : null,
              c[5] ? h("p", { "class": "code-note" }, [h("span", { "class": "label" }, "Note: "), c[5]]) : null]);
            if (c[6] !== null && c[6] !== undefined) {
              var on = function () { segs.forEach(function (sg) { sg.el.classList.toggle("on", sg.a >= c[6] && sg.b <= c[7]); }); };
              var off = function () { segs.forEach(function (sg) { sg.el.classList.remove("on"); }); };
              item.addEventListener("mouseenter", on);
              item.addEventListener("focus", on);
              item.addEventListener("mouseleave", off);
              item.addEventListener("blur", off);
            }
            ul.appendChild(item);
          });
          block.appendChild(ul);
        } else {
          block.appendChild(h("p", { "class": "small" }, "Read this statement and gave it no code."));
        }
        if (lost.length) {
          block.appendChild(h("div", { "class": "missed" }, [
            h("p", null, "Quoted words not found in the statement, so not recorded as codings:"),
            h("ul", null, lost.map(function (x) {
              return h("li", null, [h("span", { "class": "src" }, quoted(x[1])), x[2] ? " (" + x[2] + ")" : ""]);
            }))]));
        }
        grid.appendChild(block);
      });
      add(detail, grid);
      if (unread.length) {
        add(detail, h("p", { "class": "small" }, "Not read by " + unread.map(function (L) {
          return L.model + " (" + L.quals.join(", ") + ")";
        }).join("; ") + "."));
      }
      if (u.t && u.t.length) {
        add(detail, h("p", { "class": "unit-terms" }, ["Terms from Their words in this statement:"].concat(
          u.t.map(function (ti) {
            var b = h("button", { type: "button", "class": "btn" }, T[ti].term);
            b.addEventListener("click", function () { selectTerm(ti, "jump"); });
            return b;
          }))));
      }
    }
    filter.addEventListener("input", function () { state.q = filter.value; state.term = null; apply(); });
    useTerm = function (i) {
      state.term = i; state.q = ""; filter.value = ""; state.selected = -1;
      apply();
      goTo(document.getElementById("sec-readings"));
    };
    var start = D.lexDefault;
    if (start !== null && start !== undefined && T[start] && T[start].sample && T[start].sample.length) { state.term = start; }
    apply();
  }

  // ---- start ----
  if (T.length && lexTable) { selectTerm(D.lexDefault !== null && D.lexDefault !== undefined ? D.lexDefault : 0, "boot"); }
  drawNames();
  if (R) { bootReadings(); }
  var lastWidth = window.innerWidth;
  window.addEventListener("resize", function () {
    if (Math.abs(window.innerWidth - lastWidth) < 8) { return; }
    lastWidth = window.innerWidth;
    window.requestAnimationFrame(function () {
      drawNames();
      if (lex.chart) { columnChart(lex.chart.box, lex.chart.spec); }
    });
  });
}());
"""
