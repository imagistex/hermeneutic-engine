"""Words that stood out: every use of the codebook's `tone-evocative` code by the readers of the run.

    PYTHONPATH=src python3 -m hermeneutic_engine.views.standout work/wiki --codebook MEMO --out DIR [--key tone-evocative]

Writes DIR/words-that-stood-out.md and, with the same data, DIR/words-that-stood-out.json.

One code of the codebook asks a reader to mark wording that strikes it and to
say in a note what it calls up. This page gathers every coding with that code
made by the run's lenses, puts together the ones about the same words (see
passages.py), and sets them out in tiers by how many model families marked
them. Before the tiers it lists the wordings marked in the most posts, each
exactly as a reader marked it.

Reads the ledger and writes only to DIR. Makes no model call.
"""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import argparse
from collections import Counter, defaultdict

from hermeneutic_engine.views.run_lenses import EXPECT, Run, open_project
from hermeneutic_engine.views import passages as P
from hermeneutic_engine.methods import focused_coding

NAME = "words-that-stood-out"
KEY = "tone-evocative"
TOP_WORDINGS = 50


def wordings(passages: list[dict], families: list[str]) -> list[dict]:
    """Each distinct wording as a reader marked it (the bytes of one coding's
    anchor, capitals and punctuation included), with the posts it was marked
    in. Most posts first; ties by marks, then by the text itself."""
    posts, marks = defaultdict(set), Counter()
    by_family: dict = defaultdict(lambda: defaultdict(set))
    for passage in passages:
        for entry in passage["entries"]:
            text = entry["exact"]
            if text is None:
                continue
            posts[text].add(passage["unit"])
            marks[text] += 1
            by_family[text][entry["family"]].add(passage["unit"])
    rows = [{"text": text, "posts": len(units), "marks": marks[text],
             "families": {family: len(by_family[text][family]) for family in families if family in by_family[text]}}
            for text, units in posts.items()]
    rows.sort(key=lambda row: (-row["posts"], -row["marks"], row["text"]))
    return rows


def collect(project, run: Run, key: str = KEY, top: int = TOP_WORDINGS) -> dict:
    """Everything the page says, as plain data. The page is rendered from this alone."""
    code = next((c for c in focused_coding.codes_of(project, run.codebook) if c.get("key") == key), None)
    if code is None:
        raise ValueError(f"codebook {run.codebook} has no code with the key {key!r}")
    entries, counted, held = [], Counter(), Counter()
    for coding in project.records("coding"):
        if coding.get("code") != code["id"]:
            continue
        index = run.counted(coding)
        if index is None:
            if coding.get("by") in run.reader_of:  # by a lens of the run, in a batch that had not finished
                held[run.readers[run.reader_of[coding["by"]]]["label"]] += 1
            continue
        counted[index] += 1
        entries.append({"id": coding["id"], "by": coding["by"], "reader": index, "unit": coding.get("unit"),
                        "anchor": coding.get("anchor"), "said": {"note": str(coding.get("note") or "")}})

    passages, problems, checked = P.build(project, run, entries)
    tiers = P.tiers_of(passages, run.expect)
    per_day = {reader["label"]: Counter() for reader in run.readers}
    marked: dict[str, set] = {reader["label"]: set() for reader in run.readers}
    for passage in passages:
        for entry in passage["entries"]:
            per_day[entry["reader"]][passage["day"]] += 1
            marked[entry["reader"]].add(passage["unit"])
    posts_marked = Counter({index: len(marked[reader["label"]]) for index, reader in enumerate(run.readers)})
    families = list(dict.fromkeys(reader["family"] for reader in run.readers))
    rows = wordings(passages, families)
    return {
        "page": NAME, "made": run.made, "project": str(project.root), "codebook": run.codebook,
        "code": {field: code.get(field) for field in ("id", "key", "name", "definition", "apply_when",
                                                       "do_not_apply_when", "loaded")},
        "expect": run.expect, "complete": run.complete, "status": run.status_line(), "posts": len(run.posts),
        "readers": P.reader_rows(run, passages, tiers, counted, {"posts_marked": posts_marked}),
        "tiers": P.tier_rows(run, passages, tiers),
        "totals": {"entries": sum(counted.values()), "passages": len(passages), "anchors_checked": checked,
                   "posts_marked": len({p["unit"] for p in passages}),
                   "wordings": len(rows), "wordings_repeated": sum(1 for row in rows if row["posts"] > 1)},
        "unfinished": {reader["label"]: held[reader["label"]] for reader in run.readers},
        "by_day": P.by_day(run, passages, per_day, tiers),
        "top_wordings": top,
        "wordings": rows,
        "passages": passages,
        "problems": problems,
        "skipped_last_lines": dict(getattr(project, "skipped", {})),
    }


def _lead(entry: dict) -> tuple[str, str]:
    return ("" if entry["note"].strip() else "left no note"), entry["note"]


def _flat(value) -> str:
    if isinstance(value, (list, tuple)):
        value = "; ".join(str(v) for v in value)
    return " ".join(str(value or "").split())


def render(data: dict) -> str:
    totals, readers, code = data["totals"], data["readers"], data["code"]
    out = []
    if data["status"]:
        out += [f"> {data['status']}", ""]
    out += ["# Words that stood out", "",
            f"Made {data['made']} from `{data['project']}` by `hermeneutic_engine.views.standout`. This page is overwritten "
            "each time the command runs, so notes written on it will be lost.", "",
            f"One code of codebook `{data['codebook']}` asks a reader to mark wording that strikes it. The codebook "
            "has it so:", "",
            f"> **[{P.md_text(str(code['key']))}] {P.md_text(_flat(code['name']))}.** "
            f"{P.md_text(_flat(code['definition']))}"]
    for label, field in (("Apply when", "apply_when"), ("Do not apply when", "do_not_apply_when"), ("Loaded", "loaded")):
        if _flat(code.get(field)):
            out += [">", f"> {label}: {P.md_text(_flat(code[field]))}"]
    out += ["",
            f"This page gathers every use of that code by the {len(readers)} reader{'' if len(readers) == 1 else 's'} "
            f"of the full run: {totals['entries']:,} marks in {totals['posts_marked']:,} posts, which make "
            f"{totals['passages']:,} passages. Under each passage is what each reader wrote in its note.", "",
            "**How passages are made.** Marks in the same post whose bytes overlap are one passage, whichever "
            "readers made them. The passage shown is every byte from the start of the first mark to the end of the "
            "last; where a reader marked only part of it, its own words are given beside its name. Marks that only "
            "touch are separate passages.", "",
            "**How to read the tiers.** A passage's tier is the number of model families that marked it. What "
            "strikes a reader is partly the reader, so these are leads for a person to look at and not findings.", "",
            "**Exactness.** Each passage and each wording is the bytes of the stored source between its offsets, "
            "capitals and punctuation as they are. Nothing is taken from a reader's copy of the words. The readers' "
            "notes are given as they wrote them. "
            f"{totals['anchors_checked']:,} anchors were checked against the source bytes"
            + (" and all matched." if not data["problems"] else "; see Problems below."), ""]
    out += P.render_notices(data, "coding", "codings")
    out += P.render_counts(data, "Marks", "Marked", [("Posts with a mark", "posts_marked")],
                           "Marks are codings with this code, counted from the ledger. A reader's passages are the "
                           "passages it took part in; the tier columns say how many families marked each of those.")

    repeated = [row for row in data["wordings"] if row["posts"] > 1]
    shown = repeated[:data["top_wordings"]]
    out += ["## The wordings marked most often", "",
            "Each line is one wording exactly as a reader marked it: the same bytes, capitals and punctuation "
            "included, so a word and the same word in capitals are two lines. A wording counts once for each post in "
            "which some reader marked exactly those bytes; the families are the readers that marked exactly that, "
            "with the number of posts for each. "
            f"Distinct wordings: {totals['wordings']:,}. Marked in more than one post: "
            f"{totals['wordings_repeated']:,}. Marked in one post only: "
            f"{totals['wordings'] - totals['wordings_repeated']:,}. The list below has those marked in more than "
            "one post, most posts first.", ""]
    if not shown:
        out += ["No wording was marked in more than one post.", ""]
    blocks: list[str] = []
    for n, row in enumerate(shown, 1):
        who = ", ".join(f"{family} {posts:,}" for family, posts in row["families"].items())
        if P.fits_inline(row["text"]):
            out.append(f"{n}. {P.code_span(row['text'])} · {row['posts']:,} posts · {who}")
        else:
            out.append(f"{n}. (wording {n}, set below) · {row['posts']:,} posts · {who}")
            blocks += [f"Wording {n}:", "", P.fence(row["text"]), ""]
    if shown:
        out.append("")
    out += blocks
    if len(repeated) > len(shown):
        cut = shown[-1]["posts"]
        tied = sum(1 for row in repeated[len(shown):] if row["posts"] == cut)
        out += [f"The list stops at {len(shown)}. {len(repeated) - len(shown):,} more wordings were marked in more "
                f"than one post, {tied:,} of them in {cut:,} posts like the last one shown; among wordings with the "
                "same counts the order is that of the text. All of them are in the JSON beside this page.", ""]

    out += P.render_tiers(data["passages"], [row["tier"] for row in data["tiers"]], data["expect"], True, _lead,
                          "Marked")
    return "\n".join(out).rstrip("\n") + "\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="The wording the run's readers marked as standing out.")
    parser.add_argument("project")
    parser.add_argument("--codebook", required=True, help="ID of the codebook memo the run applied")
    parser.add_argument("--out", required=True, help=f"a directory; writes {NAME}.md and {NAME}.json there")
    parser.add_argument("--key", default=KEY, help=f"key of the code to gather (default {KEY})")
    parser.add_argument("--expect", type=int, default=EXPECT, help="how many readers a whole run has (default 3)")
    parser.add_argument("--top", type=int, default=TOP_WORDINGS,
                        help=f"wordings listed before the tiers (default {TOP_WORDINGS})")
    args = parser.parse_args(argv)
    project = open_project(args.project)
    run = Run(project, args.codebook, expect=args.expect)
    if not run.readers:
        print(f"no lens of the run is in the ledger for codebook {args.codebook}", file=sys.stderr)
        return 2
    data = collect(project, run, args.key, args.top)
    page, beside = P.write_page(args.out, NAME, render(data), data)
    if data["status"]:
        print(data["status"].replace("**", ""))
    print(f"{data['totals']['entries']:,} codings of [{args.key}] ("
          + ", ".join(f"{r['label']} {r['entries']:,}" for r in data["readers"]) + f") in "
          f"{data['totals']['passages']:,} passages: "
          + ", ".join(f"{row['passages']:,} by {row['name']}" for row in data["tiers"]))
    print(f"wrote {page} ({page.stat().st_size:,} bytes) and {beside}")
    for problem in data["problems"]:
        print(f"PROBLEM: {problem}", file=sys.stderr)
    return 1 if data["problems"] else 0


if __name__ == "__main__":
    sys.exit(main())
