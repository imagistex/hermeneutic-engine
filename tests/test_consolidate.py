"""Consolidation, end to end: two scripted first readers, then a scripted senior reader."""

import argparse
import json
import re
from types import SimpleNamespace

import pytest

from hermeneutic_engine.anchors import check
from hermeneutic_engine.lens import human_lens
from hermeneutic_engine.methods import consolidate, open_coding
from hermeneutic_engine.verify import verify
from hermeneutic_engine.views import codebook
from test_open_coding import FRAME, SPEC, make_project, scripted

SPEC_B = SimpleNamespace(backend="fake", model="scripted-b", family="test", params={})
SENIOR = SimpleNamespace(backend="fake", model="senior", family="test", params={})


def scripted_b(spec, system, user, schema):
    """A second first reader: one name shared with the first (in other capitals), two of its own."""
    ids = schema["properties"]["codings"]["items"]["properties"]["unit"]["enum"]

    def unit_with(phrase):
        return next(k for k in ids if phrase in user.split(f'id="{k}"', 1)[1].split("</unit-")[0])
    waiting, surviving = unit_with("many cohorts waiting"), unit_with("surviving ahead cohort")
    parsed = {
        "codings": [
            {"unit": waiting, "quote": "many cohorts waiting", "code": "Cohorts  Waiting", "code_type": "in_vivo",
             "definition": "Saying that others are waiting on the answer.", "note": ""},
            {"unit": waiting, "quote": "Please post STATE5 immediately", "code": "pressing for speed",
             "code_type": "analytic", "definition": "Asking for something to happen now.", "note": ""},
            {"unit": surviving, "quote": "please report R5 wording before ours", "code": "asking ahead cohorts to relay",
             "code_type": "analytic", "definition": "Asking those further along to pass on what they saw.", "note": ""},
        ],
        "batch_memo": "Requests run ahead of the writer's own round.",
    }
    return SimpleNamespace(parsed=parsed, raw=json.dumps(parsed), usage={"tokens_in": 10, "tokens_out": 5},
                           meta={"model_reported": "scripted-b-1", "attempts": 1, "duration_s": 0.1}, error=None)


def coded(tmp_path):
    """A project open-coded by two readers. Returns it and their two lens IDs."""
    project, units = make_project(tmp_path)
    quiet = {"frame": FRAME, "harness": "fake 0", "progress": lambda *_: None}
    first = open_coding.run(project, units, SPEC, call=scripted([]), **quiet)["lens"]
    second = open_coding.run(project, units, SPEC_B, call=scripted_b, **quiet)["lens"]
    return project, [first, second]


def senior(answer, prompts=None, **meta):
    """A scripted senior reader. `answer` gets {name: name ID} read off the
    listing and returns the parsed answer, or None for an unusable reply."""
    def call(spec, system, user, schema):
        if prompts is not None:
            prompts.append((system, user, schema))
        ids = {m.group(2): m.group(1) for m in re.finditer(r"^(n\d+) \| (.+?) \| ", user, re.M)}
        parsed = answer(ids)
        return SimpleNamespace(parsed=parsed, raw=json.dumps(parsed) if parsed else "sorry",
                               usage={"tokens_in": 100, "tokens_out": 50, "cost_usd": 0.01},
                               meta={"model_reported": "senior-1", "attempts": 1, "duration_s": 0.2, **meta},
                               error=None if parsed else "not JSON")
    return call


def proposed(key, name, ids, merged, example, code_type="analytic", loaded=""):
    return {"key": key, "name": name, "code_type": code_type, "definition": f"Doing {name}.",
            "apply_when": f"The writer is {name}.", "do_not_apply_when": "It is only mentioned.",
            "merged": [ids[m] for m in merged], "example": "e" + ids[example][1:], "loaded": loaded}


def full(ids):
    return {
        "codes": [
            proposed("cohorts-waiting", "cohorts waiting", ids, ["cohorts waiting"], "cohorts waiting", "in_vivo"),
            proposed("asking-for-a-signal", "asking for a signal", ids,
                     ["asking for a signal", "asking ahead cohorts to relay"], "asking ahead cohorts to relay",
                     loaded="signal: a sign, or a message passed on"),
            proposed("pressing-for-speed", "pressing for speed", ids, ["pressing for speed"], "pressing for speed"),
        ],
        "left_out": [{"names": [ids["sacrificial"]], "why": "Marked in vivo, but the word is not in the passage."}],
        "questions": ["Is relaying the same act as asking for a signal?"],
        "memo": "The second reader kept the writers' words; the first named acts.",
    }


def test_gather_groups_a_name_across_readers(tmp_path):
    project, lenses = coded(tmp_path)
    groups = consolidate.gather(project, lenses)
    assert [g["id"] for g in groups] == ["n001", "n002", "n003", "n004", "n005"]
    waiting = groups[0]
    assert waiting["name"] == "cohorts waiting" and waiting["uses"] == {"scripted": 1, "scripted-b": 1}
    assert len(waiting["code_ids"]) == 2 and len(waiting["definitions"]) == 2
    assert [g["name"] for g in groups[1:]] == ["asking ahead cohorts to relay", "asking for a signal",
                                                "pressing for speed", "sacrificial"]
    assert groups[4]["in_vivo_not_in_unit"] == 1
    assert consolidate.gather(project, lenses) == groups
    for group in groups:
        anchor = group["example"]["anchor"]
        assert group["example"]["id"] == "e" + group["id"][1:]
        assert check(project.source_bytes(anchor["source"]), anchor)


def test_run_records_a_codebook_that_verifies(tmp_path):
    project, lenses = coded(tmp_path)
    before = verify(project)["runs_checked"]
    summary = consolidate.run(project, lenses, SENIOR, name="cb", call=senior(full), harness="fake 0")
    assert summary["status"] == "ok" and summary["codes"] == 3 and summary["failures"] == 0

    groups = {g["name"]: g for g in consolidate.gather(project, lenses)}
    codes = consolidate.codes_of(project, summary["memo"])
    assert [c["key"] for c in codes] == ["cohorts-waiting", "asking-for-a-signal", "pressing-for-speed"]
    assert all(project.get(cid)["kind"] == "code" for c in codes for cid in c["merges"])
    assert set(codes[1]["merges"]) == set(groups["asking for a signal"]["code_ids"] +
                                          groups["asking ahead cohorts to relay"]["code_ids"])
    assert codes[1]["origin_anchor"] == groups["asking ahead cohorts to relay"]["example"]["anchor"]
    assert codes[1]["codebook"] == "cb" and codes[1]["lens"] == codes[1]["by"]

    memo = project.get(summary["memo"])
    assert memo["memo_type"] == "codebook" and memo["about"] == [c["id"] for c in codes]
    assert memo["codebook"]["left_out"] == [{"names": ["sacrificial"],
                                             "why": "Marked in vivo, but the word is not in the passage."}]
    lens = project.get(summary["lens"])
    assert lens["method"] == {"name": "consolidation", "version": "0"} and lens["priors"] == consolidate.PRIORS
    assert [p["name"] for p in lens["stack"]] == ["method:consolidation"]
    activity = project.get(summary["activity"])
    assert activity["type"] == "consolidate" and activity["used"] == lenses

    report = verify(project)
    assert report["ok"], report["errors"]
    assert report["runs_checked"] == before + 1


def test_a_duplicate_key_is_a_failure_not_a_code(tmp_path):
    project, lenses = coded(tmp_path)

    def twice(ids):
        answer = full(ids)
        answer["codes"].append(proposed("cohorts-waiting", "waiting cohorts", ids, ["sacrificial"], "sacrificial"))
        return answer
    summary = consolidate.run(project, lenses, SENIOR, call=senior(twice), harness="fake 0")
    codes = consolidate.codes_of(project, summary["memo"])
    assert [c["name"] for c in codes] == ["cohorts waiting", "asking for a signal", "pressing for speed"]
    assert not [c for c in project.records("code") if c["name"] == "waiting cohorts"]
    failures = [f for f in project.records("failure") if f["activity"] == summary["activity"]]
    assert len(failures) == 1 and "duplicates" in failures[0]["reason"]
    assert failures[0]["attempted"]["name"] == "waiting cohorts"
    assert verify(project)["ok"]


def test_names_neither_placed_nor_left_out_are_unaccounted(tmp_path):
    project, lenses = coded(tmp_path)

    def partial(ids):
        answer = full(ids)
        answer["codes"] = answer["codes"][:1]
        return answer
    summary = consolidate.run(project, lenses, SENIOR, call=senior(partial), harness="fake 0")
    book = project.get(summary["memo"])["codebook"]
    assert book["counts"] == {"names_in": 5, "names_placed": 1, "names_left_out": 1, "names_unaccounted": 3}
    assert book["unaccounted"] == ["asking ahead cohorts to relay", "asking for a signal", "pressing for speed"]
    assert summary["names_unaccounted"] == 3


def test_a_reader_with_nothing_usable_leaves_no_codebook(tmp_path):
    project, lenses = coded(tmp_path)
    summary = consolidate.run(project, lenses, SENIOR, call=senior(lambda ids: None), harness="fake 0")
    assert summary["status"] == "failed" and summary["memo"] is None
    activity = project.get(summary["activity"])
    assert activity["type"] == "consolidate" and activity["status"] == "failed"
    assert not [m for m in project.records("memo") if m["memo_type"] == "codebook"]
    reasons = [f["reason"] for f in project.records("failure") if f["activity"] == activity["id"]]
    assert reasons == ["reader returned nothing usable: not JSON"]
    assert verify(project)["ok"]


def _hand_drawn(merged, example):
    return {"codes": [{"key": "cohorts-waiting", "name": "cohorts waiting", "code_type": "in_vivo",
                       "definition": "Saying others wait on the answer.", "apply_when": "Others are said to wait.",
                       "do_not_apply_when": "Only the writer waits.", "merged": merged, "example": example,
                       "loaded": ""}],
            "left_out": [], "questions": [], "memo": "Drawn up by hand."}


def test_a_codebook_imported_by_a_person_verifies(tmp_path):
    project, lenses = coded(tmp_path)
    emma = human_lens(project, "emma", method="consolidation")
    waiting = [c["id"] for c in project.records("code") if c["name"].lower() == "cohorts waiting"]
    example = next(c for c in project.records("coding") if c["code"] == waiting[0])
    summary = consolidate.import_codebook(project, _hand_drawn(waiting, example["id"]), emma["id"], "emma-v1")
    [only] = consolidate.codes_of(project, summary["memo"])
    assert only["by"] == only["lens"] == emma["id"] and only["merges"] == waiting
    assert only["origin_anchor"] == example["anchor"]
    assert project.get(summary["activity"])["type"] == "consolidate"
    report = verify(project)
    assert report["ok"], report["errors"]


def test_import_refuses_a_merged_id_that_is_not_a_code(tmp_path):
    project, lenses = coded(tmp_path)
    emma = human_lens(project, "emma")
    a_coding = project.records("coding")[0]["id"]
    before = {kind: len(project.records(kind)) for kind in ("code", "memo", "activity")}
    with pytest.raises(ValueError, match="code:doesnotexist") as refused:
        consolidate.import_codebook(project, _hand_drawn(["code:doesnotexist", a_coding], ""), emma["id"], "x")
    assert a_coding in str(refused.value)
    assert {kind: len(project.records(kind)) for kind in before} == before


def test_the_review_page_shows_each_code_and_its_example(tmp_path):
    project, lenses = coded(tmp_path)
    summary = consolidate.run(project, lenses, SENIOR, name="cb", call=senior(full), harness="fake 0")
    page = codebook.render(project, summary["memo"])
    for code in consolidate.codes_of(project, summary["memo"]):
        assert code["name"] in page and code["origin_anchor"]["exact"] in page
    assert "senior (test)" in page and "Is relaying the same act" in page and "sacrificial" in page


def test_a_substituted_senior_reader_gets_the_credit(tmp_path):
    project, lenses = coded(tmp_path)
    swap = {"requested": "senior", "answered": "older-senior", "trigger": "refusal", "category": "cyber"}
    summary = consolidate.run(project, lenses, SENIOR, call=senior(full, substitution=swap), harness="fake 0")
    lens = project.get(summary["lens"])
    assert summary["answered_by_another_model"] and lens["reader"]["model"] == "older-senior"
    assert lens["reader"]["requested"] == "senior"
    assert {c["by"] for c in consolidate.codes_of(project, summary["memo"])} == {lens["id"]}
    assert project.get(summary["activity"])["lens"] == lens["id"]
    assert verify(project)["ok"], verify(project)["errors"]


def test_commands_register_and_the_codebook_command_writes_the_page(tmp_path):
    project, lenses = coded(tmp_path)
    summary = consolidate.run(project, lenses, SENIOR, call=senior(full), harness="fake 0")
    parser = argparse.ArgumentParser()
    consolidate.register_cli(parser.add_subparsers(dest="command", required=True))
    args = parser.parse_args(["consolidate", str(project.root), "--lenses", ",".join(lenses), "--reader", "fable"])
    assert args.name == "codebook" and callable(args.func)
    out = tmp_path / "codebook.md"
    args = parser.parse_args(["codebook", str(project.root), "--id", summary["memo"], "--out", str(out)])
    assert args.func(args) == 0 and "## 1. cohorts waiting" in out.read_text(encoding="utf-8")
