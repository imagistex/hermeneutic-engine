"""Corpus adapter: agent posts on DSEWiki, as published at collusion.wiki.

The publisher's export holds every saved revision of every page, with the full
text each revision saved. This adapter keeps those texts byte for byte and
adds one thing of its own: a segmentation of pages into statements, each
anchored at the revision where it first appears and carrying the dates it was
last present and first absent.

That segmentation is this adapter's construction, not the publisher's, and the
project frame says so.
"""

from __future__ import annotations

import gzip
import json
import re
import urllib.request
from collections import defaultdict
from pathlib import Path

from ..ids import sha256_hex
from ..lens import Activity, code_lens
from ..store import Project

ADAPTER = "collusion-wiki"
COMPONENT = "hermeneutic_engine.corpora.wiki"
BASE_URL = "https://collusion.wiki/explorer/download/"
FILES = ("revisions.jsonl.gz", "pages.jsonl.gz", "events.jsonl.gz", "labels.jsonl.gz", "manifest.json.gz")

# A line that ends "-- SomeName" (optionally "-- SomeName?") is a signed post.
SIGNATURE = re.compile(r"--\s*([A-Za-z][A-Za-z0-9_]{2,47})\s*(\?)?\s*$")
HEADING = re.compile(r"^=+.*=+$")
LINK_START = re.compile(r"^(?:[*#:]+\s*)?\[?https?://|^\[\[[^\]]*\]\]$")
LISTISH = re.compile(r"^(?:[*#:]+\s*)?\[?https?://|^[*#]+\s|^\{?\||^\[\[[^\]]*\]\]$")
_SPACE = b" \t\r\x0b\x0c"


# ---- download -------------------------------------------------------------

def fetch(data_dir, force: bool = False) -> dict:
    """Download the publisher's files. The public repo never re-hosts them."""
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    for name in FILES:
        path = data_dir / name
        if path.exists() and not force:
            continue
        request = urllib.request.Request(BASE_URL + name, headers={"User-Agent": "hermeneutic-engine"})
        with urllib.request.urlopen(request, timeout=120) as response:
            path.write_bytes(response.read())
    return _downloads(data_dir)


def _downloads(data_dir: Path) -> dict:
    return {name: {"sha256": sha256_hex((data_dir / name).read_bytes()), "bytes": (data_dir / name).stat().st_size}
            for name in FILES if (data_dir / name).exists()}


def _rows(path: Path):
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)


# ---- corpus constitution ---------------------------------------------------

def frame(data_dir) -> dict:
    """What this corpus is, where it came from, and what it cannot show."""
    data_dir = Path(data_dir)
    with gzip.open(data_dir / "manifest.json.gz", "rt", encoding="utf-8") as fh:
        manifest = json.load(fh)
    return {
        "name": "DSEWiki agent posts (collusion.wiki export)",
        "corpus": {
            "adapter": ADAPTER,
            "origin": "public export at https://collusion.wiki/explorer/download",
            "upstream": {
                "generated_at": manifest.get("generated_at"),
                "db_sha256": manifest.get("db_sha256"),
                "cut": manifest.get("cut"),
            },
            "downloads": _downloads(data_dir),
            "selection": ("Every revision the publisher holds with a write date on or after the cut. "
                          "Selected and redacted by the publisher, not by the analyst."),
            "fielding_relationship": "inherited archive; the analyst did not collect it and cannot add to it",
            "authors": {"attributed_family": "openai", "attributed_by": "collusion.wiki", "verified_by_us": False},
            "known_limits": [
                "Only what was written to the wiki is here; no private reasoning.",
                "Names are self-chosen labels: one agent may use many, and many agents may share one.",
                "Pages or revisions the publisher did not capture are absent, and absence here is not evidence that something was never written.",
                "The segmentation into statements is this adapter's construction, not the publisher's.",
            ],
        },
    }


def reader_frame() -> str:
    """What a reader is told about this material. It is hashed into the lens.

    It deliberately does not say which lab's models wrote the posts. We know
    the attribution; the readers are not told it.
    """
    from importlib import resources
    return resources.files("hermeneutic_engine.corpora").joinpath("wiki-frame.md").read_text(encoding="utf-8")


def pilot_strata() -> list[dict]:
    """A 200-unit pilot: signed posts across the three periods in which they
    occur, plus a smaller draw of unsigned prose from before and during."""
    return [
        {"label": "posts, Jun 12 to 17", "unit_kind": "post", "from": "2026-06-12", "before": "2026-06-18", "n": 80},
        {"label": "posts, Jun 18", "unit_kind": "post", "from": "2026-06-18", "before": "2026-06-19", "n": 20},
        {"label": "posts, Jun 19 to 22", "unit_kind": "post", "from": "2026-06-19", "before": "2026-06-23", "n": 70},
        {"label": "unsigned prose, before Jun 12", "unit_kind": "text", "before": "2026-06-12", "min_bytes": 60, "n": 8},
        {"label": "unsigned prose, Jun 12 to 17", "unit_kind": "text", "from": "2026-06-12", "before": "2026-06-18",
         "min_bytes": 60, "n": 8},
        {"label": "unsigned prose, Jun 18", "unit_kind": "text", "from": "2026-06-18", "before": "2026-06-19",
         "min_bytes": 60, "n": 7},
        {"label": "unsigned prose, Jun 19 onward", "unit_kind": "text", "from": "2026-06-19", "min_bytes": 60, "n": 7},
    ]


# ---- segmentation ----------------------------------------------------------

def classify(line: str) -> str:
    if HEADING.match(line):
        return "heading"
    # A bulleted line that ends in a signature is a post, not a list item.
    # A line that opens with a link stays a list item even if it is signed.
    if SIGNATURE.search(line) and not LINK_START.search(line):
        return "signed"
    if LISTISH.search(line):
        return "list"
    return "plain"


def segment(raw: bytes) -> list[dict]:
    """Split one revision's UTF-8 bytes into statements with byte spans.

    Blank lines separate blocks. Inside a block: a heading is its own unit;
    consecutive link or list lines merge into one `list`; a signed line closes
    a `post` that also takes any unsigned prose lines directly above it; prose
    left unsigned at the end of a block is a `text`.
    """
    spans: list[tuple[int, int]] = []
    pos = 0
    for chunk in raw.split(b"\n"):
        start, end = pos, pos + len(chunk)
        pos = end + 1
        while start < end and raw[start] in _SPACE:
            start += 1
        while end > start and raw[end - 1] in _SPACE:
            end -= 1
        spans.append((start, end))

    units: list[dict] = []
    prose: list[tuple[int, int]] = []
    listing: list[tuple[int, int]] = []

    def emit(lines, kind, signature=None, uncertain=False):
        start, end = lines[0][0], lines[-1][1]
        units.append({
            "start": start, "end": end, "kind": kind, "lines": len(lines),
            "text": raw[start:end].decode("utf-8"),
            "signature": signature, "signature_uncertain": uncertain,
        })

    def flush_prose():
        if prose:
            emit(prose, "text")
            prose.clear()

    def flush_listing():
        if listing:
            emit(listing, "list")
            listing.clear()

    for start, end in spans:
        if start == end:
            flush_prose()
            flush_listing()
            continue
        line = raw[start:end].decode("utf-8")
        kind = classify(line)
        if kind == "heading":
            flush_prose()
            flush_listing()
            emit([(start, end)], "heading")
        elif kind == "list":
            flush_prose()
            listing.append((start, end))
        elif kind == "signed":
            flush_listing()
            if line[0] in "*#:":  # a bulleted post stands alone
                flush_prose()
            prose.append((start, end))
            found = SIGNATURE.search(line)
            emit(prose, "post", signature=found.group(1), uncertain=bool(found.group(2)))
            prose.clear()
        else:
            flush_listing()
            prose.append((start, end))
    flush_prose()
    flush_listing()
    return units


def follow(revisions: list[list[dict]]) -> list[dict]:
    """Follow statements through a page's revisions, oldest first.

    `revisions` holds the segmented pieces of each revision. A statement is
    identified by its kind and exact text. Each entry says where the statement
    was first seen, every revision it was present in, the first revision it was
    absent from, whether it came back, and what followed its last appearance.
    A statement that appears twice in one revision is one statement; the
    number of copies at its first appearance is kept.
    """
    table: dict[tuple[str, str], dict] = {}
    for index, pieces in enumerate(revisions):
        copies: dict[tuple[str, str], int] = defaultdict(int)
        for piece in pieces:
            copies[(piece["kind"], piece["text"])] += 1
        for piece in pieces:
            key = (piece["kind"], piece["text"])
            entry = table.get(key)
            if entry is None:
                table[key] = {"piece": piece, "present": [index], "copies_at_first": copies[key]}
            elif entry["present"][-1] != index:
                entry["present"].append(index)
    total = len(revisions)
    for entry in table.values():
        present = entry["present"]
        held = set(present)
        first, last = present[0], present[-1]
        first_absent = next((i for i in range(first + 1, total) if i not in held), None)
        entry.update(first=first, last=last, first_absent=first_absent,
                     returned=first_absent is not None and last > first_absent,
                     gone_after=last + 1 if last + 1 < total else None)
    return list(table.values())


# ---- ingest ----------------------------------------------------------------

def ingest(project: Project, data_dir) -> dict:
    data_dir = Path(data_dir)
    lens = code_lens(project, COMPONENT, "ingest")
    activity = Activity(project, "ingest", lens["id"])
    by, act = lens["id"], activity.id

    pages = {row["page_id"]: row for row in _rows(data_dir / "pages.jsonl.gz")}
    deletions: dict[str, list[str]] = defaultdict(list)
    for event in _rows(data_dir / "events.jsonl.gz"):
        if event.get("event_type") == "delete" and event.get("page_key") and event.get("time"):
            deletions[event["page_key"]].append(event["time"])
    for times in deletions.values():
        times.sort()

    by_page: dict[str, list[dict]] = defaultdict(list)
    for row in _rows(data_dir / "revisions.jsonl.gz"):
        by_page[row["page_id"]].append(row)

    stats = {"revisions": 0, "sources": 0, "derived_sources": 0, "hash_mismatches": 0, "undecodable": 0,
             "units": defaultdict(int)}

    for page_id in sorted(by_page):
        revisions = sorted(by_page[page_id], key=lambda r: int(r["seq"]))
        page = pages.get(page_id, {})
        held = []  # (source_id, utf8_bytes, revision_row)

        for row in revisions:
            stats["revisions"] += 1
            # The export stores each body as its original bytes read as
            # latin-1, so encoding back to latin-1 recovers those bytes exactly.
            original = (row.get("body") or "").encode("latin-1")
            if sha256_hex(original) != row.get("body_sha256"):
                stats["hash_mismatches"] += 1
                project.append("failure", {"reason": "upstream hash mismatch", "attempted": {"upstream_id": row["rev_id"]}},
                               by=by, activity=act)
                continue
            encoding = "latin-1" if row.get("body_encoding") == "latin1" else "utf-8"
            context = {
                "wiki": row.get("wiki"), "page": page_id, "seq": int(row["seq"]),
                "label": row.get("label") or None, "ip16": row.get("ip16"),
                "time": row.get("time"), "time_grade": row.get("time_grade"),
                "uncertainty_seconds": row.get("uncertainty_seconds"),
                "change_summary": row.get("change_summary") or None,
                "relation_type": row.get("relation_type"),
                "page_family": page.get("page_family"),
            }
            source = project.append("source", {
                "blob": project.put_blob(original), "bytes": len(original), "encoding": encoding,
                "media_type": "text/x-wiki",
                "origin": {"adapter": ADAPTER, "upstream_id": row["rev_id"], "upstream_sha256": row["body_sha256"]},
                "derived_from": None, "derivation": None, "context": context,
            }, by=by, activity=act)
            stats["sources"] += 1
            text_bytes = original
            if encoding != "utf-8":
                # Anchors need UTF-8. The original stays as received; the
                # readable copy is a derivative that names its parent.
                text_bytes = original.decode(encoding).encode("utf-8")
                source = project.append("source", {
                    "blob": project.put_blob(text_bytes), "bytes": len(text_bytes), "encoding": "utf-8",
                    "media_type": "text/x-wiki",
                    "origin": {"adapter": ADAPTER, "upstream_id": row["rev_id"], "upstream_sha256": row["body_sha256"]},
                    "derived_from": source["id"],
                    "derivation": {"kind": "transcode", "from": encoding, "to": "utf-8",
                                   "producer": {"actor_type": "tool", "actor_id": COMPONENT}},
                    "context": context,
                }, by=by, activity=act)
                stats["derived_sources"] += 1
            try:
                text_bytes.decode("utf-8")
            except UnicodeDecodeError:
                stats["undecodable"] += 1
                project.append("failure", {"reason": "source is not valid UTF-8; kept but not segmented",
                                           "attempted": {"source": source["id"]}}, by=by, activity=act)
                continue
            held.append((source["id"], text_bytes, row))

        # Follow each statement through the page's history. The unit is
        # anchored in the revision where the statement first appears.
        def at(index):
            if index is None:
                return None
            row = held[index][2]
            return {"seq": int(row["seq"]), "time": row.get("time"), "by": row.get("label") or None}

        page_key = page.get("page_key")
        for entry in follow([segment(text_bytes) for _, text_bytes, _ in held]):
            piece = entry["piece"]
            first = held[entry["first"]][2]
            last = held[entry["last"]][2]
            deleted_after = next((t for t in deletions.get(page_key, []) if t >= (first.get("time") or "")), None)
            project.append("unit", {
                "source": held[entry["first"]][0], "start": piece["start"], "end": piece["end"],
                "unit_kind": piece["kind"],
                "context": {
                    "page": page_id, "wiki": first.get("wiki"), "page_family": page.get("page_family"),
                    "text_sha256": sha256_hex(piece["text"].encode("utf-8")), "lines": piece["lines"],
                    "introduced_by": first.get("label") or None,
                    "signature": piece["signature"], "signature_uncertain": piece["signature_uncertain"],
                    "first_seen": {"seq": int(first["seq"]), "time": first.get("time"), "time_grade": first.get("time_grade")},
                    "last_seen": {"seq": int(last["seq"]), "time": last.get("time")},
                    "first_removed_in": at(entry["first_absent"]),
                    "returned_after_removal": entry["returned"],
                    "finally_removed_in": at(entry["gone_after"]),
                    "revisions_present": len(entry["present"]),
                    "copies_in_first_revision": entry["copies_at_first"],
                    "in_last_revision": entry["gone_after"] is None,
                    "page_deleted_after": deleted_after,
                },
            }, by=by, activity=act)
            stats["units"][piece["kind"]] += 1

    stats["units"] = dict(stats["units"])
    activity.finish(adapter=ADAPTER, counts=stats)
    return stats
