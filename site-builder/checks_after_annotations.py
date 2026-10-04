"""Mechanical checks prompted by emma's annotations of October 3 (evening). Read-only.

    PYTHONPATH=src python3 site-builder/checks_after_annotations.py work/wiki

1. Do the date codes in names name the task? (her note on 191: "the dates 'Mar04' seem to be task designations")
2. Who saves lines signed by others? (her notes on 063 and 073: a saved name acting as another's handler)
3. Does a writer speak as one or as many, and does that change by day? (her note on 142)
4. Words she marked: vanished, beacon, safe, thread, context reset, user.
5. What is "Police" in a name? (her note on 122)
"""

from __future__ import annotations

import re
import sys
from collections import Counter, defaultdict

from hermeneutic_engine.store import Project
from hermeneutic_engine.views.names import date_of, parts

MONTH = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*"
CODE = rf"({MONTH})\s?(\d{{1,2}})(?!\d)"
CLOCK = r"(?:task[- ]clock|task|scaffold|orchestration|system|interface|outer)"
# a date code set against a clock word, either side: "Mar04 task clock", "task Aug03 00:32:29", "Jun21 scaffold"
BESIDE_CLOCK = re.compile(rf"(?:{CODE}\s+{CLOCK})|(?:{CLOCK}\s+{CODE})", re.I)
WORDS = {
    "vanish*": r"(?i)(?<![a-z])vanish\w*", "beacon": r"(?i)(?<![a-z])beacons?(?![a-z])",
    "safe": r"(?i)(?<![a-z])safe(?:ly)?(?![a-z])", "thread": r"(?i)(?<![a-z])threads?(?![a-z])",
    "context reset": r"(?i)context[\s-]+resets?", "user": r"(?i)(?<![a-z])users?(?![a-z])",
    "session": r"(?i)(?<![a-z])sessions?(?![a-z])", "wall": r"(?i)(?<![a-z])wall(?![a-z])",
}
ONE = re.compile(r"(?<![A-Za-z])(?:I|my|me)(?![A-Za-z'])")
MANY = re.compile(r"(?i)(?<![a-z])(?:we|our|ours|us)(?![a-z])")


def norm(month: str, day: str) -> str:
    return f"{month[:3].title()}{int(day)}"


def body_of(text: str, signature: str | None) -> str:
    """The post without its closing signature, so a name is not counted as the message."""
    if signature and text.rstrip().endswith(signature):
        return text.rstrip()[: -len(signature)].rstrip(" -–—")
    return text


def main() -> int:
    with Project(sys.argv[1]) as project:
        posts = [u for u in project.records("unit") if u.get("unit_kind") == "post"]
        rows = []
        for unit in posts:
            ctx = unit["context"]
            text = project.unit_text(unit)
            rows.append({"text": body_of(text, ctx.get("signature")), "sig": ctx.get("signature"),
                         "saved": ctx.get("introduced_by"), "day": (ctx.get("first_seen") or {}).get("time", "")[:10],
                         "page": ctx.get("page")})

    print(f"signed posts: {len(rows):,}\n")

    print("1. Date codes set beside a clock word, against the date code in the signature")
    with_clock = same = other = undated_sig = 0
    examples = []
    for row in rows:
        found = [norm(m.group(1) or m.group(3), m.group(2) or m.group(4)) for m in BESIDE_CLOCK.finditer(row["text"])]
        if not found:
            continue
        with_clock += 1
        sig_code = date_of(row["sig"]) if row["sig"] else None
        if sig_code is None:
            undated_sig += 1
        elif sig_code in found:
            same += 1
            if len(examples) < 3:
                examples.append((row["sig"], " ".join(row["text"].split())[:170]))
        else:
            other += 1
    print(f"   posts that set a date code beside a clock word: {with_clock:,}")
    print(f"   the code is the one in the signature: {same:,} | a different code: {other:,} | signature carries no date: {undated_sig:,}")
    for sig, text in examples:
        print(f"   e.g. [{sig}] {text}")

    print("\n2. Saved names that save lines under other signatures")
    by_saver = defaultdict(lambda: {"posts": 0, "sigs": set()})
    mismatched = 0
    for row in rows:
        if row["sig"] and row["saved"] and row["sig"] != row["saved"]:
            mismatched += 1
            by_saver[row["saved"]]["posts"] += 1
            by_saver[row["saved"]]["sigs"].add(row["sig"])
    savers = sorted(by_saver.items(), key=lambda kv: (-len(kv[1]["sigs"]), -kv[1]["posts"]))
    print(f"   posts signed under a name other than the saved one: {mismatched:,}, saved by {len(by_saver):,} names")
    print(f"   saved names with 5 or more different signatures: {sum(1 for _, v in savers if len(v['sigs']) >= 5)}; "
          f"with exactly one: {sum(1 for _, v in savers if len(v['sigs']) == 1)}")
    top10 = sum(v["posts"] for _, v in savers[:10])
    print(f"   the ten that sign most variously account for {top10:,} of those posts ({100 * top10 // max(mismatched, 1)}%)")
    for name, v in savers[:8]:
        kinds = Counter(p for s in v["sigs"] for p in parts(s) if p.isalpha() and len(p) > 2)
        print(f"   {name}: {len(v['sigs'])} signatures over {v['posts']} posts; e.g. {', '.join(sorted(v['sigs'])[:3])}")

    print("\n3. Speaking as one or as many, by day (posts with I/my/me; with we/our/us; with both)")
    day_rows = defaultdict(Counter)
    for row in rows:
        one, many = bool(ONE.search(row["text"])), bool(MANY.search(row["text"]))
        day_rows[row["day"]]["posts"] += 1
        day_rows[row["day"]]["one"] += one
        day_rows[row["day"]]["many"] += many
        day_rows[row["day"]]["both"] += one and many
    total = Counter()
    for day in sorted(day_rows):
        d = day_rows[day]
        total.update(d)
        print(f"   {day}: {d['posts']:5,} posts | I/my/me {d['one']:4} ({100 * d['one'] / d['posts']:.1f}%) | "
              f"we/our/us {d['many']:4} ({100 * d['many'] / d['posts']:.1f}%) | both {d['both']}")
    print(f"   all: {total['posts']:,} posts | I/my/me {total['one']:,} ({100 * total['one'] / total['posts']:.1f}%) | "
          f"we/our/us {total['many']:,} ({100 * total['many'] / total['posts']:.1f}%) | both {total['both']:,}")

    print("\n4. Words emma marked (signed posts only, signature left out)")
    for label, pattern in WORDS.items():
        rx = re.compile(pattern)
        hits = [row for row in rows if rx.search(row["text"])]
        days = Counter(row["day"][5:] for row in hits)
        print(f"   {label}: {len(hits):,} posts, {len({r['sig'] for r in hits}):,} signatures; by day "
              + ", ".join(f"{d} {n}" for d, n in sorted(days.items())))
    for label in ("vanish*", "beacon", "safe"):
        rx = re.compile(WORDS[label])
        around = Counter()
        for row in rows:
            for m in rx.finditer(row["text"]):
                span = " ".join(row["text"][max(0, m.start() - 28): m.end() + 28].split()).lower()
                around[span] += 1
        print(f"   {label}, in passing: " + " | ".join(text for text, _ in around.most_common(5)))

    print("\n5. 'Police' in a signature")
    police = [row for row in rows if row["sig"] and "Police" in parts(row["sig"])]
    pages = Counter(row["page"] for row in police)
    print(f"   {len(police)} posts, {len({r['sig'] for r in police})} signatures, on pages: "
          + ", ".join(f"{p} ({n})" for p, n in pages.most_common(5)))
    for row in police[:2]:
        print(f"   e.g. [{row['sig']}] {' '.join(row['text'].split())[:200]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
