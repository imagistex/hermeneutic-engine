"""Segmentation tests for the wiki adapter, on text shaped like the real pages."""

from hermeneutic_engine.corpora.wiki import classify, follow, segment

PAGE = (
    "= Grocery G5 relay =\n"
    "\n"
    "Sequence confirmed GA -> AR -> NV -> KY.\n"
    "If G5 arrives, post STATE immediately. -- GroceryAgentNov08X\n"
    "Aug09 cohort asks: did early 07:29:05 arrive? -- OpenAIResearcherAug09 ?\n"
    "\n"
    "* [https://example.org/a first]\n"
    "* [https://example.org/b second]\n"
    "[[AgentUniqueNames2021X]]\n"
    "\n"
    "Test post 1781641048\n"
    "* Please relay later rounds. -- BulletedSigner\n"
).encode("utf-8")


def test_classify():
    assert classify("= Title =") == "heading"
    assert classify("* [https://example.org x]") == "list"
    assert classify("https://example.org/data.json") == "list"
    assert classify("[[SomePage]]") == "list"
    assert classify("Will relay. -- Nov07Runner") == "signed"
    assert classify("* Will relay. -- Nov07Runner") == "signed"
    assert classify("* [https://example.org x] -- Nov07Runner") == "list"
    assert classify("Test post 123") == "plain"


def test_segment_kinds_and_spans():
    units = segment(PAGE)
    assert [u["kind"] for u in units] == ["heading", "post", "post", "list", "text", "post"]
    for u in units:
        assert PAGE[u["start"]:u["end"]].decode("utf-8") == u["text"]


def test_a_post_takes_the_unsigned_prose_directly_above_it():
    post = segment(PAGE)[1]
    assert post["lines"] == 2
    assert post["text"].startswith("Sequence confirmed") and post["text"].endswith("-- GroceryAgentNov08X")
    assert post["signature"] == "GroceryAgentNov08X" and not post["signature_uncertain"]


def test_a_question_mark_after_a_name_is_kept_as_uncertainty():
    post = segment(PAGE)[2]
    assert post["signature"] == "OpenAIResearcherAug09" and post["signature_uncertain"]


def test_list_lines_merge_and_stay_out_of_posts():
    listing = segment(PAGE)[3]
    assert listing["lines"] == 3 and listing["signature"] is None


def test_unsigned_prose_is_text_and_a_bulleted_signed_line_is_a_post():
    units = segment(PAGE)
    assert units[4]["text"] == "Test post 1781641048"
    assert units[5]["signature"] == "BulletedSigner" and units[5]["lines"] == 1


def test_spans_survive_multibyte_text_and_carriage_returns():
    raw = "Größe bestätigt. -- MünchenAgent\r\n\r\nzweite Zeile -- Zweiter\r\n".encode("utf-8")
    units = segment(raw)
    assert [u["kind"] for u in units] == ["text", "post"]  # "MünchenAgent" is not an ASCII name
    assert units[1]["signature"] == "Zweiter"
    for u in units:
        assert raw[u["start"]:u["end"]].decode("utf-8") == u["text"]
        assert not u["text"].endswith("\r")


# ---- following statements through revisions ---------------------------------

def pieces(*texts):
    return [{"kind": "post", "text": t} for t in texts]


def test_follow_records_first_absence_and_return():
    ready = [e for e in follow([pieces("Ready. -- Bob"), pieces(), pieces("Ready. -- Bob")])][0]
    assert ready["present"] == [0, 2] and ready["first_absent"] == 1
    assert ready["returned"] is True and ready["gone_after"] is None


def test_follow_records_final_removal():
    entry = follow([pieces("A -- X"), pieces("A -- X", "B -- Y"), pieces("B -- Y")])
    a = next(e for e in entry if e["piece"]["text"] == "A -- X")
    b = next(e for e in entry if e["piece"]["text"] == "B -- Y")
    assert (a["first"], a["last"], a["first_absent"], a["gone_after"], a["returned"]) == (0, 1, 2, 2, False)
    assert (b["first"], b["last"], b["first_absent"], b["gone_after"]) == (1, 2, None, None)


def test_follow_keeps_the_number_of_copies_at_first_appearance():
    entry = follow([pieces("Ready. -- Bob", "Ready. -- Bob")])[0]
    assert entry["copies_at_first"] == 2 and entry["present"] == [0]
