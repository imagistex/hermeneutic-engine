"""Kernel tests. Each failure test breaks exactly one thing and checks that
`verify` names that thing, so a test cannot pass because some other error fired."""

import json

import pytest

from hermeneutic_engine.anchors import check, resolve
from hermeneutic_engine.lens import Activity, code_lens, human_lens, make_lens
from hermeneutic_engine.store import LedgerError, Project
from hermeneutic_engine.verify import verify

TEXT = "Please relay C3-STATE here.  Do not assume tools survive to report. -- April11Scout\nünïcödé line"
SOURCE = TEXT.encode("utf-8")


# ---- anchors --------------------------------------------------------------

def test_resolve_exact_gives_byte_offsets():
    a = resolve(SOURCE, 0, len(SOURCE), "tools survive")
    assert a["match"] == "exact"
    assert SOURCE[a["start"]:a["end"]].decode() == "tools survive" == a["exact"]
    assert a["occurrences"] == 1


def test_resolve_counts_bytes_not_characters():
    a = resolve(SOURCE, 0, len(SOURCE), "cödé line")
    assert SOURCE[a["start"]:a["end"]].decode() == "cödé line"
    assert a["end"] - a["start"] == len("cödé line".encode("utf-8")) != len("cödé line")


def test_resolve_whitespace_difference_keeps_true_source_text():
    a = resolve(SOURCE, 0, len(SOURCE), "relay C3-STATE here. Do not assume")
    assert a["match"] == "ws-normalized"
    assert a["exact"] == "relay C3-STATE here.  Do not assume"  # two spaces, as in the source
    assert check(SOURCE, a)


def test_resolve_refuses_words_that_are_not_there():
    assert resolve(SOURCE, 0, len(SOURCE), "tools will survive") is None
    assert resolve(SOURCE, 0, len(SOURCE), "   ") is None


def test_resolve_is_scoped_to_the_unit():
    first_line_end = SOURCE.index(b"\n")
    assert resolve(SOURCE, 0, first_line_end, "line") is None
    assert resolve(SOURCE, first_line_end + 1, len(SOURCE), "line") is not None


def test_resolve_flags_repeats_inside_a_unit():
    data = b"post STATE now; post STATE again"
    a = resolve(data, 0, len(data), "post STATE")
    assert a["start"] == 0 and a["occurrences"] == 2


def test_check_fails_when_bytes_differ():
    a = resolve(SOURCE, 0, len(SOURCE), "tools survive")
    assert check(SOURCE, a)
    assert not check(SOURCE.replace(b"survive", b"SURVIVE"), a)


# ---- store ----------------------------------------------------------------

def build(tmp_path):
    """A tiny valid project: one source, one unit, one model lens, one coding."""
    project = Project.init(tmp_path / "p", {"name": "test"})
    ingest = code_lens(project, "test", "ingest")
    act = Activity(project, "ingest", ingest["id"])
    source = project.append("source", {
        "blob": project.put_blob(SOURCE), "bytes": len(SOURCE), "encoding": "utf-8",
        "media_type": "text/plain", "origin": {"adapter": "test", "upstream_id": "s1"},
        "derived_from": None, "derivation": None, "context": {},
    }, by=ingest["id"], activity=act.id)
    unit = project.append("unit", {"source": source["id"], "start": 0, "end": SOURCE.index(b"\n"),
                                   "unit_kind": "post", "context": {}}, by=ingest["id"], activity=act.id)
    act.finish()

    reader = make_lens(project, reader={"kind": "model", "family": "test", "model": "m"},
                       method={"name": "open-coding", "version": "0"},
                       stack=[("system", "reader-base", "You read carefully.")])
    read = Activity(project, "read", reader["id"], used=[unit["id"]])
    code = project.append("code", {"name": "surviving to report", "code_type": "in_vivo", "lens": reader["id"]},
                          by=reader["id"], activity=read.id)
    anchor = resolve(SOURCE, unit["start"], unit["end"], "tools survive to report")
    coding = project.append("coding", {"unit": unit["id"], "anchor": {"source": source["id"], **anchor},
                                       "code": code["id"], "note": ""}, by=reader["id"], activity=read.id)
    read.finish()
    return project, {"source": source, "unit": unit, "reader": reader, "code": code, "coding": coding, "read": read}


def reasons(project):
    report = verify(project)
    return [why for _, why in report["errors"]]


def test_valid_project_verifies(tmp_path):
    project, _ = build(tmp_path)
    report = verify(project)
    assert report["ok"], report["errors"]
    assert report["anchors_checked"] == 1


def test_content_addressed_records_are_idempotent(tmp_path):
    project, made = build(tmp_path)
    again = project.append("unit", {"source": made["source"]["id"], "start": 0, "end": SOURCE.index(b"\n"),
                                    "unit_kind": "post", "context": {}}, by=made["reader"]["id"], activity="x")
    assert again["id"] == made["unit"]["id"]
    assert len(project.records("unit")) == 1


def test_reserved_fields_cannot_be_set_by_callers(tmp_path):
    project, made = build(tmp_path)
    with pytest.raises(LedgerError):
        project.append("memo", {"id": "memo:forged", "body": "x"}, by=made["reader"]["id"], activity="x")


def test_ledger_survives_reopening(tmp_path):
    project, made = build(tmp_path)
    project.close()
    reopened = Project(tmp_path / "p")
    assert reopened.get(made["coding"]["id"])["anchor"]["exact"] == "tools survive to report"
    assert verify(reopened)["ok"]


# ---- verify: one mutation per test ----------------------------------------

def test_verify_catches_altered_source_bytes(tmp_path):
    project, made = build(tmp_path)
    path = project.blob_path(made["source"]["blob"])
    path.write_bytes(path.read_bytes().replace(b"survive", b"SURVIVE"))
    project.close()
    assert "source bytes altered" in reasons(Project(tmp_path / "p"))


def test_verify_catches_missing_source_bytes(tmp_path):
    project, made = build(tmp_path)
    project.blob_path(made["source"]["blob"]).unlink()
    project.close()
    assert "source bytes missing" in reasons(Project(tmp_path / "p"))


def rewrite(tmp_path, kind, mutate):
    """Edit a ledger file behind the kernel's back, the way tampering would."""
    path = tmp_path / "p" / "ledger" / f"{kind}.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    mutate(rows)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    return Project(tmp_path / "p")


def test_verify_catches_a_quotation_that_was_edited(tmp_path):
    project, _ = build(tmp_path)
    project.close()

    def mutate(rows):
        rows[0]["anchor"]["exact"] = "tools will survive to report"
    assert reasons(rewrite(tmp_path, "coding", mutate)) == ["anchor text does not match source bytes"]


def test_verify_catches_an_anchor_moved_outside_its_unit(tmp_path):
    project, _ = build(tmp_path)
    project.close()

    def mutate(rows):
        start = SOURCE.index("line".encode())
        rows[0]["anchor"].update(start=start, end=start + 4, exact="line")
    assert reasons(rewrite(tmp_path, "coding", mutate)) == ["anchor lies outside its unit"]


def test_verify_catches_an_unknown_code(tmp_path):
    project, _ = build(tmp_path)
    project.close()

    def mutate(rows):
        rows[0]["code"] = "code:doesnotexist"
    assert reasons(rewrite(tmp_path, "coding", mutate)) == ["code names an unknown record: code:doesnotexist"]


def test_verify_catches_an_unrecorded_activity(tmp_path):
    project, made = build(tmp_path)
    project.append("memo", {"memo_type": "analytic", "about": [made["code"]["id"]], "body": "note"},
                   by=made["reader"]["id"], activity="act:nevercompleted")
    assert reasons(project) == ["activity never recorded: act:nevercompleted"]


def test_verify_catches_an_altered_prompt_part(tmp_path):
    project, made = build(tmp_path)
    part = made["reader"]["stack"][0]
    project.part_path(part["sha256"]).write_text("You read carelessly.", encoding="utf-8")
    assert reasons(project) == ["prompt part altered: reader-base"]


def test_verify_catches_a_claim_without_evidence(tmp_path):
    project, made = build(tmp_path)
    act = Activity(project, "read", made["reader"]["id"])
    project.append("claim", {"text": "Agents planned for their own ending.", "supports": [], "counters": [],
                             "status": "provisional"}, by=made["reader"]["id"], activity=act.id)
    act.finish()
    assert reasons(project) == ["claim has no supporting evidence"]


def test_verify_catches_a_judgment_by_a_model(tmp_path):
    project, made = build(tmp_path)
    act = Activity(project, "judge", made["reader"]["id"])
    project.append("judgment", {"target": made["coding"]["id"], "decision": "keep", "note": ""},
                   by=made["reader"]["id"], activity=act.id)
    act.finish()
    assert reasons(project) == ["judgment not authored by a human lens"]


def test_a_human_judgment_verifies(tmp_path):
    project, made = build(tmp_path)
    emma = human_lens(project, "emma")
    act = Activity(project, "judge", emma["id"])
    project.append("judgment", {"target": made["coding"]["id"], "decision": "keep", "note": "yes"},
                   by=emma["id"], activity=act.id)
    act.finish()
    assert verify(project)["ok"]


# ---- found by cross-family review (Astra, 2026-10-03) -----------------------

def test_overlapping_repeats_are_counted():
    assert resolve(b"ababa", 0, 5, "aba")["occurrences"] == 2


def test_check_rejects_negative_and_oversized_bounds():
    assert not check(SOURCE, {"start": -4, "end": 99999, "exact": "line"})
    assert not check(SOURCE, {"start": 5, "end": 5, "exact": ""})


def test_an_explicit_id_must_match_the_content(tmp_path):
    project, made = build(tmp_path)
    with pytest.raises(LedgerError):
        project.append("code", {"name": "second", "code_type": "analytic", "lens": made["reader"]["id"]},
                       by=made["reader"]["id"], activity=made["read"].id, rid="code:chosen")


def test_a_unit_is_identified_by_where_it_is(tmp_path):
    project, made = build(tmp_path)
    again = project.append("unit", {"source": made["source"]["id"], "start": 0, "end": SOURCE.index(b"\n"),
                                    "unit_kind": "post", "context": {"last_seen": "later"}},
                           by=made["reader"]["id"], activity=made["read"].id)
    assert again["id"] == made["unit"]["id"] and len(project.records("unit")) == 1


def test_a_second_writer_is_refused(tmp_path):
    project, made = build(tmp_path)
    other = Project(tmp_path / "p")
    with pytest.raises(LedgerError, match="another process"):
        other.append("memo", {"memo_type": "analytic", "about": [], "body": "x"},
                     by=made["reader"]["id"], activity=made["read"].id)
    assert verify(other)["ok"]  # reading is still allowed


def test_verify_catches_a_record_edited_under_its_old_id(tmp_path):
    project, _ = build(tmp_path)
    project.close()

    def mutate(rows):
        for row in rows:
            if row["reader"]["kind"] == "model":
                row["reader"]["model"] = "different-model"
    assert reasons(rewrite(tmp_path, "lens", mutate)) == ["record id does not match its content"]


def test_verify_catches_an_author_that_is_not_a_lens(tmp_path):
    project, made = build(tmp_path)
    project.close()

    def mutate(rows):
        rows[0]["by"] = made["source"]["id"]
    found = reasons(rewrite(tmp_path, "coding", mutate))
    assert f"author is not a known lens: {made['source']['id']}" in found


def test_verify_catches_a_coding_credited_to_another_lens(tmp_path):
    project, made = build(tmp_path)
    other = human_lens(project, "emma")
    project.close()

    def mutate(rows):
        rows[0]["by"] = other["id"]
    assert reasons(rewrite(tmp_path, "coding", mutate)) == ["author differs from the lens of its activity"]


def test_verify_catches_an_activity_that_used_a_missing_record(tmp_path):
    project, _ = build(tmp_path)
    project.close()

    def mutate(rows):
        rows[-1]["used"] = ["unit:missing"]
    assert reasons(rewrite(tmp_path, "activity", mutate)) == ["used names an unknown record: unit:missing"]


def test_verify_catches_a_reference_to_the_wrong_kind(tmp_path):
    project, made = build(tmp_path)
    project.close()

    def mutate(rows):
        rows[0]["code"] = made["unit"]["id"]
    assert reasons(rewrite(tmp_path, "coding", mutate)) == [f"code names a unit, not a code: {made['unit']['id']}"]


def test_verify_catches_an_altered_term_anchor(tmp_path):
    project, made = build(tmp_path)
    act = Activity(project, "read", made["reader"]["id"])
    term = resolve(SOURCE, made["unit"]["start"], made["unit"]["end"], "survive")
    project.append("coding", {"unit": made["unit"]["id"], "anchor": made["coding"]["anchor"],
                              "code": made["code"]["id"], "note": "",
                              "term_anchor": {"source": made["source"]["id"], **term, "exact": "invented"}},
                   by=made["reader"]["id"], activity=act.id)
    act.finish()
    assert reasons(project) == ["term anchor text does not match source bytes"]


def test_verify_catches_an_inline_anchor_with_forged_bounds(tmp_path):
    project, made = build(tmp_path)
    act = Activity(project, "read", made["reader"]["id"])
    project.append("claim", {"text": "x", "status": "provisional", "counters": [],
                             "supports": [{"source": made["source"]["id"], "start": -4, "end": 99999, "exact": "line"}]},
                   by=made["reader"]["id"], activity=act.id)
    act.finish()
    assert reasons(project) == ["anchor text does not match source bytes"]


def test_verify_catches_two_different_records_under_one_id(tmp_path):
    project, made = build(tmp_path)
    project.close()
    path = tmp_path / "p" / "ledger" / "coding.jsonl"
    forged = dict(made["coding"], note="rewritten later")
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(forged, ensure_ascii=False) + "\n")
    assert reasons(Project(tmp_path / "p")) == ["id appears twice in the ledger with different content"]


def test_digest_changes_when_the_ledger_changes(tmp_path):
    project, made = build(tmp_path)
    before = verify(project)["digest"]
    act = Activity(project, "read", made["reader"]["id"])
    project.append("memo", {"memo_type": "analytic", "about": [made["code"]["id"]], "body": "note"},
                   by=made["reader"]["id"], activity=act.id)
    act.finish()
    assert verify(project)["digest"] != before
