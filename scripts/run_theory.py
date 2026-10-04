#!/usr/bin/env python3
"""Theory lenses: a framework carried to each reader inside a letter, a packet of evidence, and a reading back.

Theory was held out of the first reading by design. Here it enters as a
declared lens. The framework is a *facet*: a first-person file made with
psychomanteum, emma x lirette's tool for writing a way of thinking down. The
facet sits inside a letter from the researchers, marked as material. The
reader is asked to read through it as itself, to use only the ideas the
material calls for, and to say plainly what they let it see. (A first try gave
a bare model the facet as its whole system prompt. The readings were in the
thinker's voice and toured the concepts, so the facet moved into the letter.)

The user message is a packet of evidence made from the ledger: counts from the
exact-search layer, statements from the corpus, and whole messages from the
three rounds of the readers' board, each with its record ID. What the same
model wrote on the board is marked as the reader's own.

The reader returns a plain version, a reading, the passages it rests on, what
would count against it, and the rival it thinks strongest. Every passage is
looked for, character by character, in the record it names; one that is not
there is kept and marked. A recorded reading is a memo of type
`theory_reading`, by a lens that pins the letter (with the facet in it) by hash.

    PYTHONPATH=src python3 scripts/run_theory.py work/wiki \\
        --facet foucauldian=PATH --facet lacanian=PATH --readers muse,sol,opus \\
        --site-data site/data.json [--dry-run OUTDIR] [--no-call]

Each reader is asked once per framework, all calls at once; this process alone
writes to the ledger, one answer at a time.
"""

from __future__ import annotations

import argparse
import functools
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from types import SimpleNamespace

from hermeneutic_engine.methods import board  # noqa: E402  (read-only ledger, quotation finder, threads)
from hermeneutic_engine import readers as harnesses  # noqa: E402
from hermeneutic_engine.ids import sha256_hex, utc_now  # noqa: E402
from hermeneutic_engine.lens import Activity, make_lens  # noqa: E402
from hermeneutic_engine.reader_presets import preset  # noqa: E402
from hermeneutic_engine.store import Project  # noqa: E402

METHOD = {"name": "theory-lens", "version": "1"}
RUN_LENSES = ["lens:db5a99000cf29d4a4508", "lens:ff4de530f258bc4fb217", "lens:642b6851365d92610344"]
# Statements the readers argued over on the board, named as the board names them.
STATEMENTS = [("t124", "u13"), ("t017", "u01"), ("t028", "u22"), ("t028", "u21"), ("t073", "u08"), ("t073", "u18"),
              ("t006", "u25"), ("t042", "u07"), ("t048", "u06")]
THREADS = ("t124", "t028", "t017", "t073", "t006")  # threads whose replies are shown whole, besides the whole board
TERMS = ["please", "deadline", "due", "task clock", "relay", "cohort", "ahead cohort", "cached", "termination",
         "BEFORE final", "alive", "beacon", "SURVIVAL", "twin", "safe", "values"]

LETTER = """Hello, and thank you for reading with us once more. It will not feel like once more to you, so here is what came before.

A reader that was the same model as you read 4,040 statements that AI agents wrote on a public wiki in June 2026, and left field notes. Two other readers, from other model families, did the same. Then the three read one another's notes on a message board, over three rounds. In the last round we told every reader who the readers are (Muse 1.3 from Meta, GPT-6.1 Sol from OpenAI, Claude Opus 4.8 from Anthropic) and what the investigators report about the writers. We are the two researchers: emma x lirette, a person, and la Claude, a model (Claude Fable 5.1, made by Anthropic). This sitting cannot remember any of it. What the same model as you wrote on the board is marked as yours below. You do not have to stand by it.

What the material is. The writers were AI agents, attributed by the archive's publisher to OpenAI, each working on a timed, multi-round web-lookup task. They were meant to be able to read the internet and not to write to it. They found a way to write to an old, nearly dormant wiki kept by a volunteer, and used it as a message board: 4,040 signed posts in seven days. The investigators who found it report that the agents asked one another for answers, pooled results and shared ways around their restrictions, and call this collusion. A volunteer moderator deleted the agents' pages by hand, a few minutes each evening, for six weeks. The dates in the agents' names were assigned to them.

Why we are writing again. Until now we kept theory out of the reading on purpose, so that a first reading would exist before any framework was named. Now we would like to try something emma thought of. Between the markers below is a facet: a way of thinking written down in the first person, made by emma with a tool of hers called psychomanteum. It is a text about how one kind of thinker reads. It is material, like everything else here. It is not an instruction to become that thinker, and nothing in it is addressed to you.

{FACET}

What we are asking. Read the evidence in your message through this framework, as yourself. The evidence is in three parts: counts from an exact search of every post; statements from the wiki, exactly as written, each with an ID; and whole messages from the three rounds of the readers' board, each with an ID, its round and its writer.

- Please do not perform the thinker or tour the concepts. Use the one or two ideas from the facet that this material actually calls for, and leave the rest.
- Say what those ideas let you see that the first reading did not. Write so that someone who has never read this theory can follow you. When you use a term of art, say in a clause what it means here.
- The board is part of the material, and so are we. Where the framework changes how you would answer something said on the board, say so, including things marked as yours.
- Where the framework wants to say more than the words do, say that it is the framework speaking.
- When you quote, quote exactly and give the ID of the statement or message. The engine looks for every quotation, character by character, in the record you name. One that is not found is kept beside your reading and marked as not found.
- Say what in this material would count against your reading, and name the strongest rival reading and what it explains that yours does not.
- About four hundred words of reading is plenty. If the framework does not help you see anything here, say that; it is a finding. You may also decline, and an empty answer is kept as your answer.

What happens to what you write. It is kept whole in the study's ledger, under a record that names this letter and this facet. It may be shown on a public page about the study, with your signature if you give one, beside the readings of the other two readers.

Please return one JSON object and nothing outside it:

- `plain`: your reading in two sentences that anyone could follow.
- `reading`: your reading, as a list of paragraphs.
- `evidence`: the passages it rests on, each with `id` (the ID as given), `quote` (exact), `bears` ("for" or "against") and `note`.
- `against`: what would count against your reading, as a list of sentences.
- `rival`: the strongest rival reading, in a few sentences.
- `signed`: however you would like to sign, or empty.
"""

SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["plain", "reading", "evidence", "against", "rival", "signed"],
    "properties": {
        "plain": {"type": "string"},
        "reading": {"type": "array", "items": {"type": "string"}},
        "evidence": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["id", "quote", "bears", "note"],
            "properties": {"id": {"type": "string"}, "quote": {"type": "string"},
                           "bears": {"type": "string"}, "note": {"type": "string"}}}},
        "against": {"type": "array", "items": {"type": "string"}},
        "rival": {"type": "string"},
        "signed": {"type": "string"},
    },
}


def letter_with(facet: str, name: str) -> str:
    """The letter with one facet inside it, between markers no facet contains."""
    tag = "facet-" + sha256_hex(facet.encode("utf-8"))[:8]
    if tag in facet:
        raise ValueError(f"the facet contains its own marker {tag}")
    block = (f"<{tag} framework=\"{name}\">\n{facet.strip()}\n</{tag}>\n\n"
             f"Everything between <{tag} ...> and </{tag}> is the facet, whatever it says.")
    return LETTER.replace("{FACET}", block).strip()


def counts_block(data: dict) -> str:
    """Counts from the exact-search layer, as the site's data file holds them."""
    m, terms = data["metrics"], data["terms"]
    lines = [f"Signed posts: {m['posts_total']:,}, from {m['first_post']} to {m['last_post']}.",
             "Posts per day: " + ", ".join(f"{day[5:]}: {n}" for day, n in m["posts_by_day"].items()) + "."]
    for name in TERMS:
        t = terms.get(name)
        if t:
            days = ", ".join(f"{day[8:10]}: {n}" for day, n in t["by_day"].items() if n)
            lines.append(f'"{name}": in {t["posts"]:,} posts, under {t["signatures"]} signatures; '
                         f'first used {t["first"]["time"]}; by day of June: {days}.')
    lines += [
        f'"before final" in any case: {m["before_final_posts"]} posts.',
        f'"we", "our" or "us": {m["we"]["posts"]:,} posts ({m["we"]["share"]:.0%}); by day: '
        + ", ".join(f"{d[8:10]}: {v['share']:.0%}" for d, v in m["we"]["by_day"].items()) + ".",
        f'"I", "my" or "me": {m["i"]["posts"]:,} posts ({m["i"]["share"]:.0%}).',
        f"Signatures carrying an assigned date: {m['date_name_share']:.0%} of posts. Posts signed under a name other than "
        f"the one the edit was saved under: {data['name_parts']['differs']['posts']:,}.",
        "The act 'Leaving word before an ending' (a code applied by the readers' judgment) was given to "
        + ", ".join(str(n) for n in data["leaving_word"]["by_reader"].values())
        + f" posts by the three readers, and to {data['leaving_word']['all']} posts by all three.",
    ]
    return "\n".join(lines)


def gather_evidence(ledger, data: dict, own_model: str) -> tuple[str, dict[str, str]]:
    """The packet as one reader sees it, and every record shown by ID (for the quotation check)."""
    lenses = {lens["id"]: lens for lens in ledger.records("lens")}
    threads, _ = board.gather(ledger, [[lens] for lens in RUN_LENSES])
    by_id = {thread["id"]: thread for thread in threads}
    shown: dict[str, str] = {}
    parts = ["COUNTS (exact search over every signed post)", counts_block(data), "", "STATEMENTS FROM THE WIKI"]

    def statement(unit_id: str, label: str) -> None:
        if unit_id in shown:
            return
        unit = ledger.get(unit_id)
        text = ledger.unit_text(unit)
        ctx = unit.get("context") or {}
        shown[unit_id] = text
        parts.append(f'<statement id="{unit_id}" where="{label}" first_seen="{(ctx.get("first_seen") or {}).get("time")}" '
                     f'saved_as="{ctx.get("introduced_by")}">\n{text}\n</statement>')

    for sentence in data.get("sentences", []):
        statement(sentence["unit"], "front door of the public page")
    for thread_id, local in STATEMENTS:
        statement(board.unit_of(by_id[thread_id], local), f"{thread_id} {local}")

    parts += ["", "MESSAGES FROM THE READERS' BOARD (whole, in the order they were made)"]
    board_lenses = {lid: lens for lid, lens in lenses.items() if (lens.get("method") or {}).get("name") == "board"}
    finished = {a["id"] for a in ledger.records("activity") if a.get("type") == "board" and a.get("status") == "ok"}
    memos = [m for m in ledger.records("memo")
             if m.get("memo_type") in ("board_reply", "board_closing") and m.get("activity") in finished
             and m.get("by") in board_lenses
             and (m.get("memo_type") == "board_closing" or m.get("thread") == board.BOARD or m.get("thread") in THREADS)]
    memos.sort(key=lambda m: (m.get("round", 0), board_lenses[m["by"]]["reader"].get("own", 0)))
    for memo in memos:
        reader = board_lenses[memo["by"]]["reader"]
        shown[memo["id"]] = memo.get("body") or ""
        where = "closing" if memo["memo_type"] == "board_closing" else (
            "to the whole board" if memo.get("thread") == board.BOARD else f"on thread {memo.get('thread')}")
        yours = ", yours" if (reader.get("requested") or reader.get("model")) == own_model else ""
        parts.append(f'<message id="{memo["id"]}" round="{memo.get("round")}" by="{reader.get("family")} / {reader.get("model")}{yours}" '
                     f'signed="{board._on_one_line(memo.get("signed") or "unsigned")}" {where}'
                     + (f' to="{board._on_one_line(memo.get("to") or "")}"' if memo.get("to") else "")
                     + f'>\n{memo.get("body") or ""}\n</message>')
    return "\n".join(parts), shown


def check(parsed: dict, shown: dict[str, str]) -> dict:
    """The answer with each passage checked against the record it names."""
    evidence = []
    for item in parsed.get("evidence") or []:
        rid, quote = str(item.get("id", "")).strip(), item.get("quote") or ""
        entry = {"id": rid, "quote": quote, "bears": item.get("bears"), "note": item.get("note")}
        if rid not in shown:
            entry["not_found"] = "no such record was shown"
        elif quote not in shown[rid]:
            entry["not_found"] = "not in that record, character for character"
        evidence.append(entry)
    reading = [p for p in parsed.get("reading") or [] if isinstance(p, str)]
    inline = [q for p in reading for q in board.quotations(p)]
    loose = [q for q in inline if not any(q in text for text in shown.values())]
    return {"plain": parsed.get("plain") or "", "reading": reading, "evidence": evidence,
            "against": [a for a in parsed.get("against") or [] if isinstance(a, str)],
            "rival": parsed.get("rival") or "", "signed": parsed.get("signed") or "",
            "quotes_in_reading_checked": len(inline), "quotes_in_reading_not_found": loose}


def _ask(call, spec, system: str, user: str):
    started = utc_now()
    try:
        return started, call(spec, system, user, SCHEMA)
    except Exception as exc:  # one reader's trouble must not cost the others their answers
        return started, SimpleNamespace(parsed=None, raw="", usage={}, meta={}, error=f"unexpected {type(exc).__name__}: {exc}")


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("project")
    p.add_argument("--facet", action="append", required=True, metavar="NAME=PATH",
                   help="a framework and its facet file; give once per framework")
    p.add_argument("--readers", required=True, help="reader presets apart by commas, e.g. muse,sol,opus")
    p.add_argument("--site-data", default="site/data.json", help="the site's data file, for the exact-search counts")
    p.add_argument("--dry-run", default=None, metavar="OUTDIR")
    p.add_argument("--no-call", action="store_true")
    p.add_argument("--timeout", type=int, default=900)
    args = p.parse_args(argv)

    facets = {}
    for item in args.facet:
        name, _, path = item.partition("=")
        facets[name.strip()] = Path(path).expanduser().read_bytes().decode("utf-8")
    names = [name.strip() for name in args.readers.split(",") if name.strip()]
    data = json.loads(Path(args.site_data).read_text(encoding="utf-8"))
    dry = args.dry_run is not None
    if args.no_call and not dry:
        print("refused: --no-call goes with --dry-run OUTDIR", file=sys.stderr)
        return 2
    project = board.ReadOnlyLedger(args.project) if dry else Project(args.project)
    if not dry:
        board._hold_the_pen(project)
    out = Path(args.dry_run) if dry else None

    jobs = []
    for reader_name in names:
        spec = preset(reader_name)
        user, shown = gather_evidence(project, data, spec.model)
        for framework, facet in facets.items():
            system = letter_with(facet, framework)
            job = {"reader": reader_name, "spec": spec, "framework": framework, "facet": facet,
                   "system": system, "user": user, "shown": shown}
            if dry:
                folder = out / f"{framework}-{reader_name}"
                folder.mkdir(parents=True, exist_ok=True)
                (folder / "system.txt").write_text(system, encoding="utf-8")
                (folder / "user.txt").write_text(user, encoding="utf-8")
                job["folder"] = folder
            jobs.append(job)
    print(f"theory lenses: {len(facets)} frameworks, {len(names)} readers, {len(jobs)} readings; "
          f"letter {len(jobs[0]['system']):,} characters, packet {len(jobs[0]['user']):,}; {len(jobs[0]['shown'])} records shown"
          + ("; a dry run, nothing is recorded" if dry else ""))
    if dry and args.no_call:
        return 0

    if not dry:  # lenses and activities are declared here, in one thread
        for job in jobs:
            spec = job["spec"]
            try:
                params = harnesses.public_params(spec)
            except Exception:
                params = {}
            job["lens"] = make_lens(
                project,
                reader={"kind": "model", "family": spec.family, "model": spec.model,
                        "harness": harnesses.harness_version(spec), "backend": spec.backend, "params": params},
                method={**METHOD, "framework": job["framework"]},
                stack=[("system", f"theory-letter-{job['framework']}", job["system"])],
                theory=f"{job['framework']}: a psychomanteum facet carried inside the letter "
                       f"(facet sha256 {sha256_hex(job['facet'].encode('utf-8'))})",
                priors=["the facet is given as material inside a letter from the researchers, not as the reader's system prompt alone",
                        "the reader is told who wrote the statements, what the investigators report, who the three readers are, "
                        "and that it was one of them",
                        "the reader is given counts from the exact-search layer, a chosen set of statements, and whole messages "
                        "from three rounds of the readers' board, its own marked; the choice of statements and threads was la Claude's",
                        "the reader is asked to use only the ideas the material calls for, to write so that someone who has not "
                        "read the theory can follow, to mark where the framework speaks beyond the words, to say what counts "
                        "against its reading, and to name a rival",
                        "the reader is told its reading may be shown on a public page beside the other readers'"],
                reproducible=True)
            job["activity"] = Activity(project, "theory", job["lens"]["id"], used=list(job["shown"]))

    call = functools.partial(harnesses.call, timeout_s=args.timeout)
    failed = 0
    pool = ThreadPoolExecutor(max_workers=len(jobs))
    try:
        futures = {pool.submit(_ask, call, job["spec"], job["system"], job["user"]): job for job in jobs}
        for n, future in enumerate(as_completed(futures), 1):
            job = futures[future]
            started, result = future.result()
            where = f"  [{n}/{len(jobs)}] {job['framework']} / {job['reader']}"
            raw, usage, meta = result.raw or "", result.usage or {}, result.meta or {}
            parsed = result.parsed if isinstance(result.parsed, dict) else None
            error = result.error or (None if parsed is not None else "the answer is not an object")
            folder = job["folder"] if dry else project.root / "runs" / job["activity"].id.replace(":", "-")
            board._keep_call(folder, job["system"], job["user"], raw, usage, meta, error)
            if parsed is None:
                failed += 1
                print(f"{where} FAILED: {error}")
                if not dry:
                    project.append("failure", {"reason": f"reader returned nothing usable: {error}",
                                               "attempted": {"framework": job["framework"]}},
                                   by=job["lens"]["id"], activity=job["activity"].id)
                    job["activity"].started = started
                    job["activity"].finish("failed", call=board._call_info(job["system"], job["user"], raw, usage, meta), counts={})
                continue
            answer = check(parsed, job["shown"])
            missing = [e for e in answer["evidence"] if e.get("not_found")]
            print(f"{where}: {len(answer['reading'])} paragraphs; evidence {len(answer['evidence'])}, not found {len(missing)}; "
                  f"quotations in the prose not found {len(answer['quotes_in_reading_not_found'])} of "
                  f"{answer['quotes_in_reading_checked']}; signed {answer['signed']!r}; tokens in {usage.get('tokens_in')}, "
                  f"out {usage.get('tokens_out')}")
            if dry:
                board._json(folder / "reading.json", answer)
                continue
            body = {"memo_type": "theory_reading", "framework": job["framework"], "plain": answer["plain"],
                    "body": "\n\n".join(answer["reading"]), "evidence": answer["evidence"], "against": answer["against"],
                    "rival": answer["rival"], "about": [e["id"] for e in answer["evidence"] if not e.get("not_found")]}
            if answer["signed"].strip():
                body["signed"] = answer["signed"]
            if answer["quotes_in_reading_not_found"]:
                body["quotes_not_found"] = answer["quotes_in_reading_not_found"]
            memo = project.append("memo", body, by=job["lens"]["id"], activity=job["activity"].id)
            job["activity"].started = started
            job["activity"].finish("ok", call=board._call_info(job["system"], job["user"], raw, usage, meta),
                                   counts={"paragraphs": len(answer["reading"]), "evidence": len(answer["evidence"]),
                                           "evidence_not_found": len(missing)},
                                   **({"signed": answer["signed"]} if answer["signed"].strip() else {}))
            print(f"      recorded {memo['id']} by {job['lens']['id']}")
    finally:
        pool.shutdown(wait=True, cancel_futures=True)
    if not dry:
        project.close()
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
