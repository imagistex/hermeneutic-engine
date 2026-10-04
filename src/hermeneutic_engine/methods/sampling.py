"""Seeded, stratified samples of units.

The sample is recorded in the ledger as a method memo that names every unit
drawn, the seed and the strata, so anyone can redraw it and get the same units.
"""

from __future__ import annotations

import random
from collections import defaultdict

from ..lens import Activity, code_lens
from ..store import Project

COMPONENT = "hermeneutic_engine.methods.sampling"


def _matches(unit: dict, stratum: dict) -> bool:
    if unit["unit_kind"] != stratum["unit_kind"]:
        return False
    if unit["end"] - unit["start"] < stratum.get("min_bytes", 0):
        return False
    day = (unit["context"].get("first_seen", {}).get("time") or "")[:10]
    if stratum.get("from") and day < stratum["from"]:
        return False
    if stratum.get("before") and day >= stratum["before"]:
        return False
    return True


def _text_key(unit: dict) -> str:
    """Units with the same text are drawn once. A corpus that does not record
    a text hash gets no deduplication instead of treating every unit as equal."""
    return unit["context"].get("text_sha256") or unit["id"]


def stratified(project: Project, *, name: str, seed: int, strata: list[dict],
               spread_over: str = "page_family") -> tuple[dict, list[dict]]:
    """Draw `n` units from each stratum.

    A stratum is {"label", "unit_kind", "n", optional "from", "before" (dates),
    "min_bytes"}. Inside a stratum, units are grouped by the context field
    `spread_over` and drawn one group at a time in seeded order, so a few busy
    groups cannot crowd out the rest. Only one unit is drawn per distinct text.
    """
    units = sorted(project.records("unit"), key=lambda u: u["id"])
    drawn: list[dict] = []
    seen_texts: set[str] = set()
    report = []
    for stratum in strata:
        rng = random.Random(f"{seed}:{stratum['label']}")
        groups: dict[str, list[dict]] = defaultdict(list)
        available = 0
        for unit in units:
            if _matches(unit, stratum) and _text_key(unit) not in seen_texts:
                groups[str(unit["context"].get(spread_over))].append(unit)
                available += 1
        order = sorted(groups)
        rng.shuffle(order)
        for key in order:
            rng.shuffle(groups[key])
        taken: list[dict] = []
        while len(taken) < stratum["n"] and any(groups[key] for key in order):
            for key in order:
                if len(taken) >= stratum["n"]:
                    break
                while groups[key]:
                    unit = groups[key].pop()
                    text = _text_key(unit)
                    if text not in seen_texts:
                        seen_texts.add(text)
                        taken.append(unit)
                        break
        drawn.extend(taken)
        report.append({**stratum, "available": available, "drawn": len(taken), "groups": len(order)})

    lens = code_lens(project, COMPONENT, "sampling")
    activity = Activity(project, "sample", lens["id"])
    lines = [f"Sample `{name}`: {len(drawn)} units, seed {seed}, spread over `{spread_over}`.", ""]
    for row in report:
        window = " to ".join(x for x in (row.get("from"), row.get("before")) if x) or "any date"
        lines.append(f"- {row['label']}: drew {row['drawn']} of {row['available']} available "
                     f"({row['unit_kind']}, {window}, {row['groups']} groups)")
    memo = project.append("memo", {
        "memo_type": "method",
        "about": [u["id"] for u in drawn],
        "body": "\n".join(lines),
        "sample": {"name": name, "seed": seed, "spread_over": spread_over, "strata": report},
    }, by=lens["id"], activity=activity.id)
    activity.finish(sample=name, drawn=len(drawn))
    return memo, drawn


def subset(project: Project, *, name: str, unit_ids: list[str], reason: str, derived_from: str | None = None) -> dict:
    """Record a hand-picked set of units as a sample, with the reason it was picked."""
    lens = code_lens(project, COMPONENT, "sampling")
    activity = Activity(project, "sample", lens["id"], used=[derived_from] if derived_from else [])
    memo = project.append("memo", {
        "memo_type": "method",
        "about": list(unit_ids),
        "body": f"Sample `{name}`: {len(unit_ids)} units. {reason}",
        "sample": {"name": name, "derived_from": derived_from, "reason": reason},
    }, by=lens["id"], activity=activity.id)
    activity.finish(sample=name, drawn=len(unit_ids))
    return memo


def units_of(project: Project, memo_id: str) -> list[dict]:
    memo = project.get(memo_id)
    if memo is None or memo.get("memo_type") != "method" or "sample" not in memo:
        raise ValueError(f"not a sample memo: {memo_id}")
    return [project.get(uid) for uid in memo["about"]]
