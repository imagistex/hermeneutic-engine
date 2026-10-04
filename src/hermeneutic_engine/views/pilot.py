"""A review page for a pilot: every sampled unit with each reader's codings
side by side, plus what each reader was told and how reliably it quoted.

Everything on the page is scoped to the sample: readings of other units by the
same lens are left out of the counts.
"""

from __future__ import annotations

from collections import Counter, defaultdict

from ..store import Project


def _reader_name(lens: dict) -> str:
    reader = lens["reader"]
    name = f"{reader.get('model', reader.get('id'))} ({reader.get('family', reader['kind'])})"
    if reader.get("requested"):
        name += f", answering in place of {reader['requested']}"
    if not any(part["name"] == "frame" for part in lens.get("stack", [])):
        name += ", no frame"
    if lens["method"].get("version", "0") != "0":
        name += f", instructions v{lens['method']['version']}"
    return name


def render(project: Project, sample_memo_id: str, method: str = "open-coding", show: str | None = None) -> str:
    """Render the review page.

    The table at the top covers every lens. The detail below it covers only the
    lenses chosen by `show`: "all", or an instructions version such as "1". By
    default it is the latest instructions version, with the frame.
    """
    sample = project.get(sample_memo_id)
    unit_ids = sample["about"]
    wanted = set(unit_ids)

    lenses = [l for l in project.records("lens") if l["method"]["name"] == method and l["reader"]["kind"] == "model"]
    lenses.sort(key=_reader_name)
    lens_ids = {l["id"] for l in lenses}
    framed = lambda l: any(part["name"] == "frame" for part in l.get("stack", []))
    versions = sorted({l["method"].get("version", "0") for l in lenses})
    if show == "all":
        shown = lenses
    else:
        version = show or (versions[-1] if versions else "0")
        shown = [l for l in lenses if l["method"].get("version", "0") == version and framed(l)]

    by_unit: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    stats = {l["id"]: Counter() for l in lenses}
    names = {l["id"]: defaultdict(lambda: {"n": 0, "batches": set(), "kind": ""}) for l in lenses}
    for coding in project.records("coding"):
        if coding["by"] in lens_ids and coding["unit"] in wanted:
            by_unit[coding["unit"]][coding["by"]].append(coding)
            code = project.get(coding["code"])
            s = stats[coding["by"]]
            s["codings"] += 1
            s["ws"] += coding["anchor"]["match"] != "exact"
            s["in_vivo"] += code["code_type"] == "in_vivo"
            s["in_vivo_absent"] += coding.get("in_vivo_in_unit") is False
            s["loaded"] += (coding.get("note") or "").lstrip().lower().startswith("loaded:")
            entry = names[coding["by"]][code["name"].lower()]
            entry["n"] += 1
            entry["batches"].add(code.get("batch"))
            entry["kind"] = code["code_type"]
            entry["name"] = code["name"]

    in_scope = set()  # read activities that touched the sample
    for activity in project.records("activity"):
        if activity["lens"] in lens_ids and activity["type"] == "read" and wanted & set(activity.get("used", [])):
            in_scope.add(activity["id"])
            s = stats[activity["lens"]]
            call = activity.get("call", {})
            s["batches"] += 1
            s["batches_failed"] += activity["status"] != "ok"
            s["tokens_in"] += call.get("tokens_in") or 0
            s["tokens_out"] += call.get("tokens_out") or 0
    missed = defaultdict(list)
    for failure in project.records("failure"):
        if failure["by"] in lens_ids and failure["reason"].startswith("quotation not found") \
                and failure["attempted"].get("unit") in wanted:
            missed[failure["by"]].append(failure)

    out = ["# Pilot open coding: review page", "",
           f"Sample: `{sample_memo_id}`", "", sample["body"], "",
           "## How to review", "",
           "1. Read a stretch of units and each reader's codes beside them.",
           "2. Ask of the instructions (at the bottom): what should the readers have been told to do differently?",
           "3. Mark codes worth keeping, codes that are wrong, and words of theirs the readers missed.", "",
           "## Readers", "",
           "| Reader | Units coded | Codings | Distinct code names | In vivo | Flagged as loaded | Quotes not found | Tokens in / out |",
           "|---|---|---|---|---|---|---|---|"]
    for lens in lenses:
        s = stats[lens["id"]]
        coded = sum(1 for uid in unit_ids if by_unit[uid].get(lens["id"]))
        n_missed = len(missed[lens["id"]])
        attempts = s["codings"] + n_missed
        rate = f"{n_missed} of {attempts} ({n_missed / attempts:.1%})" if attempts else "0"
        share = f"{s['in_vivo']} ({s['in_vivo'] / s['codings']:.0%})" if s["codings"] else "0"
        out.append(f"| {_reader_name(lens)} | {coded} of {len(unit_ids)} | {s['codings']} | {len(names[lens['id']])} | "
                   f"{share} | {s['loaded']} | {rate} | {s['tokens_in']:,} / {s['tokens_out']:,} |")
    out.append("")
    for lens in lenses:
        reader = lens["reader"]
        if reader.get("requested"):
            why = (reader.get("substituted") or {}).get("category") or "unstated"
            out.append(f"- {reader['model']} read {stats[lens['id']]['batches']} batches that were sent to "
                       f"{reader['requested']}. The harness substituted it after a refusal (category: {why}). "
                       f"Those readings are credited to the model that made them.")
    for lens in lenses:
        s = stats[lens["id"]]
        if s["batches_failed"]:
            out.append(f"- {_reader_name(lens)}: {s['batches_failed']} of {s['batches']} batches returned nothing usable.")
        if s["in_vivo_absent"]:
            out.append(f"- {_reader_name(lens)}: {s['in_vivo_absent']} codes marked in vivo whose name is not in the unit.")
    out.append("")

    out += ["The sections below show " + (", ".join(_reader_name(l) for l in shown) or "no lens") + ". "
            "Other lenses are in the table above and in the ledger.", ""]
    out += ["## Most used code names, by reader", "",
            "Batches were read independently, so the same name in two batches may not mean the same thing. "
            "The number of batches a name came from is shown.", ""]
    for lens in shown:
        out.append(f"**{_reader_name(lens)}**")
        out.append("")
        ranked = sorted(names[lens["id"]].values(), key=lambda e: (-e["n"], e["name"]))
        for entry in ranked[:40]:
            kind = "in vivo" if entry["kind"] == "in_vivo" else "analytic"
            out.append(f"- {entry['name']} ({kind}): {entry['n']} uses in {len(entry['batches'])} batches")
        out.append("")

    out += ["## What each reader noticed across batches", ""]
    for lens in shown:
        out.append(f"**{_reader_name(lens)}**")
        out.append("")
        for memo in project.records("memo"):
            if memo["by"] == lens["id"] and memo.get("memo_type") == "analytic" and memo["activity"] in in_scope:
                out.append(f"> {memo['body'].strip()}")
                out.append("")

    out += ["## Units", ""]
    units = [project.get(uid) for uid in unit_ids]
    units.sort(key=lambda u: (u["context"].get("first_seen", {}).get("time") or "", u["id"]))
    for n, unit in enumerate(units, 1):
        ctx = unit["context"]
        out.append(f"### {n:03d} · {unit['unit_kind']} · {ctx.get('page')} · {ctx.get('first_seen', {}).get('time')}")
        facts = [f"saved as `{ctx.get('introduced_by')}`"]
        if ctx.get("signature"):
            facts.append(f"signed `{ctx['signature']}`" + (" (with a question mark)" if ctx.get("signature_uncertain") else ""))
        if ctx.get("first_removed_in"):
            facts.append(f"first absent {ctx['first_removed_in']['time']}"
                         + (", later restored" if ctx.get("returned_after_removal") else ""))
        facts.append(f"`{unit['id']}`")
        out.append(" · ".join(facts))
        out.append("")
        for line in project.unit_text(unit).split("\n"):
            out.append(f"> {line}")
        out.append("")
        for lens in shown:
            codings = by_unit[unit["id"]].get(lens["id"], [])
            out.append(f"**{_reader_name(lens)}**" + ("" if codings else ": no codes"))
            for coding in codings:
                code = project.get(coding["code"])
                kind = "in vivo" if code["code_type"] == "in_vivo" else "analytic"
                line = f"- **{code['name']}** ({kind}): “{coding['anchor']['exact']}”"
                if coding.get("definition"):
                    line += f" — {coding['definition']}"
                if coding.get("note"):
                    line += f" *Note: {coding['note']}*"
                out.append(line)
            out.append("")

    for lens in shown:
        if missed[lens["id"]]:
            out += [f"## Quotes {_reader_name(lens)} gave that are not in the unit", ""]
            for failure in missed[lens["id"]][:60]:
                attempted = failure["attempted"]
                out.append(f"- `{attempted['unit']}` · {attempted['code']}: “{attempted['quote']}”")
            out.append("")

    out += ["## What the readers were told", ""]
    if shown:
        if shown[0].get("priors"):
            out += ["No analytic theory was given to the readers. The instructions still carry these priors:", ""]
            out += [f"- {prior}" for prior in shown[0]["priors"]]
            out.append("")
        for part in shown[0]["stack"]:
            out += [f"**{part['name']}** (`{part['sha256'][:12]}`)", "",
                    "```text", project.part_path(part["sha256"]).read_text(encoding="utf-8").strip(), "```", ""]
    return "\n".join(out) + "\n"
