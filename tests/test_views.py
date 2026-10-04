"""Names and concordance: mechanical views over units."""

import re

from hermeneutic_engine.views import concordance, names
from test_lexicon import project_with_posts


def test_name_parts_and_kinds():
    assert names.parts("OpenAIResearcherAug08") == ["OpenAI", "Researcher", "Aug", "08"]
    assert names.kinds_of("OpenAIResearcherAug08") == {"institution", "role", "date"}
    assert names.kinds_of("AliceVisitor") == {"role"}
    assert names.kinds_of("AgentMassPointer13") == {"role"}  # Mass is Massachusetts here, not a collective
    assert names.date_of("OpenAIResearcherAug08") == "Aug8" == names.date_of("OpenAIAug8Watcher")
    assert names.date_of("SectorAgentMarTen") == "Mar10" and names.date_of("MayTwoObserverFreshX") == "May2"
    assert names.date_of("ResearchAgentMay") is None and names.date_of("AgentMassPointer13") is None


def test_concordance_lists_each_use_with_its_context(tmp_path):
    project = project_with_posts(tmp_path)
    rows = concordance.lines(project, re.compile(r"(?i)\blive\b"))
    assert [r["key"] for r in rows] == ["LIVE", "live"]
    assert rows[1]["left"].endswith("we are") and rows[0]["right"].startswith("now.")
    page = concordance.render(project, "live\nabsentword\n", "t")
    assert "2 uses in 1 statements" in page and "No uses." in page


def test_names_page_renders(tmp_path):
    project = project_with_posts(tmp_path)
    assert "## Naming others" in names.render(project)
