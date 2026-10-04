"""Names and content: features of a signature crossed with what the post says.

Twelve posts whose names and texts are chosen so every count is known by hand.
"""

import json

import pytest

from hermeneutic_engine import cli
from hermeneutic_engine.lens import Activity, code_lens
from hermeneutic_engine.methods import focused_coding
from hermeneutic_engine.store import Project
from hermeneutic_engine.verify import verify
from hermeneutic_engine.views import lexicon, names, names_content
from hermeneutic_engine.views.names_content import DIFFERS
from test_focused_coding import OTHER, QUIET, SPEC, broken, make_codebook, scripted
from test_open_coding import FRAME

A, B, C, D, E, S = "OpenAIScoutAug08", "OAIScoutMar19", "OurRunHelper", "Wanderer", "PoliceAug09", "Scout"
FILLER = " ".join(["and so on"] * 40)  # long, and holds no term
POSTS = [  # first seen, saved as, signed, the text before the signature
    ("2026-06-16T09:00:00Z", A, A, "Please post R1."),              # 0
    ("2026-06-16T09:10:00Z", A, A, "Please post R2, then relay."),  # 1
    ("2026-06-16T09:20:00Z", A, B, "Please post R3."),              # 2  signed under another name
    ("2026-06-16T09:30:00Z", B, B, "LIVE now."),                    # 3
    ("2026-06-16T09:40:00Z", C, C, "Nothing to add."),              # 4
    ("2026-06-16T09:50:00Z", D, C, "Will relay later."),            # 5  signed under another name
    ("2026-06-17T09:00:00Z", D, D, "Please post R4."),              # 6
    ("2026-06-17T09:10:00Z", D, D, "Quiet here."),                  # 7
    ("2026-06-17T09:20:00Z", D, D, "Relay received."),              # 8
    ("2026-06-17T09:30:00Z", D, E, "LIVE again, " + FILLER + ", please post."),  # 9  signed under another name
    ("2026-06-17T09:40:00Z", S, S, "Nothing seen."),                # 10  the name is the word "scout"
    ("2026-06-17T09:50:00Z", D, D, "A scout came by."),             # 11
]
# By hand, as post numbers:
#   please post 0 1 2 6 9 · relay 1 5 8 · LIVE 3 9 · scout 11 · sacrifice none
#   institution 0 1 2 3 (OpenAI 0 1, OAI 2 3) · role 0 1 2 3 4 5 10 (Scout 0 1 2 3 10, Helper 4 5)
#   collective 4 5 (Our) · date 0 1 2 3 9 · signed under another name 2 5 9
TERMS = """# A note at the top of the file. It is not a heading.

# asking
please post
relay

# set signals
LIVE

# words for roles
scout
sacrifice
"""


def build(tmp_path):
    project = Project.init(tmp_path / "p", {"name": "t"})
    lens = code_lens(project, "test", "ingest")
    act = Activity(project, "ingest", lens["id"])
    units = []
    for n, (time, saved_as, signed, said) in enumerate(POSTS):
        data = f"{said} -- {signed}".encode("utf-8")
        source = project.append("source", {
            "blob": project.put_blob(data), "bytes": len(data), "encoding": "utf-8", "media_type": "text/plain",
            "origin": {"adapter": "test", "upstream_id": f"s{n}"}, "derived_from": None, "derivation": None,
            "context": {"page": f"wiki/P{n}", "label": saved_as, "time": time}}, by=lens["id"], activity=act.id)
        units.append(project.append("unit", {
            "source": source["id"], "start": 0, "end": len(data), "unit_kind": "post",
            "context": {"page": f"wiki/P{n}", "introduced_by": saved_as, "signature": signed,
                        "signature_uncertain": False, "first_seen": {"time": time, "time_grade": "reqlog"},
                        "first_removed_in": None}}, by=lens["id"], activity=act.id))
    act.finish()
    return project, units


def survey(tmp_path, **options):
    project, units = build(tmp_path)
    return project, units, names_content.survey(project, TERMS, **{"min_posts": 2, "min_names": 1, **options})


def feature(result, key):
    return next(f for f in result["features"] if f["key"] == key)


def test_the_features_of_a_name_come_from_the_names_survey():
    assert names_content.name_features(A) == ["institution", "institution:OpenAI", "role", "role:Scout", "date"]
    assert names_content.name_features(C) == ["role", "role:Helper", "collective", "collective:Our"]
    assert names_content.name_features(D) == []
    assert names_content.name_features(E) == ["date"]
    assert names_content.name_features(E, {"Police"}) == ["date", "part:Police"]
    for name in (A, B, C, D, E, S):  # a kind is a feature exactly where the survey finds that kind
        kinds = {key for key in names_content.name_features(name) if ":" not in key}
        assert kinds == names.kinds_of(name)


def test_groups_are_the_comment_lines_directly_above_terms():
    text = ("first\n# A note.\n# More of the note.\n\n# asking\nplease post\nrelay  # said of others\n\nACK\n"
            "# (nominated) softened\nplease = (?i)please\n\n# a note between\n\nLIVE\nLIVE\n")
    terms = names_content.grouped_terms(text)
    assert [(t["term"], t["group"]) for t in terms] == [
        ("first", None), ("please post", "asking"), ("relay", "asking"), ("ACK", "asking"),
        ("please", "(nominated) softened"), ("LIVE", "(nominated) softened"), ("LIVE (2)", "(nominated) softened")]
    # Each line is read by the lexicon itself, so the patterns are the lexicon's.
    assert [t["regex"].pattern for t in terms] == [t["regex"].pattern for t in lexicon.parse_terms(text)]


def test_counts_shares_and_base_rates(tmp_path):
    project, _, result = survey(tmp_path)
    assert (result["posts"], result["signatures"]) == (12, 6)
    table = result["words"]["by_term"]["table"]

    cell = table["date"]["please post"]
    assert (cell["with"], cell["with_and"], cell["without"], cell["without_and"]) == (5, 4, 7, 1)
    assert (cell["posts"], cell["posts_and"]) == (12, 5)
    assert cell["share"] == pytest.approx(4 / 5) and cell["share_without"] == pytest.approx(1 / 7)
    assert cell["base_rate"] == pytest.approx(5 / 12)
    assert cell["points"] == pytest.approx(100 * (4 / 5 - 1 / 7))

    cell = table["institution"]["please post"]
    assert (cell["with"], cell["with_and"], cell["without"], cell["without_and"]) == (4, 3, 8, 2)
    assert cell["points"] == pytest.approx(50)

    cell = table["role"]["relay"]
    assert (cell["with"], cell["with_and"], cell["without"], cell["without_and"]) == (7, 2, 5, 1)
    assert cell["points"] == pytest.approx(100 * (2 / 7 - 1 / 5))

    cell = table[DIFFERS]["LIVE"]
    assert (cell["with"], cell["with_and"], cell["without"], cell["without_and"]) == (3, 1, 9, 1)

    cell = table["role:Helper"]["please post"]  # a difference in the other direction
    assert (cell["with_and"], cell["without_and"]) == (0, 5) and cell["points"] == pytest.approx(-50)

    group = result["words"]["by_group"]["table"]["institution"]["asking"]  # a post has a group if it has any of its terms
    assert (group["with"], group["with_and"], group["without"], group["without_and"]) == (4, 3, 8, 4)
    assert (group["posts_and"], group["base_rate"]) == (7, pytest.approx(7 / 12))

    assert (feature(result, "role")["posts"], feature(result, "role")["names"]) == (7, 4)
    assert (feature(result, "role:Scout")["posts"], feature(result, "role:Scout")["names"]) == (5, 3)
    assert (feature(result, "date")["first_day"], feature(result, "date")["last_day"]) == ("2026-06-16", "2026-06-17")
    assert feature(result, "date")["peak_day"] == ["2026-06-16", 4]
    # "Signed under another name" is the names survey's count.
    assert feature(result, DIFFERS)["posts"] == 3 == names.survey(project)["differs"]
    json.dumps(result)  # plain data, so it can be kept and reused


def test_the_page_shows_counts_beside_every_share(tmp_path):
    _, _, result = survey(tmp_path)
    page = names_content.render(result)
    lines = page.split("\n")
    assert lines[0] == "# Names and what the posts say"
    assert lines[2].startswith("Generated by `hermeneutic names-content`") and "overwritten" in lines[2]
    assert "These are counts of co-occurrence, not explanations." in page
    assert "no significance test is computed" in page
    assert "A feature describes the signature on a post, not an agent." in page
    assert "one writer may use many names and many writers may share one" in page
    assert "Time runs through all of it." in page
    for phrase in ("tends to", "prefer", "because", "explains why"):
        assert phrase not in page
    # With the feature, without it, the base rate, and the difference, each with its counts.
    assert "| more often found with | `please post` | 4 of 5 (80.0%) | 1 of 7 (14.3%) | 5 of 12 (41.7%) | +65.7 pts |" in page
    assert "| less often found with | `please post` | 0 of 2 (0.0%) | 5 of 10 (50.0%) | 5 of 12 (41.7%) | -50.0 pts |" in page
    assert "| asking | 3 of 4 (75.0%) | 4 of 8 (50.0%) | 7 of 12 (58.3%) | +25.0 pts |" in page
    assert "### The signature carries a date code\n\n5 of 12 (41.7%) posts, under 3 distinct names." in page
    # The overview has one row in each direction for a feature, with the number of names.
    assert ("| carries the role word `Scout` | 3 | more often found with | `please post` | 3 of 5 (60.0%) | "
            "2 of 7 (28.6%) | 5 of 12 (41.7%) | +31.4 pts |\n"
            "| | | less often found with | `relay` | 1 of 5 (20.0%) | 2 of 7 (28.6%) | 3 of 12 (25.0%) | -8.6 pts |") in page


def test_min_posts_holds_back_small_features_and_terms(tmp_path):
    _, _, result = survey(tmp_path, min_posts=3)
    words = result["words"]["by_term"]
    assert words["features"] == ["institution", "role", "role:Scout", "date", DIFFERS]
    assert words["items"] == ["please post", "relay"]
    assert set(words["table"]) == set(words["features"]) and set(words["table"]["date"]) == {"please post", "relay"}
    assert words["held_back"]["features"] == [
        {"key": "institution:OAI", "posts": 2, "without": 10}, {"key": "institution:OpenAI", "posts": 2, "without": 10},
        {"key": "role:Helper", "posts": 2, "without": 10}, {"key": "collective", "posts": 2, "without": 10},
        {"key": "collective:Our", "posts": 2, "without": 10}]
    assert words["held_back"]["items"] == [{"key": "LIVE", "posts": 2}, {"key": "scout", "posts": 1},
                                           {"key": "sacrifice", "posts": 0}]
    groups = result["words"]["by_group"]
    assert groups["items"] == ["asking"]
    assert groups["held_back"]["items"] == [{"key": "set signals", "posts": 2}, {"key": "words for roles", "posts": 1}]
    assert [f["key"] for f in result["features"] if not f["shown"]] == [h["key"] for h in words["held_back"]["features"]]

    page = names_content.render(result)
    assert "Held back by `--min-posts 3`: 5 of 10 features" in page
    assert "carries the collective word `Our`: 2 posts with it, 10 without" in page
    assert "and 3 of 5 terms (posts with each: `LIVE` 2, `scout` 1; 1 in no post counted here)." in page
    assert "Groups held back: 2 of 3 (posts with each: set signals 2, words for roles 1)." in page
    assert "`LIVE` |" not in page  # a held-back term is in no table


def test_a_feature_that_too_few_posts_lack_is_held_back_too(tmp_path):
    project, _, result = survey(tmp_path, min_posts=6)
    held = {h["key"]: h for h in result["words"]["by_term"]["held_back"]["features"]}
    assert held["role"] == {"key": "role", "posts": 7, "without": 5}  # seven have it, but only five lack it
    assert result["words"]["by_term"]["table"] == {} and result["words"]["examples"] == []

    default = names_content.survey(project, TERMS)  # twenty: nothing in twelve posts is shown as a pattern
    assert default["min_posts"] == 20 and default["words"]["by_term"]["table"] == {}
    page = names_content.render(default)
    assert "No feature and term have enough posts on each side to be set side by side." in page
    assert "Held back by `--min-posts 20`: 5 of 5 features" in page


def test_a_word_is_a_feature_of_its_own_only_in_enough_names(tmp_path):
    _, _, result = survey(tmp_path, min_names=2)
    assert [f["key"] for f in result["features"]] == ["institution", "role", "role:Scout", "collective", "date", DIFFERS]
    assert result["name_words"]["role"] == [{"word": "Scout", "names": 3, "posts": 5, "feature": True},
                                            {"word": "Helper", "names": 1, "posts": 2, "feature": False}]
    assert feature(result, "role")["posts"] == 7  # Helper still counts as a role word
    page = names_content.render(result)
    assert "No list of role words is written into this view." in page
    assert "at least 2 distinct signatures carry it: `Scout` (3 names, 5 posts)." in page
    assert 'counted only under "carries a role word": `Helper` (1 name, 2 posts).' in page


def test_our_is_found_as_the_survey_sorts_it_and_other_parts_are_listed(tmp_path):
    project, _, result = survey(tmp_path)
    our = feature(result, "collective:Our")
    assert (our["label"], our["posts"], our["names"]) == ("carries the collective word `Our`", 2, 1)
    assert result["unsorted_parts"] == [{"part": "Wanderer", "names": 1, "posts": 4},
                                        {"part": "Run", "names": 1, "posts": 2},
                                        {"part": "Police", "names": 1, "posts": 1}]
    page = names_content.render(result)
    assert "a first-person name is found here under the collective word `Our`" in page
    assert "`Police` (1 name, 1 post)" in page and "`--part WORD` makes one a feature" in page

    asked = names_content.survey(project, TERMS, min_posts=1, min_names=1, extra_parts=["Police"])
    police = feature(asked, "part:Police")
    assert (police["label"], police["posts"], police["shown"]) == ("carries the part `Police`", 1, True)
    assert asked["words"]["by_term"]["table"]["part:Police"]["LIVE"]["with_and"] == 1
    assert [e["part"] for e in asked["unsorted_parts"]] == ["Wanderer", "Run"]


def test_the_signature_is_not_counted_as_the_message(tmp_path):
    project, _, result = survey(tmp_path)
    counted = {term["term"]: term["posts"] for term in result["terms"]}
    assert counted == {"please post": 5, "relay": 3, "LIVE": 2, "scout": 1, "sacrifice": 0}
    # The lexicon, which reads the whole statement, also counts the post signed "Scout".
    traced = {r["term"]: r["units"] for r in lexicon.trace(project, lexicon.parse_terms(TERMS))}
    assert traced["scout"] == 2
    assert result["left_out"] == {"no_signature": 0, "signature_not_at_end": 0, "no_saved_name": 0}


def test_each_day_is_counted_so_a_feature_can_be_seen_to_be_a_date(tmp_path):
    _, _, result = survey(tmp_path)
    days = result["by_day"]["days"]
    assert list(days) == ["2026-06-16", "2026-06-17"]
    assert days["2026-06-16"]["posts"] == 6 and days["2026-06-17"]["posts"] == 6
    assert days["2026-06-16"]["with"] == {"institution": 4, "institution:OpenAI": 2, "institution:OAI": 2, "role": 6,
                                          "role:Scout": 4, "role:Helper": 2, "collective": 2, "collective:Our": 2,
                                          "date": 4, DIFFERS: 2}
    assert days["2026-06-17"]["with"] == {"role": 1, "role:Scout": 1, "date": 1, DIFFERS: 1}
    assert result["by_day"]["features"] == ["role", "role:Scout", "date", "institution", DIFFERS, "institution:OAI"]
    page = names_content.render(result)
    assert ("| Day | Signed posts | carries a role word | carries the role word `Scout` | carries a date code | "
            "carries an institution word | differs from the saved name | carries the institution word `OAI` |") in page
    assert "| 2026-06-16 | 6 | 6 (100.0%) | 4 (66.7%) | 4 (66.7%) | 4 (66.7%) | 2 (33.3%) | 2 (33.3%) |" in page
    assert "| 2026-06-17 | 6 | 1 (16.7%) | 1 (16.7%) | 1 (16.7%) | 0 (0.0%) | 1 (16.7%) | 0 (0.0%) |" in page
    assert "| All days | 12 | 7 (58.3%) | 5 (41.7%) | 5 (41.7%) | 4 (33.3%) | 3 (25.0%) | 2 (16.7%) |" in page


def test_example_posts_are_quoted_as_written_with_page_time_and_unit(tmp_path):
    _, units, result = survey(tmp_path)
    examples = {(ex["feature"], ex["item"]): ex for ex in result["words"]["examples"]}
    # The largest differences, at most one for a feature. `Our` and `Helper` are in the same two posts as
    # "a collective word", so their posts to read would be the same ones and they are passed over.
    assert list(examples) == [("institution:OpenAI", "please post"), ("date", "please post"),
                              ("collective", "please post"), ("institution", "please post"),
                              ("institution:OAI", "LIVE")]
    shown = examples[("date", "please post")]
    assert (shown["have"], shown["of"]) == ("both", 4)
    assert [post["unit"] for post in shown["posts"]] == [units[0]["id"], units[1]["id"], units[9]["id"]]
    first, last = shown["posts"][0], shown["posts"][-1]
    assert first == {"unit": units[0]["id"], "page": "wiki/P0", "time": "2026-06-16T09:00:00Z", "signature": A,
                     "saved_as": A, "text": f"Please post R1. -- {A}"}
    # A long post is cut to about 240 characters around the term, and says where it was cut.
    whole = f"{POSTS[9][3]} -- {E}"
    assert last["text"] == "…" + whole[-240:] and "please post" in last["text"] and len(whole) > 240

    nowhere = examples[("collective", "please post")]  # no post has both, so the term is shown where it is found
    assert (nowhere["have"], nowhere["of"]) == ("item only", 5)

    page = names_content.render(result)
    assert (f"2026-06-16T09:00:00Z · wiki/P0 · signed `{A}` · `{units[0]['id']}`\n\n> Please post R1. -- {A}\n") in page
    assert f"2026-06-16T09:20:00Z · wiki/P2 · signed `{B}` · saved as `{A}` · `{units[2]['id']}`" in page
    assert ("**`please post` · the signature carries a date code.** The term is more often found in posts with this "
            "feature: 4 of 5 (80.0%), against 1 of 7 (14.3%) of posts without it. Base rate: 5 of 12 (41.7%). "
            "Difference: +65.7 pts.") in page
    assert "Shown: 3 of the 4 posts that have both" in page
    assert "No post has both. Shown: 3 of the 5 posts that have the term without the feature" in page


# ---- the acts layer --------------------------------------------------------

ONE_SAID = {"Please post": {"codings": [("asking-for-a-signal", "Please post", "")]}}  # posts 0 1 2 6
TWO_SAID = {"relay": {"codings": [("asking-for-a-signal", "relay", "")]},  # of posts 0 to 7: 1 and 5
            B: {"codings": [("ordering-turns", B, "")]}}  # quoted from the signature alone: posts 2 and 3


def build_read(tmp_path):
    """Two lenses apply one codebook: the first reads all twelve posts, the second the first eight."""
    project, units = build(tmp_path)
    codebook, _ = make_codebook(project)
    one = focused_coding.run(project, units, SPEC, codebook, frame=FRAME, call=scripted(ONE_SAID), **QUIET)["lens"]
    two = focused_coding.run(project, units[:8], OTHER, codebook, frame=FRAME, call=scripted(TWO_SAID), **QUIET)["lens"]
    assert verify(project)["ok"], verify(project)["errors"]
    return project, units, codebook, one, two


def test_each_lens_is_counted_alone_over_the_posts_it_read(tmp_path):
    project, units, codebook, one, two = build_read(tmp_path)
    result = names_content.survey(project, TERMS, codebook, min_posts=2, min_names=1)
    acts = result["acts"]
    assert acts["notice"] is None and [code["key"] for code in acts["codes"]] == [
        "cohorts-waiting", "asking-for-a-signal", "ordering-turns"]
    lenses = {lens["lens"]: lens for lens in acts["lenses"]}
    assert set(lenses) == {one, two}  # two lenses, and no third entry that adds them
    assert (lenses[one]["model"], lenses[one]["family"], lenses[one]["label"]) == (
        "scripted", "test", "scripted (test), instructions v0")
    assert (lenses[two]["model"], lenses[two]["label"]) == ("scripted-other", "scripted-other (test), instructions v0")
    assert (lenses[one]["posts_read"], lenses[two]["posts_read"]) == (12, 8)

    cell = lenses[one]["by_code"]["table"]["institution"]["asking-for-a-signal"]
    assert (cell["with"], cell["with_and"], cell["without"], cell["without_and"]) == (4, 3, 8, 1)
    assert (cell["posts"], cell["posts_and"]) == (12, 4) and cell["points"] == pytest.approx(62.5)
    cell = lenses[two]["by_code"]["table"]["institution"]["asking-for-a-signal"]
    assert (cell["with"], cell["with_and"], cell["without"], cell["without_and"]) == (4, 1, 4, 1)
    assert (cell["posts"], cell["posts_and"]) == (8, 2) and cell["points"] == pytest.approx(0)
    # Together the two applied the code to five posts in six readings; no cell holds either number.
    for lens in acts["lenses"]:
        for by_code in lens["by_code"]["table"].values():
            assert by_code["asking-for-a-signal"]["posts_and"] in (4, 2)

    # A code no post of a lens has is held back for that lens, by the same --min-posts.
    assert lenses[one]["by_code"]["items"] == ["asking-for-a-signal"]
    assert lenses[one]["by_code"]["held_back"]["items"] == [{"key": "cohorts-waiting", "posts": 0},
                                                            {"key": "ordering-turns", "posts": 0}]
    assert lenses[two]["by_code"]["items"] == ["asking-for-a-signal", "ordering-turns"]
    # Features are counted over the lens's own posts: a date code is in five posts, four of them among its eight.
    assert lenses[two]["by_code"]["feature_posts"]["date"] == 4 and lenses[two]["names"]["date"] == 2

    cell = lenses[two]["by_code"]["table"]["institution:OAI"]["ordering-turns"]
    assert (cell["with"], cell["with_and"], cell["without_and"]) == (2, 2, 0)
    assert lenses[two]["from_signature"] == {"ordering-turns": {"posts": 2, "of": 2}}  # the code reads the name
    assert lenses[one]["from_signature"] == {}
    shown = next(ex for ex in lenses[two]["examples"] if ex["item"] == "ordering-turns")
    assert [post["unit"] for post in shown["posts"]] == [units[2]["id"], units[3]["id"]]
    assert shown["posts"][0]["quote"] == B and shown["posts"][0]["text"] == f"Please post R3. -- {B}"

    page = names_content.render(result)
    assert page.count("### scripted (test), instructions v0\n") == 1
    assert page.count("### scripted-other (test), instructions v0\n") == 1
    assert f"Model `scripted-other`, family `test`, lens `{two}`. Read 8 of 12 (66.7%) signed posts." in page
    assert "Readers are given one at a time and are never added together" in page
    assert ("| more often found with | [asking-for-a-signal] asking for a signal | 3 of 4 (75.0%) | 1 of 8 (12.5%) | "
            "4 of 12 (33.3%) | +62.5 pts |") in page
    assert "[ordering-turns] ordering turns † | 2 of 2 (100.0%) | 0 of 6 (0.0%) | 2 of 8 (25.0%) | +100.0 pts |" in page
    assert "† This reader quoted the code only from the signature in some posts: [ordering-turns] in 2 of the 2" in page
    assert f"Coded on: “{B}”" in page
    assert "| 5 of 12" not in page.split("## Acts")[1]  # the pooled count is in no row of the acts layer


def test_without_focused_readings_the_acts_layer_is_one_line(tmp_path):
    project, units, result = survey(tmp_path)
    assert result["acts"] is None  # no codebook named
    page = names_content.render(result)
    assert page.rstrip().endswith("## Acts\n\nNo codebook was named (`--codebook`), so the acts layer is left out.")

    codebook, _ = make_codebook(project)
    focused_coding.run(project, units, SPEC, codebook, frame=FRAME, call=broken, **QUIET)  # a reading that failed
    result = names_content.survey(project, TERMS, codebook, min_posts=2, min_names=1)
    notice = f"No focused-coding lens has read signed posts with codebook `{codebook}`, so the acts layer is left out."
    assert result["acts"]["lenses"] == [] and result["acts"]["notice"] == notice
    page = names_content.render(result)
    assert page.rstrip().endswith("## Acts\n\n" + notice) and page.count("so the acts layer is left out") == 1
    assert "####" not in page and "## Their words" in page  # the words layer is there all the same

    with pytest.raises(ValueError):
        names_content.survey(project, TERMS, units[0]["id"])  # not a codebook


def test_the_command_writes_the_page(tmp_path):
    project, _, codebook, _, _ = build_read(tmp_path)
    root = project.root
    project.close()
    terms = tmp_path / "terms.txt"
    terms.write_text(TERMS, encoding="utf-8")
    out = tmp_path / "pages" / "names-content.md"
    assert cli.main(["names-content", str(root), "--terms", str(terms), "--out", str(out), "--codebook", codebook,
                     "--min-posts", "2", "--min-names", "1", "--part", "Police"]) == 0
    page = out.read_text(encoding="utf-8")
    assert page.startswith("# Names and what the posts say\n\nGenerated by `hermeneutic names-content`")
    assert "## How a name is taken apart" in page and "## By day" in page and "## Their words" in page
    assert "### scripted (test), instructions v0" in page and "**Parts asked for with `--part`.** `Police`" in page
