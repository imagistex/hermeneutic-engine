"""Passages: what the readers of a run said about the same words of one post, put together.

Shared by candidates.py (unfit memos) and standout.py (codings of one code).

An entry is one record a reader made about a post: an unfit memo or a coding.
Entries about the same post whose anchors overlap (same source, byte ranges
that intersect) are one passage. Ranges are half-open, so two quotations that
only touch are two passages. Overlap chains: if A overlaps B and B overlaps C,
the three are one passage even when A and C do not touch, and the passage is
the whole span from the first byte of the first to the last byte of the last.
An entry with no anchor points at no bytes and stays alone.

Every text given for a passage or an entry is cut from the stored source by
its byte offsets. Nothing is taken from a reader's own copy of the words, and
an anchor that does not say what the source says is reported as a problem and
never quoted.

A passage's tier is the number of distinct model families among its entries.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

from hermeneutic_engine.views import run_lenses  # noqa: F401  (puts the engine on the path when PYTHONPATH is not set)
from hermeneutic_engine.anchors import check

COUNT_WORDS = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six"}
INLINE_MAX = 100  # characters; a longer quotation is set in a block of its own


# ---- building ---------------------------------------------------------------

def _ctx(unit) -> dict:
    return (unit or {}).get("context") or {}


def first_seen(unit) -> str:
    return str((_ctx(unit).get("first_seen") or {}).get("time") or "")


def day_of(time: str) -> str:
    """The UTC date of a ledger time such as 2026-06-18T04:48:38Z."""
    return time[:10] if re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?Z", time) else "unknown day"


def _span(project, unit, anchor):
    """((source, start, end), None) when the anchor points at bytes inside its
    unit that say exactly what it claims; otherwise (None, why not)."""
    source, start, end = anchor.get("source"), anchor.get("start"), anchor.get("end")
    if not (isinstance(source, str) and isinstance(start, int) and isinstance(end, int) and 0 <= start < end):
        return None, "the anchor names no span"
    if unit is None:
        return None, "the anchor's unit is not in the ledger"
    if source != unit.get("source"):
        return None, "the anchor and its unit are in different sources"
    if not (unit["start"] <= start and end <= unit["end"]):
        return None, "the anchor lies outside its unit"
    try:
        data = project.source_bytes(source)
    except Exception as exc:  # a missing blob or an unknown source
        return None, f"the source bytes could not be read ({exc})"
    if not check(data, anchor):
        return None, "the anchor's text does not match the source bytes"
    return (source, start, end), None


def build(project, run, entries: list[dict]) -> tuple[list[dict], list[str], int]:
    """Group entries into passages, describe each, and put them in order.

    An entry is {"id", "by" (its lens), "reader" (a place in run.readers),
    "unit", "anchor" (a dict or None), "said" (fields to carry through), and
    optionally "quote_not_found"}. Returns the passages (most families first;
    inside a tier by first-seen time), a list of problems, and how many
    anchors were checked against the source bytes.
    """
    problems: list[str] = []
    checked = 0
    placed, alone = {}, []
    for entry in entries:
        unit = project.get(entry["unit"]) if entry.get("unit") else None
        item = {"entry": entry, "unit": unit, "span": None, "problem": None}
        if isinstance(entry.get("anchor"), dict):
            checked += 1
            item["span"], item["problem"] = _span(project, unit, entry["anchor"])
            if item["problem"]:
                problems.append(f"{entry['id']}: {item['problem']}; it is listed alone and not quoted")
        if item["span"]:
            placed.setdefault((entry["unit"], item["span"][0]), []).append(item)
        else:
            alone.append(item)

    groups = []
    for (unit_id, source), items in placed.items():
        items.sort(key=lambda item: (item["span"][1], item["span"][2], item["entry"]["reader"], item["entry"]["id"]))
        current = None
        for item in items:
            _, start, end = item["span"]
            if current is not None and start < current["end"]:  # half-open ranges: touching is not overlapping
                current["items"].append(item)
                current["end"] = max(current["end"], end)
            else:
                current = {"unit": item["unit"], "source": source, "start": start, "end": end, "items": [item]}
                groups.append(current)
    groups += [{"unit": item["unit"], "source": None, "start": None, "end": None, "items": [item]} for item in alone]

    passages = []
    for group in groups:
        unit, ctx = group["unit"], _ctx(group["unit"])
        found = group["source"] is not None
        text = None
        if found:
            raw = project.source_bytes(group["source"])[group["start"]:group["end"]]
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:  # cannot happen when every anchor checked; said aloud if it does
                text = raw.decode("utf-8", errors="backslashreplace")
                problems.append(f"{group['items'][0]['entry']['id']}: the passage's bytes do not decode as UTF-8; "
                                "bytes that do not are shown as \\x escapes")
        members = sorted(group["items"], key=lambda item: (item["entry"]["reader"], item["span"] or ("", 0, 0),
                                                           item["entry"]["id"]))
        out_entries = []
        for item in members:
            entry, reader = item["entry"], run.readers[item["entry"]["reader"]]
            lens = project.get(entry["by"]) if entry.get("by") else None
            answered = (lens or {}).get("reader", {}).get("model")
            said = {"id": entry["id"], "family": reader["family"], "model": reader["model"],
                    "reader": reader["label"], "lens": entry.get("by"),
                    "answered_by": answered if answered and answered != reader["model"] else None}
            if item["span"]:
                _, start, end = item["span"]
                said.update(start=start, end=end, whole=(start, end) == (group["start"], group["end"]),
                            exact=project.source_bytes(group["source"])[start:end].decode("utf-8"))
            else:
                said.update(start=None, end=None, whole=False, exact=None)
                if item["problem"]:
                    said["problem"] = item["problem"]
                elif "quote_not_found" in entry:
                    said["quote_not_found"] = entry["quote_not_found"]
            said.update(entry.get("said") or {})
            out_entries.append(said)
        families = list(dict.fromkeys(entry["family"] for entry in out_entries))
        seen = first_seen(unit)
        unit_id = unit["id"] if unit else members[0]["entry"].get("unit")
        passages.append({
            "number": None, "tier": len(families), "families": families, "found": found,
            "unit": unit_id, "source": group["source"], "start": group["start"], "end": group["end"], "text": text,
            "first_seen": seen, "day": day_of(seen), "signature": ctx.get("signature"),
            "signature_uncertain": bool(ctx.get("signature_uncertain")), "saved_as": ctx.get("introduced_by"),
            "page": ctx.get("page"),
            "not_read_by": [run.readers[index]["label"] for index in run.not_read_by(unit_id)] if unit_id else [],
            "entries": out_entries,
        })

    passages.sort(key=lambda p: (-p["tier"], p["first_seen"], str(p["page"] or ""), str(p["unit"] or ""),
                                 0 if p["found"] else 1, p["start"] or 0, p["entries"][0]["id"]))
    per_tier = Counter(p["tier"] for p in passages)
    seen_in_tier: Counter = Counter()
    for passage in passages:
        seen_in_tier[passage["tier"]] += 1
        width = max(3, len(str(per_tier[passage["tier"]])))
        passage["number"] = f"{passage['tier']}.{seen_in_tier[passage['tier']]:0{width}d}"
    return passages, problems, checked


def tiers_of(passages: list[dict], expect: int) -> list[int]:
    """The tiers a page shows, most families first: every count from the
    number of readers a whole run has down to one, and above that if needed."""
    top = max([expect] + [p["tier"] for p in passages])
    return list(range(top, 0, -1))


def tier_name(n: int, expect: int) -> str:
    word = COUNT_WORDS.get(n, str(n))
    if n == 1:
        return "one family"
    if n == expect:
        return "both families" if n == 2 else f"all {word} families"
    return f"{word} families"


def by_day(run, passages: list[dict], entries_by_reader: dict[str, Counter], tiers: list[int]) -> list[dict]:
    """One row for each day of the swarm's calendar on which a signed post was
    first seen: the day's posts, its passages by tier, each reader's entries,
    and how many of the day's posts each reader had read."""
    posts, read = Counter(), {reader["label"]: Counter() for reader in run.readers}
    for unit in run.posts.values():
        day = day_of(first_seen(unit))
        posts[day] += 1
        for index in run.read_by.get(unit["id"], ()):
            read[run.readers[index]["label"]][day] += 1
    days = sorted(set(posts) | {p["day"] for p in passages})
    rows = []
    for day in days:
        here = [p for p in passages if p["day"] == day]
        rows.append({"day": day, "posts": posts[day], "passages": len(here),
                     "by_tier": {str(t): sum(1 for p in here if p["tier"] == t) for t in tiers},
                     "entries": {label: counts[day] for label, counts in entries_by_reader.items()},
                     "posts_read": {label: counts[day] for label, counts in read.items()}})
    return rows


def reader_rows(run, passages: list[dict], tiers: list[int], counted: Counter,
                extra: dict[str, Counter] | None = None) -> list[dict]:
    """One row for each reader: its entries as counted from the ledger, the
    passages it took part in, and those passages by tier. `extra` adds further
    counts to each row, as {key: counts by the reader's place}."""
    rows = []
    for index, reader in enumerate(run.readers):
        mine = [p for p in passages if any(e["reader"] == reader["label"] for e in p["entries"])]
        row = {"label": reader["label"], "family": reader["family"], "model": reader["model"],
               "lens_ids": reader["lens_ids"], "complete": reader["complete"], "posts_read": reader["posts_read"],
               "posts_total": reader["posts_total"], "activities_ok": reader["activities_ok"],
               "entries": counted[index], "passages": len(mine),
               "by_tier": {str(t): sum(1 for p in mine if p["tier"] == t) for t in tiers}}
        for key, counts in (extra or {}).items():
            row[key] = counts[index]
        rows.append(row)
    return rows


def tier_rows(run, passages: list[dict], tiers: list[int]) -> list[dict]:
    """For each tier: how many passages, and in how many of them every reader
    that has a lens had finished reading the post. Where some reader had not,
    a low tier may only mean that the others have not got there yet."""
    rows = []
    for tier in tiers:
        here = [p for p in passages if p["tier"] == tier]
        rows.append({"tier": tier, "name": tier_name(tier, run.expect), "passages": len(here),
                     "read_by_all": sum(1 for p in here if not p["not_read_by"])})
    return rows


# ---- Markdown that shows bytes as they are --------------------------------------

def fence(text: str) -> str:
    """`text` in a fenced block, byte for byte. Nothing inside a fence is read
    as Markdown, and the fence is longer than any run of backticks in the text."""
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    ticks = "`" * max(3, longest + 1)
    return f"{ticks}\n{text}\n{ticks}"


def code_span(text: str) -> str | None:
    """`text` as an inline code span that renders as exactly itself, or None
    when no code span can hold it (it is empty or has a line break)."""
    if not text or "\n" in text or "\r" in text:
        return None
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    ticks = "`" * (longest + 1)
    # A renderer strips one space from each end when both ends have one, so
    # padding is added exactly when it would otherwise change the text.
    pad = " " if text.strip(" ") and (text[0] == "`" or text[-1] == "`" or (text[0] == " " and text[-1] == " ")) else ""
    return f"{ticks}{pad}{text}{pad}{ticks}"


def name_span(text) -> str:
    """A short name (a signature, a page, a suggested name) in backticks."""
    text = "" if text is None else str(text)
    return code_span(text) or f"`{text!r}`"


_SPECIAL = re.compile(r"([\\`*_\[\]{}<>#|~$=%^&])")


def md_text(text: str) -> str:
    """A reader's own sentence with every character Markdown could act on
    escaped, so that it renders as the reader wrote it."""
    return _SPECIAL.sub(r"\\\1", text)


def one_line(text: str) -> bool:
    return "\n" not in text and "\r" not in text


def clock(time: str) -> str:
    return time[11:19] if day_of(time) != "unknown day" else (time or "time unknown")


def table(header: list[str], rows: list[list], numeric_from: int = 1) -> list[str]:
    """A table of counts. Only safe labels and numbers go in tables; anything
    a reader or a writer wrote is set in lists and blocks instead."""
    def cell(value) -> str:
        return f"{value:,}" if isinstance(value, int) else str(value).replace("|", "¦")
    out = ["| " + " | ".join(header) + " |",
           "|" + "|".join("---" if n < numeric_from else "---:" for n in range(len(header))) + "|"]
    out += ["| " + " | ".join(cell(value) for value in row) + " |" for row in rows]
    return out


def fits_inline(text) -> bool:
    """True when a text can stand inside a line as a code span: one line, not long."""
    return isinstance(text, str) and code_span(text) is not None and len(text) <= INLINE_MAX


def head(passage: dict, inline: bool) -> str:
    """The line that names a passage: its number, when and where the post was
    first seen, who signed it, and (when `inline`) its words."""
    parts = [f"**{passage['number']}**", clock(passage["first_seen"])]
    if inline and passage["found"] and fits_inline(passage["text"]):
        parts.append(code_span(passage["text"]))
    if passage["signature"]:
        parts.append(f"signed {name_span(passage['signature'])}"
                     + (" (with a question mark)" if passage["signature_uncertain"] else ""))
    if passage["page"]:
        parts.append(name_span(passage["page"]))
    if passage["unit"]:
        parts.append(f"`{passage['unit']}`")
    if not passage["found"]:
        parts.append("**anchor does not check**" if any("problem" in e for e in passage["entries"])
                     else "**quotation not found**")
    if passage["not_read_by"]:
        parts.append("not yet read by " + ", ".join(passage["not_read_by"]))
    return " · ".join(parts)


def render_passage(passage: dict, inline: bool, lead) -> list[str]:
    """One passage as Markdown lines.

    `lead(entry)` returns (what the reader did, in Markdown; its own words, as
    written). With `inline`, a short one-line passage is set inside the head
    line; otherwise the passage is a fenced block under it.
    """
    out = [head(passage, inline), ""]
    if passage["found"] and not (inline and fits_inline(passage["text"])):
        out += [fence(passage["text"]), ""]
    if not passage["found"]:
        entry = passage["entries"][0]
        if "problem" in entry:
            out += [f"Not quoted: {entry['problem']}.", ""]
        elif str(entry.get("quote_not_found") or "").strip():
            out += ["The reader gave the words below. They are not in the post, so they point at no bytes and are "
                    "grouped with nothing. They are the reader's words, not the source's:", "",
                    fence(entry["quote_not_found"]), ""]
        else:
            out += ["The reader quoted no words.", ""]
    after: list[str] = []
    for entry in passage["entries"]:
        did, words = lead(entry)
        who = f"**{entry['family']}** ({entry['model']}"
        if entry.get("answered_by"):
            who += f", answered by {entry['answered_by']}"
        who += ")"
        line = f"- {who}" + (f" {did}" if did else "")
        words = "" if words is None else str(words)
        if words.strip():
            if one_line(words):
                line += f": {md_text(words)}"
            else:
                line += ": (its words are below, as written)"
                after += [f"{entry['family']}, `{entry['id']}`, as written:", "", fence(words), ""]
        line += f" · `{entry['id']}`"
        if passage["found"] and not entry["whole"]:
            offset = (entry["start"] - passage["start"], entry["end"] - passage["start"])
            if fits_inline(entry["exact"]):
                line += f" · quoted part of the passage: {code_span(entry['exact'])}"
            else:
                line += f" · quoted part of the passage (bytes {offset[0]} to {offset[1]} of it, below)"
                after += [f"{entry['family']}, `{entry['id']}`, quoted:", "", fence(entry["exact"]), ""]
        out.append(line)
    out.append("")
    return out + after


def render_notices(data: dict, one: str, many: str) -> list[str]:
    """What was skipped or left out when the ledger was read, and any problems.
    `one` and `many` name the records ("unfit memo", "unfit memos")."""
    out = []
    for kind, line in sorted(data["skipped_last_lines"].items()):
        out += [f"Line {line:,} of `{kind}.jsonl`, its last, did not parse and was skipped: a writer was in the "
                "middle of it.", ""]
    held = {label: n for label, n in data["unfinished"].items() if n}
    if held:
        out += ["Left out, because the batch that made them had not finished when the ledger was read: "
                + "; ".join(f"{n:,} {one if n == 1 else many} by {label}" for label, n in held.items()) + ".", ""]
    if data["problems"]:
        out += ["## Problems", ""] + [f"- {md_text(problem)}" for problem in data["problems"]] + [""]
    return out


def render_counts(data: dict, entries: str, verb: str, extra: list[tuple[str, str]], note: str) -> list[str]:
    """The tables before the tiers: counts by reader and tier, then by day.

    `entries` heads the column of records counted from the ledger ("Records",
    "Marks"); `verb` is what readers did to a passage ("Flagged", "Marked");
    `extra` adds columns to the readers' table as (heading, key in each row and
    in the totals); `note` says what the first table counts.
    """
    expect, tiers = data["expect"], [row["tier"] for row in data["tiers"]]
    totals, readers = data["totals"], data["readers"]
    names = {tier: tier_name(tier, expect) for tier in tiers}
    out = ["## Counts by reader and tier", "", note, ""]
    out += table(["Reader", "Signed posts read", entries] + [heading for heading, _ in extra] + ["Passages"]
                 + [f"{verb} by {names[t]}" for t in tiers],
                 [[r["label"], f"{r['posts_read']:,} of {r['posts_total']:,}", r["entries"]]
                  + [r[key] for _, key in extra] + [r["passages"]] + [r["by_tier"][str(t)] for t in tiers]
                  for r in readers]
                 + [["All readers", "", totals["entries"]] + [totals[key] for _, key in extra] + [totals["passages"]]
                    + [row["passages"] for row in data["tiers"]]])
    out.append("")
    if data["complete"]:
        out += [f"Every reader had finished every signed post, so a passage {verb.lower()} by one family was read "
                f"by the others and not {verb.lower()} by them.", ""]
    else:
        missing = expect - len(readers)
        others = ("" if missing <= 0 else "; the other had not started and had read nothing" if missing == 1
                  else f"; the other {missing} had not started and had read nothing")
        out += ["The run is not complete, so a low tier can mean only that another reader had not reached the post. "
                "For each tier, the passages in posts that every reader with a lens had finished "
                f"({len(readers)} of the {expect} readers expected had a lens{others}):", ""]
        out += table(["Tier", "Passages", "In posts every reader with a lens had finished"],
                     [[f"{verb} by {row['name']}", row["passages"], row["read_by_all"]] for row in data["tiers"]])
        out += ["", "A passage in a post that some reader with a lens had not finished says so on its line "
                "(*not yet read by ...*).", ""]

    partial = [r["label"] for r in readers if not r["complete"]]
    out += ["## By day", "",
            "The day is the UTC date on which the post was first seen. Signed posts are all units of kind `post` "
            "first seen that day.", ""]
    out += table(["Day (UTC)", "Signed posts", "Passages"] + [f"By {names[t]}" for t in tiers]
                 + [f"{entries}, {r['label']}" for r in readers] + [f"Posts read, {label}" for label in partial],
                 [[row["day"], row["posts"], row["passages"]] + [row["by_tier"][str(t)] for t in tiers]
                  + [row["entries"][r["label"]] for r in readers] + [row["posts_read"][label] for label in partial]
                  for row in data["by_day"]]
                 + [["All days", sum(row["posts"] for row in data["by_day"]), totals["passages"]]
                    + [row["passages"] for row in data["tiers"]] + [r["entries"] for r in readers]
                    + [sum(row["posts_read"][label] for row in data["by_day"]) for label in partial]])
    out.append("")
    return out


def write_page(out_dir, name: str, text: str, data: dict) -> tuple[Path, Path]:
    """Write NAME.md and, beside it, NAME.json holding the data the page was rendered from."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    page, beside = out / f"{name}.md", out / f"{name}.json"
    page.write_bytes(text.encode("utf-8"))  # bytes, so no line ending is rewritten on the way out
    beside.write_bytes((json.dumps(data, indent=1, ensure_ascii=False) + "\n").encode("utf-8"))
    return page, beside


def render_tiers(passages: list[dict], tiers: list[int], expect: int, inline: bool, lead, noun: str) -> list[str]:
    """Every passage, tier by tier and day by day inside a tier."""
    out = []
    for tier in tiers:
        here = [p for p in passages if p["tier"] == tier]
        out += [f"## {noun} by {tier_name(tier, expect)} ({len(here):,})", ""]
        if not here:
            out += ["None.", ""]
            continue
        day = None
        for passage in here:
            if passage["day"] != day:
                day = passage["day"]
                n = sum(1 for p in here if p["day"] == day)
                out += [f"### {day} · {n:,} in this tier", ""]
            out += render_passage(passage, inline, lead)
    return out
