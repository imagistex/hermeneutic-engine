"""Passages with no place: what the readers of the run said the codebook has no code for.

    PYTHONPATH=src python3 -m hermeneutic_engine.views.candidates work/wiki --codebook MEMO --out DIR

Writes DIR/passages-with-no-place.md and, with the same data, DIR/passages-with-no-place.json.

A reader that meets a passage the codebook has no code for records an `unfit`
memo: the passage (an anchor, or the words it typed when they could not be
found), why no code holds it, and a name it would suggest. This page gathers
the unfit memos of the run's lenses, puts together the ones about the same
words (see passages.py), and sets them out in tiers by how many model families
flagged them. The rule for reading the tiers is the researcher's: two families
flagging the same words is convergence; one family alone is a finding about
that lens.

Reads the ledger and writes only to DIR. Makes no model call.
"""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import argparse
from collections import Counter

from hermeneutic_engine.views.run_lenses import EXPECT, Run, _kind, open_project
from hermeneutic_engine.views import passages as P

NAME = "passages-with-no-place"
TOP_NAMES = 15


def fold(name: str) -> str:
    """A suggested name as it is compared: whitespace runs made one space, ends trimmed, case folded."""
    return " ".join(str(name).split()).casefold()


def top_names(passages: list[dict], families: list[str], top: int = TOP_NAMES) -> list[dict]:
    """For each family, its most frequent suggested names. Names are the same
    only when they are identical after folding; similar names are not merged."""
    out = []
    for family in families:
        counts, forms, records, unnamed = Counter(), {}, 0, 0
        for passage in passages:
            for entry in passage["entries"]:
                if entry["family"] != family:
                    continue
                records += 1
                folded = fold(entry["suggested_name"])
                if not folded:
                    unnamed += 1
                    continue
                counts[folded] += 1
                forms.setdefault(folded, Counter())[entry["suggested_name"]] += 1
        ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
        shown = ranked[:top]
        cut = shown[-1][1] if len(ranked) > top else None
        out.append({
            "family": family, "records": records, "unnamed": unnamed, "distinct": len(counts),
            "once": sum(1 for n in counts.values() if n == 1),
            "top": [{"name": name, "count": n, "as_written": dict(forms[name])} for name, n in shown],
            "cut_count": cut,
            "tied_at_cut": sum(1 for _, n in ranked[top:] if n == cut) if cut is not None else 0,
        })
    return out


def collect(project, run: Run, top: int = TOP_NAMES) -> dict:
    """Everything the page says, as plain data. The page is rendered from this alone."""
    entries, counted, not_found, held = [], Counter(), Counter(), Counter()
    for memo in project.records("memo"):
        if memo.get("memo_type") != "unfit":
            continue
        index = run.counted(memo)
        if index is None:
            if memo.get("by") in run.reader_of:  # by a lens of the run, in a batch that had not finished
                held[run.readers[run.reader_of[memo["by"]]]["label"]] += 1
            continue
        about = memo.get("about") or []
        anchor = next((ref for ref in about if isinstance(ref, dict)), None)
        entry = {"id": memo["id"], "by": memo["by"], "reader": index, "anchor": anchor,
                 "unit": next((ref for ref in about if isinstance(ref, str) and _kind(ref) == "unit"), None),
                 "said": {"suggested_name": str(memo.get("suggested_name") or ""), "reason": str(memo.get("body") or "")}}
        if anchor is None and "quote_not_found" in memo:
            entry["quote_not_found"] = memo["quote_not_found"]
        counted[index] += 1
        not_found[index] += anchor is None
        entries.append(entry)

    passages, problems, checked = P.build(project, run, entries)
    for index, reader in enumerate(run.readers):  # two counts of the same thing, made separately, must agree
        if counted[index] != reader["unfit"]:
            problems.append(f"{reader['label']}: {counted[index]:,} unfit memos gathered here, "
                            f"{reader['unfit']:,} counted in the readers' report")
    tiers = P.tiers_of(passages, run.expect)
    per_day = {reader["label"]: Counter() for reader in run.readers}
    for passage in passages:
        for entry in passage["entries"]:
            per_day[entry["reader"]][passage["day"]] += 1
    families = list(dict.fromkeys(reader["family"] for reader in run.readers))
    return {
        "page": NAME, "made": run.made, "project": str(project.root), "codebook": run.codebook,
        "expect": run.expect, "complete": run.complete, "status": run.status_line(), "posts": len(run.posts),
        "readers": P.reader_rows(run, passages, tiers, counted, {"not_found": not_found}),
        "tiers": P.tier_rows(run, passages, tiers),
        "totals": {"entries": sum(counted.values()), "not_found": sum(not_found.values()),
                   "passages": len(passages), "anchors_checked": checked},
        "unfinished": {reader["label"]: held[reader["label"]] for reader in run.readers},
        "by_day": P.by_day(run, passages, per_day, tiers),
        "names": top_names(passages, families, top),
        "passages": passages,
        "problems": problems,
        "skipped_last_lines": dict(getattr(project, "skipped", {})),
    }


def _lead(entry: dict) -> tuple[str, str]:
    name = entry["suggested_name"]
    return (f"suggests {P.name_span(name)}" if name.strip() else "suggests no name"), entry["reason"]


def render(data: dict) -> str:
    totals, readers = data["totals"], data["readers"]
    out = []
    if data["status"]:
        out += [f"> {data['status']}", ""]
    out += ["# Passages with no place", "",
            f"Made {data['made']} from `{data['project']}` by `hermeneutic_engine.views.candidates`. This page is overwritten "
            "each time the command runs, so notes written on it will be lost.", "",
            f"When a reader applying codebook `{data['codebook']}` meets a passage the codebook has no code for, it "
            "records the passage, why no code holds it, and a name it would suggest. This page gathers those records "
            f"from the {len(readers)} reader{'' if len(readers) == 1 else 's'} of the full run: "
            f"{totals['entries']:,} records, which make {totals['passages']:,} passages.", "",
            "**How passages are made.** Records about the same post whose quoted bytes overlap are one passage, "
            "whichever readers made them. The passage shown is every byte from the start of the first quotation to "
            "the end of the last; where a reader quoted only part of it, its own quotation is given beside its name. "
            "Quotations that only touch are separate passages. A record whose words could not be found in the post "
            "points at no bytes: it stands alone and is marked *quotation not found*.", "",
            "**How to read the tiers.** A passage's tier is the number of model families that flagged it. The rule "
            "is the researcher's: two families flagging the same words is convergence; one family alone is a finding "
            "about that lens. A suggested name is a candidate. Nothing here has been added to the codebook.", "",
            "**Exactness.** Each passage is the bytes of the stored source between its offsets, set in a block so "
            "that nothing in it is read as formatting. A passage is never taken from a reader's copy of the words. "
            "Only a quotation that was not found is shown as the reader typed it, and it is marked as the reader's. "
            "The readers' reasons and suggested names are given as they wrote them. "
            f"{totals['anchors_checked']:,} anchors were checked against the source bytes"
            + (" and all matched." if not data["problems"] else "; see Problems below."), ""]
    out += P.render_notices(data, "unfit memo", "unfit memos")
    out += P.render_counts(data, "Records", "Flagged", [("Quotation not found", "not_found")],
                           "Records are unfit memos, counted from the ledger. A reader's passages are the passages "
                           "it took part in; the tier columns say how many families flagged each of those.")

    out += ["## Names suggested most often, by family", "",
            "Names are counted as the same only when they are identical after folding case and spacing, and are "
            "shown folded. Names that are merely alike are left apart for a person to read.", ""]
    for entry in data["names"]:
        out += [f"**{entry['family']}**: {entry['records']:,} records, {entry['distinct']:,} distinct names, "
                f"{entry['once']:,} of them used once"
                + (f"; {entry['unnamed']:,} records suggested no name" if entry["unnamed"] else "") + ".", ""]
        out += [f"{n}. {P.name_span(row['name'])} · {row['count']:,}" for n, row in enumerate(entry["top"], 1)]
        if entry["tied_at_cut"]:
            one = entry["tied_at_cut"] == 1
            out += ["", f"The list stops at {len(entry['top'])}. {entry['tied_at_cut']:,} more "
                    f"name{'' if one else 's'} also occur{'s' if one else ''} "
                    f"{entry['cut_count']:,} time{'' if entry['cut_count'] == 1 else 's'}; among names with the same "
                    "count the order is alphabetical."]
        out.append("")

    out += P.render_tiers(data["passages"], [row["tier"] for row in data["tiers"]], data["expect"], False, _lead,
                          "Flagged")
    return "\n".join(out).rstrip("\n") + "\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="The passages the run's readers said the codebook has no code for.")
    parser.add_argument("project")
    parser.add_argument("--codebook", required=True, help="ID of the codebook memo the run applied")
    parser.add_argument("--out", required=True, help=f"a directory; writes {NAME}.md and {NAME}.json there")
    parser.add_argument("--expect", type=int, default=EXPECT, help="how many readers a whole run has (default 3)")
    parser.add_argument("--top-names", type=int, default=TOP_NAMES, help="names listed for each family (default 15)")
    args = parser.parse_args(argv)
    project = open_project(args.project)
    run = Run(project, args.codebook, expect=args.expect)
    if not run.readers:
        print(f"no lens of the run is in the ledger for codebook {args.codebook}", file=sys.stderr)
        return 2
    data = collect(project, run, args.top_names)
    page, beside = P.write_page(args.out, NAME, render(data), data)
    if data["status"]:
        print(data["status"].replace("**", ""))
    print(f"{data['totals']['entries']:,} unfit memos ("
          + ", ".join(f"{r['label']} {r['entries']:,}" for r in data["readers"]) + f") in "
          f"{data['totals']['passages']:,} passages: "
          + ", ".join(f"{row['passages']:,} by {row['name']}" for row in data["tiers"]))
    print(f"wrote {page} ({page.stat().st_size:,} bytes) and {beside}")
    for problem in data["problems"]:
        print(f"PROBLEM: {problem}", file=sys.stderr)
    return 1 if data["problems"] else 0


if __name__ == "__main__":
    sys.exit(main())
