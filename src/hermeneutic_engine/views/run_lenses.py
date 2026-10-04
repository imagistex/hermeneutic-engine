"""The lenses of a full run, found in the ledger, and what each reader has done.

    PYTHONPATH=src python3 -m hermeneutic_engine.views.run_lenses work/wiki --codebook MEMO [--out DIR] [--expect 3]

A full run is one codebook applied by several readers, each under version 1 of
the focused-coding instructions and given the analytic codes only. The ledger
also holds test lenses on older codebooks, so the lenses of the run are found
by what they declare and never from a list of IDs: every lens whose reader
names the codebook and whose method is focused-coding, version "1", with code
types ["analytic"].

A reader is one reading. If another model answered some batches in place of
the one asked, or the harness changed version overnight, the ledger holds more
than one lens for the same reading (see focused_coding.readings_of). Those
lenses are put together here as one reader.

Only finished readings count, as in the engine's own views: a record is counted
when a finished ("ok") read activity of its own lens made it.

This module reads and never writes to the project. It opens it through
ReadOnlyProject, which can be used while a lane is appending to the ledger.
"""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True  # importing the engine must leave nothing new under src/

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

# Imported as a package module; no path bootstrap is needed.

from hermeneutic_engine.ids import utc_now
from hermeneutic_engine.methods import focused_coding
from hermeneutic_engine.store import KIND_BY_PREFIX, LedgerError, Project

NOT_FOUND = "quotation not found in unit"
RUN_VERSION = "1"
RUN_CODE_TYPES = ["analytic"]
EXPECT = 3  # readers a full run has: one each from three model families


# ---- reading a ledger that may be being written ----------------------------

class ReadOnlyProject(Project):
    """A project opened for reading only, while another process may be appending to it.

    It differs from Project in three ways.

    A last ledger line that does not parse is skipped and named in `skipped`:
    a writer was in the middle of it. A line that does not parse anywhere else
    is still an error.

    The activity ledger is read before any other. A reader's batch is written
    as its codings, failures and memos and then its activity record, each line
    flushed as it is written. So every finished activity seen here has all of
    its records in the files read after it, and a record whose activity is not
    seen here belongs to a batch that had not finished. Callers leave those
    out and count them (`Run.unfinished`).

    Nothing can be written through it: `append`, the lock and the blob writers
    all refuse.
    """

    def __init__(self, root):
        super().__init__(root)
        self.skipped: dict[str, int] = {}  # ledger kind -> number of the last line, which did not parse

    def _load(self, kind: str) -> dict[str, dict]:
        records = self._records.get(kind)
        if records is not None:
            return records
        if kind != "activity":
            self._load("activity")
        records = {}
        path = self.ledger_path(kind)
        if path.exists():
            # Split on the newline byte only. str.splitlines would also break a
            # line at U+0085 or U+2028, which the damaged encodings in the
            # sources can put inside a record.
            lines = path.read_bytes().split(b"\n")
            last = max((n for n, line in enumerate(lines) if line.strip()), default=-1)
            for n, line in enumerate(lines):
                if not line.strip():
                    continue
                try:
                    rec = json.loads(line.decode("utf-8"))
                    rid = rec["id"]
                except (ValueError, KeyError, TypeError):
                    if n != last:
                        raise LedgerError(f"{path.name}: line {n + 1} does not parse and is not the last line") from None
                    self.skipped[kind] = n + 1
                    continue
                earlier = records.get(rid)
                if earlier is not None and earlier != rec:
                    self.conflicts.append(rid)
                    continue  # the first one written stands, as in Project
                records[rid] = rec
        self._records[kind] = records
        return records

    def _refuse(self, *args, **kwargs):
        raise LedgerError(f"{self.root} was opened read-only")

    append = _refuse
    _acquire_lock = _refuse
    _handle = _refuse
    _put = _refuse


def open_project(root) -> ReadOnlyProject:
    return ReadOnlyProject(root)


# ---- the lenses of a run ----------------------------------------------------

def _kind(rid) -> str | None:
    return KIND_BY_PREFIX.get(str(rid).split(":", 1)[0])


def is_run_lens(lens: dict, codebook_memo_id: str) -> bool:
    reader, method = lens.get("reader") or {}, lens.get("method") or {}
    return (reader.get("codebook") == codebook_memo_id
            and method.get("name") == focused_coding.METHOD["name"]
            and str(method.get("version")) == RUN_VERSION
            and method.get("code_types") == RUN_CODE_TYPES)


def readings(project: Project, lenses: list[dict]) -> list[list[dict]]:
    """The lenses put together reading by reading, in the order the readings
    began (the order their first lenses stand in the ledger). Inside a reading
    the lens of the model that was asked comes first, and a lens where another
    model answered in its place comes after it."""
    by_id = {lens["id"]: lens for lens in lenses}
    order = {lens["id"]: n for n, lens in enumerate(lenses)}  # `lenses` is in ledger order
    groups, placed = [], set()
    for lens in lenses:
        if lens["id"] in placed:
            continue
        same = focused_coding.readings_of(project, lens) & by_id.keys()
        members = sorted((by_id[lid] for lid in same),
                         key=lambda l: (bool((l.get("reader") or {}).get("requested")), order[l["id"]]))
        placed |= same
        groups.append(members)
    return groups


class Run:
    """The readers of one full run and what the ledger holds for each.

    `readers` is a list of plain dicts (see `as_json`). The rest is for the
    pages built on top: `finished` maps each finished read activity of a run
    lens to that lens, `reader_of` maps a lens to its reader's place in
    `readers`, `read_by` maps a unit to the readers that have finished reading
    it, and `posts` holds every unit of kind post.
    """

    def __init__(self, project: Project, codebook_memo_id: str, expect: int = EXPECT):
        memo = project.get(codebook_memo_id)
        if memo is None or memo.get("kind") != "memo" or memo.get("memo_type") != "codebook":
            raise ValueError(f"not a codebook memo: {codebook_memo_id}")
        self.project = project
        self.codebook = codebook_memo_id
        self.expect = expect
        self.made = utc_now()

        self.readers: list[dict] = []
        self.reader_of: dict[str, int] = {}
        lenses = [lens for lens in project.records("lens") if is_run_lens(lens, codebook_memo_id)]
        for members in readings(project, lenses):
            first = members[0].get("reader") or {}
            asked = str(first.get("requested") or first.get("model") or "unknown model")
            family = str(first.get("family") or "unknown family")
            for lens in members:
                self.reader_of[lens["id"]] = len(self.readers)
            self.readers.append({
                "family": family, "model": asked, "label": f"{family} ({asked})",
                "lens_ids": [lens["id"] for lens in members],
                "lenses": [{"id": lens["id"], "model": (lens.get("reader") or {}).get("model"),
                            "answering_in_place_of": (lens.get("reader") or {}).get("requested"),
                            "harness": (lens.get("reader") or {}).get("harness"), "declared": lens.get("at")}
                           for lens in members],
                "activities_ok": 0, "activities_failed": 0, "units_read": 0, "posts_read": 0, "posts_total": 0,
                "complete": False, "codings": 0, "posts_uncoded": 0, "unfit": 0, "unfit_not_found": 0,
                "field_notes": 0, "signatures": [], "quotations_not_found": 0, "failures": {},
                "unfinished": {},
            })
        twice = Counter(reader["label"] for reader in self.readers)
        for reader in self.readers:  # two readings asked of the same model are told apart by their first lens
            if twice[reader["label"]] > 1:
                reader["label"] += f" [{reader['lens_ids'][0]}]"

        self.finished: dict[str, str] = {}
        self.read_by: dict[str, set[int]] = defaultdict(set)
        failed: set[str] = set()
        for activity in project.records("activity"):
            index = self.reader_of.get(activity.get("lens"))
            if index is None or activity.get("type") != "read":
                continue
            if activity.get("status") == "ok":
                self.finished[activity["id"]] = activity["lens"]
                self.readers[index]["activities_ok"] += 1
                for uid in activity.get("used") or []:
                    if _kind(uid) == "unit":
                        self.read_by[uid].add(index)
            else:
                failed.add(activity["id"])
                self.readers[index]["activities_failed"] += 1

        self.posts: dict[str, dict] = {unit["id"]: unit for unit in project.records("unit")
                                       if unit.get("unit_kind") == "post"}
        units_read: list[set] = [set() for _ in self.readers]
        for uid, who in self.read_by.items():
            for index in who:
                units_read[index].add(uid)

        coded: list[set] = [set() for _ in self.readers]
        signatures: list[Counter] = [Counter() for _ in self.readers]
        failures: list[Counter] = [Counter() for _ in self.readers]
        unfinished: list[Counter] = [Counter() for _ in self.readers]

        def place(record: dict) -> int | None:
            """Where a record is counted: its reader's place if a finished read
            activity of its own lens made it. A record by a run lens that no
            finished activity made is counted as unfinished, and left out."""
            index = self.counted(record)
            if index is None and record.get("by") in self.reader_of and record.get("activity") not in failed:
                unfinished[self.reader_of[record["by"]]][record["kind"]] += 1
            return index

        for coding in project.records("coding"):
            index = place(coding)
            if index is not None:
                self.readers[index]["codings"] += 1
                coded[index].add(coding.get("unit"))
        for memo in project.records("memo"):
            if memo.get("memo_type") not in ("unfit", "field_note"):
                continue
            index = place(memo)
            if index is None:
                continue
            if memo["memo_type"] == "unfit":
                self.readers[index]["unfit"] += 1
                if not any(isinstance(ref, dict) for ref in memo.get("about") or []):
                    self.readers[index]["unfit_not_found"] += 1
            else:
                self.readers[index]["field_notes"] += 1
                signatures[index][memo.get("signed") or None] += 1
        for failure in project.records("failure"):
            if failure.get("activity") in failed and failure.get("by") in self.reader_of:
                failures[self.reader_of[failure["by"]]][str(failure.get("reason"))] += 1
                continue
            index = place(failure)
            if index is not None:
                failures[index][str(failure.get("reason"))] += 1

        for index, reader in enumerate(self.readers):
            read_posts = units_read[index] & self.posts.keys()
            reader["units_read"] = len(units_read[index])
            reader["posts_read"] = len(read_posts)
            reader["posts_total"] = len(self.posts)
            reader["complete"] = bool(self.posts) and len(read_posts) == len(self.posts)
            reader["posts_uncoded"] = len(read_posts - coded[index])
            reader["signatures"] = [{"signed": name, "count": n} for name, n in
                                    sorted(signatures[index].items(), key=lambda kv: (-kv[1], str(kv[0] or "")))]
            reader["failures"] = dict(sorted(failures[index].items(), key=lambda kv: (-kv[1], kv[0])))
            reader["quotations_not_found"] = failures[index][NOT_FOUND]
            reader["unfinished"] = dict(sorted(unfinished[index].items()))

    # ---- what the pages ask ---------------------------------------------

    def counted(self, record: dict) -> int | None:
        """The place in `readers` of the reader that made this record, if a
        finished read activity of its own lens made it; otherwise None."""
        lens_id = self.finished.get(record.get("activity"))
        if lens_id is None or record.get("by") != lens_id:
            return None
        return self.reader_of[lens_id]

    @property
    def lens_ids(self) -> list[str]:
        return [lid for reader in self.readers for lid in reader["lens_ids"]]

    @property
    def complete(self) -> bool:
        return len(self.readers) >= self.expect and all(reader["complete"] for reader in self.readers)

    def not_read_by(self, unit_id: str) -> list[int]:
        """The readers of the run whose finished activities do not name this unit."""
        who = self.read_by.get(unit_id, ())
        return [index for index in range(len(self.readers)) if index not in who]

    def status_line(self) -> str | None:
        """One line saying which readers are not complete, or None when the run is whole."""
        parts = [f"{reader['label']} had read {reader['posts_read']:,} of {reader['posts_total']:,} signed posts"
                 for reader in self.readers if not reader["complete"]]
        missing = self.expect - len(self.readers)
        if missing > 0:
            parts.append(f"{len(self.readers)} of the {self.expect} readers expected "
                         f"{'has' if len(self.readers) == 1 else 'have'} a lens on this codebook, so "
                         f"{missing} had not started")
        if not parts:
            return None
        return ("**This run is not complete.** " + "; ".join(parts)
                + f". Every count and tier on this page is partial. Ledger read {self.made}.")

    def as_json(self) -> dict:
        return {"made": self.made, "project": str(self.project.root), "codebook": self.codebook,
                "expect": self.expect, "complete": self.complete, "status": self.status_line(),
                "posts": len(self.posts), "readers": self.readers,
                "skipped_last_lines": dict(getattr(self.project, "skipped", {}))}


# ---- the report -------------------------------------------------------------

def _code(text) -> str:
    """A name inside backticks, or as JSON when backticks could not hold it exactly."""
    text = str(text)
    if not text or "`" in text or "\n" in text or "\r" in text or text != text.strip():
        return json.dumps(text, ensure_ascii=False)
    return f"`{text}`"


def report(run: Run) -> str:
    """The readers of the run as a Markdown page that also reads plainly in a terminal."""
    out = []
    status = run.status_line()
    if status:
        out += [f"> {status}", ""]
    out += ["# The readers of the run", "",
            f"Codebook `{run.codebook}`, read from `{run.project.root}` at {run.made}. A run lens is one whose "
            f"reader names this codebook and whose method is focused-coding, version \"{RUN_VERSION}\", code types "
            f"{json.dumps(RUN_CODE_TYPES)}. Lenses that are the same reading are one reader. Only records made in "
            "a finished read activity of their own lens are counted.", "",
            f"Signed posts (units of kind `post`): {len(run.posts):,}. Readers found: {len(run.readers)} "
            f"(expected {run.expect}).", ""]
    skipped = getattr(run.project, "skipped", {})
    for kind, line in sorted(skipped.items()):
        out += [f"Note: line {line:,} of `{kind}.jsonl`, its last, did not parse and was skipped "
                "(a writer was in the middle of it).", ""]
    if not run.readers:
        out += ["No lens of this kind is in the ledger.", ""]
        return "\n".join(out)

    out += ["| Reader | Complete | Signed posts read | Read activities finished / failed | Codings | Unfit memos "
            "| Field notes | Quotations not found | Posts read and given no code |",
            "|---|---|---:|---:|---:|---:|---:|---:|---:|"]
    for reader in run.readers:
        out.append(f"| {reader['label']} | {'yes' if reader['complete'] else 'NO'} | "
                   f"{reader['posts_read']:,} of {reader['posts_total']:,} | "
                   f"{reader['activities_ok']:,} / {reader['activities_failed']:,} | {reader['codings']:,} | "
                   f"{reader['unfit']:,} | {reader['field_notes']:,} | {reader['quotations_not_found']:,} | "
                   f"{reader['posts_uncoded']:,} |")
    out.append("")

    for n, reader in enumerate(run.readers, 1):
        out += [f"## {n}. {reader['label']}", ""]
        for lens in reader["lenses"]:
            line = f"- Lens `{lens['id']}`: model `{lens['model']}`"
            if lens["answering_in_place_of"]:
                line += f", answering in place of `{lens['answering_in_place_of']}`"
            out.append(line + f", harness `{lens['harness']}`, declared {lens['declared']}")
        out.append(f"- Distinct units read: {reader['units_read']:,}, of which signed posts: "
                   f"{reader['posts_read']:,} of {reader['posts_total']:,}"
                   + ("" if reader["complete"] else
                      f" ({reader['posts_total'] - reader['posts_read']:,} not read yet)"))
        out.append(f"- Unfit memos: {reader['unfit']:,}, of which {reader['unfit_not_found']:,} with a quotation "
                   "that was not found in the unit")
        other = {reason: n for reason, n in reader["failures"].items() if reason != NOT_FOUND}
        out.append(f"- Failures: {reader['quotations_not_found']:,} quotations not found"
                   + "".join(f"; {n:,} × {_code(reason)}" for reason, n in other.items()))
        signed = ", ".join(f"{_code(s['signed']) if s['signed'] is not None else 'unsigned'} {s['count']:,}"
                           for s in reader["signatures"])
        out.append(f"- Field notes: {reader['field_notes']:,}. Signatures: {signed or 'none'}")
        if reader["unfinished"]:
            held = ", ".join(f"{n:,} {kind} record{'' if n == 1 else 's'}" for kind, n in reader["unfinished"].items())
            out.append(f"- Left out: {held} from a batch that had not finished when the ledger was read")
        out.append("")
    return "\n".join(out)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Find the lenses of a full run and report what each reader did.")
    parser.add_argument("project")
    parser.add_argument("--codebook", required=True, help="ID of the codebook memo the run applied")
    parser.add_argument("--out", default=None, help="a directory; writes run-readers.md and run-readers.json there")
    parser.add_argument("--expect", type=int, default=EXPECT, help="how many readers a whole run has (default 3)")
    parser.add_argument("--status", action="store_true",
                        help="print one line saying whether the run is complete, and nothing else")
    args = parser.parse_args(argv)
    run = Run(open_project(args.project), args.codebook, expect=args.expect)
    if args.status:
        print((run.status_line() or f"The run is complete: {len(run.readers)} readers have each read all "
               f"{len(run.posts):,} signed posts. Ledger read {run.made}.").replace("**", ""))
        return 0 if run.readers else 2
    text = report(run)
    print(text)
    if args.out:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / "run-readers.md").write_bytes(text.encode("utf-8"))
        (out / "run-readers.json").write_bytes(
            (json.dumps(run.as_json(), indent=1, ensure_ascii=False) + "\n").encode("utf-8"))
        print(f"wrote {out / 'run-readers.md'} and {out / 'run-readers.json'}")
    return 0 if run.readers else 2


if __name__ == "__main__":
    sys.exit(main())
