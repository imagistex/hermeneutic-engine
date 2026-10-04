"""The in vivo lexicon: the writers' own terms, traced mechanically.

A term is nominated by a reader, by frequency, or by the researcher. Everything
after that is counting: every unit the term occurs in, who first used it and
when, how many distinct names took it up, on how many pages, day by day, and
how much of it was later removed. No model is involved in the counts.

A term that occurs nowhere is reported as absent, with the reminder that
absence from this archive is not evidence that the idea was absent.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict

from ..anchors import resolve
from ..store import Project

_EDGE_L = r"(?<![A-Za-z0-9])"
_EDGE_R = r"(?![A-Za-z0-9])"


def parse_terms(text: str) -> list[dict]:
    """One term per line. `label = regex` gives a custom pattern; `#` starts a comment.

    A plain term matches case-insensitively as a whole word or phrase, with any
    run of spaces, hyphens or underscores between its words. A term written in capitals
    matches only in capitals, because LIVE and live are not the same sign.
    """
    terms = []
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        if " = " in line:
            label, pattern = (part.strip() for part in line.split(" = ", 1))
            terms.append({"term": label, "regex": re.compile(pattern)})
            continue
        words = [re.escape(word) for word in re.split(r"[\s\-]+", line)]
        flags = 0 if line.upper() == line and any(c.isalpha() for c in line) else re.IGNORECASE
        terms.append({"term": line, "regex": re.compile(_EDGE_L + r"[\s\-_]+".join(words) + _EDGE_R, flags)})
    return terms


def trace(project: Project, terms: list[dict], kinds=("post", "text")) -> list[dict]:
    units = [u for u in project.records("unit") if u["unit_kind"] in kinds]
    units.sort(key=lambda u: (u["context"].get("first_seen", {}).get("time") or "", u["id"]))
    texts = [(unit, project.unit_text(unit)) for unit in units]
    results = []
    for term in terms:
        hits = []
        occurrences = 0
        for unit, text in texts:
            found = term["regex"].findall(text)
            if found:
                occurrences += len(found)
                hits.append(unit)
        entry = {"term": term["term"], "pattern": term["regex"].pattern, "units": len(hits),
                 "occurrences": occurrences, "of_units": len(units)}
        if hits:
            first = hits[0]
            match = term["regex"].search(project.unit_text(first))
            anchor = resolve(project.source_bytes(first["source"]), first["start"], first["end"], match.group(0))
            by_day = Counter((u["context"]["first_seen"]["time"] or "")[:10] for u in hits)
            signers = {u["context"].get("signature") or u["context"].get("introduced_by") for u in hits}
            entry.update({
                "signers": len(signers - {None}),
                "pages": len({u["context"]["page"] for u in hits}),
                "first": {
                    "unit": first["id"], "time": first["context"]["first_seen"]["time"],
                    "time_grade": first["context"]["first_seen"].get("time_grade"),
                    "signature": first["context"].get("signature"), "saved_as": first["context"].get("introduced_by"),
                    "page": first["context"]["page"], "anchor": {"source": first["source"], **anchor},
                    "text": project.unit_text(first), "span": [match.start(), match.end()],
                },
                "last_new_use": hits[-1]["context"]["first_seen"]["time"],
                "by_day": dict(sorted(by_day.items())),
                "peak_day": by_day.most_common(1)[0],
                "later_removed": sum(1 for u in hits if u["context"].get("first_removed_in")),
            })
        results.append(entry)
    return results


def render(results: list[dict], title: str = "In vivo lexicon") -> str:
    present = sorted((r for r in results if r["units"]), key=lambda r: (r["first"]["time"], r["term"]))
    absent = [r for r in results if not r["units"]]
    total = results[0]["of_units"] if results else 0
    out = [f"# {title}", "",
           f"Counted mechanically over {total:,} statements (signed posts and unsigned prose). "
           "A statement is counted once, at its first appearance on a page. "
           "\"Names\" is the number of distinct signatures (or saving names, where unsigned) using the term.", "",
           "## Terms, in order of first appearance", "",
           "| Term | First used | By | Statements | Names | Pages | Busiest day | Last new use | Later removed |",
           "|---|---|---|---|---|---|---|---|---|"]
    for r in present:
        first = r["first"]
        who = first["signature"] or first["saved_as"] or "unsigned"
        day, n = r["peak_day"]
        out.append(f"| {r['term']} | {first['time'][:16].replace('T', ' ')} | {who} | {r['units']:,} | {r['signers']:,} | "
                   f"{r['pages']:,} | {day[5:]} ({n}) | {r['last_new_use'][:10]} | "
                   f"{r['later_removed'] / r['units']:.0%} |")
    out += ["", "## First uses", ""]
    for r in present:
        first = r["first"]
        text = first["text"]  # cut around the match itself, not the first lookalike
        lo, hi = max(0, first["span"][0] - 110), min(len(text), first["span"][1] + 110)
        snippet = ("…" if lo else "") + " ".join(text[lo:hi].split()) + ("…" if hi < len(text) else "")
        out += [f"**{r['term']}** · {first['time']} · {first['page']} · `{first['unit']}`", "",
                f"> {snippet}", ""]
    if absent:
        out += ["## Searched for and not found", "",
                "These patterns occur in no statement. Absence from this archive is not evidence that the idea was "
                "absent: the archive holds only what was written to the wiki and kept by the publisher.", ""]
        out += [f"- {r['term']} (`{r['pattern']}`)" for r in absent]
        out.append("")
    return "\n".join(out) + "\n"
