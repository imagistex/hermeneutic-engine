"""Names and content: how a signature is built, set beside what its post says.

Every signed post closes with a name its writer chose. The names survey
(views/names.py) takes a name apart, the lexicon (views/lexicon.py) finds the
writers' own terms, and focused coding records which codes each reader applied.
This view sets them side by side: for each feature of the name a post is signed
under, how often a term, a group of terms or a code is found in the same post,
and how often it is found in posts without the feature.

These are counts of co-occurrence. They explain nothing, and no test of
significance is computed. A feature describes the signature on a post, not an
agent: one writer may use many names, and many writers may share one. Time
runs through all of it, since names and wording both changed from day to day,
so the view also counts each feature by day.

The name is kept out of the message. Terms are searched in the post's text
with its closing signature left out, and a code that a reader quoted only from
the signature is marked.

Readers are never added together. Each focused-coding lens has its own tables,
over the posts that lens read. As in the divergence view, only finished
readings count.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from pathlib import Path

from ..store import Project
from . import names
from .lexicon import parse_terms

TITLE = "Names and what the posts say"
DIFFERS = "signed-as-another"  # the one feature that is about two names, not one
CLIP = 240  # characters of a post shown in an example
TOP = 5  # rows in each direction in a feature's table
DAY_FEATURES = 6  # feature columns in the table by day
EXAMPLE_PAIRS = 5  # differences given example posts, in the words layer
EXAMPLE_PAIRS_ACTS = 3  # and for each reader, in the acts layer
EXAMPLES_EACH = 3
UNSORTED_SHOWN = 20  # parts the survey does not sort, listed so they can be seen
HELD_SHOWN = 40  # held-back terms or codes named before the list is cut


# ---- features of a name ----------------------------------------------------

def name_features(name: str, extra_parts=()) -> list[str]:
    """The features of one name, as keys, from the names survey's own parsing.

    "institution", "role" and "collective" are the survey's kinds, and
    "role:Scout" says which word of a kind the name carries. "date" is a month
    and day inside the name. "part:Police" is a part the caller asked for that
    the survey does not sort into a kind.
    """
    tokens = list(dict.fromkeys(names.parts(name)))
    kinds = names.kinds_of(name)
    found = []
    for kind, words in names.KINDS.items():
        if kind in kinds:
            found.append(kind)
            found += [f"{kind}:{token}" for token in tokens if token in words]
    if "date" in kinds:
        found.append("date")
    found += [f"part:{token}" for token in tokens if token in extra_parts]
    return found


def _article(word: str) -> str:
    return "an" if word[:1].lower() in "aeiou" else "a"


def describe(key: str) -> dict:
    """What a feature is called on the page (it completes "the signature ..."),
    and how it is told."""
    kind, _, word = key.partition(":")
    if key == DIFFERS:
        return {"label": "differs from the saved name",
                "how": "the signature is not the name the edit was saved under"}
    if key == "date":
        return {"label": "carries a date code",
                "how": "a month followed by a day among the name's parts, as in `Aug08`"}
    if kind == "part":
        return {"label": f"carries the part `{word}`",
                "how": f"`{word}` is one of the name's parts (asked for with `--part`; the survey does not sort it)"}
    if word:
        return {"label": f"carries the {kind} word `{word}`", "how": f"`{word}` is one of the name's parts"}
    return {"label": f"carries {_article(kind)} {kind} word",
            "how": f"a part of the name is on the names survey's list of {kind} words"}


# ---- terms and their groups ------------------------------------------------

def grouped_terms(text: str) -> list[dict]:
    """The lexicon's terms, each with the heading it stands under in the term file.

    Lines are read by the lexicon's own `parse_terms`, one at a time, so a term
    here is exactly a term there. A heading is the comment line directly above
    a term line; it holds until the next heading. A comment followed by a blank
    line or by another comment is a note, not a heading.
    """
    terms, heading, pending, seen = [], None, None, Counter()
    for line in text.splitlines():
        found = parse_terms(line)
        if found:
            if pending:
                heading = pending
            pending = None
            term = found[0]
            seen[term["term"]] += 1
            if seen[term["term"]] > 1:  # the same label twice: keep both, told apart
                term["term"] = f"{term['term']} ({seen[term['term']]})"
            term["group"] = heading
            terms.append(term)
        elif line.strip().startswith("#"):
            pending = line.strip().lstrip("#").strip() or None
        else:
            pending = None
    return terms


# ---- counting --------------------------------------------------------------

def _signed_posts(project: Project) -> tuple[list[dict], dict]:
    """Every post with a recorded signature, in order of first appearance, with
    its text and the same text without the closing signature."""
    posts, left_out = [], {"no_signature": 0, "signature_not_at_end": 0, "no_saved_name": 0}
    for unit in project.records("unit"):
        if unit["unit_kind"] != "post":
            continue
        ctx = unit.get("context") or {}
        signature = ctx.get("signature")
        if not signature:
            left_out["no_signature"] += 1
            continue
        text = project.unit_text(unit)
        # The signature as recorded, where it closes the post. Nothing about
        # what a signature looks like is decided here.
        tail = re.search(r"--\s*" + re.escape(signature) + r"\s*\??\s*$", text)
        cut = tail.start() if tail else len(text)
        left_out["signature_not_at_end"] += tail is None
        left_out["no_saved_name"] += not ctx.get("introduced_by")
        time = (ctx.get("first_seen") or {}).get("time") or ""
        posts.append({"id": unit["id"], "start": unit["start"], "text": text, "body": text[:cut],
                      "signature_at": unit["start"] + len(text[:cut].encode("utf-8")),
                      "signature": signature, "saved_as": ctx.get("introduced_by"),
                      "time": time, "day": time[:10] or "undated", "page": ctx.get("page")})
    posts.sort(key=lambda post: (post["time"], post["id"]))
    return posts, left_out


def _cell(n_all: int, n_item: int, n_feature: int, n_both: int) -> dict:
    rest, rest_both = n_all - n_feature, n_item - n_both
    share, rest_share = n_both / n_feature, rest_both / rest
    return {"with": n_feature, "with_and": n_both, "share": share,
            "without": rest, "without_and": rest_both, "share_without": rest_share,
            "posts": n_all, "posts_and": n_item, "base_rate": n_item / n_all,
            "points": 100 * (share - rest_share)}


def _cross(universe: set, features: dict[str, set], items: dict[str, set], min_posts: int) -> dict:
    """Every feature against every item, over one set of posts.

    A feature is held back unless at least `min_posts` posts have it and at
    least `min_posts` lack it, since a share is taken over each side. An item
    is held back unless at least `min_posts` posts have it.
    """
    n_all = len(universe)
    shown, held, counts = {}, [], {}
    for key, members in features.items():
        inside = members & universe
        counts[key] = len(inside)
        if len(inside) >= min_posts and n_all - len(inside) >= min_posts:
            shown[key] = inside
        else:
            held.append({"key": key, "posts": len(inside), "without": n_all - len(inside)})
    kept, held_items = {}, []
    for key, members in items.items():
        inside = members & universe
        if len(inside) >= min_posts:
            kept[key] = inside
        else:
            held_items.append({"key": key, "posts": len(inside)})
    table = {feature: {item: _cell(n_all, len(having), len(members), len(members & having))
                       for item, having in kept.items()}
             for feature, members in shown.items()}
    return {"posts": n_all, "features": list(shown), "items": list(kept), "feature_posts": counts,
            "table": table, "held_back": {"features": held, "items": held_items}}


def _pool(with_feature: set, with_item: set) -> tuple[str, set]:
    """The posts to read for one pair: those with both the feature and the item,
    or, where no post has both, those with the item and without the feature."""
    both = with_feature & with_item
    return ("both", both) if both else ("item only", with_item - with_feature)


def _largest(table: dict, k: int, pool) -> list[tuple[str, str]]:
    """The largest differences, in either direction, to give example posts for.

    At most one for a feature. A feature whose posts to read would be exactly
    those already taken for the same item under another feature is passed over,
    so the same posts are not printed twice under two descriptions.
    """
    ranked = sorted(((abs(cell["points"]), feature, item) for feature, by_item in table.items()
                     for item, cell in by_item.items() if round(cell["points"], 1)),
                    key=lambda row: (-row[0], row[1], row[2]))
    picked, used, taken = [], set(), set()
    for _, feature, item in ranked:
        if len(picked) == k:
            break
        if feature in used:
            continue
        used.add(feature)
        posts = (item, frozenset(pool(feature, item)))
        if posts not in taken:
            taken.add(posts)
            picked.append((feature, item))
    return picked


def _spread(items: list, k: int) -> list:
    """Up to `k` of `items`, evenly spaced from the first to the last."""
    if len(items) <= k or k < 2:
        return list(items[:max(k, 0)])
    return [items[(n * (len(items) - 1)) // (k - 1)] for n in range(k)]


def _clip(text: str, start: int, end: int) -> str:
    """The text as it stands, cut to about CLIP characters around [start, end)."""
    if len(text) <= CLIP:
        return text
    lo = max(0, min((start + end) // 2 - CLIP // 2, len(text) - CLIP))
    hi = lo + CLIP
    return ("…" if lo else "") + text[lo:hi] + ("…" if hi < len(text) else "")


def _examples(posts: list[dict], with_feature: set, with_item: set, locate) -> dict:
    """A few of the posts to read for one pair, spaced evenly through time.
    `locate` says where in a post the term or the coded words are."""
    have, wanted = _pool(with_feature, with_item)
    pool = [post for post in posts if post["id"] in wanted]
    shown = []
    for post in _spread(pool, EXAMPLES_EACH):
        start, end, quote = locate(post)
        entry = {"unit": post["id"], "page": post["page"], "time": post["time"], "signature": post["signature"],
                 "saved_as": post["saved_as"], "text": _clip(post["text"], start, end)}
        if quote:
            entry["quote"] = quote
        shown.append(entry)
    return {"have": have, "of": len(pool), "posts": shown}


def _names_with(posts_by_id: dict, members: set) -> int:
    return len({posts_by_id[uid]["signature"] for uid in members})


def survey(project: Project, terms_text: str, codebook_memo_id: str | None = None, *, min_posts: int = 20,
           min_names: int = 5, extra_parts=()) -> dict:
    """Cross the features of the name each post is signed under with what the post says.

    Returns plain counts. `words` crosses features with lexicon terms and with
    the term file's groups; `acts`, when a codebook is named, crosses them with
    the codes each focused-coding lens applied, one lens at a time. Each cell
    holds the posts with the feature (`with`), those of them with the item
    (`with_and`, `share`), the same for posts without the feature, the base
    rate over all posts counted, and the difference in percentage points.
    """
    min_posts, min_names = max(1, int(min_posts)), max(1, int(min_names))
    extra = {str(part) for part in extra_parts}
    posts, left_out = _signed_posts(project)
    by_id = {post["id"]: post for post in posts}
    signed = Counter(post["signature"] for post in posts)

    per_name = {name: name_features(name, extra) for name in signed}
    members: dict[str, set] = defaultdict(set)  # feature -> posts
    carriers: dict[str, set] = defaultdict(set)  # feature -> signatures
    for post in posts:
        keys = list(per_name[post["signature"]])
        if post["saved_as"] and post["signature"] != post["saved_as"]:  # the names survey's rule
            keys.append(DIFFERS)
        post["features"] = keys
        for key in keys:
            members[key].add(post["id"])
            carriers[key].add(post["signature"])

    # Which words become features of their own is read off the data: the words
    # of each of the survey's kinds that signatures carry, where enough names carry them.
    name_words, keys = {}, []
    for kind in names.KINDS:
        found = sorted((key for key in members if key.startswith(kind + ":")), key=lambda k: (-len(members[k]), k))
        name_words[kind] = [{"word": key.split(":", 1)[1], "names": len(carriers[key]), "posts": len(members[key]),
                             "feature": len(carriers[key]) >= min_names} for key in found]
        keys.append(kind)
        keys += [key for key in found if len(carriers[key]) >= min_names]
    keys.append("date")
    name_words["part"] = [{"word": word, "names": len(carriers[f"part:{word}"]), "posts": len(members[f"part:{word}"]),
                           "feature": len(carriers[f"part:{word}"]) >= min_names} for word in sorted(extra)]
    keys += [f"part:{entry['word']}" for entry in name_words["part"] if entry["feature"]]
    keys.append(DIFFERS)
    feature_sets = {key: set(members[key]) for key in keys}

    sorted_words = set().union(*names.KINDS.values()) | set(names.MONTHS) | set(names.NUMBER_WORDS) | extra
    loose_names, loose_posts = Counter(), Counter()
    for name, n in signed.items():
        for token in set(names.parts(name)):
            if len(token) > 1 and not token.isdigit() and token not in sorted_words:
                loose_names[token] += 1
                loose_posts[token] += n
    unsorted = [{"part": token, "names": loose_names[token], "posts": n}
                for token, n in sorted(loose_posts.items(), key=lambda kv: (-kv[1], kv[0]))]

    terms = grouped_terms(terms_text)
    patterns = {term["term"]: term["regex"] for term in terms}
    has = {label: {post["id"] for post in posts if regex.search(post["body"])} for label, regex in patterns.items()}
    in_group: dict[str, set] = {}
    group_terms: dict[str, list] = {}
    for term in terms:
        if term["group"]:
            in_group.setdefault(term["group"], set()).update(has[term["term"]])
            group_terms.setdefault(term["group"], []).append(term["term"])

    everyone = set(by_id)
    by_term = _cross(everyone, feature_sets, has, min_posts)
    by_group = _cross(everyone, feature_sets, in_group, min_posts)

    features = []
    for key in keys:
        days = Counter(post["day"] for post in posts if post["id"] in feature_sets[key])
        features.append({"key": key, **describe(key), "posts": len(feature_sets[key]), "names": len(carriers[key]),
                         "shown": key in by_term["table"], "first_day": min(days) if days else None,
                         "last_day": max(days) if days else None,
                         "peak_day": list(days.most_common(1)[0]) if days else None})

    def term_finder(label):
        def locate(post):
            match = patterns[label].search(post["body"])
            return (match.start(), match.end(), None) if match else (0, 0, None)
        return locate

    examples = []
    for feature, item in _largest(by_term["table"], EXAMPLE_PAIRS, lambda f, i: _pool(feature_sets[f], has[i])[1]):
        examples.append({"feature": feature, "item": item, "cell": by_term["table"][feature][item],
                         **_examples(posts, feature_sets[feature], has[item], term_finder(item))})

    day_rows: dict[str, dict] = {}
    for post in posts:
        row = day_rows.setdefault(post["day"], {"posts": 0, "with": Counter()})
        row["posts"] += 1
        row["with"].update(key for key in post["features"] if key in feature_sets)
    top = sorted(by_term["features"], key=lambda key: -len(feature_sets[key]))[:DAY_FEATURES]

    result = {
        "posts": len(posts), "signatures": len(signed), "min_posts": min_posts, "min_names": min_names,
        "left_out": left_out,
        "features": features,
        "name_words": name_words,
        "unsorted_parts": unsorted[:UNSORTED_SHOWN], "unsorted_total": len(unsorted),
        "terms": [{"term": term["term"], "pattern": term["regex"].pattern, "group": term["group"],
                   "posts": len(has[term["term"]])} for term in terms],
        "groups": [{"group": group, "terms": group_terms[group], "posts": len(having)}
                   for group, having in in_group.items()],
        "words": {"by_term": by_term, "by_group": by_group, "examples": examples},
        "by_day": {"features": top,
                   "days": {day: {"posts": row["posts"], "with": dict(row["with"])}
                            for day, row in sorted(day_rows.items())}},
        "acts": None,
    }
    if codebook_memo_id:
        result["acts"] = _acts(project, codebook_memo_id, posts, feature_sets, min_posts)
    return result


def _acts(project: Project, codebook_memo_id: str, posts: list[dict], feature_sets: dict[str, set],
          min_posts: int) -> dict:
    """The codes each focused-coding lens applied to the posts it read.

    One table for each lens, over that lens's posts alone. A lens has read a
    post when a finished read activity of the lens names it, and only codings
    made in such an activity count: the divergence view's rule.
    """
    # Imported here: the words layer needs neither.
    from ..methods.focused_coding import METHOD, codes_of
    from .divergence import reader_name

    codes = codes_of(project, codebook_memo_id)
    by_code_id = {code["id"]: code for code in codes}
    by_id = {post["id"]: post for post in posts}
    lenses = {lens["id"]: lens for lens in project.records("lens")
              if (lens.get("method") or {}).get("name") == METHOD["name"]
              and (lens.get("reader") or {}).get("codebook") == codebook_memo_id}

    finished: dict[str, str] = {}  # activity -> lens
    read: dict[str, set] = defaultdict(set)  # lens -> posts
    for activity in project.records("activity"):
        if activity.get("lens") in lenses and activity.get("type") == "read" and activity.get("status") == "ok":
            finished[activity["id"]] = activity["lens"]
            read[activity["lens"]].update(uid for uid in activity.get("used") or [] if uid in by_id)
    applied: dict = defaultdict(lambda: defaultdict(dict))  # lens -> code key -> post -> [anchor]
    for coding in project.records("coding"):
        lens_id = finished.get(coding["activity"])
        code = by_code_id.get(coding["code"])
        if lens_id is None or coding["by"] != lens_id or code is None or coding["unit"] not in by_id:
            continue
        read[lens_id].add(coding["unit"])  # a lens that coded a post has read it
        applied[lens_id][code["key"]].setdefault(coding["unit"], []).append(coding.get("anchor") or {})

    labels = {}
    for lens_id, lens in lenses.items():
        method = lens.get("method") or {}
        label = reader_name(lens)
        if method.get("version") is not None:
            label += f", instructions v{method['version']}"
        if method.get("code_types"):
            label += ", only the " + " and ".join(str(t).replace("_", " ") for t in method["code_types"]) + " codes"
        labels[lens_id] = label
    twice = Counter(labels.values())
    for lens_id, label in list(labels.items()):
        if twice[label] > 1:  # the same reading under two harness versions is still two lenses
            harness = (lenses[lens_id].get("reader") or {}).get("harness")
            labels[lens_id] = f"{label}, harness {harness}"

    out = []
    for lens_id in sorted((lid for lid in lenses if read[lid]), key=lambda lid: (labels[lid], lid)):
        universe = read[lens_id]
        items = {code["key"]: set(applied[lens_id].get(code["key"], {})) for code in codes}
        crossed = _cross(universe, feature_sets, items, min_posts)
        from_signature = {}
        for key, by_post in applied[lens_id].items():
            only = sum(1 for uid, anchors in by_post.items()
                       if all(anchor.get("start", -1) >= by_id[uid]["signature_at"] for anchor in anchors))
            if only:
                from_signature[key] = {"posts": only, "of": len(by_post)}

        def quote_finder(key, lens_id=lens_id):
            def locate(post):
                anchor = min(applied[lens_id][key][post["id"]], key=lambda a: a.get("start", 0))
                before = post["text"].encode("utf-8")[:max(0, anchor.get("start", 0) - post["start"])]
                start = len(before.decode("utf-8", errors="ignore"))
                return start, start + len(anchor.get("exact") or ""), anchor.get("exact")
            return locate

        def read_for(feature, item, universe=universe, items=items):
            return _pool(feature_sets[feature] & universe, items[item] & universe)[1]

        examples = []
        for feature, item in _largest(crossed["table"], EXAMPLE_PAIRS_ACTS, read_for):
            examples.append({"feature": feature, "item": item, "cell": crossed["table"][feature][item],
                             **_examples(posts, feature_sets[feature] & universe, items[item] & universe,
                                         quote_finder(item))})
        reader = lenses[lens_id].get("reader") or {}
        out.append({"lens": lens_id, "label": labels[lens_id], "model": reader.get("model"),
                    "family": reader.get("family"), "posts_read": len(universe),
                    "names": {key: _names_with(by_id, feature_sets[key] & universe) for key in feature_sets},
                    "by_code": crossed, "from_signature": from_signature, "examples": examples})

    notice = None
    if not out:
        notice = (f"No focused-coding lens has read signed posts with codebook `{codebook_memo_id}`, "
                  "so the acts layer is left out.")
    return {"codebook": codebook_memo_id, "notice": notice, "lenses": out,
            "codes": [{"key": code["key"], "name": code.get("name"), "code_type": code.get("code_type")}
                      for code in codes]}


# ---- the page --------------------------------------------------------------

def _md(text) -> str:
    return " ".join(str(text).split()).replace("|", "¦")


def _share(n: int, of: int) -> str:
    return f"{n:,} of {of:,}" + (f" ({n / of:.1%})" if of else "")


def _points(value: float) -> str:
    return f"{round(value, 1) + 0.0:+.1f} pts"


def _numbers(cell: dict) -> str:
    return (f"{_share(cell['with_and'], cell['with'])} | {_share(cell['without_and'], cell['without'])} | "
            f"{_share(cell['posts_and'], cell['posts'])} | {_points(cell['points'])}")


def _head(first: list[str], base: str) -> list[str]:
    columns = first + ["With the feature", "Without it", f"{base} (base rate)", "Difference"]
    return ["| " + " | ".join(columns) + " |", "|" + "---|" * len(first) + "---:|---:|---:|---:|"]


def _extremes(by_item: dict, top: int = TOP) -> tuple[list, list]:
    """The items found more often with a feature, and those found less often,
    largest difference first. A difference that rounds to nothing is in neither."""
    rows = [(item, cell) for item, cell in by_item.items() if round(cell["points"], 1)]
    more = sorted((row for row in rows if row[1]["points"] > 0), key=lambda row: (-row[1]["points"], row[0]))
    less = sorted((row for row in rows if row[1]["points"] < 0), key=lambda row: (row[1]["points"], row[0]))
    return more[:top], less[:top]


def _direction_rows(by_item: dict, show, top: int) -> list[str]:
    more, less = _extremes(by_item, top)
    return ([f"more often found with | {show(item)} | {_numbers(cell)} |" for item, cell in more]
            + [f"less often found with | {show(item)} | {_numbers(cell)} |" for item, cell in less])


def _held_items(held: list[dict], show) -> str:
    """Held-back items with their counts, the fullest first; those in no post are only counted."""
    if not held:
        return ""
    some = sorted((h for h in held if h["posts"]), key=lambda h: -h["posts"])
    named = [f"{show(h['key'])} {h['posts']:,}" for h in some[:HELD_SHOWN]]
    notes = []
    if named:
        more = len(some) - len(named)
        notes.append("posts with each: " + ", ".join(named) + (f", and {more} more" if more > 0 else ""))
    if len(held) > len(some):
        notes.append(f"{len(held) - len(some)} in no post counted here")
    return " (" + "; ".join(notes) + ")"


def _held(crossed: dict, labels: dict, show, word: str, min_posts: int) -> str:
    held = crossed["held_back"]
    n_features = len(crossed["features"]) + len(held["features"])
    n_items = len(crossed["items"]) + len(held["items"])
    line = f"Held back by `--min-posts {min_posts}`: {len(held['features'])} of {n_features} features"
    if held["features"]:
        line += " (" + "; ".join(f"{labels[h['key']]}: {h['posts']:,} posts with it, {h['without']:,} without"
                                 for h in held["features"]) + ")"
    return line + f", and {len(held['items'])} of {n_items} {word}s" + _held_items(held["items"], show) + "."


def _overview(crossed: dict, labels: dict, counts: dict, show, word: str, base: str) -> list[str]:
    if not crossed["table"] or not crossed["items"]:
        return [f"No feature and {word} have enough posts on each side to be set side by side.", ""]
    out = _head(["The signature…", "Names", "", word.capitalize()], base)
    for key, by_item in crossed["table"].items():
        rows = _direction_rows(by_item, show, 1) or ["no difference | | | | | |"]
        lead = f"| {labels[key]} | {counts[key]:,} | "
        out += [(lead if n == 0 else "| | | ") + row for n, row in enumerate(rows)]
    return out + [""]


def _feature_tables(crossed: dict, labels: dict, counts: dict, show, word: str, base: str, level: str,
                    by_group: dict | None = None) -> list[str]:
    out = []
    for key, by_item in crossed["table"].items():
        out += [f"{level} The signature {labels[key]}", "",
                f"{_share(crossed['feature_posts'][key], crossed['posts'])} posts, under {counts[key]:,} distinct "
                f"name{'' if counts[key] == 1 else 's'}.", ""]
        if by_group and by_group["table"].get(key):
            out += _head(["Group of terms"], base)
            out += [f"| {_md(group)} | {_numbers(cell)} |" for group, cell in by_group["table"][key].items()]
            out.append("")
        rows = _direction_rows(by_item, show, TOP)
        if rows:
            out += [f"The {word}s with the largest differences, up to {TOP} in each direction:", ""]
            out += _head(["", word.capitalize()], base)
            out += ["| " + row for row in rows]
        elif by_item:
            out.append(f"No {word} differs between posts with this feature and posts without it.")
        else:
            out.append(f"No {word} has enough posts to be shown.")
        out.append("")
    return out


def _quote(text: str) -> list[str]:
    """A passage as written. `<` is escaped so markup in a post cannot hide part of it."""
    return [f"> {line}".rstrip() for line in text.replace("<", "\\<").split("\n")]


def _example_blocks(examples: list[dict], labels: dict, show, word: str) -> list[str]:
    out = []
    for ex in examples:
        cell = ex["cell"]
        direction = "more" if cell["points"] > 0 else "less"
        out += [f"**{show(ex['item'])} · the signature {labels[ex['feature']]}.** "
                f"The {word} is {direction} often found in posts with this feature: "
                f"{_share(cell['with_and'], cell['with'])}, against {_share(cell['without_and'], cell['without'])} "
                f"of posts without it. Base rate: {_share(cell['posts_and'], cell['posts'])}. "
                f"Difference: {_points(cell['points'])}.", ""]
        if ex["have"] == "both":
            out += [f"Shown: {len(ex['posts'])} of the {ex['of']:,} posts that have both, spaced evenly from the "
                    "earliest to the latest by first appearance.", ""]
        else:
            out += [f"No post has both. Shown: {len(ex['posts'])} of the {ex['of']:,} posts that have the {word} "
                    "without the feature, spaced evenly from the earliest to the latest by first appearance.", ""]
        for post in ex["posts"]:
            facts = [post["time"] or "undated", str(post["page"]), f"signed `{post['signature']}`"]
            if post["saved_as"] and post["saved_as"] != post["signature"]:
                facts.append(f"saved as `{post['saved_as']}`")
            facts.append(f"`{post['unit']}`")
            out += [" · ".join(facts), ""] + _quote(post["text"]) + [""]
            if post.get("quote"):
                out += [f"Coded on: “{' '.join(post['quote'].split())}”", ""]
    return out


def _word_list(entries: list[dict], key: str = "word") -> str:
    return ", ".join(f"`{e[key]}` ({e['names']:,} name{'' if e['names'] == 1 else 's'}, {e['posts']:,} "
                     f"post{'' if e['posts'] == 1 else 's'})" for e in entries)


def render(result: dict, title: str = TITLE) -> str:
    """The page, from what `survey` returned. Nothing is counted here."""
    n, min_posts, min_names = result["posts"], result["min_posts"], result["min_names"]
    features = {f["key"]: f for f in result["features"]}
    labels = {key: f["label"] for key, f in features.items()}
    counts = {key: f["names"] for key, f in features.items()}
    by_term, by_group = result["words"]["by_term"], result["words"]["by_group"]
    acts = result["acts"]
    lenses = acts["lenses"] if acts else []

    out = [f"# {title}", "",
           "Generated by `hermeneutic names-content` from the ledger and a term list. This page is overwritten each "
           "time the command runs, so notes written on it will be lost.", ""]
    if not n:
        return "\n".join(out + ["There are no signed posts in this project.", ""])

    pairs = sum(len(by_item) for crossed in [by_term, by_group] + [lens["by_code"] for lens in lenses]
                for by_item in crossed["table"].values())
    out += [f"{n:,} signed posts close in {result['signatures']:,} distinct signatures. For each post this page takes "
            "the name it is signed under, notes how that name is built, and counts how often each feature of the "
            "name is found in the same post as a term from the term list"
            + (", or as a code a reader applied" if lenses else "") + ".", "",
            "These are counts of co-occurrence, not explanations. A row says that a feature of the signature and a "
            "wording are found in the same posts more often, or less often, than elsewhere. It does not say why, and "
            "no significance test is computed.", "",
            "- **A feature describes the signature on a post, not an agent.** Names are self-chosen: one writer may "
            "use many names and many writers may share one, so nothing here follows a writer. The number of distinct "
            "names stands beside the number of posts, since a feature found in few names may be the wording of a few "
            "busy signatures.",
            "- **Time runs through all of it.** How names were built and which words were in use both changed from "
            "day to day, so a feature can be a date under another description. The section \"By day\" shows how many "
            "of each day's posts carry each feature. Read it before reading any difference.",
            "- **Every share stands beside its counts and its base rate.** A row gives the posts with the feature "
            "and how many of them have the term, the same for the posts without the feature, and the base rate over "
            "all posts counted. The difference is the first share minus the second, in percentage points.",
            f"- **Many pairs are compared at once.** {pairs:,} pairs of a feature and a term, group or code are set "
            "side by side on this page. Among that many, some large differences are to be expected by chance alone. "
            "A large difference is a place to read, and example posts are given for the largest.",
            "- **The name is not counted as the message.** Terms are searched in a post's text with its closing "
            "signature left out, so that a post signed `Scout` is not counted as using the word \"scout\". Counts "
            "can therefore be lower than on the lexicon page, which also counts unsigned prose.",
            f"- **Small numbers are held back.** A feature is shown only if at least {min_posts} posts have it and at "
            f"least {min_posts} lack it. A term, group or code is shown only if at least {min_posts} posts have it. "
            "What was held back is listed with its counts.", ""]
    left = result["left_out"]
    if left["no_signature"] or left["signature_not_at_end"]:
        out += [f"{left['no_signature']:,} posts have no recorded signature and are left out. In "
                f"{left['signature_not_at_end']:,} posts the recorded signature was not found closing the text, and "
                "the whole text was searched.", ""]

    # ---- how a name is taken apart
    out += ["## How a name is taken apart", "",
            "The names survey (`views/names.py`) splits a name at its capitals and digits and sorts the parts into "
            "kinds from short lists declared there. This page uses the survey's functions and parses nothing itself. "
            "One signature can have several features.", "",
            "| The signature… | How it is told | Names | Posts | First and last day | Busiest day | In the tables |",
            "|---|---|---:|---:|---|---|---|"]
    for f in result["features"]:
        when = "–" if not f["posts"] else f["first_day"] if f["first_day"] == f["last_day"] else \
            f"{f['first_day']} to {f['last_day']}"
        peak = f"{f['peak_day'][0]} ({f['peak_day'][1]:,} posts)" if f["posts"] else "–"
        out.append(f"| {f['label']} | {f['how']} | {f['names']:,} | {_share(f['posts'], n)} | {when} | {peak} | "
                   f"{'yes' if f['shown'] else 'held back'} |")
    out.append("")
    for kind in names.KINDS:
        found = result["name_words"].get(kind) or []
        kept, rest = [e for e in found if e["feature"]], [e for e in found if not e["feature"]]
        general = f"carries {_article(kind)} {kind} word"
        text = (f"**{kind.capitalize()} words.** No list of {kind} words is written into this view. The words are "
                f"read off the data: every part of a signature that the names survey sorts as {_article(kind)} "
                f"{kind} word. A word is a feature of its own when at least {min_names} distinct "
                f"{'signature carries' if min_names == 1 else 'signatures carry'} it")
        text += (": " + _word_list(kept) + "." if kept else "; none does.")
        if rest:
            text += f" In fewer signatures, and so counted only under \"{general}\": {_word_list(rest)}."
        if not found:
            text = f"**{kind.capitalize()} words.** No signature carries a part that the names survey sorts as " \
                   f"{_article(kind)} {kind} word."
        if kind == "collective" and "Our" in names.KINDS[kind]:
            text += (" The survey has no first-person kind. It sorts `Our` (as in `OurRun…`) with its collective "
                     "words, so a first-person name is found here under the collective word `Our`.")
        out += [text, ""]
    out += ["**Date codes.** A month followed by a day among the name's parts, as the survey's `date_of` reads it "
            "(`Aug08`, `MarTen`).", ""]
    differs = "**Signed under another name.** The signature is not the name the edit was saved under: the survey's rule."
    if left["no_saved_name"]:
        differs += f" {left['no_saved_name']:,} posts have no saved name recorded and count as not differing."
    out += [differs, ""]
    asked = result["name_words"].get("part") or []
    if asked:
        out += ["**Parts asked for with `--part`.** " + _word_list(asked) + ". The same rule applies: a part is a "
                f"feature when at least {min_names} distinct "
                f"{'signature carries' if min_names == 1 else 'signatures carry'} it.", ""]
    if result["unsorted_parts"]:
        out += [f"**Parts the survey does not sort.** {result['unsorted_total']:,} other parts occur in signatures. "
                "The most used, by posts: " + _word_list(result["unsorted_parts"], "part")
                + ". They are not features here. `--part WORD` makes one a feature for a run; adding it to a list in "
                  "`views/names.py` gives it a kind.", ""]

    # ---- by day
    columns = result["by_day"]["features"]
    out += ["## By day", "",
            "A post counts on the day it first appears on a page. Each cell is the number of that day's signed posts "
            "whose signature has the feature, and its share of that day's posts."
            + (f" The columns are the {len(columns)} features with the most posts among those shown." if columns
               else " No feature has enough posts to be shown.")
            + " Where a feature is found on some days and hardly on others, a difference in the tables below may be "
              "a difference between those days.", "",
            "| " + " | ".join(["Day", "Signed posts"] + [labels[key] for key in columns]) + " |",
            "|---|---:|" + "---:|" * len(columns)]
    total = Counter()
    for day, row in result["by_day"]["days"].items():
        total.update(row["with"])
        cells = [f"{row['with'].get(key, 0):,} ({row['with'].get(key, 0) / row['posts']:.1%})" for key in columns]
        out.append("| " + " | ".join([day, f"{row['posts']:,}"] + cells) + " |")
    out.append("| " + " | ".join(["All days", f"{n:,}"] + [f"{total[key]:,} ({total[key] / n:.1%})" for key in columns])
               + " |")
    out.append("")

    # ---- their words
    def show_term(label):
        return f"`{_md(label)}`"

    def show_group(label):
        return _md(label)

    n_terms, n_groups = len(result["terms"]), len(result["groups"])
    out += ["## Their words", "",
            f"{n_terms:,} terms from the term list, each matched by the lexicon's own pattern (`views/lexicon.py`)"
            + (f", in {n_groups} groups. A group is the comment line directly above a run of terms in the term file, "
               "and a post has a group when it has at least one of its terms." if n_groups else ".")
            + " Counted over all signed posts.", "",
            "### Overview: the largest difference in each direction, for each feature", ""]
    out += _overview(by_term, labels, counts, show_term, "term", "All signed posts")
    out += [_held(by_term, labels, show_term, "term", min_posts), ""]
    if n_groups:
        held_groups = by_group["held_back"]["items"]
        out += [f"Groups held back: {len(held_groups)} of {n_groups}" + _held_items(held_groups, show_group) + ".", ""]
    out += _feature_tables(by_term, labels, counts, show_term, "term", "All signed posts", "###", by_group)
    if result["words"]["examples"]:
        out += ["### Posts to read for the largest differences", "",
                "For the largest differences above, at most one for a feature, passing over a feature whose posts "
                "would be the ones already shown for another. The posts are quoted from the source as written, cut "
                f"to about {CLIP} characters around the term where they are longer.", ""]
        out += _example_blocks(result["words"]["examples"], labels, show_term, "term")

    # ---- acts
    out += ["## Acts", ""]
    if acts is None:
        out += ["No codebook was named (`--codebook`), so the acts layer is left out.", ""]
    elif acts["notice"]:
        out += [acts["notice"], ""]
    else:
        code_names = {code["key"]: code.get("name") for code in acts["codes"]}
        out += [f"Codebook `{acts['codebook']}`, {len(acts['codes'])} codes. Each reader below is a focused-coding "
                "lens that applied it. Readers are given one at a time and are never added together: each read its "
                "own posts under its own instructions, and a count from one is not a count from another. A post has "
                "a code when the reader applied it at least once in a finished reading. Shares are taken over the "
                "signed posts that reader read.", ""]
        for lens in lenses:
            crossed = lens["by_code"]
            marked = lens["from_signature"]

            def show_code(key, marked=marked):
                return f"[{_md(key)}] {_md(code_names.get(key))}" + (" †" if key in marked else "")

            out += [f"### {_md(lens['label'])}", "",
                    f"Model `{lens['model']}`, family `{lens['family']}`, lens `{lens['lens']}`. "
                    f"Read {_share(lens['posts_read'], n)} signed posts.", ""]
            if marked:
                out += ["† This reader quoted the code only from the signature in some posts: "
                        + "; ".join(f"[{_md(key)}] in {m['posts']:,} of the {m['of']:,} posts it applied it to"
                                    for key, m in marked.items())
                        + ". There the code reads the name itself, so finding it with a feature of the name says "
                          "little about the message.", ""]
            out += ["#### Overview: the largest difference in each direction, for each feature", ""]
            out += _overview(crossed, labels, lens["names"], show_code, "code", "All posts this reader read")
            out += [_held(crossed, labels, show_code, "code", min_posts), ""]
            out += _feature_tables(crossed, labels, lens["names"], show_code, "code", "All posts this reader read",
                                   "####")
            if lens["examples"]:
                out += ["#### Posts to read for this reader's largest differences", "",
                        "At most one for a feature, passing over a feature whose posts would be the ones already "
                        f"shown for another. Quoted from the source as written, cut to about {CLIP} characters "
                        "around the words the reader coded where they are longer.", ""]
                out += _example_blocks(lens["examples"], labels, show_code, "code")
    return "\n".join(out) + "\n"


# ---- command line ----------------------------------------------------------

def cmd_names_content(args) -> int:
    terms_text = Path(args.terms).read_text(encoding="utf-8")
    with Project(args.project) as project:
        result = survey(project, terms_text, args.codebook, min_posts=args.min_posts, min_names=args.min_names,
                        extra_parts=args.part)
    text = render(result, title=args.title)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(f"wrote {out} ({len(text):,} characters)")
    return 0


def register_cli(sub) -> None:
    p = sub.add_parser("names-content", help="set how a signature is built beside what its post says")
    p.add_argument("project")
    p.add_argument("--terms", required=True, help="a file of lexicon terms, one per line, grouped under '# heading' lines")
    p.add_argument("--out", required=True, help="the Markdown file to write")
    p.add_argument("--codebook", default=None,
                   help="ID of a codebook memo: adds the codes each focused-coding lens applied, one lens at a time")
    p.add_argument("--min-posts", type=int, default=20,
                   help="hold back a feature, term or code with fewer posts than this (default 20)")
    p.add_argument("--min-names", type=int, default=5,
                   help="a word of a name is a feature of its own when this many distinct signatures carry it (default 5)")
    p.add_argument("--part", action="append", default=[],
                   help="also treat this name part as a feature (repeatable), e.g. --part Police")
    p.add_argument("--title", default=TITLE)
    p.set_defaults(func=cmd_names_content)
