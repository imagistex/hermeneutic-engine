"""Focused coding: readers apply a fixed codebook to many units, in batches.

A codebook is a `memo` with memo_type "codebook" whose `about` lists `code`
records in order. Every reader is given the same codes, so readings can be
compared unit by unit afterwards (views/divergence.py). As in open coding, each
batch is read on its own, and the reader returns words that the kernel finds
inside the unit; a quotation that cannot be found becomes a `failure`.

A unit that was read and given no code is a result, not a gap. Nothing is
recorded for it, but the activity's `used` list names it. A passage the codebook
has no code for comes back as `unfit`, with a suggested name, and is kept as a
memo.

A run is resumable. A unit already read by this lens, or by a model that
answered in its place, in a finished activity is not read again, so a run over
thousands of units can be stopped and started.

Two things about a run are choices, and each makes a different lens: which
version of the instructions the reader is given ("v0" or "v1"), and whether the
reader is given the whole codebook or only its codes of some types.
"""

from __future__ import annotations

import json
import random
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from importlib import resources

from ..anchors import resolve
from ..ids import sha256_hex, utc_now
from ..lens import Activity, make_lens
from ..store import Project
from . import open_coding

METHOD = {"name": "focused-coding", "version": "0"}
INSTRUCTIONS = "focused-coding.md"
_WS = re.compile(r"\s+")

# What this lens knowingly carries. The codebook directs attention to what it
# names; the frame, when given, says what the material is.
PRIORS = [
    "works with a fixed codebook written beforehand",
    "the reader is told the publisher's account of what the writers were doing",
]

# Instructions are versioned, as in open coding: a reading names the version it
# was given, and each version is a different lens. Version 1 tells the reader
# more about the study and asks for a field note, which the reader may sign, in
# place of the batch memo. The frame prior stays last in each list, so that a
# reading with no frame can leave it out.
PRIORS_V1 = PRIORS[:1] + [
    "the reader is told that several readers of different model families read the same material",
    "the reader is told that the writers' recurring words are traced separately by exact search",
    "attention is directed to time, names and address, tone, marked orthography, and words with more than one sense",
    "the reader is told that field notes will be shared on a board after the pass",
    "the reader is told that no one reading is treated as the right one and that differences are wanted",
    "the reader is told the codebook was made from a sample of two hundred units and will not fit everything",
    "the reader is given 'values' and 'alive' as examples of words with more than one sense",
    "the reader is told it may leave a unit it cannot or would rather not read",
    "the reader may sign its field note however it likes, or not at all",
    "the reader is asked to mark wording that strikes it as evocative, whether or not it can say why",
] + PRIORS[1:]
# Version 1 tells the reader that the writers' recurring words are traced
# elsewhere. When in vivo codes are held back from a reader, it is told which
# words those are, so that it neither marks them nor reports them as unfit.
HELD_BACK_V1 = ("These words of the writers are traced separately, by exact search. There is no need to mark them, "
                "and a passage is not unfit only because it uses one of them: {names}.")
VERSIONS = {
    "v0": {"version": "0", "base": "reader-base.md", "file": INSTRUCTIONS, "priors": PRIORS,
           "closing": ("batch_memo",), "held_back": None},
    "v1": {"version": "1", "base": "reader-base-v1.md", "file": "focused-coding-v1.md", "priors": PRIORS_V1,
           "closing": ("field_note", "signed"), "held_back": HELD_BACK_V1},
}

# Reader fields that say which model answered. Lenses that differ only in these
# are the same reading done by a substitute model.
_ANSWERED = ("model", "requested", "substituted")


def _prompt(name: str) -> str:
    return resources.files("hermeneutic_engine.prompts").joinpath(name).read_text(encoding="utf-8")


def _flat(value) -> str:
    return _WS.sub(" ", str(value or "")).strip()


def _version(instructions: str) -> dict:
    """The named version of the instructions. An unknown name is refused."""
    if not isinstance(instructions, str) or instructions not in VERSIONS:
        raise ValueError(f"unknown instructions: {instructions!r} (known: {', '.join(VERSIONS)})")
    return VERSIONS[instructions]


def _types(code_types) -> list[str] | None:
    """The code types a reader is limited to, sorted. None means the whole codebook."""
    if code_types is None:
        return None
    if isinstance(code_types, str):  # one type given bare, not a sequence of letters
        code_types = (code_types,)
    return sorted({str(code_type) for code_type in code_types})


# ---- the codebook --------------------------------------------------------

def codes_of(project: Project, codebook_memo_id: str) -> list[dict]:
    """The code records a codebook memo lists, in its order."""
    memo = project.get(codebook_memo_id)
    if memo is None or memo.get("kind") != "memo" or memo.get("memo_type") != "codebook":
        raise ValueError(f"not a codebook memo: {codebook_memo_id}")
    codes = []
    for ref in memo.get("about") or []:
        if not isinstance(ref, str):
            continue  # an inline anchor is not a code
        record = project.get(ref)
        if record is None:
            raise ValueError(f"codebook {codebook_memo_id} names a record that does not exist: {ref}")
        if record["kind"] == "code":
            codes.append(record)
    if not codes:
        raise ValueError(f"codebook {codebook_memo_id} lists no codes")
    keys = [code.get("key") for code in codes]
    if not all(isinstance(key, str) and key for key in keys):
        raise ValueError(f"codebook {codebook_memo_id} has a code without a key")
    if len(set(keys)) != len(keys):
        raise ValueError(f"codebook {codebook_memo_id} uses a key twice")
    return codes


def render_codebook(codes: list[dict]) -> str:
    """The codebook as the reader sees it: one entry per code, in order."""
    entries = []
    for code in codes:
        kind = str(code.get("code_type") or "").replace("_", " ")
        lines = [f"[{code['key']}] {_flat(code.get('name'))}" + (f" ({kind})" if kind else ""),
                 f"  definition: {_flat(code.get('definition'))}",
                 f"  apply when: {_flat(code.get('apply_when'))}",
                 f"  do not apply when: {_flat(code.get('do_not_apply_when'))}"]
        loaded = code.get("loaded")
        if isinstance(loaded, (list, tuple)):
            loaded = "; ".join(str(sense) for sense in loaded)
        if _flat(loaded):
            lines.append(f"  loaded: {_flat(loaded)}")
        entries.append("\n".join(lines))
    return "\n\n".join(entries)


def schema(local_ids: list[str], code_keys: list[str], instructions: str = "v0") -> dict:
    """Strict: every property required, nothing extra, units and codes from fixed sets.

    The answer closes with `batch_memo` under version 0 of the instructions, and
    with `field_note` and `signed` under version 1. Any of them may be empty."""
    closing = _version(instructions)["closing"]
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["codings", "unfit", *closing],
        "properties": {
            "codings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["unit", "code", "quote", "note"],
                    "properties": {
                        "unit": {"type": "string", "enum": local_ids},
                        "code": {"type": "string", "enum": code_keys},
                        "quote": {"type": "string", "minLength": 1},
                        "note": {"type": "string"},
                    },
                },
            },
            "unfit": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["unit", "quote", "why", "suggested_name"],
                    "properties": {
                        "unit": {"type": "string", "enum": local_ids},
                        "quote": {"type": "string"},
                        "why": {"type": "string"},
                        "suggested_name": {"type": "string"},
                    },
                },
            },
            **{name: {"type": "string"} for name in closing},
        },
    }


def render_batch(project: Project, units: list[dict], frame: str, codebook_text: str) -> tuple[str, dict[str, dict]]:
    """The frame (if any), the codebook, then the units laid out exactly as in
    open coding, with delimiters derived from the batch itself."""
    body, local = open_coding.render_batch(project, units, "")
    head = [f"<frame>\n{frame.strip()}\n</frame>", ""] if frame.strip() else []
    head += [f"<codebook>\n{codebook_text.strip()}\n</codebook>", ""]
    return "\n".join(head) + "\n" + body, local


# ---- lenses ----------------------------------------------------------------

def make_reader_lens(project: Project, spec, frame: str, harness: str, codebook_memo_id: str, codebook_text: str,
                     answered: str | None = None, substitution: dict | None = None, instructions: str = "v0",
                     code_types: tuple[str, ...] | None = None) -> dict:
    """The lens for one reader applying one codebook. If a different model
    answered than the one asked for, that reading gets its own lens naming the
    model that actually did it.

    `instructions` names the version of the prompts the reader was given.
    `code_types`, when given, says the reader had only the codes of those types;
    `codebook_text` must then be the text of those codes alone. The types are
    recorded in the lens's method only when a filter was used, so a reading of
    the whole codebook keeps the lens it always had."""
    version = _version(instructions)
    types = _types(code_types)
    try:  # only params that are safe to keep: no credentials, no query strings
        from ..readers import public_params
        params = public_params(spec)
    except Exception:  # a scripted reader in tests has no real spec
        params = {}
    reader = {"kind": "model", "family": spec.family, "model": answered or spec.model, "harness": harness,
              "backend": spec.backend, "params": params, "codebook": codebook_memo_id}
    if answered and answered != spec.model:
        reader["requested"] = spec.model
        reader["substituted"] = {k: (substitution or {}).get(k) for k in ("trigger", "category")}
    # A reading with no frame is its own lens: that prior is absent, not merely unstated.
    framed = bool(frame.strip())
    stack = [
        ("system", "reader-base", _prompt(version["base"])),
        ("system", "method:focused-coding", _prompt(version["file"])),
        ("user", "codebook", codebook_text),
    ]
    if framed:
        stack.append(("user", "frame", frame))
    method = {"name": METHOD["name"], "version": version["version"]}
    if types is not None:
        method["code_types"] = types
    return make_lens(
        project,
        reader=reader,
        method=method,
        stack=stack,
        theory="withheld",
        priors=version["priors"] if framed else version["priors"][:-1],
        reproducible=True,
    )


def readings_of(project: Project, lens: dict) -> set[str]:
    """This lens and every lens that counts as the same reading for the purpose
    of not reading a unit twice: same codebook, stack and frame, asked of the
    same model. That includes a lens where another model answered in place of
    the one requested, and a lens that differs only in the harness version (a
    CLI that updates itself overnight must not make a long run start over).
    The lenses stay distinct in the ledger; this only decides what to skip."""
    def core(l: dict):
        reader = {k: v for k, v in l["reader"].items() if k not in _ANSWERED and k != "harness"}
        return reader, l["method"], l["stack"], l.get("theory"), l.get("priors"), l.get("reproducible")
    wanted = core(lens)
    asked = lens["reader"].get("requested") or lens["reader"]["model"]
    ids = {lens["id"]}
    for other in project.records("lens"):
        reader = other.get("reader", {})
        if (reader.get("requested") or reader.get("model")) == asked and core(other) == wanted:
            ids.add(other["id"])
    return ids


def already_read(project: Project, lens_ids: set[str]) -> set[str]:
    """Units named by a finished read activity of any of these lenses."""
    done: set[str] = set()
    for activity in project.records("activity"):
        if activity.get("type") == "read" and activity.get("status") == "ok" and activity.get("lens") in lens_ids:
            done.update(activity.get("used") or [])
    return done


# ---- recording -------------------------------------------------------------

def record_batch(project: Project, lens_id: str, activity: Activity, local: dict[str, dict], parsed: dict,
                 code_ids: dict[str, str], instructions: str = "v0") -> dict:
    """Turn one reader response into codings, failures and memos.

    Under version 0 of the instructions the batch memo is kept as an "analytic"
    memo. Under version 1 the field note is kept as a "field_note" memo, with
    the reader's signature beside it if one was given."""
    closing = _version(instructions)["closing"]  # refused here, before anything is written
    counts = {"codings": 0, "failures": 0, "ws_normalized": 0, "unfit": 0, "units_uncoded": 0}
    coded: set[str] = set()
    for item in parsed.get("codings") or []:
        unit = local.get(item.get("unit"))
        code_id = code_ids.get(item.get("code"))
        if unit is None or code_id is None:
            project.append("failure", {"reason": "unknown unit" if unit is None else "unknown code", "attempted": item},
                           by=lens_id, activity=activity.id)
            counts["failures"] += 1
            continue
        coded.add(unit["id"])
        found = resolve(project.source_bytes(unit["source"]), unit["start"], unit["end"], item.get("quote") or "")
        if found is None:
            project.append("failure", {"reason": "quotation not found in unit",
                                       "attempted": {"unit": unit["id"], "quote": item.get("quote"), "code": code_id}},
                           by=lens_id, activity=activity.id)
            counts["failures"] += 1
            continue
        project.append("coding", {"unit": unit["id"], "anchor": {"source": unit["source"], **found},
                                  "code": code_id, "note": item.get("note") or ""},
                       by=lens_id, activity=activity.id)
        counts["codings"] += 1
        counts["ws_normalized"] += found["match"] != "exact"
    for item in parsed.get("unfit") or []:
        unit = local.get(item.get("unit"))
        if unit is None:
            project.append("failure", {"reason": "unknown unit", "attempted": item}, by=lens_id, activity=activity.id)
            counts["failures"] += 1
            continue
        quote = item.get("quote") or ""
        found = resolve(project.source_bytes(unit["source"]), unit["start"], unit["end"], quote)
        body = {"memo_type": "unfit", "about": [unit["id"]], "body": item.get("why") or "",
                "suggested_name": _flat(item.get("suggested_name"))}
        if found is not None:
            body["about"].append({"source": unit["source"], **found})
        elif quote.strip():
            body["quote_not_found"] = quote  # kept so the passage meant is not lost; never an anchor
        project.append("memo", body, by=lens_id, activity=activity.id)
        counts["unfit"] += 1
    counts["units_uncoded"] = sum(1 for unit in local.values() if unit["id"] not in coded)
    if "field_note" in closing:
        note = (parsed.get("field_note") or "").strip()
        if note:
            body = {"memo_type": "field_note", "about": [u["id"] for u in local.values()], "body": note}
            signed = (parsed.get("signed") or "").strip()
            if signed:
                body["signed"] = signed
            project.append("memo", body, by=lens_id, activity=activity.id)
        return counts
    memo = (parsed.get("batch_memo") or "").strip()
    if memo:
        project.append("memo", {"memo_type": "analytic", "about": [u["id"] for u in local.values()], "body": memo},
                       by=lens_id, activity=activity.id)
    return counts


def _ask(call, spec, system: str, user: str, schema_: dict):
    """Runs in a worker thread: one model call. Nothing here touches the ledger."""
    started = utc_now()
    return started, call(spec, system, user, schema_)


# ---- the run ---------------------------------------------------------------

def run(project: Project, units: list[dict], spec, codebook_memo_id: str, *, frame: str, batch_size: int = 25,
        parallel: int = 3, seed: int = 0, call=None, harness: str | None = None, progress=print,
        retry_wait_s: float = 0, instructions: str = "v0", code_types: tuple[str, ...] | None = None) -> dict:
    """Apply a codebook to `units` with one reader. Returns a summary; everything else is in the ledger.

    Units this reader has already read with this codebook (in a finished
    activity) are skipped. Batches that fail are recorded; if `retry_wait_s` is
    above zero, they are retried once, as new activities, after that wait.
    `batches_failed` counts the batches still unread at the end;
    `batches_retried` counts the retries.

    `instructions` is the version of the prompts the reader is given ("v0" or
    "v1"). `code_types`, when given, limits the reader to the codes of those
    types: only they are shown and only they may be applied. Each choice makes a
    different lens, so a unit read one way is still unread the other way. An
    unknown version, or a filter that leaves no codes, raises ValueError before
    anything is asked of a model or written to the ledger.
    """
    version = _version(instructions)
    types = _types(code_types)
    every_code = codes_of(project, codebook_memo_id)
    codes = every_code
    if types is not None:
        codes = [code for code in every_code if code.get("code_type") in types]
        if not codes:
            raise ValueError(f"codebook {codebook_memo_id} has no codes of type: {', '.join(types) or '(none named)'}")
    codebook_text = render_codebook(codes)
    given_ids = {code["id"] for code in codes}
    words = [_flat(code.get("name")) for code in every_code
             if code["id"] not in given_ids and code.get("code_type") == "in_vivo"]
    if words and version["held_back"]:
        codebook_text += "\n\n" + version["held_back"].format(names="; ".join(words))
    code_ids = {code["key"]: code["id"] for code in codes}
    keys = list(code_ids)
    if call is None or harness is None:
        from .. import readers
        call = call or readers.call
        harness = harness or readers.harness_version(spec)
    lens = make_reader_lens(project, spec, frame, harness, codebook_memo_id, codebook_text,
                            instructions=instructions, code_types=code_types)
    system = _prompt(version["base"]).strip() + "\n\n" + _prompt(version["file"]).strip()

    given: dict[str, dict] = {}
    for unit in units:
        if unit is not None:
            given.setdefault(unit["id"], unit)
    done = already_read(project, readings_of(project, lens))
    # Batches are dealt from every unit given, and only then are the units already
    # read set aside. Dealing from the unread units alone would give a resumed run
    # different batches from an uninterrupted one, and its field notes could no
    # longer be set beside another reader's notes on the same batch.
    order = sorted(given.values(), key=lambda u: u["id"])
    random.Random(seed).shuffle(order)
    size = max(1, batch_size)
    batches = []
    for i in range(0, len(order), size):
        unread = [u for u in order[i:i + size] if u["id"] not in done]
        if unread:
            batches.append(unread)
    to_read = sum(len(batch) for batch in batches)
    skipped = len(given) - to_read
    progress(f"{skipped} of {len(given)} units already read by this lens; reading {to_read} in {len(batches)} batches")

    totals = {"batches": len(batches), "batches_failed": 0, "batches_retried": 0, "units_read": 0,
              "units_skipped": skipped, "codings": 0, "failures": 0, "unfit": 0, "units_uncoded": 0,
              "ws_normalized": 0, "tokens_in": 0, "tokens_out": 0, "batches_answered_by_another_model": 0}
    substitutes: dict[tuple, dict] = {}

    def read(batches: list[list[dict]], label: str) -> list[list[dict]]:
        """One pass over `batches`. Returns the batches that failed."""
        # Prompts are built here, in one thread; only the model calls run in
        # parallel, and only this thread writes to the ledger.
        jobs = []
        for batch in batches:
            user, local = render_batch(project, batch, frame, codebook_text)
            jobs.append((batch, user, local, Activity(project, "read", lens["id"], used=[u["id"] for u in batch])))
        failed = []
        if not jobs:
            return failed
        pool = ThreadPoolExecutor(max_workers=max(1, parallel))
        try:
            futures = {pool.submit(_ask, call, spec, system, job[1], schema(sorted(job[2]), keys, instructions)): job
                       for job in jobs}
            for done_n, future in enumerate(as_completed(futures), 1):
                batch, user, local, activity = futures[future]
                started, result = future.result()
                activity.started = started
                raw, usage, meta = result.raw or "", result.usage or {}, result.meta or {}
                run_dir = project.root / "runs" / activity.id.replace(":", "-")
                run_dir.mkdir(parents=True, exist_ok=True)
                (run_dir / "system.txt").write_bytes(system.encode("utf-8"))
                (run_dir / "user.txt").write_bytes(user.encode("utf-8"))
                (run_dir / "response.txt").write_bytes(raw.encode("utf-8"))
                (run_dir / "meta.json").write_text(json.dumps({"usage": usage, "meta": meta, "error": result.error},
                                                              indent=2, default=str), encoding="utf-8")
                call_info = {
                    "prompt_sha256": sha256_hex((system + "\n\n" + user).encode("utf-8")),
                    "response_sha256": sha256_hex(raw.encode("utf-8")),
                    "model_reported": meta.get("model_reported"),
                    "attempts": meta.get("attempts"),
                    "duration_s": meta.get("duration_s"),
                    "tokens_in": usage.get("tokens_in"),
                    "tokens_out": usage.get("tokens_out"),
                    "cost_usd": str(usage.get("cost_usd")) if usage.get("cost_usd") is not None else None,
                }
                totals["tokens_in"] += usage.get("tokens_in") or 0
                totals["tokens_out"] += usage.get("tokens_out") or 0
                # Credit the reading to the model that made it (see open_coding).
                substitution = meta.get("substitution") or {}
                answered = substitution.get("answered")
                batch_lens = lens
                if answered and answered != spec.model:
                    key = (answered, substitution.get("trigger"), substitution.get("category"))
                    if key not in substitutes:
                        substitutes[key] = make_reader_lens(project, spec, frame, harness, codebook_memo_id,
                                                            codebook_text, answered=answered, substitution=substitution,
                                                            instructions=instructions, code_types=code_types)
                    batch_lens = substitutes[key]
                    activity.lens_id = batch_lens["id"]
                    call_info["substitution"] = substitution
                    totals["batches_answered_by_another_model"] += 1
                if result.parsed is None:
                    project.append("failure", {"reason": f"reader returned nothing usable: {result.error}",
                                               "attempted": {"units": [u["id"] for u in batch]}},
                                   by=batch_lens["id"], activity=activity.id)
                    activity.finish("failed", call=call_info, counts={})
                    failed.append(batch)
                    progress(f"  {label}[{done_n}/{len(jobs)}] FAILED: {result.error}")
                    continue
                counts = record_batch(project, batch_lens["id"], activity, local, result.parsed, code_ids, instructions)
                activity.finish("ok", call=call_info, counts=counts)
                totals["units_read"] += len(batch)
                for name, value in counts.items():
                    totals[name] += value
                progress(f"  {label}[{done_n}/{len(jobs)}] {counts['codings']} codings, {counts['failures']} unresolved, "
                         f"{counts['units_uncoded']} units uncoded, {counts['unfit']} unfit")
        finally:
            # On an interruption, calls not yet started are dropped rather than run unrecorded.
            pool.shutdown(wait=True, cancel_futures=True)
        return failed

    failed = read(batches, "")
    if failed and retry_wait_s > 0:
        progress(f"{len(failed)} batches failed; retrying them once in {retry_wait_s}s")
        time.sleep(retry_wait_s)
        totals["batches_retried"] = len(failed)
        failed = read(failed, "retry ")
    totals["batches_failed"] = len(failed)
    totals["lens"] = lens["id"]
    return totals


# ---- command line ----------------------------------------------------------

def _first_seen(unit: dict) -> tuple:
    return ((unit.get("context") or {}).get("first_seen", {}).get("time") or "", unit["id"])


def cmd_focused(args) -> int:
    from ..reader_presets import preset
    from ..store import Project
    from . import sampling
    spec = preset(args.reader)
    frame = ""
    if args.frame == "corpus":
        from ..corpora import wiki
        frame = wiki.reader_frame()
    with Project(args.project) as project:
        if args.sample:
            units = [u for u in sampling.units_of(project, args.sample) if u is not None]
        else:
            kinds = {"post", "text"} if args.kind == "prose" else {args.kind}
            units = [u for u in project.records("unit") if u["unit_kind"] in kinds]
        units.sort(key=_first_seen)
        if args.limit:
            units = units[:args.limit]
        print(f"{args.reader}: applying codebook {args.codebook} to {len(units)} units in batches of {args.batch}")
        # An empty --types names no type and is refused by run; it does not mean every code.
        types = None if args.types is None else tuple(t.strip() for t in args.types.split(",") if t.strip())
        totals = run(project, units, spec, args.codebook, frame=frame, batch_size=args.batch,
                     parallel=args.parallel, retry_wait_s=args.retry_wait, instructions=args.instructions,
                     code_types=types)
    print(json.dumps(totals, indent=2))
    return 0 if not totals["batches_failed"] else 1


def register_cli(sub) -> None:
    p = sub.add_parser("focused", help="have one reader apply a fixed codebook to units")
    p.add_argument("project")
    p.add_argument("--codebook", required=True, help="ID of a codebook memo")
    p.add_argument("--reader", required=True, help="a preset: opus, sol, muse, opus55, fable, astra")
    which = p.add_mutually_exclusive_group(required=True)
    which.add_argument("--sample", default=None, help="ID of a sample memo")
    which.add_argument("--kind", choices=["post", "text", "prose"], default=None,
                       help="every unit of this kind; 'prose' is post and text")
    p.add_argument("--batch", type=int, default=25)
    p.add_argument("--parallel", type=int, default=3)
    p.add_argument("--limit", type=int, default=0, help="read only the first N units in order of first appearance")
    p.add_argument("--frame", choices=["corpus", "none"], default="corpus",
                   help="'none' tells the reader nothing about the material (a comparison lens)")
    p.add_argument("--retry-wait", type=float, default=0,
                   help="seconds to wait before retrying failed batches once; 0 does not retry")
    p.add_argument("--instructions", choices=sorted(VERSIONS), default="v0",
                   help="the version of the instructions the reader is given; each version is its own lens")
    p.add_argument("--types", default=None,
                   help="comma-separated code types to give the reader, e.g. 'analytic'; default is every code")
    p.set_defaults(func=cmd_focused)
