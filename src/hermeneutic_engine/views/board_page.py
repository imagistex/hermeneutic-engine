"""One round of the readers' board as a Markdown page for the researcher.

Reads the ledger only. Every reply, request and closing is shown exactly as it
is kept; nothing is trimmed or tidied. The notes a reply answers are folded
beneath it, so the reply can be read against what it was replying to.

    PYTHONPATH=src python3 -m hermeneutic_engine.views.board_page work/wiki --round 1 --out PAGE.md
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path

from hermeneutic_engine.store import Project

ORDINAL = ["first", "second", "third", "fourth", "fifth"]


def quoted(text: str) -> str:
    """A block quotation that keeps every line as written."""
    return "\n".join("> " + line if line else ">" for line in text.split("\n"))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("project")
    parser.add_argument("--round", type=int, default=1)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)

    project = Project(args.project)
    lenses = {lens["id"]: lens for lens in project.records("lens")}
    memos = project.records("memo")
    memo_by_id = {memo["id"]: memo for memo in memos}
    by_activity = defaultdict(list)
    for memo in memos:
        by_activity[memo.get("activity")].append(memo)

    def is_round(activity: dict) -> bool:
        method = lenses.get(activity.get("lens"), {}).get("method", {})
        return activity.get("type") == "board" and method.get("name") == "board" and method.get("round") == args.round

    activities = sorted((a for a in project.records("activity") if is_round(a)),
                        key=lambda a: lenses[a["lens"]]["reader"].get("own", 0))
    if not activities:
        raise SystemExit(f"no board activity for round {args.round} in {args.project}")

    # Which notebook each first-pass lens belongs to, and who wrote it.
    notebooks = lenses[activities[0]["lens"]]["reader"].get("notebooks") or []
    notebook_of = {lens_id: index for index, group in enumerate(notebooks) for lens_id in group}

    def who(index: int) -> str:
        reader = lenses[notebooks[index][0]]["reader"]
        return f"{reader.get('family')}, {reader.get('model')}"

    out = [f"# The readers' board, round {args.round}", "",
           "*Generated from the ledger. Do not write in this page; it is overwritten on each render. "
           "Every message is shown exactly as the reader gave it.*", "",
           "The readers were not told which family wrote which notebook. This page says, because it is for you.", ""]

    out += ["| Notebook | Written by | Signed this round | Replies | To the whole board | Asked to see again | "
            "Quotations checked | Not found | Status |", "|---|---|---|---|---|---|---|---|---|"]
    for activity in activities:
        lens = lenses[activity["lens"]]
        own = lens["reader"].get("own", 0)
        counts = activity.get("counts") or {}
        signed = activity.get("signed")
        out.append(f"| {ORDINAL[own]} | {who(own)} | {('`' + signed + '`') if signed and signed.strip() else 'unsigned'} | "
                   f"{counts.get('replies', 0)} | {counts.get('replies_to_board', 0)} | {counts.get('look_again', 0)} | "
                   f"{counts.get('quotes_checked', 0)} | {counts.get('quotes_not_found', 0)} | {activity.get('status')} |")
    out.append("")

    # Threads that drew more than one reader.
    drew = defaultdict(set)
    for activity in activities:
        own = lenses[activity["lens"]]["reader"].get("own", 0)
        for memo in by_activity[activity["id"]]:
            if memo.get("memo_type") == "board_reply" and memo.get("thread") != "board":
                drew[memo["thread"]].add(ORDINAL[own])
    shared = {thread: sorted(names) for thread, names in drew.items() if len(names) > 1}
    if shared:
        out += ["**Threads that drew a reply from more than one reader:** "
                + "; ".join(f"{thread} ({', '.join(names)})" for thread, names in sorted(shared.items())), ""]

    for activity in activities:
        lens = lenses[activity["lens"]]
        own = lens["reader"].get("own", 0)
        call = activity.get("call") or {}
        kept = by_activity[activity["id"]]
        out += [f"## The {ORDINAL[own]} notebook's reader ({who(own)})", "",
                f"Lens `{lens['id']}`, activity `{activity['id']}`. Model reported: {call.get('model_reported')}. "
                f"Tokens in {call.get('tokens_in')}, out {call.get('tokens_out')}; {call.get('duration_s')} seconds.", ""]
        replies = [m for m in kept if m.get("memo_type") == "board_reply"]
        requests = [m for m in kept if m.get("memo_type") == "board_request"]
        closings = [m for m in kept if m.get("memo_type") == "board_closing"]
        if not kept:
            out += ["This reader returned nothing: no replies, no requests, no closing.", ""]
        if replies:
            out += ["### Replies", ""]
        for memo in replies:
            where = "the whole board" if memo.get("thread") == "board" else f"thread {memo.get('thread')}"
            to = memo.get("to")
            out += [f"**On {where}**, to: {to if to and to.strip() else '(no one named)'}", "", quoted(memo.get("body", "")), ""]
            if memo.get("quotes_not_found"):
                out += ["Quoted, and not found among the notes: " + "; ".join(f"`{q}`" for q in memo["quotes_not_found"]), ""]
            notes = [memo_by_id[note_id] for note_id in memo.get("about") or [] if note_id in memo_by_id]
            if notes:
                out += [f"> [!note]- The notes in {memo.get('thread')}"]
                for note in notes:
                    index = notebook_of.get(note.get("by"), -1)
                    label = ORDINAL[index] if index >= 0 else "?"
                    mine = ", this reader's own" if index == own else ""
                    signed = note.get("signed")
                    out += [f"> **{label} notebook{mine}**, {('signed ' + signed) if signed else 'unsigned'}:", ">"]
                    out += ["> " + line for line in note.get("body", "").split("\n")]
                    out += [">"]
                out += [""]
        if requests:
            out += ["### Asked to see again", ""]
            for memo in requests:
                out += [f"- **{memo.get('thread')}**: {memo.get('body') or '(no reason given)'}"]
            out += [""]
        if closings:
            out += ["### Closing", ""]
            for memo in closings:
                out += [quoted(memo.get("body", "")), ""]
                if memo.get("quotes_not_found"):
                    out += ["Quoted, and not found among the notes: " + "; ".join(f"`{q}`" for q in memo["quotes_not_found"]), ""]

    Path(args.out).write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"wrote {args.out}: {len(activities)} readers")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
