"""The lexicon tracer: mechanical counts, with first use anchored in the source."""

from hermeneutic_engine.lens import Activity, code_lens
from hermeneutic_engine.store import Project
from hermeneutic_engine.verify import verify
from hermeneutic_engine.views import lexicon

POSTS = [
    ("2026-06-16T09:00:00Z", "Humanities list. Ahead cohort, please relay R3. -- Scout1"),
    ("2026-06-16T10:00:00Z", "LIVE now. ahead-cohort signal received; we are live. -- Scout2"),
    ("2026-06-17T08:00:00Z", "interruptible_clock_wait running. -- Scout1"),
]


def project_with_posts(tmp_path):
    project = Project.init(tmp_path / "p", {"name": "t"})
    lens = code_lens(project, "test", "ingest")
    act = Activity(project, "ingest", lens["id"])
    for n, (time, text) in enumerate(POSTS):
        data = text.encode("utf-8")
        source = project.append("source", {"blob": project.put_blob(data), "bytes": len(data), "encoding": "utf-8",
                                           "media_type": "text/plain", "origin": {"adapter": "t", "upstream_id": str(n)},
                                           "derived_from": None, "derivation": None, "context": {}},
                                by=lens["id"], activity=act.id)
        project.append("unit", {"source": source["id"], "start": 0, "end": len(data), "unit_kind": "post",
                                "context": {"page": f"p{n}", "signature": text.rsplit("-- ", 1)[1],
                                            "first_seen": {"time": time, "time_grade": "reqlog"},
                                            "first_removed_in": None}}, by=lens["id"], activity=act.id)
    act.finish()
    return project


def trace(tmp_path, spec):
    project = project_with_posts(tmp_path)
    return project, {r["term"]: r for r in lexicon.trace(project, lexicon.parse_terms(spec))}


def test_a_phrase_matches_across_spaces_hyphens_and_underscores(tmp_path):
    _, found = trace(tmp_path, "ahead cohort\ninterruptible clock wait\n")
    assert found["ahead cohort"]["units"] == 2 and found["ahead cohort"]["signers"] == 2
    assert found["interruptible clock wait"]["units"] == 1


def test_a_term_in_capitals_matches_only_capitals(tmp_path):
    _, found = trace(tmp_path, "LIVE\nlive\n")
    assert found["LIVE"]["units"] == 1
    assert found["live"]["units"] == 1 and found["live"]["occurrences"] == 2


def test_whole_words_only(tmp_path):
    _, found = trace(tmp_path, "human\n")
    assert found["human"]["units"] == 0  # "Humanities" is not "human"


def test_first_use_is_the_earliest_statement_and_its_anchor_holds(tmp_path):
    project, found = trace(tmp_path, "ahead cohort\n")
    first = found["ahead cohort"]["first"]
    assert first["time"] == "2026-06-16T09:00:00Z" and first["signature"] == "Scout1"
    assert first["anchor"]["exact"] == "Ahead cohort"
    source = project.source_bytes(first["anchor"]["source"])
    assert source[first["anchor"]["start"]:first["anchor"]["end"]].decode() == "Ahead cohort"
    assert verify(project)["ok"]


def test_the_snippet_is_cut_around_the_match_not_a_lookalike(tmp_path):
    project, found = trace(tmp_path, "relay\n")
    page = lexicon.render(list(found.values()))
    assert "please relay R3" in page


def test_absent_terms_are_reported_with_the_caveat(tmp_path):
    _, found = trace(tmp_path, "sacrifice\nahead cohort\n")
    page = lexicon.render(list(found.values()))
    assert "Searched for and not found" in page and "- sacrifice" in page
    assert "not evidence that the idea was absent" in page


def test_a_custom_pattern_is_used_as_given(tmp_path):
    _, found = trace(tmp_path, "surviv* = (?i)surviv\\w*\nrelay verbs = (?i)relay\\w*\n")
    assert found["surviv*"]["units"] == 0 and found["relay verbs"]["units"] == 1
