"""Mechanical verification.

`verify` checks a project's internal integrity and its declared provenance:

  - every source still hashes to the bytes it was stored with;
  - every content-addressed record still has the ID its content gives it;
  - every anchor resolves byte-exact in its source, inside its unit;
  - every assertion names a lens as its author and an activity by that lens;
  - every reference between records points at a record of the right kind;
  - the stored prompt and response of each model call match the hashes the
    ledger recorded for them.

Two limits, stated plainly:

  - It does not show that any interpretation is right.
  - It cannot show who really wrote a record. Someone who rewrites a whole
    project consistently can make it verify. The `digest` it prints is a
    fingerprint of the ledger; publishing or signing that fingerprint somewhere
    outside the project (a commit, a tag, a post) is what makes later
    tampering detectable.

Each record's validity depends only on its own content, the records it names,
and its source's bytes, so this is one linear pass.
"""

from __future__ import annotations

import time

from .anchors import check
from .ids import sha256_hex
from .store import CONTENT_ADDRESSED, KIND_BY_PREFIX, PREFIX, Project, body_of, record_id

# field -> kinds a reference in that field may point at (None = any record)
REFS = {
    "source": {"derived_from": {"source"}},
    "unit": {"source": {"source"}},
    "activity": {"lens": {"lens"}, "used": None},
    "code": {"lens": {"lens"}, "supersedes": {"code"}, "merges": {"code"}},
    "coding": {"unit": {"unit"}, "code": {"code"}},
    "memo": {"about": None},
    "claim": {"supports": None, "counters": None, "rivals": {"claim"}},
    "judgment": {"target": None},
}


def _kind(rid) -> str | None:
    return KIND_BY_PREFIX.get(str(rid).split(":", 1)[0])


def _refs(value) -> list:
    if value is None:
        return []
    return [value] if isinstance(value, (str, dict)) else list(value)


def ledger_digest(project: Project) -> str:
    """One fingerprint for the frame and every ledger file, in a fixed order."""
    parts = [sha256_hex((project.root / "hermeneutic.json").read_bytes())]
    for kind in PREFIX:
        path = project.ledger_path(kind)
        parts.append(sha256_hex(path.read_bytes()) if path.exists() else "-")
    return sha256_hex("\n".join(parts).encode("ascii"))


def verify(project: Project) -> dict:
    t0 = time.time()
    errors: list[tuple[str, str]] = []
    warnings: list[tuple[str, str]] = []
    counts = {kind: len(project.records(kind)) for kind in PREFIX}

    def bad(rid: str, why: str) -> None:
        errors.append((rid, why))

    for rid in project.conflicts:
        bad(rid, "id appears twice in the ledger with different content")

    # ---- identity: content-addressed records keep the ID their content gives
    for kind in CONTENT_ADDRESSED:
        for rec in project.records(kind):
            if record_id(kind, body_of(rec)) != rec["id"]:
                bad(rec["id"], "record id does not match its content")

    # ---- lenses: every prompt part is on disk and hashes to its name ----
    for lens in project.records("lens"):
        for part in lens.get("stack", []):
            path = project.part_path(part["sha256"])
            if not path.exists():
                bad(lens["id"], f"prompt part missing: {part['name']}")
            elif sha256_hex(path.read_bytes()) != part["sha256"]:
                bad(lens["id"], f"prompt part altered: {part['name']}")

    # ---- envelope: authored by a lens, inside an activity by that lens --
    for kind in PREFIX:
        for rec in project.records(kind):
            author = project.get(rec["by"]) if _kind(rec["by"]) == "lens" else None
            if author is None:
                bad(rec["id"], f"author is not a known lens: {rec['by']}")
            if kind == "lens":
                continue
            activity = project.get(rec["activity"]) if _kind(rec["activity"]) == "activity" else None
            if activity is None:
                bad(rec["id"], f"activity never recorded: {rec['activity']}")
            elif activity["lens"] != rec["by"]:
                bad(rec["id"], "author differs from the lens of its activity")

    # ---- references point at records of the right kind ------------------
    for kind, fields in REFS.items():
        for rec in project.records(kind):
            for field, allowed in fields.items():
                for ref in _refs(rec.get(field)):
                    if isinstance(ref, dict):
                        continue  # an inline anchor; checked below
                    target = project.get(ref)
                    if target is None:
                        bad(rec["id"], f"{field} names an unknown record: {ref}")
                    elif allowed is not None and target["kind"] not in allowed:
                        bad(rec["id"], f"{field} names a {target['kind']}, not a {' or '.join(sorted(allowed))}: {ref}")
    for code in project.records("code"):
        if code.get("lens") and code["lens"] != code["by"]:
            bad(code["id"], "code's lens differs from its author")

    # ---- sources: bytes on disk equal the bytes that were stored --------
    blob_ok: dict[str, bool | None] = {}
    for src in project.records("source"):
        sha = src["blob"]
        if sha not in blob_ok:
            path = project.blob_path(sha)
            blob_ok[sha] = (sha256_hex(path.read_bytes()) == sha) if path.exists() else None
        if blob_ok[sha] is None:
            bad(src["id"], "source bytes missing")
        elif not blob_ok[sha]:
            bad(src["id"], "source bytes altered")

    def source_data(source_id) -> bytes | None:
        src = project.get(source_id) if _kind(source_id) == "source" else None
        if src is None or not blob_ok.get(src["blob"]):
            return None
        return project.blob(src["blob"])

    # ---- units: spans lie inside their source and decode cleanly --------
    for unit in project.records("unit"):
        data = source_data(unit["source"])
        if data is None:
            bad(unit["id"], f"unit points at unusable source: {unit['source']}")
            continue
        if not (0 <= unit["start"] < unit["end"] <= len(data)):
            bad(unit["id"], "unit span outside source")
            continue
        try:
            data[unit["start"]:unit["end"]].decode("utf-8")
        except UnicodeDecodeError:
            bad(unit["id"], "unit span splits a character")

    anchors_checked = 0

    def check_anchor(rid: str, anchor: dict, unit: dict | None = None, what: str = "anchor") -> None:
        nonlocal anchors_checked
        anchors_checked += 1
        data = source_data(anchor.get("source", ""))
        if data is None:
            bad(rid, f"{what} points at unusable source")
            return
        if unit is not None:
            if anchor["source"] != unit["source"]:
                bad(rid, f"{what} and unit are in different sources")
            elif not (unit["start"] <= anchor.get("start", -1) < anchor.get("end", -1) <= unit["end"]):
                bad(rid, f"{what} lies outside its unit")
        if not check(data, anchor):
            bad(rid, f"{what} text does not match source bytes")

    # ---- every anchor, wherever it sits ----------------------------------
    for code in project.records("code"):
        if code.get("origin_anchor"):
            check_anchor(code["id"], code["origin_anchor"], what="origin anchor")
    for coding in project.records("coding"):
        unit = project.get(coding["unit"]) if _kind(coding["unit"]) == "unit" else None
        check_anchor(coding["id"], coding["anchor"], unit)
        if coding.get("term_anchor"):
            check_anchor(coding["id"], coding["term_anchor"], unit, what="term anchor")
    for kind in ("memo", "claim", "judgment"):
        for rec in project.records(kind):
            for field in REFS[kind]:
                for ref in _refs(rec.get(field)):
                    if isinstance(ref, dict):
                        check_anchor(rec["id"], ref)

    # ---- claims and judgments -------------------------------------------
    for claim in project.records("claim"):
        if not claim.get("supports"):
            bad(claim["id"], "claim has no supporting evidence")
    for judgment in project.records("judgment"):
        author = project.get(judgment["by"])
        if author is not None and author.get("reader", {}).get("kind") != "human":
            bad(judgment["id"], "judgment not authored by a human lens")

    # ---- model calls: what was sent and what came back ------------------
    runs_checked = 0
    for activity in project.records("activity"):
        call = activity.get("call") or {}
        if not call.get("prompt_sha256"):
            continue
        run_dir = project.root / "runs" / activity["id"].replace(":", "-")
        system, user, response = run_dir / "system.txt", run_dir / "user.txt", run_dir / "response.txt"
        if not (system.exists() and user.exists() and response.exists()):
            warnings.append((activity["id"], "raw prompt and response are not present"))
            continue
        runs_checked += 1
        # Bytes, not text: reading as text would rewrite carriage returns.
        if sha256_hex(system.read_bytes() + b"\n\n" + user.read_bytes()) != call["prompt_sha256"]:
            bad(activity["id"], "stored prompt does not match the recorded hash")
        if sha256_hex(response.read_bytes()) != call.get("response_sha256"):
            bad(activity["id"], "stored response does not match the recorded hash")

    return {
        "ok": not errors,
        "counts": counts,
        "blobs": len(blob_ok),
        "anchors_checked": anchors_checked,
        "runs_checked": runs_checked,
        "n_errors": len(errors),
        "errors": errors[:200],
        "n_warnings": len(warnings),
        "warnings": warnings[:50],
        "digest": ledger_digest(project),
        "seconds": round(time.time() - t0, 2),
        "shows": "internal integrity and declared provenance",
        "does_not_show": "that any interpretation is right, or who really wrote a record",
    }
