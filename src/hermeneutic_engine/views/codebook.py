"""A codebook laid out for a researcher to review and correct.

Each code shows what it is for, where it stops, which first-pass names it
gathers, and one passage it was drawn from. The page ends with what was left
out, what the proposer could not settle, and the proposer's memo.
"""

from __future__ import annotations

from collections import Counter

from ..methods.consolidate import codes_of, name_key, reader_label
from ..store import Project

SHOW_NAMES = 8  # merged names listed under each code
SHOW_UNACCOUNTED = 60


def _md(text) -> str:
    """Words from the ledger, shown as written. `<` is escaped so that a
    passage containing markup cannot hide part of itself."""
    return str(text or "").replace("<", "\\<")


def _proposer(lens: dict | None) -> str:
    if lens is None:
        return "an unknown lens"
    reader = lens.get("reader") or {}
    if reader.get("kind") == "human":
        return str(reader.get("id"))
    who = f"{reader.get('model') or reader.get('id')} ({reader.get('family') or reader.get('kind')})"
    if reader.get("requested"):
        who += f", answering in place of {reader['requested']}"
    if reader.get("kind") == "model" and not lens.get("reproducible", True):
        who += ", in conversation"
    return who


def _first_pass(project: Project, code_ids: list[str], order: dict[str, int]) -> list[dict]:
    """The first-pass codes under a code, following `merges` down through any
    earlier codebooks it was built from, in the order they were written."""
    out, seen, queue = [], set(), list(code_ids)
    while queue:
        cid = queue.pop(0)
        if cid in seen:
            continue
        seen.add(cid)
        code = project.get(cid)
        if code is None:
            continue
        if code.get("merges"):
            queue.extend(code["merges"])
        else:
            out.append(code)
    return sorted(out, key=lambda c: order.get(c["id"], len(order)))


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


def render(project: Project, codebook_memo_id: str) -> str:
    codes = codes_of(project, codebook_memo_id)
    memo = project.get(codebook_memo_id)
    book = memo.get("codebook") or {}
    counts = book.get("counts") or {}
    uses = Counter(coding["code"] for coding in project.records("coding"))
    order = {code["id"]: i for i, code in enumerate(project.records("code"))}
    labels: dict[str, str] = {}

    def label(lens_id: str) -> str:
        if lens_id not in labels:
            labels[lens_id] = reader_label(project.get(lens_id))
        return labels[lens_id]

    title = book.get("name") or codebook_memo_id
    out = [f"# Codebook `{title}`, proposed by {_md(_proposer(project.get(memo['by'])))}", ""]
    line = _plural(len(codes), "code")
    if counts:
        readers = list(dict.fromkeys(label(lid) for lid in book.get("source_lenses", [])))
        line += (f" · {counts.get('names_in', 0)} names"
                 + (f" from {', '.join(readers)}" if readers else "")
                 + f": {counts.get('names_placed', 0)} placed, {counts.get('names_left_out', 0)} left out, "
                   f"{counts.get('names_unaccounted', 0)} unaccounted")
    out += [line + f" · memo `{codebook_memo_id}`", ""]

    for n, code in enumerate(codes, 1):
        kind = "in vivo" if code.get("code_type") == "in_vivo" else "analytic"
        out += [f"## {n}. {_md(code['name'])}", "", f"{kind} · key `{code.get('key')}` · `{code['id']}`", ""]
        for title, field in (("Definition", "definition"), ("Apply when", "apply_when"),
                             ("Do not apply when", "do_not_apply_when"), ("Loaded", "loaded")):
            if code.get(field):
                out += [f"**{title}.** {_md(code[field])}", ""]

        names: dict[str, dict] = {}
        readers_here = set()
        for first in _first_pass(project, code.get("merges", []), order):
            entry = names.setdefault(name_key(first["name"]), {"written": Counter(), "uses": 0})
            entry["written"][first["name"]] += uses[first["id"]] or 1
            entry["uses"] += uses[first["id"]]
            readers_here.add(label(first.get("lens") or first["by"]))
        ranked = sorted(names.items(), key=lambda kv: (-kv[1]["uses"], kv[0]))
        # As in `gather`: the form used most, a tie going to the form written first.
        shown = [f"{_md(max(e['written'].items(), key=lambda kv: kv[1])[0])} ({_plural(e['uses'], 'use')})"
                 for _, e in ranked[:SHOW_NAMES]]
        if len(ranked) > SHOW_NAMES:
            shown.append(f"and {len(ranked) - SHOW_NAMES} more")
        out += [f"Merges {_plural(len(ranked), 'first-pass name')} from {_plural(len(readers_here), 'reader')}"
                + (": " + ", ".join(shown) if shown else "") + ".", ""]

        anchor = code.get("origin_anchor")
        if anchor:
            source = project.get(anchor.get("source")) or {}
            ctx = source.get("context") or {}
            where = " ".join(part for part in (f"from {_md(ctx['page'])}" if ctx.get("page") else "",
                                               f"at {_md(ctx['time'])}" if ctx.get("time") else "") if part)
            out += [f"Example, {where}:" if where else "Example:", ""]
            out += [f"> {_md(text)}" if text.strip() else ">" for text in anchor.get("exact", "").splitlines()]
            out.append("")

    out += ["## Left out", ""]
    left_out = book.get("left_out") or []
    unaccounted = book.get("unaccounted") or []
    for entry in left_out:
        why = _md(entry.get("why"))
        out.append(f"- {', '.join(_md(n) for n in entry.get('names', []))}" + (f": {why}" if why else ""))
    if left_out:
        out.append("")
    if unaccounted:
        listed = ", ".join(_md(n) for n in unaccounted[:SHOW_UNACCOUNTED])
        more = len(unaccounted) - SHOW_UNACCOUNTED
        out += [f"Neither placed in a code nor listed as left out ({len(unaccounted)}): {listed}"
                + (f", and {more} more" if more > 0 else "") + ".", ""]
    if not left_out and not unaccounted:
        out += ["Nothing was left out.", ""]

    out += ["## Questions for the researcher", ""]
    questions = book.get("questions") or []
    out += [f"{i}. {_md(' '.join(str(q).split()))}" for i, q in enumerate(questions, 1)] or ["None."]
    out.append("")

    out += ["## Memo", "", _md(memo.get("body")).strip() or "None.", ""]
    return "\n".join(out)
