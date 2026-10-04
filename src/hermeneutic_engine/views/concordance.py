"""Concordance: every use of a word, shown in its line of context.

A keyword-in-context listing is the plainest instrument for a word that may
be doing two jobs at once. It does not decide what a word means in any one
place. It lays the uses side by side, in time order, so a reader can.
"""

from __future__ import annotations

from ..store import Project
from .lexicon import parse_terms


def lines(project: Project, regex, kinds=("post", "text"), width: int = 60) -> list[dict]:
    units = [u for u in project.records("unit") if u["unit_kind"] in kinds]
    units.sort(key=lambda u: (u["context"].get("first_seen", {}).get("time") or "", u["id"]))
    rows = []
    for unit in units:
        text = project.unit_text(unit)
        for match in regex.finditer(text):
            left = " ".join(text[max(0, match.start() - width):match.start()].split())
            right = " ".join(text[match.end():match.end() + width].split())
            ctx = unit["context"]
            rows.append({
                "time": ctx["first_seen"]["time"], "who": ctx.get("signature") or ctx.get("introduced_by") or "unsigned",
                "page": ctx["page"], "unit": unit["id"], "left": left, "key": match.group(0), "right": right,
            })
    return rows


def _cell(text: str) -> str:
    return text.replace("|", "¦").replace("\n", " ")


def render(project: Project, terms_text: str, title: str, per_term: int = 60) -> str:
    """One section per term. Long concordances are thinned evenly across time,
    so the lines shown run from the first use to the last."""
    out = [f"# {title}", "",
           "Each line is one use of the word, with the text on either side, in order of first appearance. "
           "Where a word has more uses than fit, every nth is shown so the lines still span the whole period. "
           "Nothing here decides what a word means; it puts the uses side by side.", ""]
    for term in parse_terms(terms_text):
        rows = lines(project, term["regex"])
        out += [f"## {term['term']}", ""]
        if not rows:
            out += ["No uses.", ""]
            continue
        statements = len({r["unit"] for r in rows})
        names = len({r["who"] for r in rows})
        shown = rows if len(rows) <= per_term else [rows[round(i * (len(rows) - 1) / (per_term - 1))] for i in range(per_term)]
        out += [f"{len(rows):,} uses in {statements:,} statements under {names:,} names, "
                f"{rows[0]['time'][:10]} to {rows[-1]['time'][:10]}."
                + (f" Showing {len(shown)}." if len(shown) < len(rows) else ""), "",
                "| When | Who | | Word | |", "|---|---|---:|:---:|:---|"]
        for r in shown:
            out.append(f"| {r['time'][5:16].replace('T', ' ')} | {_cell(r['who'])} | {_cell(r['left'])} | "
                       f"**{_cell(r['key'])}** | {_cell(r['right'])} |")
        out.append("")
    return "\n".join(out) + "\n"
