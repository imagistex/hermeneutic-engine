#!/usr/bin/env python3
"""Build the public pages from the ledger and the hand-written copy.

    PYTHONPATH=src python3 site-builder/build_site.py work/wiki --copy site-builder/site.json --out site
    PYTHONPATH=src python3 site-builder/build_site.py work/wiki --copy site-builder/site.json --out <folder> --single

The project is only read: this script opens the ledger, never appends, and
writes nothing outside the output folder. Every number on the pages is counted
here; every quotation is checked to be an exact substring of its unit's text,
and the build stops if one is not.

Outputs, by default (a small site of static pages; links are relative and nothing is
fetched, so it opens straight from disk and from a subpath of a static host):
    <out>/index.html         the front door: the first sentence, alone on its leaf
    <out>/leaf-2.html ...    one leaf for each further sentence; "another" turns to the next and wraps
    <out>/<file>             one page for each entry of `pages` in the copy
    <out>/style.css          shared by every page
    <out>/site.js            used only by the page of words, to open the entry a link names
    <out>/data.json          the computed data, for inspection

Outputs with --single (the one-page build; give it a folder of its own, it also writes index.html):
    <out>/index.html      a complete standalone document
    <out>/artifact.html   the same page as a fragment (title, font link, style, content, script)
    <out>/data.json

Adding a page: one entry in `pages` in the copy (id, file, blocks, and a title unless the
heading of its first block serves), and, for a new kind of block, one renderer added to
BLOCKS. A block that is only a heading and paragraphs needs no renderer.
"""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True  # nothing is written beside the outputs, not even bytecode under src/

import argparse
import html
import importlib.util
import json
import random
import re
import time
from bisect import bisect_left, bisect_right
from collections import Counter
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

from notebook_sections import enrich, render_theory, render_method, render_culture, render_board, render_apparatus, render_thread, thread_file
from engine_visual import render_engine, render_svg

from hermeneutic_engine.store import Project
from hermeneutic_engine.views.lexicon import parse_terms
from hermeneutic_engine.views.names import KINDS, date_of, kinds_of, parts

try:  # a fingerprint of the ledger files; cheap, unlike the full verify pass
    from hermeneutic_engine.verify import ledger_digest
except ImportError:  # pragma: no cover
    ledger_digest = None

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
FONT_HREF = ("https://fonts.googleapis.com/css2?family=Courier+Prime:wght@400;700"
             "&family=Gentium+Book+Plus:ital,wght@0,400;0,700;1,400&display=swap")
WEEK = ("2026-06-16", "2026-06-22")  # the days every by-day count covers at least
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December"]
PLAIN = {"sacrific*": "sacrifice"}  # a stem that is not a word, as the sentence should say it
ID_OK = re.compile(r"^[A-Za-z0-9._~-]+$")
FILE_OK = re.compile(r"^[a-z0-9][a-z0-9._-]*\.html$")
PLACEHOLDER = re.compile(r"\{([a-z][a-z0-9_]*)\}")
SPREAD_HOURS = (1, 3, 6, 24, 48)  # hours after a term's first use at which its signatures are counted
FIRST_SIGNATURES = 5              # how many of the first signatures to use a term are listed
FASTEST = 10                      # how many terms the table of fastest spread holds
FRONT = "index.html"              # the front door: the first sentence
LEAF = "leaf-{n}.html"            # the leaf of the n-th sentence, from the second on
SHARED = ("style.css", "site.js")


class BuildError(Exception):
    """Something the page must not be built over."""


# ---- small helpers -----------------------------------------------------------

def esc(value) -> str:
    return html.escape(str(value), quote=True)


def num(n: int) -> str:
    return f"{n:,}"


def whole_pct(share: float) -> str:
    return str(int(share * 100 + 0.5))


def parse_time(stamp: str) -> datetime:
    return datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def day_label(day: str) -> str:
    """2026-06-16 -> 16 Jun"""
    return f"{int(day[8:10])} {MONTHS[int(day[5:7]) - 1][:3]}"


def long_date(stamp: str) -> str:
    return f"{int(stamp[8:10])} {MONTHS[int(stamp[5:7]) - 1]} {stamp[:4]}"


def short_date(stamp: str) -> str:
    return f"{int(stamp[8:10])} {MONTHS[int(stamp[5:7]) - 1][:3]} {stamp[:4]}"


def clock(stamp: str) -> str:
    return stamp[11:16]


def count_words(n: int) -> str:
    return {1: "once", 2: "twice"}.get(n, f"{num(n)} times")


def entry_id(term: str) -> str:
    slug = re.sub(r"\s+", "-", term.strip().replace("*", "~"))
    slug = "w-" + re.sub(r"[^A-Za-z0-9._~-]", "", slug)
    if not ID_OK.match(slug):
        raise BuildError(f"cannot make an id for the term {term!r}")
    return slug


def load_checks():
    """The definitions in site-builder/checks_after_annotations.py, so the numbers match that file."""
    path = HERE / "checks_after_annotations.py"
    spec = importlib.util.spec_from_file_location("checks_after_annotations", path)
    if spec is None or spec.loader is None or not path.exists():
        raise BuildError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def find_file(name: str, beside: Path) -> Path:
    for candidate in (Path(name), REPO / name, beside.parent / name):
        if candidate.exists():
            return candidate
    raise BuildError(f"file named in the copy was not found: {name}")


# ---- reading the project (never writing) ------------------------------------

READ_KINDS = ("activity", "lens", "code", "memo", "coding", "source", "unit")


def open_project(root: str):
    """Open and read the ledger. A run may be appending to it, so a half-written
    last line is waited out, and the fingerprint is taken before and after the
    read: when the two agree, the page describes exactly that state of the ledger.

    Activities are read first. A reading appends its codings and notes before
    its activity record, so everything belonging to a finished activity is
    already there when the later files are read.
    """
    problem = None
    for attempt in range(3):
        if attempt:
            time.sleep(1.5)
        try:
            project = Project(root)
            before = ledger_digest(project) if ledger_digest else None
            for kind in READ_KINDS:
                project.records(kind)
            after = ledger_digest(project) if ledger_digest else None
        except (ValueError, KeyError, OSError) as error:  # a half-written line fails to parse
            problem = error
            continue
        if before == after or attempt == 2:
            return project, after, before == after
        problem = "the ledger changed while it was being read"
    raise BuildError(f"could not read the ledger at {root} after three tries: {problem}")


# ---- counting ----------------------------------------------------------------

def post_rows(project: Project, checks) -> list[dict]:
    rows = []
    for unit in project.records("unit"):
        if unit.get("unit_kind") != "post":
            continue
        ctx = unit["context"]
        text = project.unit_text(unit)
        signature = ctx.get("signature")
        rows.append({
            "id": unit["id"], "text": text, "body": checks.body_of(text, signature), "sig": signature,
            "saved": ctx.get("introduced_by"), "time": (ctx.get("first_seen") or {}).get("time") or "",
            "page": ctx.get("page") or "",
        })
    rows.sort(key=lambda row: (row["time"], row["id"]))
    for row in rows:
        row["day"] = row["time"][:10]
    return rows


def day_range(rows: list[dict]) -> list[str]:
    seen = [row["day"] for row in rows if row["day"]]
    first = min([WEEK[0], *seen])
    last = max([WEEK[1], *seen])
    day, days = datetime.strptime(first, "%Y-%m-%d"), []
    while day.strftime("%Y-%m-%d") <= last:
        days.append(day.strftime("%Y-%m-%d"))
        day += timedelta(days=1)
    return days


def trace(rows: list[dict], regex, days: list[str]) -> tuple[dict, list[tuple[dict, re.Match]]]:
    hits = [(row, match) for row in rows if (match := regex.search(row["body"]))]
    by_day = Counter(row["day"] for row, _ in hits)
    stats = {
        "pattern": regex.pattern, "posts": len(hits), "share": len(hits) / len(rows) if rows else 0,
        "signatures": len({row["sig"] for row, _ in hits}), "by_day": {day: by_day.get(day, 0) for day in days},
        "first": None,
    }
    if hits:
        row = hits[0][0]
        stats["first"] = {"time": row["time"], "signature": row["sig"], "page": row["page"], "unit": row["id"]}
    return stats, hits


def spread_of(hits: list[tuple[dict, re.Match]], days: list[str], rows: list[dict], times: list[datetime]) -> dict:
    """How a term crossed signatures, counted over the same posts `trace` found.

    A signature takes a term up with the first post signed under it that uses the term. Posts
    come in order of time, then unit id, so two signatures within one second fall in id order.
    `adoption` is the number of signatures that had taken the term up no later than 1, 3, 6, 24
    and 48 hours after its first use; the first signature is among them. `posting` is what that
    number is out of: the signatures that signed any post at all, with the term or without, from
    the first use to the same hour. `curve` is the adoption number at each whole hour after the
    first use, to the end of the last day shown; an hour is listed only where the number changed,
    so between two listed hours it holds. `rows` is every signed post in order and `times` their times.
    """
    taken: dict = {}
    for row, _ in hits:
        taken.setdefault(row["sig"], row)
    order = list(taken.values())  # a dict keeps the order in which its keys first came
    if not order:
        return {"signatures_total": 0, "pages_total": 0, "adoption": None, "posting": None, "first_signatures": [],
                "curve": None}
    start = parse_time(order[0]["time"])
    after = [(parse_time(row["time"]) - start).total_seconds() for row in order]  # in order, so sorted
    opened = bisect_left(times, start)
    posting = {f"{hours}h": len({row["sig"] for row in rows[opened:bisect_right(times, start + timedelta(hours=hours))]})
               for hours in SPREAD_HOURS}
    end = parse_time(days[-1] + "T00:00:00Z") + timedelta(days=1)
    span = (end - start).total_seconds()
    points, level = [], None
    for hour in range(int(span // 3600) + 1):
        n = bisect_right(after, hour * 3600)
        if n != level:
            points.append([hour, n])
            level = n
    if level != len(order):  # a signature in the last part of an hour before the end of the last day
        points.append([round(span / 3600, 2), len(order)])
    return {
        "signatures_total": len(order),
        "pages_total": len({row["page"] for row, _ in hits}),
        "adoption": {f"{hours}h": bisect_right(after, hours * 3600) for hours in SPREAD_HOURS},
        "posting": posting,
        "first_signatures": [{"signature": row["sig"], "time": row["time"], "page": row["page"], "unit": row["id"]}
                             for row in order[:FIRST_SIGNATURES]],
        "curve": {"from": order[0]["time"], "to": end.strftime("%Y-%m-%dT%H:%M:%SZ"), "hours": round(span / 3600, 2),
                  "points": points},
    }


def window(body: str, start: int, end: int, width: int = 160) -> tuple[int, int]:
    """About `width` characters around a match, cut at word boundaries, never inside the match."""
    pad = max(0, width - (end - start))
    lo = max(0, start - pad // 2)
    hi = min(len(body), end + pad - (start - lo))
    lo = max(0, start - (pad - (hi - end)))
    if lo > 0:
        while lo < start and not body[lo - 1].isspace():
            lo += 1
    if hi < len(body):
        while hi > end and not body[hi].isspace():
            hi -= 1
    while lo < start and body[lo].isspace():
        lo += 1
    while hi > end and body[hi - 1].isspace():
        hi -= 1
    return lo, hi


def passage_of(row: dict, match: re.Match) -> dict:
    lo, hi = window(row["body"], match.start(), match.end())
    text = row["body"][lo:hi]
    # The body is the post without its closing signature, so it is a prefix of the unit's text
    # and these offsets hold in the unit's text too. Checked, not assumed.
    if row["text"][lo:hi] != text or text not in row["text"]:
        raise BuildError(f"passage is not an exact substring of {row['id']}: {text!r}")
    return {"unit": row["id"], "signature": row["sig"], "time": row["time"], "page": row["page"],
            "start": lo, "end": hi, "text": text, "match": [match.start() - lo, match.end() - lo],
            "cut_before": lo > 0, "cut_after": hi < len(row["body"])}


def pick_passages(hits: list[tuple[dict, re.Match]], k: int) -> list[dict]:
    """The first use, then uses evenly spaced in time up to the last one. A passage
    that repeats one already chosen is passed over for its nearest neighbour in time."""
    if not hits or k < 1:
        return []
    times = [parse_time(row["time"]).timestamp() for row, _ in hits]
    targets = [times[0]]
    if k > 1:
        targets += [times[0] + (times[-1] - times[0]) * j / (k - 1) for j in range(1, k)]
    chosen, used, seen = [], set(), set()
    for target in targets:
        for index in sorted(range(len(hits)), key=lambda i: (abs(times[i] - target), i)):
            if index in used:
                continue
            passage = passage_of(*hits[index])
            key = " ".join(passage["text"].split()).lower()
            if key in seen:
                continue
            used.add(index)
            seen.add(key)
            chosen.append(passage)
            break
    chosen.sort(key=lambda passage: (passage["time"], passage["unit"]))
    return chosen


def place_doors(quote: str, doors: list, where: str) -> list[dict]:
    """Each door is an exact stretch of the sentence. Whole words are preferred, and doors do not overlap."""
    placed = []
    for pair in doors:
        text, term = pair[0], pair[1]
        spots = [m.start() for m in re.finditer(re.escape(text), quote)]
        free = [s for s in spots if all(s + len(text) <= d["start"] or s >= d["end"] for d in placed)]
        whole = [s for s in free
                 if (s == 0 or not quote[s - 1].isalnum()) and (s + len(text) == len(quote) or not quote[s + len(text)].isalnum())]
        if not free:
            raise BuildError(f"{where}: door {text!r} is not an exact substring of the sentence")
        start = (whole or free)[0]
        placed.append({"text": text, "term": term, "start": start, "end": start + len(text)})
    placed.sort(key=lambda door: door["start"])
    return placed


def named_terms(copy: dict) -> list[str]:
    names: list[str] = []
    for family in copy["words"]["families"]:
        names += family["terms"]
    for sentence in copy["sentences"]:
        names += [door[1] for door in sentence.get("doors", [])]
    for takeaway in copy["takeaways"]:
        chart = takeaway.get("chart") or {}
        names += chart.get("terms", []) + chart.get("present", []) + chart.get("absent_probe", [])
    names += ["please", "before final, any case", "human"]  # the placeholders in the copy
    return list(dict.fromkeys(names))


def readers_of(project: Project, codebook_id: str) -> list[dict]:
    """Focused-coding lenses on this codebook that have finished reading at least one batch."""
    activities = project.records("activity")
    codings = project.records("coding")
    memos = project.records("memo")
    readers = []
    for lens in project.records("lens"):
        reader = lens.get("reader") or {}
        if (lens.get("method") or {}).get("name") != "focused-coding" or reader.get("codebook") != codebook_id:
            continue
        done = [a for a in activities if a.get("type") == "read" and a.get("status") == "ok" and a.get("lens") == lens["id"]]
        if not done:
            continue
        done_ids = {a["id"] for a in done}
        read = {unit for a in done for unit in (a.get("used") or [])}
        notes = [m for m in memos if m.get("by") == lens["id"] and m.get("memo_type") == "field_note"
                 and m.get("activity") in done_ids and (m.get("body") or "").strip()]
        signed = Counter(m.get("signed") or "unsigned" for m in notes)
        longest = sorted(notes, key=lambda m: (-len(m["body"]), m["id"]))[:2]
        readers.append({
            "lens": lens["id"], "model": reader.get("model"), "family": reader.get("family"),
            "requested": reader.get("requested"), "declared": lens.get("at"),
            "code_types": (lens.get("method") or {}).get("code_types"),
            "batches": len(done), "units_read": len(read),
            "codings": sum(1 for c in codings if c.get("by") == lens["id"] and c.get("activity") in done_ids),
            "field_notes": len(notes),
            "signatures": [{"signed": name, "notes": n} for name, n in sorted(signed.items(), key=lambda kv: (-kv[1], kv[0]))],
            "notes": [{"memo": m["id"], "signed": m.get("signed") or None, "at": m.get("at"),
                       "body": m["body"]} for m in longest],
        })
    readers.sort(key=lambda r: (r["declared"] or "", r["lens"]))
    return readers


def compute(project: Project, copy: dict, copy_path: Path, digest: str | None, steady: bool) -> dict:
    checks = load_checks()
    warnings: list[str] = []
    terms_path = find_file(copy["terms_file"], copy_path)
    regexes = {term["term"]: term["regex"] for term in parse_terms(terms_path.read_text(encoding="utf-8"))}
    wanted = named_terms(copy)
    for name in wanted:
        if name not in regexes:
            raise BuildError(f"term named in the copy is missing from {terms_path}: {name!r}")

    rows = post_rows(project, checks)
    if not rows:
        raise BuildError("the project holds no signed posts")
    total = len(rows)
    days = day_range(rows)
    posts_by_day = Counter(row["day"] for row in rows)

    # ---- every named term, over signed posts with the signature left out; and how it crossed signatures
    terms, hits_of = {}, {}
    times = [parse_time(row["time"]) for row in rows]
    for name in wanted:
        terms[name], hits_of[name] = trace(rows, regexes[name], days)
        terms[name].update(spread_of(hits_of[name], days, rows, times))

    # ---- the codebook's in vivo codes, for the senses left open
    codebook = project.get(copy["codebook"])
    if codebook is None or codebook.get("memo_type") != "codebook":
        raise BuildError(f"not a codebook memo: {copy['codebook']}")
    senses = {}
    for ref in codebook.get("about") or []:
        code = project.get(ref) if isinstance(ref, str) else None
        if code and code.get("kind") == "code" and code.get("code_type") == "in_vivo":
            loaded = code.get("loaded")
            if isinstance(loaded, (list, tuple)):
                loaded = "; ".join(str(sense) for sense in loaded)
            if (loaded or "").strip():
                senses[str(code.get("name") or "").strip().lower()] = {"code": code["id"], "key": code.get("key"),
                                                                        "text": loaded.strip()}

    # ---- their words
    per_term = int(copy["words"].get("quotes_per_term", 3))
    families, ids, passages_total = [], {}, 0
    for family in copy["words"]["families"]:
        entries = []
        for name in family["terms"]:
            eid = entry_id(name)
            if eid in ids.values():
                raise BuildError(f"two terms would share the id {eid}")
            ids[name] = eid
            passages = pick_passages(hits_of[name], per_term)
            passages_total += len(passages)
            if not terms[name]["posts"]:
                warnings.append(f"word {name!r} occurs in no signed post")
            entries.append({"term": name, "id": eid, "passages": passages,
                            "senses_left_open": senses.get(name.lower())})
        families.append({"name": family["name"], "entries": entries})

    # ---- the entries that crossed most signatures in their first hour; ties go to the next count
    # (three hours), then to the earlier first use, then to the alphabet
    soon, later = f"{SPREAD_HOURS[0]}h", f"{SPREAD_HOURS[1]}h"
    ranked = sorted((name for name in ids if terms[name]["adoption"]),
                    key=lambda name: (-terms[name]["adoption"][soon], -terms[name]["adoption"][later],
                                      terms[name]["first"]["time"], name))
    fastest = [{"term": name, "id": ids[name], "first": terms[name]["first"]["time"],
                "adoption": terms[name]["adoption"], "posting": terms[name]["posting"],
                "signatures_total": terms[name]["signatures_total"]} for name in ranked[:FASTEST]]
    if len(ranked) > FASTEST and terms[ranked[FASTEST - 1]]["adoption"][soon] == terms[ranked[FASTEST]]["adoption"][soon]:
        warnings.append(f"the table of fastest spread is cut between two terms with the same first-hour count: "
                        f"{ranked[FASTEST - 1]!r} is in, {ranked[FASTEST]!r} is out, by the tie rule")

    # ---- the sentences
    sentences = []
    for index, item in enumerate(copy["sentences"], 1):
        unit = project.get(item["unit"])
        if unit is None or unit.get("kind") != "unit":
            raise BuildError(f"sentence {index}: no such unit {item['unit']}")
        text = project.unit_text(unit)
        at = text.find(item["quote"])
        if at < 0:
            raise BuildError(f"sentence {index}: the quotation is not found exactly in {unit['id']}: {item['quote']!r}")
        ctx = unit["context"]
        doors = place_doors(item["quote"], item.get("doors", []), f"sentence {index}")
        for door in doors:
            door["entry"] = ids.get(door["term"])
            door["posts"] = terms[door["term"]]["posts"]
            if door["entry"] is None:
                warnings.append(f"sentence {index}: door {door['text']!r} leads to the term {door['term']!r}, which has no "
                                f"entry in words.families; it is set as plain text until the term is added to a family")
            if not regexes[door["term"]].search(door["text"]):
                warnings.append(f"sentence {index}: door {door['text']!r} is not itself matched by the term {door['term']!r}")
        if unit.get("unit_kind") != "post" or not ctx.get("signature"):
            warnings.append(f"sentence {index} ({unit['id']}) is a {unit.get('unit_kind')} unit with "
                            f"{'no signature' if not ctx.get('signature') else 'a signature'}; it is not among the signed "
                            f"posts the counts are made over. Shown as unsigned, saved as {ctx.get('introduced_by')}")
        sentences.append({
            "unit": unit["id"], "unit_kind": unit.get("unit_kind"), "quote": item["quote"], "start": at,
            "end": at + len(item["quote"]), "signature": ctx.get("signature"), "saved_as": ctx.get("introduced_by"),
            "time": (ctx.get("first_seen") or {}).get("time"), "page": ctx.get("page"), "gloss": item.get("gloss", ""),
            "doors": doors,
        })

    # ---- metrics for the placeholders
    dated = sum(1 for row in rows if row["sig"] and date_of(row["sig"]))
    with_clock = same = other = undated = 0
    for row in rows:
        found = [checks.norm(m.group(1) or m.group(3), m.group(2) or m.group(4))
                 for m in checks.BESIDE_CLOCK.finditer(row["body"])]
        if not found:
            continue
        with_clock += 1
        code = date_of(row["sig"]) if row["sig"] else None
        if code is None:
            undated += 1
        elif code in found:
            same += 1
        else:
            other += 1
    voice = {"one": checks.ONE, "many": checks.MANY}
    said = {key: Counter() for key in voice}
    for row in rows:
        for key, regex in voice.items():
            if regex.search(row["body"]):
                said[key][row["day"]] += 1
    voice_stats = {
        key: {"posts": sum(counts.values()), "share": sum(counts.values()) / total,
              "by_day": {day: {"posts": counts.get(day, 0), "of": posts_by_day.get(day, 0),
                               "share": counts.get(day, 0) / posts_by_day[day] if posts_by_day.get(day) else None}
                         for day in days}}
        for key, counts in said.items()
    }

    # ---- words one might expect: searched in every post and text unit, signature included
    # Beside the term list's own pattern, each stem is also looked for loosely (inside longer words,
    # page names and names), so that "missing from the whole wiki" can be checked by the author.
    statements = []
    for unit in project.records("unit"):
        ctx = unit["context"]
        text = project.unit_text(unit)
        names = " ".join(filter(None, (ctx.get("page"), ctx.get("signature"), ctx.get("introduced_by"))))
        statements.append((unit["unit_kind"], text, text.lower(), names, names.lower()))
    probes = {}
    please = next((t for t in copy["takeaways"] if (t.get("chart") or {}).get("type") == "present_absent"), None)
    probe_names = please["chart"]["absent_probe"] if please else []
    for name in probe_names + ["human"]:
        regex = regexes[name]
        stem = name.rstrip("*").lower()
        loose = re.compile(r"[A-Za-z]*" + re.escape(stem) + r"[A-Za-z]*", re.I)
        found = {"units": Counter(), "occurrences": Counter(), "loose": Counter()}
        for kind, text, text_lower, names, names_lower in statements:
            n = len(regex.findall(text))
            if n:
                found["units"][kind] += 1
                found["occurrences"][kind] += n
            if stem in text_lower:
                found["loose"].update(loose.findall(text))
            if stem in names_lower:
                found["loose"].update(f"{word} (in a name)" for word in set(loose.findall(names)))
        probes[name] = {
            "pattern": regex.pattern,
            "post_and_text_units": found["units"]["post"] + found["units"]["text"],
            "post_and_text_occurrences": found["occurrences"]["post"] + found["occurrences"]["text"],
            "other_units": {k: v for k, v in found["units"].items() if k not in ("post", "text")},
            "loose_stem_matches_anywhere": dict(found["loose"].most_common(8)),
        }
    absent =[name for name in probe_names if probes[name]["post_and_text_units"] == 0]
    occurring = [name for name in probe_names if probes[name]["post_and_text_units"]]

    # ---- how signatures are built
    name_parts = {"date": 0, "role": 0, "institution": 0, "differs": 0}
    examples = {"date": Counter(), "role": Counter(), "institution": Counter()}
    for row in rows:
        signature = row["sig"] or ""
        kinds = kinds_of(signature)
        tokens = parts(signature)
        for kind in ("date", "role", "institution"):
            name_parts[kind] += kind in kinds
        for kind in ("role", "institution"):
            examples[kind].update({token for token in tokens if token in KINDS[kind]})
        if "date" in kinds:
            for token, following in zip(tokens, tokens[1:]):
                if date_of(token + following):
                    examples["date"][token + following[:2]] += 1
                    break
        name_parts["differs"] += bool(row["saved"]) and signature != row["saved"]
    name_parts = {kind: {"posts": n, "share": n / total} for kind, n in name_parts.items()}
    for kind, counter in examples.items():
        name_parts[kind]["most_used"] = [token for token, _ in counter.most_common(3)]

    metrics = {
        "posts_total": total, "first_post": rows[0]["time"], "last_post": rows[-1]["time"],
        "posts_by_day": {day: posts_by_day.get(day, 0) for day in days},
        "date_name_posts": dated, "date_name_share": dated / total,
        "clock": {"posts_with_a_date_beside_a_clock": with_clock, "same_as_signature": same, "a_different_code": other,
                  "signature_has_no_date": undated, "total_with_dated_signature": same + other},
        "we": voice_stats["many"], "i": voice_stats["one"],
        "please_posts": terms["please"]["posts"], "please_share": terms["please"]["share"],
        "before_final_posts": terms["before final, any case"]["posts"],
        "human": probes["human"], "absent": absent, "probe_words_that_occur": occurring,
        "ledger_digest": digest, "ledger_steady_during_read": steady,
    }
    fills = {
        "posts_total": num(total),
        "date_name_share": whole_pct(dated / total),
        "clock_same": num(same), "clock_total": num(same + other),
        "we_share": whole_pct(voice_stats["many"]["share"]), "i_share": whole_pct(voice_stats["one"]["share"]),
        "please_share": whole_pct(terms["please"]["share"]),
        "before_final_posts": num(terms["before final, any case"]["posts"]),
        "absent_list": ", ".join(PLAIN.get(name, name.rstrip("*")) for name in absent),
        "human_count": count_words(probes["human"]["post_and_text_occurrences"]),
        "differs_posts": num(name_parts["differs"]["posts"]),
        "differs_share": whole_pct(name_parts["differs"]["share"]),
        "fastest_count": num(len(fastest)),
    }
    if digest:
        fills["ledger_digest"] = digest
    for name in occurring:
        warnings.append(f"probe word {name!r} was expected absent but occurs in "
                        f"{probes[name]['post_and_text_units']} post or text units; left out of the sentence and the chart")
    for name in absent:
        elsewhere = probes[name]["other_units"]
        loose = probes[name]["loose_stem_matches_anywhere"]
        if elsewhere:
            warnings.append(f"probe word {name!r} is absent from post and text units but occurs in other units: {elsewhere}")
        elif loose:
            warnings.append(f"probe word {name!r} is absent as a word, but its stem occurs inside other words or names: {loose}")
    human_elsewhere = probes["human"]["other_units"]
    if human_elsewhere:
        warnings.append(f"'human' is counted over post and text units ({probes['human']['post_and_text_occurrences']}); "
                        f"it also occurs in other units: {human_elsewhere}")

    if not steady:
        warnings.append("the ledger was being written while it was read; the digest is of the files as last seen")

    return {
        "built": {"project": str(project.root), "copy": str(copy_path), "terms_file": str(terms_path),
                  "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")},
        "days": days, "metrics": metrics, "placeholders": fills, "terms": terms, "words": families,
        "fastest": fastest, "sentences": sentences, "probes": probes, "name_parts": name_parts,
        "readers": readers_of(project, copy["codebook"]), "passages_total": passages_total, "warnings": warnings,
    }


# ---- filling the copy ---------------------------------------------------------

def filler(fills: dict):
    def fill(text: str) -> str:
        def one(match: re.Match) -> str:
            if match.group(1) not in fills:
                raise BuildError(f"unknown placeholder in the copy: {{{match.group(1)}}}")
            return fills[match.group(1)]
        return PLACEHOLDER.sub(one, text)
    return fill


# ---- charts: inline SVG, drawn to scale -----------------------------------------
#
# The stretchable charts have no viewBox. Positions across are percentages and
# positions down are pixels, so the chart fills whatever width it is given while
# its lettering keeps one size, on a phone as on a desk.

def pct(value: float) -> str:
    return f"{value:.2f}%"


def chart_first_use(chart: dict, data: dict) -> str:
    items = [{"term": name, **data["terms"][name]["first"]} for name in chart["terms"] if data["terms"][name]["first"]]
    if not items:
        return ""
    items.sort(key=lambda item: item["time"])
    times = [parse_time(item["time"]) for item in items]
    start = times[0].replace(minute=0, second=0)
    end = times[-1].replace(minute=0, second=0)
    if end < times[-1]:
        end += timedelta(hours=1)
    if end == start:
        end += timedelta(hours=1)
    span = (end - start).total_seconds()
    step = 1 if span <= 6 * 3600 else 2 if span <= 12 * 3600 else 6 if span <= 48 * 3600 else 24
    across = lambda moment: 1.5 + 97 * (moment - start).total_seconds() / span
    axis, row, first_row = 30, 22, 56
    height = first_row + row * (len(items) - 1) + 10
    out = [f'<line class="ax" x1="1.5%" x2="98.5%" y1="{axis}" y2="{axis}"/>']
    tick, ticks = start, []
    while tick <= end:
        ticks.append(tick)
        tick += timedelta(hours=step)
    many_days = start.date() != end.date()
    for i, tick in enumerate(ticks):
        x = across(tick)
        anchor = "start" if i == 0 else "end" if i == len(ticks) - 1 else "middle"
        stamp = tick.strftime("%Y-%m-%dT%H:%M:%SZ")
        label = f"{day_label(stamp[:10])} {clock(stamp)}" if many_days and (i == 0 or tick.hour == 0) else clock(stamp)
        out.append(f'<line class="ax" x1="{pct(x)}" x2="{pct(x)}" y1="{axis}" y2="{axis - 5}"/>')
        out.append(f'<text class="p" x="{pct(x)}" y="{axis - 11}" text-anchor="{anchor}">{esc(label)}</text>')
    # the latest term takes the top row, so no label runs across another term's leader
    for r, (item, moment) in enumerate(sorted(zip(items, times), key=lambda pair: pair[1], reverse=True)):
        x, y = across(moment), first_row + r * row
        room = (100 - x) / 100 * 300  # pixels to the right of the tick on the narrowest screen
        to_right = room >= len(item["term"]) * 7.7 + 52
        out.append(f'<line class="ld" x1="{pct(x)}" x2="{pct(x)}" y1="{axis}" y2="{y - 4}"/>')
        out.append(f'<line class="tk" x1="{pct(x)}" x2="{pct(x)}" y1="{axis - 6}" y2="{axis + 6}"/>')
        word = f'<tspan class="w">{esc(item["term"])}</tspan>'
        when = f'<tspan class="p" dx="7">{clock(item["time"])}</tspan>'
        out.append(f'<text class="halo" x="{pct(x)}" y="{y}" dx="{7 if to_right else -7}" '
                   f'text-anchor="{"start" if to_right else "end"}">{word}{when}</text>')
    told = "; ".join(f"{item['term']} {clock(item['time'])}" for item in items)
    return (f'<svg class="chart" width="100%" height="{height}" role="img" '
            f'aria-label="{esc("First appearance, " + long_date(items[0]["time"]) + ", UTC: " + told)}">{"".join(out)}</svg>')


def chart_by_day_share(chart: dict, data: dict) -> str:
    days = data["days"]
    keys = {"many": "we", "one": "i"}
    series = [(label, data["metrics"][keys[key]]["by_day"]) for label, key in chart["series"]]
    top, plot = 20, 116
    base = top + plot
    height = base + 40
    highest = max((cell["share"] or 0) for _, by_day in series for cell in by_day.values())
    ceiling = max(0.2, (int(highest * 100) // 20 + 1) * 20 / 100)
    left = 11.5
    across = lambda i: left + (100 - left) * (i + 0.5) / len(days)
    down = lambda share: base - plot * share / ceiling
    out = []
    level = 0.0
    while level <= ceiling + 1e-9:
        y = down(level)
        out.append(f'<line class="{"ax" if level == 0 else "gr"}" x1="{pct(left)}" x2="100%" y1="{y:.1f}" y2="{y:.1f}"/>')
        out.append(f'<text class="p" x="{pct(left - 2)}" y="{y + 4:.1f}" text-anchor="end">{int(level * 100 + 0.5)}%</text>')
        level += 0.2
    for i, day in enumerate(days):
        out.append(f'<text class="p" x="{pct(across(i))}" y="{base + 17}" text-anchor="middle">{day_label(day)}</text>')
        out.append(f'<text class="p s" x="{pct(across(i))}" y="{base + 31}" text-anchor="middle">of {num(data["metrics"]["posts_by_day"][day])}</text>')
    told = []
    for label, by_day in series:
        points = [(across(i), down(by_day[day]["share"])) for i, day in enumerate(days) if by_day[day]["share"] is not None]
        for (x1, y1), (x2, y2) in zip(points, points[1:]):
            out.append(f'<line class="ln" x1="{pct(x1)}" y1="{y1:.1f}" x2="{pct(x2)}" y2="{y2:.1f}"/>')
        for x, y in points:
            out.append(f'<circle class="dt" cx="{pct(x)}" cy="{y:.1f}" r="2.2"/>')
        # the label sits over one end of the line: the end where the line does not climb through it
        clear_at_end = len(points) < 2 or points[-2][1] > points[-1][1] - 7
        clear_at_start = len(points) < 2 or points[1][1] > points[0][1] - 7
        if clear_at_end or not clear_at_start:
            out.append(f'<text class="halo" x="100%" y="{points[-1][1] - 9:.1f}" text-anchor="end">{esc(label)}</text>')
        else:
            out.append(f'<text class="halo" x="{pct(points[0][0])}" dx="-4" y="{points[0][1] - 9:.1f}">{esc(label)}</text>')
        told.append(label + ": " + ", ".join(
            f"{day_label(day)} {whole_pct(by_day[day]['share'])}%" for day in days if by_day[day]["share"] is not None))
    return (f'<svg class="chart" width="100%" height="{height}" role="img" aria-label="{esc(". ".join(told))}">'
            f'{"".join(out)}</svg>')


def chart_by_day_counts(chart: dict, data: dict) -> str:
    days, names = data["days"], chart["terms"]
    head, row, bar = 24, 58, 18
    height = head + row * len(names) + 4
    highest = max((data["terms"][name]["by_day"][day] for name in names for day in days), default=0) or 1
    across = [100 * (i + 0.5) / len(days) for i in range(len(days))]
    width = 100 / len(days) * 0.44
    out = [f'<text class="p" x="{pct(x)}" y="12" text-anchor="middle">{day_label(day)}</text>' for x, day in zip(across, days)]
    told = []
    for r, name in enumerate(names):
        stats = data["terms"][name]
        top = head + r * row
        base = top + row - 8
        out.append(f'<text class="w" x="0" y="{top + 13}">{esc(name)}</text>')
        out.append(f'<text class="p" x="100%" y="{top + 13}" text-anchor="end">{num(stats["posts"])} posts</text>')
        out.append(f'<line class="gr" x1="0" x2="100%" y1="{base}" y2="{base}"/>')
        for x, day in zip(across, days):
            n = stats["by_day"][day]
            if n:
                tall = max(1.0, bar * n / highest)
                out.append(f'<rect class="br" x="{pct(x - width / 2)}" y="{base - tall:.1f}" width="{pct(width)}" height="{tall:.1f}"/>')
                out.append(f'<text class="s" x="{pct(x)}" y="{base - tall - 4:.1f}" text-anchor="middle">{num(n)}</text>')
            else:
                out.append(f'<text class="p s" x="{pct(x)}" y="{base - 4}" text-anchor="middle">0</text>')
        told.append(f"{name}: " + ", ".join(f"{day_label(day)} {stats['by_day'][day]}" for day in days))
    return (f'<svg class="chart" width="100%" height="{height}" role="img" aria-label="{esc(". ".join(told))}">'
            f'{"".join(out)}</svg>')


def bar(share: float, track: bool = False) -> str:
    back = '<rect class="trk" width="100%" height="100%"/>' if track else ""
    return (f'<svg class="bar" width="100%" height="8" aria-hidden="true" focusable="false">{back}'
            f'<rect class="br" width="{pct(100 * share)}" height="100%"/></svg>')


def chart_present_absent(chart: dict, data: dict) -> str:
    total = data["metrics"]["posts_total"]
    present = "".join(
        f'<li><span class="pa-w">{esc(name)}</span>{bar(data["terms"][name]["posts"] / total)}'
        f'<span class="pa-n">{num(data["terms"][name]["posts"])}</span></li>' for name in chart["present"])
    absent = "".join(
        f'<li><span class="pa-w">{esc(name)}</span><span></span><span class="pa-n zero">0</span></li>'
        for name in data["metrics"]["absent"] if name in chart["absent_probe"])
    return f'<ul class="pa">{present}</ul><ul class="pa pa-absent">{absent}</ul>'


NAME_PART_LABELS = (("date", "carries a date code"), ("role", "carries a role word"),
                    ("institution", "carries an institution word"),
                    ("differs", "differs from the name the edit was saved under"))


def chart_name_parts(chart: dict, data: dict) -> str:
    rows = []
    for kind, label in NAME_PART_LABELS:
        part = data["name_parts"][kind]
        eg = ", ".join(part.get("most_used") or [])
        eg = f' <span class="eg">{esc(eg)}</span>' if eg else ""
        rows.append(f'<li><span class="np-l">{esc(label)}{eg}</span><span class="np-v">{whole_pct(part["share"])}%</span>'
                    f'{bar(part["share"], track=True)}</li>')
    return f'<ul class="np">{"".join(rows)}</ul>'


CHARTS = {"first_use": chart_first_use, "name_parts": chart_name_parts, "by_day_share": chart_by_day_share,
          "present_absent": chart_present_absent, "by_day_counts": chart_by_day_counts}


def sparkline(days: list[str], by_day: dict, first_time: str | None) -> str:
    """The week as a line, each day's count at its noon, with a tick at the moment of first use."""
    wide, tall, top, base = 120.0, 24.0, 3.5, 20.5
    start = parse_time(days[0] + "T00:00:00Z")
    seconds = len(days) * 86400
    highest = max(by_day.values(), default=0) or 1
    points = " ".join(f"{wide * (i + 0.5) / len(days):.1f},{base - (base - top) * by_day[day] / highest:.1f}"
                      for i, day in enumerate(days))
    tick = ""
    if first_time:
        x = wide * (parse_time(first_time) - start).total_seconds() / seconds
        tick = f'<line class="t" x1="{x:.1f}" x2="{x:.1f}" y1="1" y2="{tall - 1}"/>'
    return (f'<svg class="spark" viewBox="0 0 {wide:.0f} {tall:.0f}" preserveAspectRatio="none" aria-hidden="true" focusable="false">'
            f'<line class="b" x1="0" x2="{wide:.0f}" y1="{base}" y2="{base}"/><polyline class="l" points="{points}"/>{tick}</svg>')


def curve_line(days: list[str], stats: dict) -> str:
    """The signatures that had taken a word up, as a step line across the same week as the
    sparkline: it leaves the foot at the first use and ends at the height of all of them.
    A short mark on the foot stands at each midnight."""
    wide, tall, top, base = 240.0, 40.0, 3.0, 35.0
    start = parse_time(days[0] + "T00:00:00Z")
    seconds = len(days) * 86400
    first = (parse_time(stats["curve"]["from"]) - start).total_seconds()
    most = stats["signatures_total"] or 1
    across = lambda hours: min(wide, wide * (first + hours * 3600) / seconds)
    down = lambda n: base - (base - top) * n / most
    path = [f"M{across(0):.1f} {base:.1f}"]
    for hours, n in stats["curve"]["points"]:
        path.append(f"H{across(hours):.1f}V{down(n):.1f}")
    path.append(f"H{wide:.1f}")
    marks = "".join(f'<line class="d" x1="{wide * i / len(days):.1f}" x2="{wide * i / len(days):.1f}" y1="{base}" y2="{base + 3}"/>'
                    for i in range(len(days) + 1))
    return (f'<svg class="curve" viewBox="0 0 {wide:.0f} {tall:.0f}" preserveAspectRatio="none" aria-hidden="true" focusable="false">'
            f'<line class="b" x1="0" x2="{wide:.0f}" y1="{base}" y2="{base}"/>{marks}<path class="l" d="{"".join(path)}"/></svg>')


def pencil_stroke(seed: str) -> str:
    """A slightly irregular underline, drawn the same way every build for the same words."""
    rng = random.Random("red pencil " + seed)
    def stroke(x0: float, x1: float, mid: float, wobble: float) -> str:
        count = rng.randint(4, 6)
        tilt = rng.uniform(-0.7, 0.7)
        xs = [x0 + (x1 - x0) * i / (count - 1) for i in range(count)]
        ys = [mid + tilt * (i / (count - 1) - 0.5) + rng.uniform(-wobble, wobble) for i in range(count)]
        path = [f"M{xs[0]:.1f} {ys[0]:.2f}"]
        for i in range(count - 1):  # a Catmull-Rom curve through the points, written as cubics
            ax, ay = (xs[i - 1], ys[i - 1]) if i else (xs[0], ys[0])
            dx, dy = (xs[i + 2], ys[i + 2]) if i + 2 < count else (xs[-1], ys[-1])
            path.append(f"C{xs[i] + (xs[i + 1] - ax) / 6:.1f} {ys[i] + (ys[i + 1] - ay) / 6:.2f} "
                        f"{xs[i + 1] - (dx - xs[i]) / 6:.1f} {ys[i + 1] - (dy - ys[i]) / 6:.2f} {xs[i + 1]:.1f} {ys[i + 1]:.2f}")
        return f'<path d="{" ".join(path)}"/>'
    first = stroke(rng.uniform(-1.5, 0.5), rng.uniform(100, 102.5), 5.0, 1.1)
    second = stroke(rng.uniform(3, 12), rng.uniform(84, 97), 6.2 + rng.uniform(-0.4, 0.6), 0.8)
    return (f'<svg class="pencil" viewBox="0 0 100 10" preserveAspectRatio="none" aria-hidden="true" focusable="false">'
            f'{first}{second}</svg>')


# ---- the page -------------------------------------------------------------------

CSS = r"""
/* Layout: a single notebook leaf, one text column about 64 characters wide, with a red margin rule down the left like ruled paper; dates, counts and reader marks sit in the margin on wide screens and fold inline on phones. */
:root {
  --paper: #f5f7fa;
  --ink: #1c2538;
  --pencil: #5c6678;
  --rule: #d3dae4;
  --red: #c2292e;
  --wash: #eaeff5;
  --typed: "Courier Prime", "Courier New", Courier, "Nimbus Mono PS", monospace;
  --book: "Gentium Book Plus", "Gentium Plus", "Iowan Old Style", "Palatino Linotype", Palatino, Georgia, serif;
  --measure: 29em;
  color-scheme: light;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) { --paper: #0e131d; --ink: #e8ebf0; --pencil: #9aa5b7; --rule: #283246; --red: #f1726b; --wash: #161d2b; color-scheme: dark }
}
:root[data-theme="dark"] { --paper: #0e131d; --ink: #e8ebf0; --pencil: #9aa5b7; --rule: #283246; --red: #f1726b; --wash: #161d2b; color-scheme: dark }
body { margin: 0; background: var(--paper); color: var(--ink) }
[hidden] { display: none !important }

.leaf { max-width: 66rem; margin-inline: auto; padding-inline: max(16px, 3.5vw); padding-block: 0;
  font-family: var(--book); font-size: clamp(1.0625rem, 0.98rem + 0.34vw, 1.1875rem); line-height: 1.56;
  font-variant-numeric: lining-nums; -webkit-text-size-adjust: 100%; text-size-adjust: 100% }
.leaf *, .leaf *::before, .leaf *::after { box-sizing: border-box }
.sheet { --margin: 0rem; --gap: 0.875rem; --mn-gap: 0.85rem; min-width: 0; min-height: 100vh; min-height: 100dvh; margin-left: var(--margin);
  border-left: 1px solid var(--red); padding-left: var(--gap); padding-block: 2.25rem 4.5rem }

/* small labels: the body face in small caps (letters only; figures keep their size and are tabular) */
.lbl, .mast h1, .fam-h, .facts dt, .senses h4, .spread h4, .page-h, .reader-h .fam { font-variant-caps: all-small-caps; letter-spacing: 0.07em }
.mn { color: var(--pencil); font-size: 0.8em; line-height: 1.4; font-variant-numeric: tabular-nums lining-nums }

.mast { max-width: var(--measure); margin: 0 0 2.4rem }
.mast h1 { margin: 0; font-size: 1.14em; font-weight: 400; line-height: 1.3; letter-spacing: 0.11em }
.dek { margin: 0.35rem 0 0; font-style: italic; text-wrap: pretty }
.status { margin: 0.6rem 0 0; color: var(--pencil); font-size: 0.86em; text-wrap: pretty }

/* the sentence */
.sentences { list-style: none; margin: 0; padding: 0 }
.s { max-width: calc(var(--measure) + var(--gap)); margin-left: calc(-1 * var(--gap)); padding: 0.95rem 0 0.95rem var(--gap); border-top: 1px solid var(--rule) }
.s.is-current { max-width: none; border-top: 0; padding-top: 0; padding-bottom: 1.3rem }
.nb { white-space: nowrap }
.s blockquote { margin: 0 }
.big { margin: 0; max-width: var(--measure); font-family: var(--typed); font-size: 1em; line-height: 1.5; overflow-wrap: anywhere }
.is-current .big { max-width: 31ch; font-size: clamp(1.6rem, 0.95rem + 3vw, 2.85rem); line-height: 1.27; letter-spacing: -0.015em; text-wrap: balance }
.s-meta { margin: 0.7rem 0 0.35rem; max-width: var(--measure); color: var(--pencil); font-size: 0.86em; line-height: 1.45; overflow-wrap: anywhere }
.is-current .s-meta { margin-top: 1.25rem }
.s-meta > span + span::before { content: " \00b7  " }
.sig, .page { font-family: var(--typed); font-size: 0.94em }
.sig { color: var(--ink) }
.s-meta .mn { font-size: 1em }
.gloss { margin: 0; max-width: var(--measure); text-wrap: pretty }
.again { display: flex; align-items: baseline; gap: 0.75rem; margin: 0; padding-left: 0; color: var(--pencil); font-size: 0.86em; font-variant-numeric: tabular-nums lining-nums }
.another { margin: 0; padding: 0 0 1px; border: 0; border-bottom: 1px solid var(--pencil); border-radius: 0; background: none; color: inherit; font: inherit; font-style: italic; cursor: pointer }
.another:hover { color: var(--ink); border-bottom-color: var(--ink) }

/* a leaf of its own: the sentence, what its writers were doing, and the way on to the pages */
.leaf .mast h1 a, .leaf a.another, .leaf a.door { text-decoration: none }
.ctx { margin: 1.1rem 0 0; max-width: calc(var(--measure) / 0.93); font-size: 0.93em; line-height: 1.5; text-wrap: pretty }
.contents ol { list-style: none; margin: 0; padding: 0 }
.contents li { padding-block: 0.2rem }
.sec h2.page-h { margin: 0; font-size: 1em; color: var(--pencil); letter-spacing: 0.07em }
.page-head + .sec { margin-top: 1.1rem }
.turn { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 0.3rem 2rem; color: var(--pencil) }
.sec.turn p { margin: 0; font-size: 0.86em; line-height: 1.45 }
.sec.turn .next { margin-left: auto; text-align: right }
.leaf .turn a { color: var(--ink) }
.turn + .colophon { margin-top: 1.5rem }

/* a door: a word of theirs underlined in red pencil */
.door { position: relative; display: inline-block; margin: 0; padding: 0; border: 0; border-radius: 0; background: none; color: inherit;
  font: inherit; letter-spacing: inherit; text-align: inherit; white-space: nowrap; cursor: pointer; -webkit-appearance: none; appearance: none }
.door:hover { background: var(--wash) }
.pencil { position: absolute; left: -0.05em; bottom: -0.2em; width: calc(100% + 0.1em); height: 0.36em; overflow: visible; color: var(--red); pointer-events: none }
.pencil path { fill: none; stroke: currentColor; stroke-width: max(1.2px, 0.055em); stroke-linecap: round; stroke-linejoin: round; vector-effect: non-scaling-stroke }
.pencil path + path { stroke-width: max(0.8px, 0.03em); opacity: 0.55 }

.leaf :focus-visible { outline: 2px solid var(--ink); outline-offset: 3px }

/* sections: a ruled line that meets the margin rule */
.sec { max-width: calc(var(--measure) + var(--gap)); margin: 3.1rem 0 0 calc(-1 * var(--gap)); padding: 1.1rem 0 0 var(--gap); border-top: 1px solid var(--rule) }
.sec.opening { max-width: none; margin-top: 0; padding-top: 0; border-top: 0 }
.sec h2 { margin: 0 0 0.7em; font-size: 1.55em; font-weight: 400; line-height: 1.18; text-wrap: balance }
.sec p { margin: 0 0 0.9em; text-wrap: pretty }
.sec p:last-child { margin-bottom: 0 }
.leaf a { color: inherit; text-decoration: underline; text-decoration-color: var(--pencil); text-decoration-thickness: 1px; text-underline-offset: 0.18em }
.leaf a:hover { text-decoration-color: var(--ink) }
.small, .sources .by, .q-meta, figcaption, .colophon p { font-size: 0.86em; line-height: 1.45 }
.sources { list-style: none; margin: 1.3rem 0 0; padding: 0; display: grid; gap: 0.7rem }
.sources .by { display: block; color: var(--pencil); margin-top: 0.1rem }
.note { margin-top: 1.2rem; font-style: italic; color: var(--pencil) }
.sec p.note { margin-top: 1.2rem }

/* charts */
.fig { margin: 1.5rem 0 0 }
.fig-scroll { overflow-x: auto; overflow-y: hidden }
figcaption { margin-top: 0.6rem; font-style: italic; color: var(--pencil) }
.chart { display: block; overflow: visible }
.chart text { font-family: var(--book); font-size: 12.5px; fill: var(--ink); font-variant-numeric: tabular-nums lining-nums }
.chart .p { fill: var(--pencil) }
.chart .s { font-size: 11px }
.chart .w { font-family: var(--typed); font-size: 12.5px }
.chart .halo { paint-order: stroke; stroke: var(--paper); stroke-width: 5px; stroke-linejoin: round }
.chart .halo tspan { stroke: var(--paper) }
.chart .ax { stroke: var(--pencil); stroke-width: 1 }
.chart .gr { stroke: var(--rule); stroke-width: 1 }
.chart .ld { stroke: var(--pencil); stroke-width: 1; stroke-dasharray: 1 3 }
.chart .tk { stroke: var(--ink); stroke-width: 2 }
.chart .ln { stroke: var(--ink); stroke-width: 1.4; stroke-linecap: round }
.chart .dt, .br { fill: var(--ink) }
.trk { fill: var(--rule) }
.bar { display: block }
.pa, .np { list-style: none; margin: 0; padding: 0 }
.pa li { display: grid; grid-template-columns: 7.2em minmax(0, 1fr) 3.4em; align-items: center; column-gap: 0.7rem; padding-block: 0.2rem; border-bottom: 1px solid var(--rule) }
.pa li:first-child, .np li:first-child { border-top: 1px solid var(--rule) }
.pa-absent { margin-top: 0.7rem }
.pa-w { font-family: var(--typed); font-size: 0.86em }
.pa-n, .np-v { text-align: right; font-size: 0.86em; font-variant-numeric: tabular-nums lining-nums }
.pa-n.zero { color: var(--pencil) }
.np li { display: grid; grid-template-columns: minmax(0, 1fr) auto; align-items: baseline; column-gap: 0.8rem; row-gap: 0.3rem; padding: 0.45rem 0 0.6rem; border-bottom: 1px solid var(--rule) }
.np-l { font-size: 0.9em; line-height: 1.35 }
.np .bar { grid-column: 1 / -1 }
.eg { font-family: var(--typed); font-size: 0.9em; color: var(--pencil); white-space: nowrap }

/* their words */
.fam-h { margin: 2rem 0 0.5rem; font-size: 1em; font-weight: 400; color: var(--pencil) }
.w { margin-left: calc(-1 * var(--gap)); padding-left: var(--gap); border-top: 1px solid var(--rule); scroll-margin-top: 1rem }
.fam .w:last-child { border-bottom: 1px solid var(--rule) }
.w[open] { background: var(--wash) }
.w > summary { position: relative; display: grid; grid-template-columns: 0.75em minmax(0, 1fr) auto auto; align-items: baseline; column-gap: 0.6rem; padding-block: 0.4rem; list-style: none; cursor: pointer }
.w > summary::-webkit-details-marker { display: none }
.w > summary::before { content: "+"; font-family: var(--typed); color: var(--pencil) }
.w[open] > summary::before { content: "\2212" }
.w-word { min-width: 0; font-family: var(--typed); font-weight: 700; overflow-wrap: anywhere }
.w-count { color: var(--pencil); font-size: 0.86em; white-space: nowrap; font-variant-numeric: tabular-nums lining-nums }
.w-count b { font-weight: 400; color: var(--ink) }
.w-spark { align-self: center }
.spark { display: block; width: 5.25rem; height: 1.4rem; overflow: visible }
.spark line, .spark polyline { vector-effect: non-scaling-stroke }
.spark .b { stroke: var(--rule); stroke-width: 1 }
.spark .l { fill: none; stroke: var(--ink); stroke-width: 1.2; stroke-linejoin: round }
.spark .t { stroke: var(--red); stroke-width: 1.75 }
.w-body { padding: 0.15rem 0.6rem 1.2rem calc(0.75em + 0.6rem) }
.facts { display: grid; grid-template-columns: auto minmax(0, 1fr); column-gap: 0.9rem; row-gap: 0.25rem; margin: 0; font-size: 0.9em; line-height: 1.45 }
.facts dt { color: var(--pencil); white-space: nowrap }
.facts dd { margin: 0; min-width: 0; overflow-wrap: anywhere }
.wk { display: grid; grid-template-columns: repeat(auto-fit, minmax(2.6em, 1fr)); gap: 0.1rem 0.3rem; list-style: none; margin: 0; padding: 0; max-width: 24em }
.wk li { display: grid }
.wk .d { color: var(--pencil); font-size: 0.82em; white-space: nowrap }
.wk .n { font-variant-numeric: tabular-nums lining-nums }
.passages { display: grid; gap: 0.95rem; list-style: none; margin: 1rem 0 0; padding: 0.9rem 0 0; border-top: 1px solid var(--rule) }
.q { margin: 0; font-family: var(--typed); font-size: 0.9em; line-height: 1.5; overflow-wrap: anywhere }
.q .quote { white-space: pre-wrap }
.q b { font-weight: 700 }
.q .el, .q .qm { color: var(--pencil) }
.sec p.q { margin: 0 }
.sec p.q-meta { margin: 0.25rem 0 0; color: var(--pencil); overflow-wrap: anywhere }
.senses { margin-top: 1rem; padding-top: 0.8rem; border-top: 1px solid var(--rule) }
.senses h4 { margin: 0 0 0.15rem; font-size: 0.9em; font-weight: 400; color: var(--pencil) }
.senses p { font-size: 0.94em; line-height: 1.5 }

/* how a word spread: a line of figures, the curve of signatures across the week, the first to sign it */
.spread { margin-top: 1rem; padding-top: 0.8rem; border-top: 1px solid var(--rule) }
.spread h4 { margin: 0 0 0.15rem; font-size: 0.9em; font-weight: 400; color: var(--pencil) }
.spread p { font-size: 0.94em; line-height: 1.5; font-variant-numeric: tabular-nums lining-nums }
.curve { display: block; width: 100%; height: 2.6rem; margin-top: 0.7rem; overflow: visible }
.curve line, .curve path { vector-effect: non-scaling-stroke }
.curve .b { stroke: var(--rule); stroke-width: 1 }
.curve .d { stroke: var(--pencil); stroke-width: 1 }
.curve .l { fill: none; stroke: var(--ink); stroke-width: 1.3; stroke-linejoin: round }
.sec p.curve-ax { display: flex; justify-content: space-between; margin: 0.1rem 0 0.9rem; color: var(--pencil); font-size: 0.82em; line-height: 1.4 }
.first { list-style: none; margin: 0; padding: 0 }
.first li { margin-left: calc(-0.75em - 0.6rem); padding: 0.1rem 0 0.1rem calc(0.75em + 0.6rem); line-height: 1.5 }
.first .sig { overflow-wrap: anywhere }
.first .mn { display: block; white-space: nowrap }
.fast { margin: 1.6rem 0 0 }
.fast .fam-h { margin-top: 0 }
.fast table { width: 100%; border-collapse: collapse; font-size: 0.9em; line-height: 1.4; font-variant-numeric: tabular-nums lining-nums }
.fast th, .fast td { padding: 0.32rem 0 0.32rem 0.6rem; border-top: 1px solid var(--rule); text-align: right; font-weight: 400; white-space: nowrap }
.fast tr:last-child th, .fast tr:last-child td { border-bottom: 1px solid var(--rule) }
.fast thead th { color: var(--pencil); font-size: 0.9em; border-top: 0; white-space: normal; vertical-align: bottom }
.fast th:first-child, .fast td:first-child { padding-left: 0; text-align: left; white-space: normal }
.fast th:nth-child(2), .fast td:nth-child(2) { text-align: left }
.fast tbody th { font-family: var(--typed); font-weight: 700 }
.sec p.fast-note { margin: 0.5rem 0 0; color: var(--pencil) }

/* readers */
.reader { position: relative; margin-top: 1.6rem; padding-top: 1rem; border-top: 1px solid var(--rule) }
.reader-h { margin: 0; font-family: var(--book); font-size: 1em; font-weight: 700; color: var(--ink) }
.reader-h .fam { font-weight: 400; color: var(--pencil) }
.reader-facts { font-variant-numeric: tabular-nums lining-nums }
.sec p.reader-facts { margin: 0.2rem 0 0.4rem }
.lens { display: block; font-family: var(--typed); font-size: 0.78em; color: var(--pencil); overflow-wrap: anywhere }
.signed-as { color: var(--pencil) }
.signed-as span { color: var(--ink); white-space: nowrap }
.fnote { margin: 1.1rem 0 0 }
.fnote p { font-size: 0.94em; line-height: 1.52; white-space: pre-line }
.sec .fnote p.mn { margin: 0.3rem 0 0; font-size: 0.8em; font-style: italic; white-space: normal }
.fnote p.mn::before { content: "\2014\2009" }

.plain { list-style: none; margin: 0 0 1.4rem; padding: 0 }
.plain li { padding: 0.5rem 0; border-top: 1px solid var(--rule) }
.plain li:last-child { border-bottom: 1px solid var(--rule) }
.held { font-style: italic }
.colophon { color: var(--pencil) }
.colophon p { margin: 0 0 0.45em }
.digest { overflow-wrap: anywhere; font-variant-numeric: tabular-nums lining-nums }

@media (min-width: 54rem) {
  .sheet { --margin: 10rem; --gap: 1.75rem; padding-block: 3.5rem 6rem }
  .has-mn { position: relative }
  .mn { position: absolute; top: 0; right: calc(100% + var(--gap) + var(--mn-gap)); width: calc(var(--margin) - var(--mn-gap)); margin: 0; text-align: right }
  .s-meta .mn { top: 0.45rem; font-size: 0.93em }
  .is-current .s-meta .mn { top: 0.8rem }
  .s-meta > .mn::before { content: none }
  .s-meta .mn .d, .s-meta .mn .t { display: block }
  .s-meta .mn .c { display: none }
  .w > summary { grid-template-columns: 0.75em minmax(0, 1fr) auto }
  .w-count.mn { top: 0.4rem; font-size: 0.86em; line-height: 1.82 }
  .chart text, .chart .w { font-size: 13.5px }
  .chart .s { font-size: 12px }
  .spark { width: 8rem }
  .first li { padding-block: 0 }
  .first .mn { top: 0.3rem }
  .reader-h.mn { top: 1rem; font-size: 0.86em; line-height: 1.5; color: var(--ink) }
  .reader-h.mn .fam { display: block }
  .sec .fnote p.mn { top: 0.15rem; margin: 0 }
  .fnote p.mn::before { content: none }
}
@media (prefers-reduced-motion: reduce) {
  .leaf *, .leaf *::before, .leaf *::after { transition: none !important; animation: none !important; scroll-behavior: auto !important }
}
"""

CSS += (HERE / "notebook.css").read_text(encoding="utf-8")

# The script is in two parts. The one page takes both: it turns the sentences over in place and
# its doors are buttons. The site takes only the second, on the page of words: its leaves are
# plain pages and its doors are links, so they need no script at all.

JS_TURNING = r"""
  var items = Array.prototype.slice.call(leaf.querySelectorAll('.sentences > .s'));
  var another = leaf.querySelector('.another');
  var place = leaf.querySelector('.s-place');
  var list = leaf.querySelector('.sentences');
  var current = 0;
  function show(index) {
    current = (index + items.length) % items.length;
    items.forEach(function (item, i) {
      item.hidden = i !== current;
      item.classList.toggle('is-current', i === current);
    });
    if (place) place.textContent = (current + 1) + ' / ' + items.length;
  }
  if (items.length > 1 && another) {
    show(Math.floor(Math.random() * items.length));
    another.hidden = false;
    if (list) list.setAttribute('aria-live', 'polite');
    another.addEventListener('click', function () { show(current + 1); });
  }
  leaf.addEventListener('click', function (event) {
    var door = event.target.closest ? event.target.closest('button.door') : null;
    if (door) openEntry(door.getAttribute('data-entry'), true);
  });
"""

JS_ENTRIES = r"""
  var still = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  function openEntry(id, focus) {
    var entry = id ? document.getElementById(id) : null;
    if (!entry || !leaf.contains(entry)) return;
    if (entry.tagName === 'DETAILS') entry.open = true;
    entry.scrollIntoView({ behavior: still ? 'auto' : 'smooth', block: 'start' });
    var summary = entry.querySelector('summary');
    if (focus && summary) summary.focus({ preventScroll: true });
  }
  function named(hash) {
    var id = (hash || '').slice(1);
    try { id = decodeURIComponent(id); } catch (error) {}
    return id;
  }
  leaf.addEventListener('click', function (event) {
    var link = event.target.closest ? event.target.closest('a[href^="#"]') : null;
    if (link) openEntry(named(link.getAttribute('href')), false);
  });
  function fromHash() {
    var id = named(window.location.hash);
    if (id) openEntry(id, false);
  }
  window.addEventListener('hashchange', fromHash);
  fromHash();
"""


def script(turning: bool) -> str:
    return ("\n(function () {\n  var leaf = document.querySelector('.leaf');\n  if (!leaf) return;"
            + (JS_TURNING if turning else "") + JS_ENTRIES + "})();\n")


BOARD_JS = r"""
(function () {
  function openRecord() {
    var id; try { id = decodeURIComponent(location.hash.slice(1)); } catch (_) { return; }
    var el = document.getElementById(id);
    if (!el) return;
    var parent = el.parentElement;
    while (parent) { if (parent.tagName === 'DETAILS') parent.open = true; parent = parent.parentElement; }
    if (location.hash) requestAnimationFrame(function () { el.scrollIntoView({block:'start'}); });
  }
  addEventListener('hashchange', openRecord); openRecord();
})();
"""

def when_html(stamp: str | None, margin: bool = False) -> str:
    if not stamp:
        return ""
    inner = (f'<time datetime="{esc(stamp)}"><span class="d">{long_date(stamp)}</span><span class="c">, </span>'
             f'<span class="t">{clock(stamp)} UTC</span></time>')
    return f'<span class="when{" mn" if margin else ""}">{inner}</span>'


def sentence_body(sentence: dict, words_file: str | None = None, buttons: bool = True) -> str:
    """The quotation with its doors, the signature and page beneath it, and the gloss.

    On the one page a door is a button the script answers. On a leaf it is a link to the word's
    entry on the page of words (`words_file`), which needs no script; where no page shows the
    words, the door stays plain text. Every element that holds quoted words carries `data-quote`
    with its unit, so the finished pages can be checked against the source.
    """
    quote, at, pieces = sentence["quote"], 0, []
    doors = sentence["doors"]
    for d, door in enumerate(doors):
        if not door["entry"] or not (words_file or buttons):
            continue  # no entry to open: the words stay plain text
        # A door does not break across lines, and takes the punctuation that touches it
        # along ("vanishes," "R5/termination."), so a line never opens on a comma.
        limit = doors[d + 1]["start"] if d + 1 < len(doors) else len(quote)
        lo, hi = door["start"], door["end"]
        while lo > at and not quote[lo - 1].isspace() and door["start"] - lo < 6:
            lo -= 1
        if lo > at and not quote[lo - 1].isspace():
            lo = door["start"]  # a long run before it: leave the line free to break there
        while hi < limit and not quote[hi].isspace() and hi - door["end"] < 6:
            hi += 1
        if hi < limit and not quote[hi].isspace():
            hi = door["end"]
        told = f'{esc(door["term"])}: {num(door["posts"])} signed posts'
        stroke = pencil_stroke(sentence["unit"] + door["text"])
        if words_file:
            button = (f'<a class="door" href="{esc(words_file)}#{esc(door["entry"])}" '
                      f'title="{told}">{esc(door["text"])}{stroke}</a>')
        else:
            button = (f'<button type="button" class="door" data-entry="{esc(door["entry"])}" '
                      f'title="{told}">{esc(door["text"])}{stroke}</button>')
        pieces.append(esc(quote[at:lo]))
        if lo < door["start"] or hi > door["end"]:
            pieces.append(f'<span class="nb">{esc(quote[lo:door["start"]])}{button}{esc(quote[door["end"]:hi])}</span>')
        else:
            pieces.append(button)
        at = hi
    pieces.append(esc(quote[at:]))
    if sentence["signature"]:
        who = f'<span class="sig">{esc(sentence["signature"])}</span>'
    else:
        saved = f', saved as <span class="page">{esc(sentence["saved_as"])}</span>' if sentence["saved_as"] else ""
        who = f'<span>unsigned{saved}</span>'
    return (f'<blockquote><p class="big" data-quote="{esc(sentence["unit"])}">{"".join(pieces)}</p></blockquote>'
            f'<p class="s-meta">{who}{when_html(sentence["time"], margin=True)}'
            f'<span>page <span class="page">{esc(sentence["page"])}</span></span></p>'
            f'<p class="gloss">{esc(sentence["gloss"])}</p>')


def render_sentences(data: dict, another: str) -> str:
    """The one page: every sentence in a list, turned over in place by the script."""
    items = [f'<li class="s{" is-current" if index == 0 else ""}" data-unit="{esc(sentence["unit"])}"><div class="has-mn">'
             f'{sentence_body(sentence)}</div></li>' for index, sentence in enumerate(data["sentences"])]
    return (f'<section class="sec opening" id="sentence" aria-label="A sentence from the wiki">'
            f'<ol class="sentences">{"".join(items)}</ol>'
            f'<p class="again"><button type="button" class="another" hidden>{esc(another)}</button>'
            f'<span class="s-place"></span></p></section>')


def render_leaf(index: int, copy: dict, data: dict, fill, site: dict) -> str:
    """A leaf: one sentence, the standing line that says what its writers were doing, the way to
    the next sentence, and the list of pages. Nothing here needs script."""
    sentence, total = data["sentences"][index], len(data["sentences"])
    context = ""
    if copy.get("leaf_context"):
        more = (f' <a href="{esc(site["context"]["file"])}">{esc(site["context"]["title"])}</a>'
                if site["context"] else "")
        context = f'<p class="ctx">{esc(fill(copy["leaf_context"]))}{more}</p>'
    again = ""
    if total > 1:
        again = (f'<p class="again"><a class="another" href="{esc(site["leaves"][(index + 1) % total])}">'
                 f'{esc(site["another"])}</a><span class="s-place">{index + 1} / {total}</span></p>')
    contents = "".join(f'<li><a href="{esc(page["file"])}">{esc(page["title"])}</a></li>' for page in site["pages"])
    contents = f'<nav class="sec contents" id="contents"><p class="eyebrow">{esc(copy["nav"]["section"])}</p><ol>{contents}</ol></nav>' if contents else ""
    return (f'<main id="main"><section class="sec opening" id="sentence" aria-label="A sentence from the wiki">'
            f'<div class="s is-current" data-unit="{esc(sentence["unit"])}"><div class="has-mn">'
            f'{sentence_body(sentence, site["words"], buttons=False)}{context}</div></div>{again}</section>'
            f'{contents}</main>')


def render_what_happened(block: dict, fill) -> str:
    paragraphs = "".join(f"<p>{esc(fill(p))}</p>" for p in block["paragraphs"])
    sources = "".join(
        f'<li><a href="{esc(source["url"])}">{esc(fill(source["label"]))}</a>'
        f'<span class="by">{esc(fill(source["by"]))}</span></li>' for source in block.get("sources", []))
    note = f'<p class="note small">{esc(fill(block["attribution_note"]))}</p>' if block.get("attribution_note") else ""
    return (f'<section class="sec" id="what-happened"><h2>{esc(fill(block["heading"]))}</h2>{paragraphs}'
            f'<ul class="sources">{sources}</ul>{note}</section>')


def render_takeaways(takeaways: list, data: dict, fill) -> str:
    out = []
    for takeaway in takeaways:
        chart = takeaway.get("chart") or {}
        figure = ""
        if chart:
            if chart.get("type") not in CHARTS:
                raise BuildError(f"takeaway {takeaway['id']!r} asks for an unknown chart: {chart.get('type')!r}")
            drawn = CHARTS[chart["type"]](chart, data)
            figure = (f'<figure class="fig fig-{esc(chart["type"])}"><div class="fig-scroll">{drawn}</div>'
                      f'<figcaption>{esc(fill(chart.get("caption", "")))}</figcaption></figure>')
        out.append(f'<section class="sec take" id="t-{esc(takeaway["id"])}"><h2>{esc(fill(takeaway["headline"]))}</h2>'
                   f'<p>{esc(fill(takeaway["body"]))}</p>{figure}</section>')
    return "".join(out)


def render_passage(passage: dict) -> str:
    text, (a, b) = passage["text"], passage["match"]
    before = '<span class="el">… </span>' if passage["cut_before"] else ""
    after = '<span class="el"> …</span>' if passage["cut_after"] else ""
    quote = f'{esc(text[:a])}<b>{esc(text[a:b])}</b>{esc(text[b:])}'
    return (f'<li><p class="q">{before}<span class="qm">“</span>'
            f'<span class="quote" data-quote="{esc(passage["unit"])}">{quote}</span>'
            f'<span class="qm">”</span>{after}</p>'
            f'<p class="q-meta"><span class="sig">{esc(passage["signature"])}</span> · {short_date(passage["time"])}, '
            f'{clock(passage["time"])} UTC · page <span class="page">{esc(passage["page"])}</span> · '
            f'<span class="page">{esc(passage["unit"])}</span></p></li>')


def render_spread(labels: dict, stats: dict, days: list[str], fills: dict) -> str:
    """How a word crossed signatures: a line of figures filled from its counts, the curve of
    signatures across the week, and the first signatures to use it, each with its time."""
    if not labels or not stats.get("curve"):
        return ""
    own = {"signatures_total": num(stats["signatures_total"]), "pages_total": num(stats["pages_total"]),
           **{f"by_{key}": num(n) for key, n in stats["adoption"].items()},
           **{f"posting_{key}": num(n) for key, n in stats["posting"].items()}}
    fill = filler({**fills, **own})
    firsts = "".join(
        f'<li class="has-mn"><span class="sig">{esc(item["signature"])}</span>'
        f'<time class="mn" datetime="{esc(item["time"])}">{day_label(item["time"][:10])}, {clock(item["time"])}</time></li>'
        for item in stats["first_signatures"])
    return (f'<div class="spread"><h4>{esc(fill(labels["heading"]))}</h4><p>{esc(fill(labels["line"]))}</p>'
            f'{curve_line(days, stats)}<p class="curve-ax"><span>{day_label(days[0])}</span>'
            f'<span>{day_label(days[-1])}</span></p>'
            f'<h4>{esc(fill(labels["first_label"]))}</h4><ol class="first">{firsts}</ol></div>')


def render_fastest(labels: dict, data: dict, fill) -> str:
    """The entries that crossed most signatures in their first hour, each a link to its entry: when
    it was first used, how many signatures used it within the hour and out of how many posting,
    and how many used it in all."""
    if not labels or not data["fastest"]:
        return ""
    soon = f"{SPREAD_HOURS[0]}h"
    within = lambda item: esc(filler({"by": num(item["adoption"][soon]), "posting": num(item["posting"][soon])})(labels["hour_cell"]))
    head = "".join(f'<th scope="col">{esc(fill(labels[key]))}</th>'
                   for key in ("term_label", "first_label", "hour_label", "total_label"))
    rows = "".join(
        f'<tr><th scope="row"><a href="#{esc(item["id"])}">{esc(item["term"])}</a></th>'
        f'<td><time datetime="{esc(item["first"])}">{day_label(item["first"][:10])}, {clock(item["first"])}</time></td>'
        f'<td>{within(item)}</td><td>{num(item["signatures_total"])}</td></tr>' for item in data["fastest"])
    note = f'<p class="fast-note small">{esc(fill(labels["note"]))}</p>' if labels.get("note") else ""
    return (f'<div class="fast"><h3 class="fam-h">{esc(fill(labels["heading"]))}</h3>'
            f'<div class="fig-scroll"><table><thead><tr>{head}</tr></thead><tbody>{rows}</tbody></table></div>{note}</div>')


def render_words(block: dict, data: dict, fill) -> str:
    days = data["days"]
    spread = block.get("spread") or {}
    for key in ("heading", "line", "first_label"):
        if spread and not spread.get(key):
            raise BuildError(f"words.spread needs {key!r}")
    fastest = block.get("fastest") or {}
    for key in ("heading", "term_label", "first_label", "hour_label", "hour_cell", "total_label"):
        if fastest and not fastest.get(key):
            raise BuildError(f"words.fastest needs {key!r}")
    families = []
    for family in data["words"]:
        entries = []
        for entry in family["entries"]:
            stats = data["terms"][entry["term"]]
            first = stats["first"]
            facts = ""
            if first:
                facts += (f'<dt>first use</dt><dd>{short_date(first["time"])}, {clock(first["time"])} UTC · '
                          f'<span class="sig">{esc(first["signature"])}</span> · page '
                          f'<span class="page">{esc(first["page"])}</span></dd>')
            week = "".join(f'<li><span class="d">{day_label(day)}</span><span class="n">{num(stats["by_day"][day])}</span></li>'
                           for day in days)
            facts += (f'<dt>signatures</dt><dd>{num(stats["signatures"])}</dd>'
                      f'<dt>by day</dt><dd><ol class="wk">{week}</ol></dd>')
            passages = "".join(render_passage(p) for p in entry["passages"])
            passages = f'<ol class="passages">{passages}</ol>' if passages else ""
            senses = ""
            if entry["senses_left_open"]:
                senses = (f'<div class="senses"><h4>senses left open</h4>'
                          f'<p>{esc(entry["senses_left_open"]["text"])}</p></div>')
            entries.append(
                f'<details class="w" id="{esc(entry["id"])}"><summary class="has-mn">'
                f'<span class="w-word">{esc(entry["term"])}</span>'
                f'<span class="w-count mn"><b>{num(stats["posts"])}</b> <span class="lbl">posts</span></span>'
                f'<span class="w-spark">{sparkline(days, stats["by_day"], first["time"] if first else None)}</span></summary>'
                f'<div class="w-body"><dl class="facts">{facts}</dl>{passages}'
                f'{render_spread(spread, stats, days, data["placeholders"])}{senses}</div></details>')
        families.append(f'<div class="fam"><h3 class="fam-h">{esc(family["name"])}</h3>{"".join(entries)}</div>')
    notes = " ".join(fill(spread[key]) for key in ("caveat", "posting_note") if spread.get(key))
    caveat = f'<p class="note small">{esc(notes)}</p>' if notes else ""
    return (f'<section class="sec" id="their-words"><h2>{esc(fill(block["heading"]))}</h2>'
            f'<p>{esc(fill(block["intro"]))}</p>{caveat}{render_fastest(fastest, data, fill)}{"".join(families)}</section>')


def render_readers(block: dict, data: dict, fill) -> str:
    out = [f'<section class="sec" id="readers"><h2>{esc(fill(block["heading"]))}</h2>']
    out += [f"<p>{esc(fill(p))}</p>" for p in block["paragraphs"]]
    out.append(f'<p><a href="what-happened.html">{esc(block["source_label"])}</a> · <a href="{esc(block["board_href"])}">{esc(block["board_link"])}</a></p>')
    out.append(f'<p class="note small">{esc(block["model_note"])}</p>')
    out.append(f'<h3>{esc(block["exchange_heading"])}</h3><p class="small">{esc(block["exchange_note"])}</p>')
    for item in block.get('exchanges',[]):
        out.append(f'<article class="exchange"><p class="eyebrow">{esc(item["label"])}</p><blockquote class="message"><div class="exact" data-quote="{esc(item["memo"])}">{esc(item["quote"])}</div></blockquote><p class="signature">{esc(item["signed"])}</p><p class="small"><a href="{data["board_locations"][item["memo"]]}">{esc(block["exchange_link"])}</a></p></article>')
    out.append(f'<p>{esc(block["signatures_note"])}</p>')
    for reader in data["readers"]:
        signed = ", ".join(f'<span>{esc(item["signed"])} ×{item["notes"]}</span>' for item in reader["signatures"])
        notes = "".join(f'<div class="fnote"><div class="exact" data-quote="{esc(note["memo"])}">{esc(note["body"])}</div><p class="signature">{esc(note["signed"] or block["unsigned"])}</p></div>' for note in reader["notes"])
        out.append(f'<article class="reader"><p class="eyebrow">{esc(reader["family"])}</p><h3>{esc(reader["model"])}</h3>'
                   f'<p class="reader-facts">{num(reader["units_read"])} {esc(block["units_label"])} · {num(reader["codings"])} {esc(block["codings_label"])} · {num(reader["field_notes"])} {esc(block["notes_label"])}</p>'
                   f'<details><summary>{esc(block["signatures_label"])}</summary><p class="signed-as small">{signed}</p></details>'
                   f'<details><summary>{esc(block["sample_label"])}</summary><p class="small">{esc(block["sample_note"])}</p>{notes}</details>'
                   f'{render_apparatus(reader["apparatus"], block["provenance_label"])}</article>')
    out.append("</section>")
    return "".join(out)


def render_reading(block: dict, fill) -> str:
    body = f'<p class="held">{esc(fill(block.get("note", "")))}</p>'
    if block.get("status") != "held":
        body += "".join(f"<p>{esc(fill(p))}</p>" for p in block.get("paragraphs", []))
    return f'<section class="sec" id="reading"><h2>{esc(fill(block["heading"]))}</h2>{body}</section>'


def render_limits(block: dict, fill) -> str:
    lists = "".join(f'<ul class="plain">{"".join(f"<li>{esc(fill(item))}</li>" for item in block.get(key, []))}</ul>'
                    for key in ("adds", "cannot") if block.get(key))
    risk = f'<p>{esc(fill(block["own_risk"]))}</p>' if block.get("own_risk") else ""
    return f'<section class="sec" id="limits"><h2>{esc(fill(block["heading"]))}</h2>{lists}{risk}</section>'


def render_colophon(block: dict, data: dict, fill, status: str | None = None) -> str:
    """The foot. On the pages of the site the note on the state of the draft is its first line;
    on the one page that note stands in the masthead, as it did."""
    lines = [f"<p>{esc(fill(status))}</p>"] if status else []
    for line in block.get("lines", []):
        if "{ledger_digest}" in line:
            if not data["metrics"]["ledger_digest"]:
                continue  # no fingerprint to show: the line is left out
            head, tail = line.split("{ledger_digest}", 1)
            lines.append(f'<p>{esc(fill(head))}<span class="digest">{esc(data["metrics"]["ledger_digest"])}</span>{esc(fill(tail))}</p>')
        else:
            lines.append(f"<p>{esc(fill(line))}</p>")
    return f'<footer class="sec colophon" id="colophon">{"".join(lines)}</footer>'


# ---- blocks, and the pages made of them --------------------------------------------
#
# A block is one part of the copy and the function that sets it. The one page shows every block
# in ONE_PAGE, in that order. The site shows, on each page, the blocks its entry in `pages`
# names. `site` is the plan of the site, or None on the one page.

BLOCKS = {
    "what_happened": lambda copy, data, fill, site: render_what_happened(copy["what_happened"], fill),
    "takeaways": lambda copy, data, fill, site: render_takeaways(copy["takeaways"], data, fill),
    "words": lambda copy, data, fill, site: render_words(copy["words"], data, fill),
    "readers": lambda copy, data, fill, site: render_readers(copy["readers"], data, fill),
    "reading": render_theory,
    "board": render_board,
    "method": render_method,
    "engine": render_engine,
    "culture": render_culture,
    "limits": lambda copy, data, fill, site: render_limits(copy["limits"], fill),
    "colophon": lambda copy, data, fill, site: render_colophon(
        copy["colophon"], data, fill, status=copy.get("status_note") if site else None),
}
ONE_PAGE = ("what_happened", "takeaways", "words", "readers", "reading", "limits", "colophon")


def render_prose(name: str, block: dict, fill) -> str:
    """A block that is only words: a heading, paragraphs, and a closing note where there is one."""
    heading = f'<h2>{esc(fill(block["heading"]))}</h2>' if block.get("heading") else ""
    paragraphs = "".join(f"<p>{esc(fill(p))}</p>" for p in block["paragraphs"])
    note = f'<p class="note small">{esc(fill(block["note"]))}</p>' if block.get("note") else ""
    return f'<section class="sec" id="{esc(name.replace("_", "-"))}">{heading}{paragraphs}{note}</section>'


def render_block(name: str, copy: dict, data: dict, fill, site: dict | None) -> str:
    if name in BLOCKS:
        if name not in copy:
            raise BuildError(f"the copy has nothing under {name!r}, which a page is to show")
        return BLOCKS[name](copy, data, fill, site)
    block = copy.get(name)
    if isinstance(block, dict) and block.get("paragraphs"):
        return render_prose(name, block, fill)
    raise BuildError(f"a page names the block {name!r}: it has no renderer in BLOCKS, and the copy "
                     f"holds no heading and paragraphs under that name")


def masthead(copy: dict, fill, home: str | None = None, status: bool = False) -> str:
    """The title and the line under it. On every page but the front door the title leads back to it."""
    title = esc(fill(copy["title"]))
    if home:
        title = f'<a href="{esc(home)}">{title}</a>'
    note = f'<p class="status">{esc(fill(copy["status_note"]))}</p>' if status and copy.get("status_note") else ""
    return f'<header class="mast"><h1>{title}</h1><p class="dek">{esc(fill(copy["dek"]))}</p>{note}</header>'


def sheet(body: str) -> str:
    return f'<div class="leaf"><div class="sheet">{body}</div></div>'


def head_tags(title: str, style: str) -> str:
    return f'<title>{esc(title)}</title>\n{style}\n'


def document(description: str, head: str, content: str, scripts: str = "") -> str:
    return ("<!doctype html>\n"
            '<html lang="en">\n<head>\n<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
            f'<meta name="description" content="{esc(description)}">\n'
            f"{head}</head>\n<body>\n{content}\n{scripts}</body>\n</html>\n")


def render_single(copy: dict, data: dict) -> dict:
    """The one page: the sentences turned over in place, then every block, in one document."""
    fill = filler(data["placeholders"])
    title = fill(copy["title"])
    another = fill((copy.get("nav") or {}).get("another") or "another")
    content = sheet(masthead(copy, fill, status=True) + render_sentences(data, another)
                    + "".join(render_block(name, copy, data, fill, None) for name in ONE_PAGE))
    # The page carries the data it was made from, as it did. Two things are left to data.json, to keep
    # the page from growing by them: each term's curve, hour by hour, and its list of first signatures.
    carried = {**data, "terms": {name: {key: value for key, value in stats.items() if key not in ("curve", "first_signatures")}
                                 for name, stats in data["terms"].items()},
               "left_to_data_json": ["terms.*.curve", "terms.*.first_signatures"]}
    embedded = json.dumps(carried, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/").replace("<!--", "<\\!--")
    head = head_tags(title, f"<style>{CSS}</style>")
    scripts = (f'<script type="application/json" id="site-data">{embedded}</script>\n'
               f"<script>{script(True)}</script>\n")
    fragment = head + content + "\n" + scripts
    files = render_site(copy, data)
    files.update({"index.html": document(fill(copy["dek"]), head, content, scripts), "artifact.html": fragment})
    return files


def plan_site(copy: dict, data: dict, fill) -> dict:
    """The files of the site and what leads where, from the sentences and the `pages` list."""
    total = len(data["sentences"])
    if not total:
        raise BuildError("the site needs a sentence for its front door")
    leaves = [FRONT] + [LEAF.format(n=n) for n in range(2, total + 1)]
    nav = copy.get("nav") or {}
    pages, ids, taken = [], set(), set(leaves) | set(SHARED) | {"data.json"}
    for entry in copy.get("pages") or []:
        for key in ("id", "file", "blocks"):
            if not entry.get(key):
                raise BuildError(f"an entry of `pages` has no {key!r}: {entry!r}")
        if not ID_OK.match(entry["id"]) or entry["id"] in ids:
            raise BuildError(f"`pages`: the id {entry['id']!r} is used twice or cannot be carried by a link")
        if not FILE_OK.match(entry["file"]):
            raise BuildError(f"`pages`: {entry['file']!r} is not a plain lower-case .html file name")
        if entry["file"] in taken:
            raise BuildError(f"`pages`: two files would be named {entry['file']!r}")
        ids.add(entry["id"])
        taken.add(entry["file"])
        headings = [fill(copy[name]["heading"]) if isinstance(copy.get(name), dict) and copy[name].get("heading") else None
                    for name in entry["blocks"]]
        title = fill(entry["title"]) if entry.get("title") else next((h for h in headings if h), None)
        if not title:
            raise BuildError(f"`pages`: {entry['id']!r} needs a title, since none of its blocks has a heading")
        # A page whose first block does not open with the page's title is given the title as a running head.
        pages.append({"id": entry["id"], "file": entry["file"], "title": title, "blocks": list(entry["blocks"]),
                      "head": headings[0] != title})
    if pages and not (nav.get("previous") and nav.get("next")):
        raise BuildError("the copy needs nav.previous and nav.next: the words on the line at the foot of each page")
    context = None
    if copy.get("leaf_context_page"):
        context = next((page for page in pages if page["id"] == copy["leaf_context_page"]), None)
        if context is None:
            raise BuildError(f"leaf_context_page names no entry of `pages`: {copy['leaf_context_page']!r}")
    return {
        "title": fill(copy["title"]), "leaves": leaves, "pages": pages, "context": context,
        "words": next((page["file"] for page in pages if "words" in page["blocks"]), None),
        "another": fill(nav.get("another") or "another"),
        "previous": fill(nav.get("previous") or ""), "next": fill(nav.get("next") or ""),
    }


def notebook_nav(copy, site, active=None):
    label = copy["nav"]
    items = f'<li><a href="index.html">{esc(label["home"])}</a></li>'
    items += "".join(f'<li><a href="{esc(p["file"])}"' + (' aria-current="page"' if p['id'] == active else '') + f'>{esc(p["title"])}</a></li>' for p in site['pages'])
    return f'<a class="skip-link" href="#main">{esc(label["skip"])}</a><details class="notebook-nav"><summary>{esc(label["contents"])}</summary><nav aria-label="{esc(label["section"])}"><ol>{items}</ol></nav></details>'


def render_turn(index: int, site: dict) -> str:
    """The line at the foot of a page: the leaf before it and the leaf after. Before the first
    page is the front door; after the last there is nothing."""
    pages = site["pages"]
    before = pages[index - 1] if index else {"file": FRONT, "title": site["title"]}
    after = pages[index + 1] if index + 1 < len(pages) else None
    out = (f'<p class="prev"><span class="lbl">{esc(site["previous"])}</span> '
           f'<a href="{esc(before["file"])}">{esc(before["title"])}</a></p>')
    if after:
        out += (f'<p class="next"><span class="lbl">{esc(site["next"])}</span> '
                f'<a href="{esc(after["file"])}">{esc(after["title"])}</a></p>')
    return f'<nav class="sec turn">{out}</nav>'


def render_site(copy: dict, data: dict) -> dict:
    """The site: a leaf for each sentence, a page for each entry of `pages`, one stylesheet, one script."""
    fill = filler(data["placeholders"])
    site = plan_site(copy, data, fill)
    title, description = site["title"], fill(copy["dek"])
    style = '<link rel="stylesheet" href="style.css">'
    if not site["words"] and any(door["entry"] for sentence in data["sentences"] for door in sentence["doors"]):
        data["warnings"].append("no page shows the words, so the doors in the sentences are set as plain text")
    files = {}
    for index, name in enumerate(site["leaves"]):
        own = title if index == 0 else f"{title} · {index + 1} / {len(site['leaves'])}"
        body = notebook_nav(copy, site) + masthead(copy, fill, home=FRONT if index else None) + render_leaf(index, copy, data, fill, site)
        files[name] = document(description, head_tags(own, style), sheet(body))
    foot_everywhere = copy.get("colophon_at_foot", True) and copy.get("colophon")
    for index, page in enumerate(site["pages"]):
        blocks = "".join(render_block(name, copy, data, fill, site) for name in page["blocks"])
        head = f'<div class="sec page-head"><h2 class="page-h">{esc(page["title"])}</h2></div>' if page["head"] else ""
        foot = render_block("colophon", copy, data, fill, site) if foot_everywhere and "colophon" not in page["blocks"] else ""
        body = notebook_nav(copy, site, page["id"]) + masthead(copy, fill, home=FRONT) + f'<main id="main">{head}{blocks}</main>' + render_turn(index, site) + foot
        scripts = '<script src="site.js"></script>\n' if any(name in page["blocks"] for name in ("words", "board")) else ""
        files[page["file"]] = document(description, head_tags(f"{page['title']} · {title}", style), sheet(body), scripts)
        if page['id'] == 'engine':
            e = copy['engine']
            body = '<div class="engine-demo">' + notebook_nav(copy, site, 'engine')
            body += f'<header><h1>{esc(e["heading"])}</h1><p>{esc(e["kicker"])}</p></header>'
            body += f'<main id="main">{blocks}</main></div>'
            files[page['file']] = document(e['intro'], head_tags(e['heading'], style), body)
    for thread, records in data['board_threads'].items():
        body = notebook_nav(copy, site, 'board') + masthead(copy, fill, home=FRONT)
        body += f'<main id="main">{render_thread(thread, records, copy)}</main>'
        body += render_block('colophon', copy, data, fill, site)
        files[thread_file(thread)] = document(description, head_tags(f'{copy["board"]["thread"]} {thread} · {title}', style), sheet(body))
    from board_forum import render_conversation, conversation_file
    for thread, item in data['conversations'].items():
        body = notebook_nav(copy, site, 'board') + masthead(copy, fill, home=FRONT)
        body += f'<main id="main">{render_conversation(thread, item, copy, data)}</main>'
        heading = copy['board']['whole'] if thread == 'board' else f'{copy["board"]["thread"]} {thread}'
        files[conversation_file(thread)] = document(description, head_tags(f'{heading} · {title}', style), sheet(body))
    from theory_readings import render_sources
    if data.get('theory_sources'):
        body = notebook_nav(copy, site, 'reading') + masthead(copy, fill, home=FRONT)
        body += f'<main id="main">{render_sources(copy, data)}</main>'
        files['theory-sources.html'] = document(description, head_tags(copy['reading']['ledger']['sources_heading'], style), sheet(body))
    files["style.css"] = CSS.lstrip("\n")
    files["site.js"] = script(False).lstrip("\n") + BOARD_JS
    # The checked-in SVG and HTML derive from the same authoritative copy.
    # Refuse stale artwork after a copy edit; refresh it with engine_visual.py.
    if copy.get('engine'):
        source = HERE / 'images/engine.svg'
        expected = render_svg(copy).encode('utf-8')
        if not source.is_file() or source.read_bytes() != expected:
            raise BuildError('engine.svg is missing or stale; run python3 -B site-builder/engine_visual.py')
        files['images/engine.svg'] = source.read_bytes()
    for plate in copy.get("plates", {}).values():
        for name in (plate["file"], "prompts/" + plate["prompt"]):
            source = HERE / "images" / name
            if not source.is_file():
                raise BuildError(f"image asset missing: {source}")
            files["images/" + name] = source.read_bytes()
    files["board-records.json"] = json.dumps({"rounds": data["board"], "notes": data["board_contexts"]}, ensure_ascii=False, indent=1) + "\n"
    return files


# ---- checks on what was made -----------------------------------------------------

class Scan(HTMLParser):
    """What one finished page defines, points to and quotes."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.ids: list[str] = []
        self.links: list[str] = []                # every href and src
        self.entries: list[str] = []              # the entries the one page's doors open
        self.quotes: list[tuple[str, str]] = []   # (unit, the words as a reader gets them)
        self._held: list[dict] = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if attrs.get("id"):
            self.ids.append(attrs["id"])
        self.links += [attrs[key] for key in ("href", "src") if attrs.get(key) is not None]
        if attrs.get("data-entry"):
            self.entries.append(attrs["data-entry"])
        for held in self._held:
            held["depth"] += held["tag"] == tag
        if attrs.get("data-quote"):
            self._held.append({"tag": tag, "depth": 1, "unit": attrs["data-quote"], "text": []})

    def handle_endtag(self, tag):
        for held in list(self._held):
            held["depth"] -= held["tag"] == tag
            if not held["depth"]:
                self._held.remove(held)
                self.quotes.append((held["unit"], "".join(held["text"])))

    def handle_data(self, data):
        for held in self._held:
            held["text"].append(data)


def tokens_in(value) -> set[str]:
    """Every {placeholder} written anywhere in the copy."""
    if isinstance(value, str):
        return {match.group(0) for match in PLACEHOLDER.finditer(value)}
    if isinstance(value, dict):
        value = list(value.values())
    if isinstance(value, list):
        return set().union(*(tokens_in(item) for item in value))
    return set()


def check_outputs(files: dict, data: dict, copy: dict, project: Project) -> dict:
    """Checks on every page that was made, whichever build made it. Nothing is written if one fails.

    On each page: no id twice, and none a link cannot carry; no placeholder of the copy left
    unfilled; every link inside the site leads to a file that was made, and every anchor to an id
    on that page; every quotation, read back out of the finished markup, is an exact substring of
    the unit it names; no colour outside the tokens. Returns what was counted.
    """
    pages = {name: text for name, text in files.items() if name.endswith(".html")}
    fragment = files.get("artifact.html")
    if fragment is not None:
        if not fragment.startswith("<title>"):
            raise BuildError("artifact.html does not begin with its title")
        own = fragment.split('<script type="application/json"')[0]
        if re.search(r"<(?:!doctype|/?html|/?head|/?body)[\s>]", own, re.I):
            raise BuildError("artifact.html contains a document tag")
        if not re.match(r"<title>[^<]*</title>\s*<style>", fragment):
            raise BuildError("artifact.html must open with its title, the font link, then the style")

    scans, tokens = {}, tokens_in(copy)
    # The token blocks are the only place a colour may be written: not in the rest of the
    # stylesheet, and not in any attribute of the markup.
    colour = r"#[0-9a-fA-F]{3,8}\b|\brgba?\(|\bhsla?\("
    literal = re.search(colour, re.sub(r":root[^{]*\{[^}]*\}", "", CSS))
    if literal:
        raise BuildError(f"a colour is written outside the tokens, in the stylesheet: {literal.group(0)}")
    for name, text in pages.items():
        scan = Scan()
        scan.feed(text)
        scan.close()
        scans[name] = scan
        twice = [one for one, n in Counter(scan.ids).items() if n > 1]
        if twice:
            raise BuildError(f"{name}: ids used twice: {twice}")
        for one in scan.ids:
            if not ID_OK.match(one):
                raise BuildError(f"{name}: an id uses characters a link cannot carry: {one!r}")
        markup = re.sub(r"<(script|style)\b.*?</\1>", "", text, flags=re.S)
        left = sorted(token for token in tokens if token in markup)
        if left:
            raise BuildError(f"{name}: a placeholder of the copy was left unfilled: {', '.join(left)}")
        literal = re.search(r'\s(?:fill|stroke|color|style|stop-color|bgcolor)="[^"]*(?:' + colour + ")", markup)
        if literal:
            raise BuildError(f"{name}: a colour is written outside the tokens: {literal.group(0)}")

    links = anchors = 0
    for name, scan in scans.items():
        for target in scan.links:
            parts = urlsplit(target)
            if parts.scheme or parts.netloc:
                continue  # another site
            path = unquote(parts.path)
            if path.startswith("/") or ".." in path.split("/"):
                raise BuildError(f"{name}: a link leaves the folder, so the site could not be moved: {target!r}")
            file = path or name
            if file not in files:
                raise BuildError(f"{name}: a link leads to a file that was not made: {target!r}")
            links += 1
            if parts.fragment:
                if file not in scans or unquote(parts.fragment) not in scans[file].ids:
                    raise BuildError(f"{name}: a link leads to an anchor that is not on {file}: {target!r}")
                anchors += 1
        for entry in scan.entries:
            if entry not in scan.ids:
                raise BuildError(f"{name}: a door opens an entry that is not on the page: {entry!r}")

    source = {'prompt:' + part['sha256']: part['text'] for r in data['board'] for a in r['answers'] for part in a['apparatus']['stack']}
    from theory_readings import quote_sources
    source.update(quote_sources(project, data))
    quotes = Counter()
    for name, scan in scans.items():
        for unit_id, words in scan.quotes:
            if unit_id not in source:
                unit = project.get(unit_id)
                if unit is None or unit.get("kind") not in ("unit", "memo"):
                    raise BuildError(f"{name}: a quotation names a unit that is not in the ledger: {unit_id!r}")
                source[unit_id] = project.unit_text(unit) if unit["kind"] == "unit" else unit.get("body", "")
            if not words or words not in source[unit_id]:
                raise BuildError(f"{name}: a quotation is not an exact substring of {unit_id}: {words!r}")
            quotes[(unit_id, words)] += 1
    for index, sentence in enumerate(data["sentences"], 1):
        if (sentence["unit"], sentence["quote"]) not in quotes:
            raise BuildError(f"sentence {index} is on no page as it is written in {sentence['unit']}")
    if any("their-words" in scan.ids for scan in scans.values()):
        for family in data["words"]:
            for entry in family["entries"]:
                for passage in entry["passages"]:
                    if (passage["unit"], passage["text"]) not in quotes:
                        raise BuildError(f"a passage for {entry['term']!r} is on no page as it is written in {passage['unit']}")

    for part in (JS_TURNING, JS_ENTRIES):
        if re.search(r"\bfetch\b|XMLHttpRequest|\bimport\b", part):
            raise BuildError("the script must not fetch or import: the pages are to open from disk")
    return {"pages": len(pages), "links": links, "anchors": anchors, "quotations": sum(quotes.values()),
            "units_quoted": len(source)}


def log(data: dict, pages: dict, out: Path, counted: dict) -> None:
    metrics, fills = data["metrics"], data["placeholders"]
    entries = sum(len(family["entries"]) for family in data["words"])
    print(f"project: {data['built']['project']}   posts: {num(metrics['posts_total'])} "
          f"({metrics['first_post']} to {metrics['last_post']})")
    print(f"terms traced: {len(data['terms'])}   word entries: {entries}   passages shown: {data['passages_total']} "
          f"(each an exact substring of its unit)")
    print(f"sentences: {len(data['sentences'])}, each found exactly in its unit")
    print(f"checked in the finished pages ({data['built']['mode']}): {counted['pages']} pages; {num(counted['links'])} links "
          f"inside the site, {num(counted['anchors'])} of them to an anchor, all found; {num(counted['quotations'])} quotations "
          f"read back out of the markup, each an exact substring of its unit ({counted['units_quoted']} units)")
    if data["fastest"]:
        soon = f"{SPREAD_HOURS[0]}h"
        print(f"most signatures within {soon} of first use (of those posting in that time): "
              + ", ".join(f"{item['term']} {item['adoption'][soon]} of {item['posting'][soon]}" for item in data["fastest"]))
    print("placeholders:")
    for name, value in fills.items():
        print(f"  {{{name}}} = {value}")
    clock_ = metrics["clock"]
    print(f"  behind them: dated signatures {num(metrics['date_name_posts'])}/{num(metrics['posts_total'])} "
          f"({100 * metrics['date_name_share']:.2f}%); date beside a clock in {clock_['posts_with_a_date_beside_a_clock']} posts "
          f"(same {clock_['same_as_signature']}, different {clock_['a_different_code']}, undated signature {clock_['signature_has_no_date']}); "
          f"we {metrics['we']['posts']} ({100 * metrics['we']['share']:.2f}%), I {metrics['i']['posts']} ({100 * metrics['i']['share']:.2f}%); "
          f"please {metrics['please_posts']} ({100 * metrics['please_share']:.2f}%)")
    print("  name parts: " + ", ".join(f"{kind} {part['posts']} ({100 * part['share']:.1f}%)" for kind, part in data["name_parts"].items()))
    print(f"probe words absent from every post and text unit ({len(metrics['absent'])}): {', '.join(metrics['absent']) or 'none'}")
    print(f"probe words expected absent that occur: {', '.join(metrics['probe_words_that_occur']) or 'none'}")
    human = metrics["human"]
    print(f"'human': {human['post_and_text_occurrences']} occurrence(s) in {human['post_and_text_units']} post or text unit(s); "
          f"other units {human['other_units'] or 'none'}")
    if data["readers"]:
        for reader in data["readers"]:
            print(f"reader {reader['model']} ({reader['family']}) {reader['lens']}: {reader['batches']} batches, "
                  f"{num(reader['units_read'])} units, {num(reader['codings'])} codings, {reader['field_notes']} field notes")
    else:
        print("readers: no focused-coding lens on this codebook has finished a batch; the section shows its paragraphs only")
    for framework, group in data.get('theory', {}).items():
        print(f'theory {framework}: {len(group["readings"])} completed readings; '
              f'{sum(a["status"] != "ok" for a in group["attempts"])} unsuccessful activities; '
              f'{sum(bool(r.get("gloss")) for r in group["readings"])} glosses')
    print(f"ledger digest: {metrics['ledger_digest'] or 'not available; the colophon line is left out'}"
          f"{'' if metrics['ledger_steady_during_read'] else '  (the ledger moved during the read)'}")
    if data["warnings"]:
        print(f"notes for the author ({len(data['warnings'])}):")
        for warning in data["warnings"]:
            print(f"  - {warning}")
    else:
        print("notes for the author: none")
    for name, text in pages.items():
        print(f"wrote {out / name}  ({len(text if isinstance(text, bytes) else text.encode('utf-8')) / 1024:.0f} KB)")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Build the public pages. The project is only read.")
    parser.add_argument("project")
    parser.add_argument("--copy", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--single", action="store_true",
                        help="the one-page build: index.html as one page, and artifact.html (give it a folder of its own)")
    args = parser.parse_args(argv)
    try:
        copy_path = Path(args.copy)
        copy = json.loads(copy_path.read_text(encoding="utf-8"))
        project, digest, steady = open_project(args.project)
        data = enrich(project, compute(project, copy, copy_path, digest, steady), copy)
        data["built"]["mode"] = "one page" if args.single else "site"
        pages = render_single(copy, data) if args.single else render_site(copy, data)
        counted = check_outputs(pages, data, copy, project)
    except (BuildError, ValueError, OSError) as error:
        print(f"build error: {error}", file=sys.stderr)
        return 1
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    data["built"]["files"] = sorted(pages)
    listed = json.dumps(data, ensure_ascii=False, indent=1) + "\n"
    # A pair of numbers (a point of a curve, the place of a match) is kept on one line. A string cannot
    # hold a bare line break in JSON, so nothing inside quoted text is touched by this.
    pages["data.json"] = re.sub(r"\[\n\s*(-?\d+(?:\.\d+)?),\n\s*(-?\d+(?:\.\d+)?)\n\s*\]", r"[\1, \2]", listed)
    for name, text in pages.items():
        target = out / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(text, bytes):
            target.write_bytes(text)
        else:
            target.write_text(text, encoding="utf-8")
    log(data, pages, out, counted)
    others = sorted(path.name for path in out.glob("*.html") if path.name not in pages)
    if others:
        print(f"in {out} but not written by this build (left as they were): {', '.join(others)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
