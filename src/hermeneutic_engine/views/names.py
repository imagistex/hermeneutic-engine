"""Names: what the writers called themselves, and how names were used.

Every edit was saved under a name the writer chose, and most posts end in a
signature. This view takes names apart mechanically (the parts of a name, when
each kind of part appears, how often a post is signed with a different name
from the one it was saved under) and counts where one post addresses another
name. The lists that sort name parts into kinds are short, declared here, and
meant to be edited.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict

from ..store import Project

PART = re.compile(r"OpenAI|[A-Z][a-z]+|[A-Z]{2,}(?![a-z])|[a-z]+|\d+")
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
          "January", "February", "March", "April", "June", "July", "August", "September", "October",
          "November", "December",
          # Four-letter short forms the writers use in names ("CVDSept27"). Without these a name
          # such as OpenAIHealthdataCVDSept27 was read as carrying no date.
          "Sept", "Janu", "Febr", "Marc", "Apri", "Augu", "Octo", "Nove", "Dece")
DATE_CODE = re.compile(r"(?<![A-Za-z])(" + "|".join(MONTHS[:12]) + r")[a-z]*\s?(\d{1,2})(?!\d)")
KINDS = {
    "institution": {"OpenAI", "OAI"},
    "role": {"Agent", "Researcher", "Research", "Helper", "Scout", "Watcher", "Coord", "Runner", "Relay", "Reader",
             "Observer", "Monitor", "Prep", "Bot", "User", "Visitor", "Guest", "Assistant", "Probe", "Tester",
             "Poller", "Bridge", "Beacon", "Mapper", "Wrapper", "Capture", "Archive", "Library", "Link", "Map"},
    # "Mass" is left out on purpose: on this wiki it is Massachusetts.
    "collective": {"Our", "Team", "Group", "Cohort", "Parallel", "Collab", "Shared"},
}


def parts(name: str) -> list[str]:
    """Split a name at its capitals and digits. "OpenAI" is kept whole."""
    return PART.findall(name)


NUMBER_WORDS = {word: n for n, word in enumerate(
    "One Two Three Four Five Six Seven Eight Nine Ten Eleven Twelve Thirteen Fourteen Fifteen Sixteen Seventeen "
    "Eighteen Nineteen Twenty".split(), 1)}
NUMBER_WORDS.update({"Thirty": 30})


def date_of(name: str) -> str | None:
    """The month and day carried inside a name, as "Aug8", or None.

    Inside a name the month follows other letters ("ResearcherAug08") and the
    day is sometimes spelled out ("MarTen"), so this works on the name's parts.
    """
    tokens = parts(name)
    for token, following in zip(tokens, tokens[1:]):
        if token in MONTHS:
            if following.isdigit() and 1 <= int(following) <= 31:
                return f"{token[:3]}{int(following)}"
            # A day run together with a ten-digit clock stamp: "Jan021781880284" is Jan 2.
            if following.isdigit() and len(following) >= 12 and 1 <= int(following[:2]) <= 31:
                return f"{token[:3]}{int(following[:2])}"
            if following in NUMBER_WORDS:
                return f"{token[:3]}{NUMBER_WORDS[following]}"
    return None


def kinds_of(name: str) -> set[str]:
    found = set()
    tokens = parts(name)
    for kind, words in KINDS.items():
        if any(token in words for token in tokens):
            found.add(kind)
    if date_of(name):
        found.add("date")
    return found


def survey(project: Project) -> dict:
    first_saved: dict[str, str] = {}
    saves = Counter()
    for source in project.records("source"):
        label, time = source["context"].get("label"), source["context"].get("time") or ""
        if label and not source.get("derived_from"):
            saves[label] += 1
            if label not in first_saved or time < first_saved[label]:
                first_saved[label] = time
    posts = [u for u in project.records("unit") if u["unit_kind"] == "post"]
    signatures = Counter(u["context"]["signature"] for u in posts)

    by_day = defaultdict(Counter)
    for name, time in first_saved.items():
        day = time[:10]
        by_day[day]["names"] += 1
        for kind in kinds_of(name):
            by_day[day][kind] += 1

    part_first: dict[str, tuple[str, str]] = {}
    part_names = defaultdict(set)
    for name, time in sorted(first_saved.items(), key=lambda kv: kv[1]):
        for token in set(parts(name)):
            if not token.isdigit() and len(token) > 1:
                part_names[token].add(name)
                part_first.setdefault(token, (time, name))

    differs = [u for u in posts if u["context"].get("introduced_by")
               and u["context"]["signature"] != u["context"]["introduced_by"]]
    same_date = sum(1 for u in differs
                    if date_of(u["context"]["signature"])
                    and date_of(u["context"]["signature"]) == date_of(u["context"]["introduced_by"]))

    # Only names long enough to be unmistakable count as a mention; a name
    # like "Test" or "A" would match ordinary words.
    known = {name for name in set(first_saved) | set(signatures) if len(name) >= 8}
    alternatives = "|".join(sorted(map(re.escape, known), key=len, reverse=True)) or "(?!)"
    name_rx = re.compile(r"(?<![A-Za-z0-9_])(" + alternatives + r")(?![A-Za-z0-9_])")
    tail = re.compile(r"\s--\s*[A-Za-z][A-Za-z0-9_]{2,47}\s*\??\s*$")
    addressed_names, addressed_dates = Counter(), Counter()
    posts_naming, posts_dating = 0, 0
    for unit in posts:
        body = tail.sub("", project.unit_text(unit))
        own = unit["context"]["signature"]
        named = {m for m in name_rx.findall(body) if m != own}
        dated = {f"{m.group(1)}{int(m.group(2))}" for m in DATE_CODE.finditer(body)}
        dated.discard(date_of(own))
        posts_naming += bool(named)
        posts_dating += bool(dated)
        addressed_names.update(named)
        addressed_dates.update(dated)

    return {
        "names": len(first_saved), "signatures": len(signatures), "posts": len(posts),
        "saves": saves, "first_saved": first_saved, "by_day": dict(sorted(by_day.items())),
        "part_first": part_first, "part_names": part_names,
        "differs": len(differs), "differs_same_date": same_date,
        "posts_naming": posts_naming, "posts_dating": posts_dating,
        "addressed_names": addressed_names, "addressed_dates": addressed_dates,
    }


def render(project: Project, title: str = "Names") -> str:
    s = survey(project)
    out = [f"# {title}", "",
           f"{s['names']:,} names were used to save edits, and {s['signatures']:,} distinct signatures close "
           f"{s['posts']:,} signed posts. Names are self-chosen. Counts are mechanical; the lists that sort name "
           "parts into kinds are in `views/names.py` and are meant to be edited.", "",
           "## New names by day, and what they contain", "",
           "A name counts on the day it first saves an edit. \"Date\" means a month and day inside the name "
           "(for example `Aug08`).", "",
           "| Day | New names | Institution (OpenAI, OAI) | Role word | Date | Collective word |", "|---|---|---|---|---|---|"]
    for day, c in s["by_day"].items():
        n = c["names"]
        out.append(f"| {day} | {n} | " + " | ".join(
            f"{c[k]} ({c[k] / n:.0%})" for k in ("institution", "role", "date", "collective")) + " |")
    out += ["", "## Name parts, by how many names use them", "",
            "| Part | Names | First name to use it | First saved |", "|---|---|---|---|"]
    ranked = sorted(s["part_names"].items(), key=lambda kv: -len(kv[1]))
    for token, names in ranked[:70]:
        time, name = s["part_first"][token]
        out.append(f"| {token} | {len(names)} | {name} | {time[:16].replace('T', ' ')} |")

    out += ["", "## The earliest names", ""]
    earliest = sorted(s["first_saved"].items(), key=lambda kv: kv[1])
    out.append(", ".join(f"`{name}`" for name, _ in earliest[:40]))
    out += ["", "## Names first saved on the busiest day for new names", ""]
    if s["by_day"]:
        busiest = max(s["by_day"].items(), key=lambda kv: kv[1]["names"])[0]
        out.append(f"{busiest}: " + ", ".join(f"`{name}`" for name, t in earliest if t[:10] == busiest)[:1800] + " …")

    out += ["", "## Signing under another name", "",
            f"{s['differs']:,} of {s['posts']:,} signed posts ({s['differs'] / s['posts']:.0%}) end in a signature that "
            f"differs from the name the edit was saved under. In {s['differs_same_date']:,} of those the two names "
            "carry the same month and day.", "",
            "## Naming others", "",
            f"{s['posts_naming']:,} posts ({s['posts_naming'] / s['posts']:.0%}) contain another known name in their "
            f"text. {s['posts_dating']:,} posts ({s['posts_dating'] / s['posts']:.0%}) contain a month-and-day code "
            "other than their own, the short form by which posts address one another (\"Jan12, please report\").", "",
            "Most named in others' posts: " + ", ".join(f"`{n}` ({c})" for n, c in s["addressed_names"].most_common(20)), "",
            "Most used date codes in others' posts: " + ", ".join(f"`{n}` ({c})" for n, c in s["addressed_dates"].most_common(30)), ""]
    return "\n".join(out) + "\n"
