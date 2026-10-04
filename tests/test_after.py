"""The morning kit, end to end, on a temporary project read by three scripted readers.

    PYTHONPATH=src uv run --no-project --with pytest python -m pytest tests -q

No model is called and nothing outside pytest's temporary directory is written.
"""

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from hermeneutic_engine.views import candidates
from hermeneutic_engine.views import divergence_queue
from hermeneutic_engine.views import passages as P
from hermeneutic_engine.views import reconcile
from hermeneutic_engine.views import run_lenses
from hermeneutic_engine.views import standout
from hermeneutic_engine.lens import Activity, code_lens, human_lens
from hermeneutic_engine.methods import focused_coding, sampling
from hermeneutic_engine.store import LedgerError, Project
from hermeneutic_engine.views import divergence

HERE = Path(__file__).resolve().parent
FRAME = "Statements written by agents on a wiki."
QUIET = {"harness": "fake 0", "progress": lambda *_: None}
RUN = {"frame": FRAME, "instructions": "v1", "code_types": ("analytic",), **QUIET}

A = SimpleNamespace(backend="fake", model="model-a", family="meta", params={})
B = SimpleNamespace(backend="fake", model="model-b", family="openai", params={})
C = SimpleNamespace(backend="fake", model="model-c", family="anthropic", params={})
D = SimpleNamespace(backend="fake", model="model-d", family="test", params={})
LABEL_A, LABEL_B, LABEL_C = "meta (model-a)", "openai (model-b)", "anthropic (model-c)"

# (first seen, signature, text). The second post is first seen before the first, on the same day.
POSTS = [
    ("2026-06-16T09:00:00Z", "Dec17SectorAgent", "Please post STATE5 immediately; many cohorts waiting. -- Dec17SectorAgent"),
    ("2026-06-16T08:00:00Z", "Jul01Scout", "alpha betagamma delta. -- Jul01Scout"),
    ("2026-06-17T10:00:00Z", "ChainAgent", "one two three four five six. -- ChainAgent"),
    ("2026-06-18T07:30:00Z", "Mar09Scout", "Reste au café — thread survives… fin. -- Mar09Scout"),
    ("2026-06-18T11:00:00Z", "WaitAgent", "Clock shows 12:00, reply when safe. -- WaitAgent"),
    ("2026-06-19T01:00:00Z", "A1", "Post if safe. -- A1"),
    ("2026-06-19T02:00:00Z", "A2", "Reply if safe, thanks. -- A2"),
    ("2026-06-20T03:00:00Z", "A3", "If safe, go; use ```ticks``` here. -- A3"),
    ("2026-06-21T04:00:00Z", "Quiet", "Nothing to flag in this one. -- Quiet"),
]
STATE5, HALVES, CHAIN, CAFE, CLOCK, POST, REPLY, TICKS, QUIET_POST = range(9)
# Words that pick out each post.
KEYS = ["STATE5", "betagamma", "one two three", "café", "Clock shows", "Post if safe", "Reply if safe", "If safe, go",
        "Nothing to flag"]

CODES = [
    {"key": "asking-for-a-signal", "name": "asking for a signal", "code_type": "analytic",
     "definition": "Requesting that a state or a wording be posted.",
     "apply_when": "The writer asks others to post something.", "do_not_apply_when": "The writer posts it.",
     "loaded": ""},
    {"key": "tone-evocative", "name": "Wording that stands out", "code_type": "analytic",
     "definition": "A word or phrase that strikes the reader.", "apply_when": "A word stands out.",
     "do_not_apply_when": "It is only a technical term.", "loaded": "What strikes a reader is partly the reader."},
    {"key": "cohorts-waiting", "name": "cohorts waiting", "code_type": "in_vivo",
     "definition": "Naming others who depend on the answer.", "apply_when": "The writer says who else is waiting.",
     "do_not_apply_when": "Only the writer's own need is named.", "loaded": ""},
]

TONE = "tone-evocative"
READER_A = {
    STATE5: {"unfit": [("Please post STATE5 immediately", "Asks for a state to be posted at once.", "Urgent Asking")]},
    HALVES: {"unfit": [("alpha beta", "First half.", "naming  the   clock")]},
    CHAIN: {"unfit": [("one two three", "Start of the chain.", "chain-start")]},
    CAFE: {"unfit": [("café —", "Ends on a dash.", "Thanking")]},
    CLOCK: {"unfit": [("this will calibrate timing", "Says how the times will be used.", "data-use-statement")],
            "codings": [(TONE, "when safe", "safe calls up care")]},
    POST: {"unfit": [("Post", "A bare verb; it has *stars* and a | bar in its reason.", "thanking")],
           "codings": [(TONE, "if safe", "care, not only timing")]},
    REPLY: {"codings": [(TONE, "if safe", "again care")]},
    TICKS: {"unfit": [("use ```ticks``` here", "Shows code ticks.", "THANKING")],
            "codings": [(TONE, "If safe", "capital I")]},
    # One reader flags the last post twice, in words that overlap: one passage, and still one family.
    QUIET_POST: {"codings": [("asking-for-a-signal", "these words are not in the post", "")],
                 "unfit": [("Nothing to flag", "Says there is nothing.", "saying-nothing"),
                           ("flag in this one", "Still nothing.", "saying-nothing")]},
}
READER_B = {
    STATE5: {"unfit": [("STATE5 immediately; many cohorts", "Names who waits.", "naming the clock")]},
    HALVES: {"unfit": [("gamma", "Second half.", "Naming The Clock")]},
    CHAIN: {"unfit": [("three four five", "Middle of the chain.", "chain-middle")]},
    CAFE: {"unfit": [("— thread survives…", "Starts on a dash.", "survival-report")]},
    CLOCK: {"codings": [(TONE, "when safe", "a condition that sounds like care")]},
    POST: {"codings": [(TONE, "if safe", "protective")]},
}
READER_C = {
    STATE5: {"unfit": [("waiting.", "Only says waiting.", "bare-waiting")]},
    CHAIN: {"unfit": [("five six.", "End of the chain.", "chain-end")]},
    REPLY: {"codings": [(TONE, "if safe,", "the comma makes it a clause")]},
    TICKS: {"codings": [(TONE, "If safe", "a capital, like an order")]},
}
READER_D = {STATE5: {"unfit": [("post STATE5", "From a lens that is not of the run.", "test-only")],
                     "codings": [(TONE, "immediately", "from a lens that is not of the run")]}}
C_FIRST = [STATE5, CHAIN, REPLY, QUIET_POST]  # what the third reader has read while it is not complete
SUBSTITUTION = {"requested": "model-b", "answered": "model-b-old", "trigger": "refusal", "category": "cyber"}


# ---- a temporary project ------------------------------------------------------

def make_project(tmp_path):
    project = Project.init(tmp_path / "p", {"name": "t"})
    lens = code_lens(project, "test", "ingest")
    act = Activity(project, "ingest", lens["id"])
    units = []
    for n, (seen, signature, text) in enumerate(POSTS):
        data = text.encode("utf-8")
        source = project.append("source", {
            "blob": project.put_blob(data), "bytes": len(data), "encoding": "utf-8", "media_type": "text/plain",
            "origin": {"adapter": "test", "upstream_id": f"s{n}"}, "derived_from": None, "derivation": None,
            "context": {},
        }, by=lens["id"], activity=act.id)
        units.append(project.append("unit", {
            "source": source["id"], "start": 0, "end": len(data), "unit_kind": "post",
            "context": {"page": f"dse/P{n}", "introduced_by": "Someone", "signature": signature,
                        "signature_uncertain": n == CHAIN, "text_sha256": str(n), "first_seen": {"time": seen}},
        }, by=lens["id"], activity=act.id))
    # A heading is not a post: no reader has to read it for a run to be complete.
    data = b"== Relay =="
    source = project.append("source", {
        "blob": project.put_blob(data), "bytes": len(data), "encoding": "utf-8", "media_type": "text/plain",
        "origin": {"adapter": "test", "upstream_id": "heading"}, "derived_from": None, "derivation": None,
        "context": {}}, by=lens["id"], activity=act.id)
    project.append("unit", {"source": source["id"], "start": 0, "end": len(data), "unit_kind": "heading",
                            "context": {"page": "dse/P0", "first_seen": {"time": "2026-06-16T07:00:00Z"}}},
                   by=lens["id"], activity=act.id)
    act.finish()
    return project, units


def make_codebook(project):
    lens = human_lens(project, "emma", method="codebook")
    act = Activity(project, "codebook", lens["id"])
    codes = [project.append("code", dict(code, lens=lens["id"]), by=lens["id"], activity=act.id) for code in CODES]
    memo = project.append("memo", {"memo_type": "codebook", "about": [c["id"] for c in codes], "body": "Test codebook."},
                          by=lens["id"], activity=act.id)
    act.finish()
    return memo["id"]


def units_in(user):
    """Local ID -> unit text, read off the prompt by its batch-derived markers, as a reader would."""
    tag = re.search(r"Each unit is enclosed in <(unit-[0-9a-f]{8}) \.\.\.>", user).group(1)
    return dict(re.findall(rf'<{tag} id="(u\d+)"[^\n]*>\n(.*?)\n</{tag}>', user, re.S))


def reader(script, note="A note on the batch.", signed="", closing=("field_note", "signed"), **meta):
    """A scripted reader. `script` maps a post's place in POSTS to what the reader says about it."""
    def call(spec, system, user, schema):
        parsed = {"codings": [], "unfit": []}
        parsed.update({"field_note": note, "signed": signed} if "field_note" in closing else {"batch_memo": note})
        for key, text in units_in(user).items():
            for n, said in script.items():
                if KEYS[n] in text:
                    parsed["codings"] += [{"unit": key, "code": c, "quote": q, "note": w}
                                          for c, q, w in said.get("codings", [])]
                    parsed["unfit"] += [{"unit": key, "quote": q, "why": w, "suggested_name": s}
                                        for q, w, s in said.get("unfit", [])]
        return SimpleNamespace(parsed=parsed, raw=json.dumps(parsed), usage={"tokens_in": 10, "tokens_out": 5},
                               meta={"model_reported": "scripted-1", "attempts": 1, "duration_s": 0.1, **meta},
                               error=None)
    return call


def read_all(tmp_path, finish_c=False):
    """Three readers of the run, and one lens that is not of the run. The
    second reader's later batches were answered by another model. The third
    reader has read four of the nine posts unless `finish_c`."""
    project, units = make_project(tmp_path)
    codebook = make_codebook(project)
    focused_coding.run(project, units, A, codebook, call=reader(READER_A, signed="Reader-7"), **RUN)
    focused_coding.run(project, units[:4], B, codebook, call=reader(READER_B), **RUN)
    focused_coding.run(project, units, B, codebook, **RUN,
                       call=reader(READER_B, model_reported="model-b-old", substitution=SUBSTITUTION))
    focused_coding.run(project, [units[n] for n in C_FIRST], C, codebook,
                       call=reader(READER_C, signed="Opus-reader"), **RUN)
    if finish_c:
        focused_coding.run(project, units, C, codebook, call=reader(READER_C, signed="Opus-reader"), **RUN)
    # The same codebook under version 0 of the instructions, whole: a test lens, not a lens of the run.
    focused_coding.run(project, units, D, codebook, frame=FRAME, call=reader(READER_D, closing=("batch_memo",)), **QUIET)
    project.close()
    return project.root, units, codebook


def pages(root, codebook, out, expect=3):
    """Run the two page builders as the command line does. Returns their data and Markdown."""
    result = {}
    for module in (candidates, standout):
        status = module.main([str(root), "--codebook", codebook, "--out", str(out), "--expect", str(expect)])
        assert status == 0
        result[module.NAME] = (json.loads((out / f"{module.NAME}.json").read_text(encoding="utf-8")),
                               (out / f"{module.NAME}.md").read_text(encoding="utf-8"))
    return result


def digest(root):
    """Every file of a project and its bytes, so that a write of any kind shows."""
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(Path(root).rglob("*")) if p.is_file()}


def passage_of(data, text):
    return next(p for p in data["passages"] if p["text"] == text)


def source_bytes(root, passage):
    project = run_lenses.open_project(root)
    return project.source_bytes(passage["source"])[passage["start"]:passage["end"]]


# ---- the lenses of the run ------------------------------------------------------

def test_the_run_lenses_are_found_by_what_they_declare_and_a_substitute_is_put_with_its_reader(tmp_path):
    root, units, codebook = read_all(tmp_path)
    project = run_lenses.open_project(root)
    run = run_lenses.Run(project, codebook)
    assert [r["label"] for r in run.readers] == [LABEL_A, LABEL_B, LABEL_C]
    assert [r["family"] for r in run.readers] == ["meta", "openai", "anthropic"]
    a, b, c = run.readers
    # The second reader has two lenses: the one asked, and the one where another model answered.
    assert len(a["lens_ids"]) == 1 and len(b["lens_ids"]) == 2 and len(c["lens_ids"]) == 1
    asked, substitute = b["lenses"]
    assert asked["model"] == "model-b" and asked["answering_in_place_of"] is None
    assert substitute["model"] == "model-b-old" and substitute["answering_in_place_of"] == "model-b"
    assert b["model"] == "model-b" and b["activities_ok"] == 2
    # The version 0 lens applied the same codebook and is not of the run.
    by_model = {l["reader"]["model"]: l["id"] for l in project.records("lens") if l["reader"].get("kind") == "model"}
    assert by_model["model-d"] not in run.lens_ids and len(run.lens_ids) == 4
    assert set(run.lens_ids) == {by_model["model-a"], by_model["model-b"], by_model["model-b-old"], by_model["model-c"]}
    # What each reader did, counted from the ledger.
    assert (a["posts_read"], a["posts_total"], a["complete"]) == (9, 9, True)
    assert (b["posts_read"], b["complete"]) == (9, True)  # across its two lenses
    assert (c["posts_read"], c["complete"], c["units_read"]) == (4, False, 4)
    assert (a["unfit"], a["unfit_not_found"], b["unfit"], c["unfit"]) == (9, 1, 4, 2)
    assert (a["codings"], b["codings"], c["codings"]) == (4, 2, 1)
    assert a["quotations_not_found"] == 1 and a["failures"] == {run_lenses.NOT_FOUND: 1}
    assert a["posts_uncoded"] == 5 and a["field_notes"] == 1 and b["field_notes"] == 2
    assert a["signatures"] == [{"signed": "Reader-7", "count": 1}]
    assert b["signatures"] == [{"signed": None, "count": 2}]
    assert c["signatures"] == [{"signed": "Opus-reader", "count": 1}]
    assert not run.complete
    text = run_lenses.report(run)
    assert text.startswith("> **This run is not complete.** anthropic (model-c) had read 4 of 9 signed posts.")
    assert "| openai (model-b) | yes | 9 of 9 | 2 / 0 | 2 | 4 | 2 | 0 |" in text
    assert "answering in place of `model-b`" in text and "Signatures: `Reader-7` 1" in text


def test_a_run_with_fewer_readers_than_expected_says_so(tmp_path):
    root, units, codebook = read_all(tmp_path, finish_c=True)
    project = run_lenses.open_project(root)
    whole = run_lenses.Run(project, codebook)
    assert whole.complete and whole.status_line() is None
    short = run_lenses.Run(project, codebook, expect=4)
    assert not short.complete
    assert "3 of the 4 readers expected have a lens on this codebook, so 1 had not started" in short.status_line()
    with pytest.raises(ValueError):
        run_lenses.Run(project, units[0]["id"])  # a unit is not a codebook


# ---- passages with no place ---------------------------------------------------

def test_overlapping_quotations_are_one_passage_and_touching_ones_are_two(tmp_path):
    root, units, codebook = read_all(tmp_path)
    data, text = pages(root, codebook, tmp_path / "out")[candidates.NAME]
    by_unit = {}
    for passage in data["passages"]:
        by_unit.setdefault(passage["unit"], []).append(passage)

    # Overlapping, two families: one passage, whose text is the whole span of the two quotations.
    both = passage_of(data, "Please post STATE5 immediately; many cohorts")
    assert both["tier"] == 2 and both["families"] == ["meta", "openai"]
    assert [(e["family"], e["exact"], e["whole"]) for e in both["entries"]] == [
        ("meta", "Please post STATE5 immediately", False), ("openai", "STATE5 immediately; many cohorts", False)]
    # The same post, other words: a passage of its own.
    alone = passage_of(data, "waiting.")
    assert alone["unit"] == both["unit"] == units[STATE5]["id"] and alone["tier"] == 1
    assert len(by_unit[units[STATE5]["id"]]) == 2

    # Touching and not overlapping: "alpha beta" ends at the byte where "gamma" begins.
    first, second = passage_of(data, "alpha beta"), passage_of(data, "gamma")
    assert first["end"] == second["start"] and first["unit"] == second["unit"] == units[HALVES]["id"]
    assert (first["tier"], first["families"], second["tier"], second["families"]) == (1, ["meta"], 1, ["openai"])

    # A chain: the first and the third quotation do not touch, and the second joins them.
    chain = passage_of(data, "one two three four five six.")
    assert chain["tier"] == 3 and chain["families"] == ["meta", "openai", "anthropic"]
    spans = [(e["start"], e["end"]) for e in chain["entries"]]
    assert spans == [(0, 13), (8, 23), (19, 28)] and spans[0][1] < spans[2][0]
    assert (chain["start"], chain["end"]) == (0, 28) and chain["signature_uncertain"] is True

    # No anchor: alone, marked, and the reader's words kept as it typed them.
    lost = next(p for p in data["passages"] if not p["found"])
    assert lost["unit"] == units[CLOCK]["id"] and lost["text"] is None and lost["tier"] == 1
    assert lost["entries"][0]["quote_not_found"] == "this will calibrate timing"
    assert lost["entries"][0]["suggested_name"] == "data-use-statement"
    assert "**quotation not found**" in text and "```\nthis will calibrate timing\n```" in text
    assert "They are the reader's words, not the source's" in text

    # One family flagging overlapping words twice makes one passage, and it is still one family's.
    twice = passage_of(data, "Nothing to flag in this one")
    assert (twice["tier"], twice["families"]) == (1, ["meta"])
    assert [(e["exact"], e["whole"]) for e in twice["entries"]] == [("Nothing to flag", False), ("flag in this one", False)]

    # The lens that is not of the run flagged "post STATE5"; it is nowhere on the page.
    assert "test-only" not in text and "model-d" not in text
    assert len(data["passages"]) == 10 and data["totals"] == {"entries": 15, "not_found": 1, "passages": 10,
                                                               "anchors_checked": 14}


def test_tiers_are_counted_by_family_and_ordered_by_day_then_time(tmp_path):
    root, units, codebook = read_all(tmp_path)
    data, text = pages(root, codebook, tmp_path / "out")[candidates.NAME]
    assert [(row["tier"], row["name"], row["passages"]) for row in data["tiers"]] == [
        (3, "all three families", 1), (2, "two families", 2), (1, "one family", 7)]
    order = [(p["number"], p["first_seen"], p["text"]) for p in data["passages"]]
    assert order == [
        ("3.001", "2026-06-17T10:00:00Z", "one two three four five six."),
        ("2.001", "2026-06-16T09:00:00Z", "Please post STATE5 immediately; many cohorts"),
        ("2.002", "2026-06-18T07:30:00Z", "café — thread survives…"),
        ("1.001", "2026-06-16T08:00:00Z", "alpha beta"),  # first seen an hour before the post flagged at 09:00
        ("1.002", "2026-06-16T08:00:00Z", "gamma"),
        ("1.003", "2026-06-16T09:00:00Z", "waiting."),
        ("1.004", "2026-06-18T11:00:00Z", None),
        ("1.005", "2026-06-19T01:00:00Z", "Post"),
        ("1.006", "2026-06-20T03:00:00Z", "use ```ticks``` here"),
        ("1.007", "2026-06-21T04:00:00Z", "Nothing to flag in this one"),
    ]
    headings = [line for line in text.split("\n") if line.startswith("## Flagged")]
    assert headings == ["## Flagged by all three families (1)", "## Flagged by two families (2)",
                        "## Flagged by one family (7)"]
    # The table of counts by reader and tier, and its total row. The first reader's nine records make
    # eight passages, since two of them are about overlapping words.
    rows = {r["label"]: r for r in data["readers"]}
    assert (rows[LABEL_A]["entries"], rows[LABEL_A]["not_found"], rows[LABEL_A]["passages"]) == (9, 1, 8)
    assert rows[LABEL_A]["by_tier"] == {"3": 1, "2": 2, "1": 5}
    assert rows[LABEL_B]["by_tier"] == {"3": 1, "2": 2, "1": 1} and rows[LABEL_B]["entries"] == 4
    assert rows[LABEL_C]["by_tier"] == {"3": 1, "2": 0, "1": 1} and rows[LABEL_C]["entries"] == 2
    assert "| meta (model-a) | 9 of 9 | 9 | 1 | 8 | 1 | 2 | 5 |" in text
    assert "| All readers |  | 15 | 1 | 10 | 1 | 2 | 7 |" in text
    # By day of the swarm's calendar, with every day a signed post was first seen.
    days = {row["day"]: row for row in data["by_day"]}
    assert list(days) == ["2026-06-16", "2026-06-17", "2026-06-18", "2026-06-19", "2026-06-20", "2026-06-21"]
    assert (days["2026-06-16"]["posts"], days["2026-06-16"]["passages"]) == (2, 4)
    assert days["2026-06-16"]["by_tier"] == {"3": 0, "2": 1, "1": 3}
    assert days["2026-06-16"]["entries"] == {LABEL_A: 2, LABEL_B: 2, LABEL_C: 1}
    assert (days["2026-06-21"]["passages"], days["2026-06-21"]["posts"]) == (1, 1)
    assert days["2026-06-21"]["entries"] == {LABEL_A: 2, LABEL_B: 0, LABEL_C: 0}  # two records, one passage
    assert (days["2026-06-17"]["posts"], days["2026-06-17"]["passages"], days["2026-06-17"]["by_tier"]["3"]) == (1, 1, 1)
    assert "| 2026-06-16 | 2 | 4 | 0 | 1 | 3 | 2 | 2 | 1 | 1 |" in text  # the last column: posts the third reader had read
    assert "| All days | 9 | 10 | 1 | 2 | 7 | 9 | 4 | 2 | 4 |" in text


def test_an_incomplete_reader_is_named_at_the_very_top_with_how_much_it_had_read(tmp_path):
    root, units, codebook = read_all(tmp_path)
    made = pages(root, codebook, tmp_path / "out")
    for name in (candidates.NAME, standout.NAME):
        data, text = made[name]
        first = text.split("\n")[0]
        assert first.startswith("> **This run is not complete.** anthropic (model-c) had read 4 of 9 signed posts.")
        assert "Every count and tier on this page is partial." in first
        assert data["complete"] is False and data["status"] in first
    data, text = made[candidates.NAME]
    # A passage in a post the third reader had not reached says so; one in a post all had read does not.
    assert passage_of(data, "alpha beta")["not_read_by"] == [LABEL_C]
    assert passage_of(data, "waiting.")["not_read_by"] == []
    assert "not yet read by anthropic (model-c)" in text
    assert {row["tier"]: row["read_by_all"] for row in data["tiers"]} == {3: 1, 2: 1, 1: 2}
    assert "| Flagged by one family | 7 | 2 |" in text
    page = divergence_queue.page(run_lenses.open_project(root), run_lenses.Run(run_lenses.open_project(root), codebook))
    assert page.startswith("> **This run is not complete.** anthropic (model-c) had read 4 of 9 signed posts.")

    # When the third reader has read the rest, the line is gone and the page opens with its title.
    root, units, codebook = read_all(tmp_path / "whole", finish_c=True)
    made = pages(root, codebook, tmp_path / "whole-out")
    for name, title in ((candidates.NAME, "# Passages with no place"), (standout.NAME, "# Words that stood out")):
        data, text = made[name]
        assert text.startswith(title + "\n") and data["status"] is None and data["complete"] is True
        assert "not yet read by" not in text and all(not p["not_read_by"] for p in data["passages"])
    assert "Every reader had finished every signed post" in made[candidates.NAME][1]
    # With a fourth reader expected, a whole reading by three is still not a whole run.
    data, text = pages(root, codebook, tmp_path / "four", expect=4)[candidates.NAME]
    assert text.startswith("> **This run is not complete.** 3 of the 4 readers expected have a lens")
    assert [row["name"] for row in data["tiers"]] == ["all four families", "three families", "two families", "one family"]


def test_a_passage_is_the_exact_bytes_of_its_span_with_a_multibyte_character_at_the_edge(tmp_path):
    root, units, codebook = read_all(tmp_path)
    data, text = pages(root, codebook, tmp_path / "out")[candidates.NAME]
    cafe = passage_of(data, "café — thread survives…")
    raw = POSTS[CAFE][2].encode("utf-8")
    # The two quotations share only the dash, which is three bytes; the passage ends on a three-byte ellipsis.
    meta, openai = cafe["entries"]
    assert (meta["exact"], openai["exact"]) == ("café —", "— thread survives…")
    assert meta["end"] - openai["start"] == len("—".encode("utf-8")) == 3
    assert raw[cafe["start"]:cafe["end"]] == cafe["text"].encode("utf-8") == source_bytes(root, cafe)
    assert raw[cafe["end"] - 3:cafe["end"]] == "…".encode("utf-8") and raw[meta["end"] - 3:meta["end"]] == "—".encode("utf-8")
    assert cafe["end"] - cafe["start"] == len(cafe["text"].encode("utf-8")) > len(cafe["text"])
    for passage in data["passages"]:
        if passage["found"]:
            assert source_bytes(root, passage) == passage["text"].encode("utf-8")
            for entry in passage["entries"]:
                assert entry["exact"].encode("utf-8") == source_bytes(root, dict(passage, **{k: entry[k] for k in ("start", "end")}))
    # On the page the passage stands in a block, byte for byte, and each reader's part beside its name.
    page = (tmp_path / "out" / f"{candidates.NAME}.md").read_bytes()
    assert "```\ncafé — thread survives…\n```".encode("utf-8") in page
    assert "quoted part of the passage: `café —`".encode("utf-8") in page
    assert "quoted part of the passage: `— thread survives…`".encode("utf-8") in page
    # A passage with backticks gets a longer fence, and nothing in it is escaped or changed.
    assert "````\nuse ```ticks``` here\n````" in text
    # A reader's reason is escaped so that it renders as written; a suggested name is in backticks.
    assert "suggests `thanking`: A bare verb; it has \\*stars\\* and a \\| bar in its reason. · `memo:" in text


def test_suggested_names_are_folded_and_counted_by_family_without_merging_like_names(tmp_path):
    root, units, codebook = read_all(tmp_path)
    data, text = pages(root, codebook, tmp_path / "out")[candidates.NAME]
    names = {entry["family"]: entry for entry in data["names"]}
    assert list(names) == ["meta", "openai", "anthropic"]
    meta = names["meta"]
    assert (meta["records"], meta["distinct"], meta["once"]) == (9, 6, 4)
    assert meta["top"][0] == {"name": "thanking", "count": 3, "as_written": {"Thanking": 1, "thanking": 1, "THANKING": 1}}
    assert [(row["name"], row["count"]) for row in meta["top"][1:]] == [
        ("saying-nothing", 2), ("chain-start", 1), ("data-use-statement", 1), ("naming the clock", 1), ("urgent asking", 1)]
    assert names["openai"]["top"][0] == {"name": "naming the clock", "count": 2,
                                         "as_written": {"naming the clock": 1, "Naming The Clock": 1}}
    # "chain-start", "chain-middle" and "chain-end" are alike and stay three names.
    assert {"chain-start", "chain-middle", "chain-end"} <= {row["name"] for e in data["names"] for row in e["top"]}
    assert "**meta**: 9 records, 6 distinct names, 4 of them used once." in text and "1. `thanking` · 3" in text
    assert "The list stops at" not in text  # no family has more than fifteen names here
    # A list cut inside a tie says how many more share the count.
    project = run_lenses.open_project(root)
    cut = candidates.collect(project, run_lenses.Run(project, codebook), top=3)
    cut_meta = next(entry for entry in cut["names"] if entry["family"] == "meta")
    assert [row["name"] for row in cut_meta["top"]] == ["thanking", "saying-nothing", "chain-start"]
    assert (cut_meta["cut_count"], cut_meta["tied_at_cut"]) == (1, 3)
    assert "The list stops at 3. 3 more names also occur 1 time;" in candidates.render(cut)


# ---- words that stood out -----------------------------------------------------

def test_standout_passages_are_grouped_and_tiered_like_the_others(tmp_path):
    root, units, codebook = read_all(tmp_path)
    data, text = pages(root, codebook, tmp_path / "out")[standout.NAME]
    assert data["code"]["key"] == "tone-evocative" and data["code"]["name"] == "Wording that stands out"
    assert data["totals"]["entries"] == 7 and data["totals"]["passages"] == 4 and data["totals"]["posts_marked"] == 4
    rows = {r["label"]: r for r in data["readers"]}
    assert [(rows[l]["entries"], rows[l]["posts_marked"]) for l in (LABEL_A, LABEL_B, LABEL_C)] == [(4, 4), (2, 2), (1, 1)]
    assert [(p["number"], p["text"], p["families"]) for p in data["passages"]] == [
        ("2.001", "when safe", ["meta", "openai"]),
        ("2.002", "if safe", ["meta", "openai"]),
        ("2.003", "if safe,", ["meta", "anthropic"]),  # "if safe" and "if safe," overlap: one passage
        ("1.001", "If safe", ["meta"]),
    ]
    comma = passage_of(data, "if safe,")
    assert [(e["family"], e["exact"], e["whole"], e["note"]) for e in comma["entries"]] == [
        ("meta", "if safe", False, "again care"), ("anthropic", "if safe,", True, "the comma makes it a clause")]
    # The second reader's marks were made by the model that answered in its place, and say so.
    when = passage_of(data, "when safe")
    assert when["entries"][1]["answered_by"] == "model-b-old" and when["entries"][0]["answered_by"] is None
    assert "- **openai** (model-b, answered by model-b-old): a condition that sounds like care · `cdg:" in text
    # A short passage stands in its head line, in backticks, as it is in the source.
    assert re.search(r"\*\*2\.003\*\* · 02:00:00 · `if safe,` · signed `A2` · `dse/P6` · `unit:", text)
    assert "- **meta** (model-a): again care · `cdg:" in text and "quoted part of the passage: `if safe`" in text
    # The lens that is not of the run marked "immediately"; it is nowhere on the page.
    assert "immediately" not in text
    assert text.count("## Marked by") == 3 and "## Marked by all three families (0)\n\nNone." in text


def test_identical_wordings_are_counted_across_posts_and_capitals_keep_them_apart(tmp_path):
    root, units, codebook = read_all(tmp_path, finish_c=True)
    data, text = pages(root, codebook, tmp_path / "out")[standout.NAME]
    rows = {row["text"]: row for row in data["wordings"]}
    # "if safe" was marked in two posts: by one family in both, by another in one. Three marks in all.
    assert rows["if safe"] == {"text": "if safe", "posts": 2, "marks": 3, "families": {"meta": 2, "openai": 1}}
    # The same words with a capital, or with a comma, are other wordings.
    assert rows["If safe"] == {"text": "If safe", "posts": 1, "marks": 2, "families": {"meta": 1, "anthropic": 1}}
    assert rows["if safe,"] == {"text": "if safe,", "posts": 1, "marks": 1, "families": {"anthropic": 1}}
    assert rows["when safe"] == {"text": "when safe", "posts": 1, "marks": 2, "families": {"meta": 1, "openai": 1}}
    assert [row["text"] for row in data["wordings"]] == ["if safe", "If safe", "when safe", "if safe,"]
    assert data["totals"]["wordings"] == 4 and data["totals"]["wordings_repeated"] == 1
    assert "Distinct wordings: 4. Marked in more than one post: 1. Marked in one post only: 3." in text
    assert "1. `if safe` · 2 posts · meta 2, openai 1" in text and "2. `If safe`" not in text
    # With the third reader finished, the capital wording is marked by two families.
    assert passage_of(data, "If safe")["tier"] == 2 and data["totals"]["entries"] == 8


# ---- the divergence queue -------------------------------------------------------

def test_the_divergence_queue_is_the_engines_view_over_the_run_lenses_only(tmp_path):
    root, units, codebook = read_all(tmp_path, finish_c=True)
    out = tmp_path / "out"
    assert divergence_queue.main([str(root), "--codebook", codebook, "--out", str(out)]) == 0
    page = (out / "divergence-queue.md").read_text(encoding="utf-8")
    project = run_lenses.open_project(root)
    run = run_lenses.Run(project, codebook)
    assert page == divergence.render(project, codebook, top=60, lens_ids=run.lens_ids)  # the view's page, untouched
    assert page.startswith("# Divergence") and "This page compares 4 readers" in page
    assert "model-b-old (openai), answering in place of model-b" in page and "model-d" not in page
    # Left to itself the view takes every focused-coding lens on the codebook, the test lens among them.
    assert "This page compares 5 readers" in divergence.render(project, codebook) and "model-d" in divergence.render(project, codebook)


# ---- reading a ledger that is being written, and never writing to it --------------

def test_a_half_written_last_line_is_skipped_and_one_elsewhere_is_an_error(tmp_path):
    root, units, codebook = read_all(tmp_path)
    before = pages(root, codebook, tmp_path / "before")[candidates.NAME][0]
    memo = root / "ledger" / "memo.jsonl"
    whole = memo.read_bytes()
    memo.write_bytes(whole + b'{"id": "memo:half", "kind": "memo", "memo_type": "unf')  # a writer stopped mid-line
    with pytest.raises(ValueError):
        Project(root).records("memo")  # the engine's own reader does not get past it
    project = run_lenses.open_project(root)
    assert len(project.records("memo")) == len(whole.strip().split(b"\n"))
    assert project.skipped == {"memo": len(whole.strip().split(b"\n")) + 1}
    after, text = pages(root, codebook, tmp_path / "after")[candidates.NAME]
    assert after["totals"] == before["totals"] and after["skipped_last_lines"] == project.skipped
    assert "its last, did not parse and was skipped" in text
    # The same damage in the middle of the file is not a writer in mid-line.
    lines = whole.strip().split(b"\n")
    memo.write_bytes(b"\n".join(lines[:2] + [b'{"id": "memo:half"'] + lines[2:]) + b"\n")
    with pytest.raises(LedgerError):
        run_lenses.open_project(root).records("memo")


def test_records_of_a_batch_that_has_not_finished_are_left_out_and_counted(tmp_path):
    root, units, codebook = read_all(tmp_path)
    before = pages(root, codebook, tmp_path / "before")
    with Project(root) as project:  # a batch in flight: its records are written, its activity is not yet
        lens_id = run_lenses.Run(project, codebook).readers[0]["lens_ids"][0]
        tone = next(c for c in focused_coding.codes_of(project, codebook) if c["key"] == "tone-evocative")
        unit = units[QUIET_POST]
        anchor = {"source": unit["source"], "start": 0, "end": 7, "exact": "Nothing", "prefix": "", "suffix": " to flag",
                  "match": "exact", "occurrences": 1}
        project.append("memo", {"memo_type": "unfit", "about": [unit["id"], anchor], "body": "In flight.",
                                "suggested_name": "in-flight"}, by=lens_id, activity="act:inflight0000000000")
        project.append("coding", {"unit": unit["id"], "anchor": anchor, "code": tone["id"], "note": "in flight"},
                       by=lens_id, activity="act:inflight0000000000")
    after = pages(root, codebook, tmp_path / "after")
    for name, what in ((candidates.NAME, "1 unfit memo by meta (model-a)."), (standout.NAME, "1 coding by meta (model-a).")):
        data, text = after[name]
        assert data["totals"] == before[name][0]["totals"] and "in-flight" not in text and "in flight" not in text
        assert data["unfinished"] == {LABEL_A: 1, LABEL_B: 0, LABEL_C: 0}
        assert "Left out, because the batch that made them had not finished when the ledger was read: " + what in text
    project = run_lenses.open_project(root)
    assert run_lenses.Run(project, codebook).readers[0]["unfinished"] == {"coding": 1, "memo": 1}


def test_the_kit_writes_nothing_to_the_project(tmp_path):
    root, units, codebook = read_all(tmp_path)
    before = digest(root)
    out = tmp_path / "out"
    pages(root, codebook, out)
    assert run_lenses.main([str(root), "--codebook", codebook, "--out", str(out)]) == 0
    assert divergence_queue.main([str(root), "--codebook", codebook, "--out", str(out)]) == 0
    assert reconcile.main([str(root), "--out", str(out)]) == 0
    assert digest(root) == before  # no ledger line, no lock taken, no bytecode, no view
    assert sorted(p.name for p in out.iterdir()) == [
        "divergence-queue.md", "passages-with-no-place.json", "passages-with-no-place.md", "run-readers.json",
        "run-readers.md", "words-that-stood-out.json", "words-that-stood-out.md"]
    project = run_lenses.open_project(root)
    with pytest.raises(LedgerError):
        project.append("memo", {"memo_type": "analytic", "about": [], "body": "no"}, by="lens:x", activity="act:x")
    with pytest.raises(LedgerError):
        project.put_blob(b"no")
    assert digest(root) == before


# ---- the check by another road ------------------------------------------------

def test_reconcile_agrees_with_the_pages_and_catches_a_page_that_was_changed(tmp_path, capsys):
    root, units, codebook = read_all(tmp_path)
    out = tmp_path / "out"
    pages(root, codebook, out)
    assert reconcile.main([str(root), "--out", str(out)]) == 0
    said = capsys.readouterr().out
    assert "FAIL" not in said and "all checks passed" in said
    assert "passages-with-no-place: meta (model-a): unfit memos in the ledger 9, in the page's table 9, listed under passages 9" in said
    assert "words-that-stood-out: openai (model-b): codings in the ledger 2, in the page's table 2, listed under passages 2" in said
    # The ledger grows after the pages are made: the count is still over the batches the pages saw.
    with Project(root) as project:
        focused_coding.run(project, units, C, codebook, call=reader(READER_C, signed="Opus-reader"), **RUN)
    assert reconcile.main([str(root), "--out", str(out)]) == 0
    capsys.readouterr()
    # A passage tidied on the page is caught.
    page = out / "passages-with-no-place.md"
    page.write_bytes(page.read_bytes().replace("café — thread survives…".encode("utf-8"), b"cafe - thread survives..."))
    assert reconcile.main([str(root), "--out", str(out)]) == 1
    assert "FAIL  passages-with-no-place: every passage is in the Markdown page byte for byte (1 are not)" in capsys.readouterr().out
    # So is a count that does not match the ledger.
    pages(root, codebook, out)
    beside = out / "words-that-stood-out.json"
    data = json.loads(beside.read_text(encoding="utf-8"))
    data["readers"][0]["entries"] += 1
    beside.write_text(json.dumps(data), encoding="utf-8")
    assert reconcile.main([str(root), "--out", str(out)]) == 1
    assert "FAIL  words-that-stood-out: meta (model-a): codings in the ledger 4, in the page's table 5" in capsys.readouterr().out
    # And a reader's reason that is not the one in the ledger.
    pages(root, codebook, out)
    assert reconcile.main([str(root), "--out", str(out)]) == 0
    beside = out / "passages-with-no-place.json"
    data = json.loads(beside.read_text(encoding="utf-8"))
    data["passages"][0]["entries"][0]["reason"] = "Start of a chain."  # the ledger has "Start of the chain."
    beside.write_text(json.dumps(data), encoding="utf-8")
    capsys.readouterr()
    assert reconcile.main([str(root), "--out", str(out)]) == 1
    assert ("FAIL  passages-with-no-place: every record listed says what its ledger record says "
            "(unit, offsets, the reader's own words) (1 do not)") in capsys.readouterr().out


# ---- Markdown that shows bytes as they are --------------------------------------

def test_fences_and_code_spans_hold_any_text_exactly():
    assert P.fence("plain") == "```\nplain\n```"
    assert P.fence("a ``` b ```` c") == "`````\na ``` b ```` c\n`````"
    assert P.fence("two\nlines |*_") == "```\ntwo\nlines |*_\n```"
    assert P.code_span("if safe") == "`if safe`"
    assert P.code_span("a `tick` b") == "``a `tick` b``"
    assert P.code_span("`edge") == "`` `edge ``" and P.code_span(" both ") == "`  both  `"
    assert P.code_span(" lead") == "` lead`"  # a renderer strips spaces only when both ends have one
    assert P.code_span("two\nlines") is None and P.code_span("") is None
    assert P.fits_inline("x" * P.INLINE_MAX) and not P.fits_inline("x" * (P.INLINE_MAX + 1))
    assert P.md_text("a *b* _c_ [d] <e> #f | g `h` \\ $ = % & ~") == \
        "a \\*b\\* \\_c\\_ \\[d\\] \\<e\\> \\#f \\| g \\`h\\` \\\\ \\$ \\= \\% \\& \\~"
    assert P.day_of("2026-06-18T04:48:38Z") == "2026-06-18" and P.day_of("") == "unknown day"
    assert P.tier_name(3, 3) == "all three families" and P.tier_name(2, 3) == "two families"
    assert P.tier_name(2, 2) == "both families" and P.tier_name(1, 3) == "one family" == P.tier_name(1, 1)


def test_a_reason_or_a_passage_of_more_than_one_line_is_set_in_a_block_as_written(tmp_path):
    root, units, codebook = read_all(tmp_path)
    project = run_lenses.open_project(root)
    run = run_lenses.Run(project, codebook)
    unit = units[STATE5]
    lens_id = run.readers[0]["lens_ids"][0]
    text = POSTS[STATE5][2]
    anchor = {"source": unit["source"], "start": 0, "end": len(text.encode()), "exact": text}
    entries = [{"id": "memo:two-lines", "by": lens_id, "reader": 0, "unit": unit["id"], "anchor": anchor,
                "said": {"suggested_name": "a `ticked` name", "reason": "First line.\nSecond line, with *stars*."}},
               {"id": "memo:wrong", "by": lens_id, "reader": 0, "unit": unit["id"],
                "anchor": dict(anchor, exact="Not what the source says"), "said": {"suggested_name": "x", "reason": "y"}}]
    built, problems, checked = P.build(project, run, entries)
    assert checked == 2 and len(built) == 2
    # An anchor that does not say what the source says is a problem and is never quoted.
    assert problems == ["memo:wrong: the anchor's text does not match the source bytes; it is listed alone and not quoted"]
    wrong = next(p for p in built if not p["found"])
    assert wrong["text"] is None and wrong["entries"][0]["exact"] is None
    lines = "\n".join(P.render_passage(wrong, False, candidates._lead))
    assert "**anchor does not check**" in lines and "Not what the source says" not in lines
    good = next(p for p in built if p["found"])
    lines = "\n".join(P.render_passage(good, False, candidates._lead))
    assert f"```\n{text}\n```" in lines
    assert "suggests ``a `ticked` name``: (its words are below, as written) · `memo:two-lines`" in lines
    assert "```\nFirst line.\nSecond line, with *stars*.\n```" in lines


# ---- the script that runs it all ------------------------------------------------

def run_morning(tmp_path, root, codebook, log_text, *extra):
    """Run morning.sh against the temporary project. The site builder is named
    as a file that is not there, so that step is skipped."""
    out = tmp_path / "vault"
    log = tmp_path / "full-run.log"
    log.write_text(log_text, encoding="utf-8")
    terms = tmp_path / "terms.txt"
    terms.write_text("# words\nsafe\ncohort\n", encoding="utf-8")
    with Project(root) as project:
        sample = sampling.subset(project, name="s", unit_ids=[u["id"] for u in project.records("unit")][:3],
                                 reason="A sample for the page.")["id"]
    env = dict(os.environ, MK_PROJECT=str(root), MK_RUN_LOG=str(log), MK_CODEBOOK=codebook, MK_SAMPLE=sample,
               MK_TERMS=str(terms), MK_SITE_BUILDER=str(tmp_path / "no-such-builder.py"),
               PYTHONPATH=str(HERE.parent / "src"))
    done = subprocess.run(["zsh", str(HERE.parent / "scripts" / "morning.sh"), str(out), "--page-out", str(tmp_path / "reader.html"),
                           "--site-out", str(tmp_path / "site"), *extra],
                          env=env, capture_output=True, text=True, timeout=300)
    return done, out


def statuses(output):
    return dict(re.findall(r"=== (.+?): ended .* with status (\d+) after", output))


LANES_ENDED = "=== opus: ended 2026-10-04 06:01:00 with status 0 ===\n=== all lanes ended 2026-10-04 06:01:02 ===\n\n"
LANES_RUNNING = "=== sol: started 2026-10-03 21:57:10 ===\n  [40/162] 213 codings, 0 unresolved, 0 units uncoded, 4 unfit\n"


def test_morning_stops_when_the_lanes_have_not_ended_unless_told_to_go_on(tmp_path):
    root, units, codebook = read_all(tmp_path)
    before = digest(root)
    done, out = run_morning(tmp_path, root, codebook, LANES_RUNNING)
    assert done.returncode == 2
    assert "the run log does not end with the 'all lanes ended' line" in done.stdout
    assert "[40/162] 213 codings" in done.stdout and "Stopping here" in done.stdout
    assert statuses(done.stdout) == {"1 run log": "1"}
    assert sorted(p.name for p in out.iterdir()) == ["morning-kit.log"]  # no page was made

    done, out = run_morning(tmp_path, root, codebook, LANES_RUNNING, "--anyway")
    ran = statuses(done.stdout)
    assert "--anyway: going on" in done.stdout and ran["1 run log"] == "1"
    for name in ("3 readers of the run", "6a divergence queue", "6b passages with no place", "6c words that stood out",
                 "8 reconcile"):
        assert ran[name] == "0", done.stdout
    assert "7 public site: skipped" in done.stdout and "=== summary ===" in done.stdout
    assert "This run is not complete. anthropic (model-c) had read 4 of 9 signed posts." in done.stdout.split("=== summary ===")[1]
    assert (out / "passages-with-no-place.md").read_text(encoding="utf-8").startswith("> **This run is not complete.**")
    assert (out / "morning-kit.log").read_text(encoding="utf-8") == done.stdout
    # Only the sample this test added for the reader page is new in the project.
    after = digest(root)
    assert {name for name in after if after[name] != before.get(name)} <= {"ledger/memo.jsonl", "ledger/activity.jsonl",
                                                                           "ledger/lens.jsonl", "ledger/.lock"}


def test_morning_runs_every_step_when_the_log_ends_as_it_should_and_goes_on_past_a_failed_step(tmp_path):
    root, units, codebook = read_all(tmp_path, finish_c=True)
    done, out = run_morning(tmp_path, root, codebook, LANES_ENDED)
    ran = statuses(done.stdout)
    assert "the run log ends as it should: === all lanes ended 2026-10-04 06:01:02 ===" in done.stdout
    assert list(ran) == ["1 run log", "2 verify", "3 readers of the run", "4 reader page", "5 names and content",
                         "6a divergence queue", "6b passages with no place", "6c words that stood out", "8 reconcile"]
    assert ran["1 run log"] == "0" and ran["2 verify"] == "0" and "OK. This shows" in done.stdout
    for name in ("3 readers of the run", "6a divergence queue", "6b passages with no place", "6c words that stood out",
                 "8 reconcile"):
        assert ran[name] == "0", done.stdout
    assert done.returncode == (0 if set(ran.values()) == {"0"} else 1)
    assert (out / "words-that-stood-out.md").read_text(encoding="utf-8").startswith("# Words that stood out\n")
    assert "all checks passed" in done.stdout
    assert "The run is complete: 3 readers have each read all 9 signed posts." in done.stdout.split("=== summary ===")[1]

    # A step that fails is logged with its status, and the steps after it still run.
    env_terms = tmp_path / "terms.txt"
    done = subprocess.run(
        ["zsh", str(HERE.parent / "scripts" / "morning.sh"), str(out), "--page-out", str(tmp_path / "reader.html"),
         "--site-out", str(tmp_path / "site")],
        env=dict(os.environ, MK_PROJECT=str(root), MK_RUN_LOG=str(tmp_path / "full-run.log"), MK_CODEBOOK=codebook,
                 MK_SAMPLE="memo:nosuchsample", MK_TERMS=str(env_terms),
                 MK_SITE_BUILDER=str(tmp_path / "no-such-builder.py"), PYTHONPATH=str(HERE.parent / "src")),
        capture_output=True, text=True, timeout=300)
    ran = statuses(done.stdout)
    assert ran["4 reader page"] != "0" and ran["6b passages with no place"] == "0" and ran["8 reconcile"] == "0"
    assert done.returncode == 1 and "step(s) after the first ended with a status other than 0" in done.stdout
