"""Divergence: readers who applied the same codebook, compared unit by unit.

Every reader in a focused-coding run is given the same codes, so for any unit
that two of them read it is plain which codes each applied. This view counts
where they applied the same codes and where they did not, and lists the units
where they differ most, so a person knows where to read first.

Agreement is diagnostic, not truth. Readers can agree and all be wrong, and a
unit where they differ may be one the codebook fits poorly. The numbers are
plain counts and ratios; no chance-corrected statistic is computed.

Only finished readings count. A lens has read a unit when a finished ("ok")
read activity of that lens names the unit in `used`, and only codings made in
such an activity are compared.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path

from ..methods.focused_coding import METHOD, codes_of
from ..store import KIND_BY_PREFIX, Project

NOT_FOUND = "quotation not found in unit"


def _is_unit(rid) -> bool:
    return KIND_BY_PREFIX.get(str(rid).split(":", 1)[0]) == "unit"


def reader_name(lens: dict) -> str:
    reader = lens.get("reader") or {}
    name = f"{reader.get('model') or reader.get('id') or lens['id']} ({reader.get('family') or reader.get('kind')})"
    if reader.get("requested"):
        name += f", answering in place of {reader['requested']}"
    if reader.get("kind") == "model" and not any(part.get("name") == "frame" for part in lens.get("stack", [])):
        name += ", no frame"
    return name


def _lenses(project: Project, codebook_memo_id: str, lens_ids) -> list[dict]:
    if lens_ids is None:
        return [lens for lens in project.records("lens")
                if (lens.get("method") or {}).get("name") == METHOD["name"]
                and (lens.get("reader") or {}).get("codebook") == codebook_memo_id]
    found = []
    for lens_id in dict.fromkeys(lens_ids):
        lens = project.get(lens_id)
        if lens is None or lens.get("kind") != "lens":
            raise ValueError(f"not a lens: {lens_id}")
        found.append(lens)
    return found


def compare(project: Project, codebook_memo_id: str, lens_ids=None) -> dict:
    """Which lens applied which code to each unit read by at least two of them.

    Returns plain counts: per reader, per code (units where all, some but not
    all, and exactly one of the unit's readers applied it), per pair of readers
    (Jaccard overlap of their (unit, code) pairs over the units both read), and
    per unit (`divergence`: how many codes some but not all of its readers applied).
    """
    codes = codes_of(project, codebook_memo_id)
    code_ids = [code["id"] for code in codes]
    in_book = set(code_ids)
    lenses = _lenses(project, codebook_memo_id, lens_ids)
    base = {lens["id"]: reader_name(lens) for lens in lenses}
    same = Counter(base.values())
    names = {lid: name if same[name] == 1 else f"{name} [{lid}]" for lid, name in base.items()}
    order = sorted(names, key=lambda lid: (names[lid], lid))
    wanted = set(order)

    finished: dict[str, str] = {}  # activity -> lens
    read_by: dict[str, set] = defaultdict(set)  # unit -> lenses
    for activity in project.records("activity"):
        if activity.get("lens") in wanted and activity.get("type") == "read" and activity.get("status") == "ok":
            finished[activity["id"]] = activity["lens"]
            for uid in activity.get("used") or []:
                if _is_unit(uid):
                    read_by[uid].add(activity["lens"])

    applied: dict[str, dict[str, set]] = defaultdict(lambda: defaultdict(set))  # unit -> code -> lenses
    quotes: dict = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))  # unit -> code -> lens -> [quote]
    n_codings = Counter()
    for coding in project.records("coding"):
        lens_id = finished.get(coding["activity"])
        if lens_id is None or coding["by"] != lens_id or coding["code"] not in in_book:
            continue
        unit = coding["unit"]
        read_by[unit].add(lens_id)  # a lens that coded a unit has read it
        applied[unit][coding["code"]].add(lens_id)
        quotes[unit][coding["code"]][lens_id].append({"exact": coding["anchor"]["exact"], "note": coding.get("note") or ""})
        n_codings[lens_id] += 1

    missed: dict = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))  # unit -> code -> lens -> [quote]
    n_missed = Counter()
    for failure in project.records("failure"):
        lens_id = finished.get(failure["activity"])
        if lens_id is None or failure.get("reason") != NOT_FOUND:
            continue
        n_missed[lens_id] += 1
        attempted = failure.get("attempted") or {}
        if attempted.get("code") in in_book:
            missed[attempted.get("unit")][attempted["code"]][lens_id].append(attempted.get("quote") or "")

    compared = sorted(uid for uid, readers in read_by.items() if len(readers) >= 2)

    readers = {}
    for lid in order:
        units_read = sum(1 for rs in read_by.values() if lid in rs)
        pairs = sum(1 for by_code in applied.values() for lenses_ in by_code.values() if lid in lenses_)
        readers[lid] = {
            "name": names[lid],
            "units_read": units_read,
            "units_compared": sum(1 for uid in compared if lid in read_by[uid]),
            "codings": n_codings[lid],
            "codes_applied": pairs,
            "codes_per_unit": pairs / units_read if units_read else None,
            "quotes_not_found": n_missed[lid],
        }

    per_code = {cid: {"key": code["key"], "name": code.get("name"), "all": 0, "some": 0, "one": 0}
                for cid, code in zip(code_ids, codes)}
    per_unit = {}
    for uid in compared:
        who = read_by[uid]
        divergence = 0
        for cid in code_ids:
            n = len(applied[uid][cid] & who) if cid in applied[uid] else 0
            if n == 0:
                continue
            if n == len(who):
                per_code[cid]["all"] += 1
            else:
                per_code[cid]["some"] += 1
                divergence += 1
            if n == 1:
                per_code[cid]["one"] += 1
        per_unit[uid] = {
            "readers": [lid for lid in order if lid in who],
            "divergence": divergence,
            "applied": {cid: [lid for lid in order if lid in applied[uid][cid]]
                        for cid in code_ids if applied[uid].get(cid)},
            "quotes": {cid: {lid: list(qs) for lid, qs in by_lens.items()} for cid, by_lens in quotes[uid].items()},
            "not_found": {cid: {lid: list(qs) for lid, qs in by_lens.items()} for cid, by_lens in missed[uid].items()}
            if uid in missed else {},
        }

    pairs = []
    for a, b in combinations(order, 2):
        both = [uid for uid in compared if a in read_by[uid] and b in read_by[uid]]
        pa = {(uid, cid) for uid in both for cid, ls in applied[uid].items() if a in ls}
        pb = {(uid, cid) for uid in both for cid, ls in applied[uid].items() if b in ls}
        union = len(pa | pb)
        pairs.append({"a": a, "b": b, "units": len(both), "a_pairs": len(pa), "b_pairs": len(pb),
                      "both": len(pa & pb), "jaccard": len(pa & pb) / union if union else None})

    suggested = defaultdict(lambda: {"count": 0, "readers": set(), "forms": Counter()})
    for memo in project.records("memo"):
        if memo.get("memo_type") != "unfit" or finished.get(memo["activity"]) != memo["by"]:
            continue
        name = " ".join(str(memo.get("suggested_name") or "").split())
        if name:
            entry = suggested[name.lower()]
            entry["count"] += 1
            entry["readers"].add(memo["by"])
            entry["forms"][name] += 1
    unfit_names = sorted(({"name": e["forms"].most_common(1)[0][0], "count": e["count"], "readers": len(e["readers"])}
                          for e in suggested.values()), key=lambda e: (-e["count"], e["name"].lower()))

    return {
        "codebook": codebook_memo_id,
        "codes": codes,
        "readers": readers,
        "units_compared": len(compared),
        "per_code": per_code,
        "pairs": pairs,
        "per_unit": per_unit,
        "unfit_names": unfit_names,
    }


# ---- the page --------------------------------------------------------------

def _cell(text) -> str:
    return " ".join(str(text).split()).replace("|", "¦")


def _ratio(value) -> str:
    return "–" if value is None else f"{value:.2f}"


def _first_seen(unit: dict) -> str:
    return (unit.get("context") or {}).get("first_seen", {}).get("time") or ""


def _context_line(unit: dict, readers: list[str]) -> str:
    ctx = unit.get("context") or {}
    facts = [f"Read by {', '.join(readers)}"]
    if ctx.get("introduced_by"):
        facts.append(f"saved as `{ctx['introduced_by']}`")
    if ctx.get("signature"):
        facts.append(f"signed `{ctx['signature']}`" + (" (with a question mark)" if ctx.get("signature_uncertain") else ""))
    facts.append(f"`{unit['id']}`")
    return " · ".join(facts)


def _code_line(code: dict, entry: dict, readers: dict, cid: str) -> str:
    who = entry["readers"]
    by = entry["applied"].get(cid, [])
    said = []
    for lid in by:
        for q in entry["quotes"].get(cid, {}).get(lid, []):
            text = f"{readers[lid]['name']} “{' '.join(q['exact'].split())}”"
            if q["note"]:
                text += f" *(note: {' '.join(q['note'].split())})*"
            said.append(text)
    line = f"- **[{code['key']}] {code.get('name')}** ({len(by)} of {len(who)}): " + "; ".join(said) + "."
    absent = []
    for lid in who:
        if lid in by:
            continue
        lost = entry["not_found"].get(cid, {}).get(lid)
        absent.append(readers[lid]["name"] + (f" (its quotation “{' '.join(lost[0].split())}” is not in the unit)"
                                              if lost else ""))
    if absent:
        line += " Not applied by " + ", ".join(absent) + "."
    return line


def render(project: Project, codebook_memo_id: str, top: int = 60, *, lens_ids=None) -> str:
    """A Markdown page: readers, codes, pairs, the units to read first, and the
    names readers suggested where the codebook did not fit."""
    result = compare(project, codebook_memo_id, lens_ids)
    readers, per_unit = result["readers"], result["per_unit"]
    codes = {code["id"]: code for code in result["codes"]}
    n_compared = result["units_compared"]

    out = [f"# Divergence: codebook `{codebook_memo_id}`", "",
           f"This page compares {len(readers)} readers who applied the same codebook ({len(codes)} codes) to the same "
           "units. For every unit read by at least two of them it records which codes each reader applied, counts "
           "where they agree and where they differ, and lists the units where they differ most, so that a person "
           "knows where to read first.", "",
           "It is not a measure of correctness. Agreement is diagnostic, not truth: readers can agree and all be "
           "wrong, and where they differ the codebook may fit the unit poorly or the unit may hold more than one "
           "reading. No reader is treated as the standard. The numbers are plain counts and ratios; no "
           "chance-corrected statistic such as kappa is computed.", "",
           f"Units read by at least two readers: {n_compared:,}.", ""]
    if len(readers) < 2:
        out += ["Fewer than two readers have applied this codebook, so there is nothing to compare yet.", ""]

    out += ["## Readers", "",
            "| Reader | Lens | Units read | Also read by another | Codings | Codes per unit | Quotes not found |",
            "|---|---|---:|---:|---:|---:|---:|"]
    for lid, r in readers.items():
        out.append(f"| {_cell(r['name'])} | `{lid}` | {r['units_read']:,} | {r['units_compared']:,} | {r['codings']:,} | "
                   f"{_ratio(r['codes_per_unit'])} | {r['quotes_not_found']:,} |")
    out += ["", "Codes per unit counts each code once per unit, however many passages it was quoted for. "
            "A quotation that is not in its unit is never a coding; those are counted in the last column.", ""]

    out += ["## Codes", "",
            f"Over the {n_compared:,} units read by at least two readers: units where every reader of the unit applied "
            "the code, units where some did and some did not, and units where exactly one did. With two readers "
            "the last two columns are the same.", "",
            "| Code | All | Some, not all | Exactly one |", "|---|---:|---:|---:|"]
    for row in result["per_code"].values():
        out.append(f"| [{_cell(row['key'])}] {_cell(row['name'])} | {row['all']:,} | {row['some']:,} | {row['one']:,} |")
    out.append("")

    out += ["## Pairs of readers", "",
            "Over the units both read: the (unit, code) pairs each applied, the pairs both applied, and their overlap "
            "(pairs both applied, divided by pairs either applied).", "",
            "| Reader | Reader | Units both read | Pairs (first / second / both) | Overlap |", "|---|---|---:|---:|---:|"]
    for p in result["pairs"]:
        out.append(f"| {_cell(readers[p['a']]['name'])} | {_cell(readers[p['b']]['name'])} | {p['units']:,} | "
                   f"{p['a_pairs']:,} / {p['b_pairs']:,} / {p['both']:,} | {_ratio(p['jaccard'])} |")
    out.append("")

    units = {uid: project.get(uid) for uid, entry in per_unit.items() if entry["divergence"]}
    ranked = sorted((uid for uid, unit in units.items() if unit is not None),
                    key=lambda uid: (-per_unit[uid]["divergence"], _first_seen(units[uid]), uid))
    shown = ranked[:max(0, top)]
    out += ["## Where to read first", "",
            f"{len(ranked):,} of {n_compared:,} units have a code that some of their readers applied and others did "
            f"not. Showing {len(shown):,}, most divergent first, earliest first among ties.", ""]
    for n, uid in enumerate(shown, 1):
        unit, entry = units[uid], per_unit[uid]
        d = entry["divergence"]
        where = [unit["unit_kind"], (unit.get("context") or {}).get("page"), _first_seen(unit)]
        out.append(f"### {n}. Readers differ on {d} code{'' if d == 1 else 's'} · " + " · ".join(str(w) for w in where if w))
        out += [_context_line(unit, [readers[lid]["name"] for lid in entry["readers"]]), ""]
        out += [f"> {line}" for line in project.unit_text(unit).split("\n")]
        out.append("")
        for cid in entry["applied"]:
            out.append(_code_line(codes[cid], entry, readers, cid))
        out.append("")

    out += ["## Names suggested where the codebook did not fit", "",
            "From `unfit` memos: passages a reader said no code covered, with the name it suggested. Names are "
            "grouped ignoring case and spacing.", ""]
    if not result["unfit_names"]:
        out.append("No reader reported a passage the codebook did not fit.")
    for entry in result["unfit_names"][:40]:
        out.append(f"- {entry['name']}: {entry['count']:,} (from {entry['readers']} of {len(readers)} readers)")
    return "\n".join(out) + "\n"


# ---- command line ----------------------------------------------------------

def cmd_divergence(args) -> int:
    with Project(args.project) as project:
        text = render(project, args.codebook, top=args.top)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(f"wrote {out} ({len(text):,} characters)")
    return 0


def register_cli(sub) -> None:
    p = sub.add_parser("divergence", help="compare readers who applied the same codebook, unit by unit")
    p.add_argument("project")
    p.add_argument("--codebook", required=True, help="ID of a codebook memo")
    p.add_argument("--out", required=True)
    p.add_argument("--top", type=int, default=60, help="how many of the most divergent units to show")
    p.set_defaults(func=cmd_divergence)
