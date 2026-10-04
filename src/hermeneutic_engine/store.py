"""The project store: immutable source bytes and an append-only ledger.

A project is a directory:

    hermeneutic.json   frame: question, corpus constitution, schema version
    sources/           immutable source bytes, one file per SHA-256
    lenses/            exact bytes of every prompt part a lens was built from
    ledger/            append-only JSONL, one file per record kind
    runs/              raw prompt and response for every model call
    views/             derived and rebuildable; never authoritative

Nothing in the ledger is edited. A correction is a new record that names the
one it supersedes, and the old record stays readable.

One process writes to a project at a time. The first write takes a lock; a
second writer is refused instead of being allowed to interleave.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from . import SCHEMA_VERSION
from .ids import content_id, event_id, sha256_hex, utc_now

try:  # advisory locking is POSIX-only; elsewhere the single-writer rule is on the caller
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None

PREFIX = {
    "source": "src",
    "unit": "unit",
    "lens": "lens",
    "activity": "act",
    "code": "code",
    "coding": "cdg",
    "memo": "memo",
    "claim": "clm",
    "judgment": "jdg",
    "failure": "fail",
}
KIND_BY_PREFIX = {v: k for k, v in PREFIX.items()}

# These kinds get their ID from their content, so writing one twice is harmless.
CONTENT_ADDRESSED = frozenset({"source", "unit", "lens", "code"})

# A unit is identified by where it is, not by what an adapter currently knows
# about it. Its context can be recomputed later without making a second unit.
IDENTITY_FIELDS = {"unit": ("source", "start", "end", "unit_kind")}

# Envelope fields the kernel sets on every record.
RESERVED = frozenset({"id", "kind", "v", "at", "by", "activity"})

FRAME_FILE = "hermeneutic.json"


class LedgerError(Exception):
    """A write the ledger refuses."""


def body_of(record: dict) -> dict:
    return {k: v for k, v in record.items() if k not in RESERVED}


def record_id(kind: str, body: dict) -> str:
    """The ID a content-addressed record must have."""
    fields = IDENTITY_FIELDS.get(kind)
    identity = {k: body[k] for k in fields} if fields else body
    return content_id(PREFIX[kind], identity)


class Project:
    def __init__(self, root):
        self.root = Path(root)
        if not (self.root / FRAME_FILE).exists():
            raise LedgerError(f"not a hermeneutic-engine project: {self.root}")
        self._records: dict[str, dict[str, dict]] = {}
        self._handles: dict[str, object] = {}
        self._blob_cache: dict[str, bytes] = {}
        self._lock = None
        self.conflicts: list[str] = []  # IDs that appear twice in a ledger file with different content

    # ---- creation -------------------------------------------------------

    @classmethod
    def init(cls, root, frame: dict | None = None) -> "Project":
        root = Path(root)
        if (root / FRAME_FILE).exists():
            raise LedgerError(f"project already exists: {root}")
        for sub in ("sources", "lenses", "ledger", "runs", "views"):
            (root / sub).mkdir(parents=True, exist_ok=True)
        doc = {"schema": SCHEMA_VERSION, "created": utc_now()}
        doc.update(frame or {})
        (root / FRAME_FILE).write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return cls(root)

    @property
    def frame(self) -> dict:
        return json.loads((self.root / FRAME_FILE).read_text(encoding="utf-8"))

    # ---- immutable bytes ------------------------------------------------

    def _put(self, folder: str, data: bytes, suffix: str = "") -> str:
        sha = sha256_hex(data)
        path = self.root / folder / sha[:2] / f"{sha}{suffix}"
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
            tmp.write_bytes(data)
            os.replace(tmp, path)
        return sha

    def blob_path(self, sha: str) -> Path:
        return self.root / "sources" / sha[:2] / sha

    def put_blob(self, data: bytes) -> str:
        """Store source bytes exactly as received. Returns their SHA-256."""
        return self._put("sources", data)

    def blob(self, sha: str) -> bytes:
        data = self._blob_cache.get(sha)
        if data is None:
            data = self.blob_path(sha).read_bytes()
            if len(self._blob_cache) > 20000:
                self._blob_cache.clear()
            self._blob_cache[sha] = data
        return data

    def part_path(self, sha: str) -> Path:
        return self.root / "lenses" / sha[:2] / f"{sha}.txt"

    def put_part(self, data: bytes) -> str:
        """Store the exact bytes of one prompt part a lens is built from."""
        return self._put("lenses", data, ".txt")

    # ---- ledger ---------------------------------------------------------

    def ledger_path(self, kind: str) -> Path:
        return self.root / "ledger" / f"{kind}.jsonl"

    def _load(self, kind: str) -> dict[str, dict]:
        records = self._records.get(kind)
        if records is None:
            records = {}
            path = self.ledger_path(kind)
            if path.exists():
                with open(path, "r", encoding="utf-8", newline="\n") as fh:
                    for line in fh:
                        if not line.strip():
                            continue
                        rec = json.loads(line)
                        earlier = records.get(rec["id"])
                        if earlier is not None and earlier != rec:
                            self.conflicts.append(rec["id"])
                            continue  # the first one written stands
                        records[rec["id"]] = rec
            self._records[kind] = records
        return records

    def _acquire_lock(self) -> None:
        if self._lock is not None or fcntl is None:
            return
        handle = open(self.root / "ledger" / ".lock", "w")
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            raise LedgerError(f"another process is writing to {self.root}") from None
        self._lock = handle

    def _handle(self, kind: str):
        fh = self._handles.get(kind)
        if fh is None:
            self._acquire_lock()
            fh = open(self.ledger_path(kind), "a", encoding="utf-8", newline="\n")
            self._handles[kind] = fh
        return fh

    def records(self, kind: str) -> list[dict]:
        if kind not in PREFIX:
            raise LedgerError(f"unknown record kind: {kind}")
        return list(self._load(kind).values())

    def get(self, rid: str) -> dict | None:
        kind = KIND_BY_PREFIX.get(str(rid).split(":", 1)[0])
        if kind is None:
            return None
        return self._load(kind).get(rid)

    def append(self, kind: str, body: dict, *, by: str, activity: str,
               rid: str | None = None, at: str | None = None) -> dict:
        """Append one record. Content-addressed kinds are idempotent."""
        if kind not in PREFIX:
            raise LedgerError(f"unknown record kind: {kind}")
        clash = RESERVED & body.keys()
        if clash:
            raise LedgerError(f"body uses reserved fields: {sorted(clash)}")
        self._acquire_lock()  # before reading, so the check below cannot race another writer
        records = self._load(kind)
        if kind in CONTENT_ADDRESSED:
            computed = record_id(kind, body)
            if rid is not None and rid != computed:
                raise LedgerError(f"id {rid} does not match content (expected {computed})")
            rid = computed
            if rid in records:
                return records[rid]
        else:
            rid = rid or event_id(PREFIX[kind])
            if rid in records:
                raise LedgerError(f"duplicate id: {rid}")
        rec = {"id": rid, "kind": kind, "v": SCHEMA_VERSION, "at": at or utc_now(), "by": by, "activity": activity}
        rec.update(body)
        fh = self._handle(kind)
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        fh.flush()
        records[rid] = rec
        return rec

    def close(self) -> None:
        for fh in self._handles.values():
            fh.close()
        self._handles.clear()
        if self._lock is not None:
            self._lock.close()
            self._lock = None

    def __enter__(self) -> "Project":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ---- conveniences ---------------------------------------------------

    def source_bytes(self, source_id: str) -> bytes:
        rec = self.get(source_id)
        if rec is None:
            raise LedgerError(f"unknown source: {source_id}")
        return self.blob(rec["blob"])

    def unit_text(self, unit: dict) -> str:
        return self.source_bytes(unit["source"])[unit["start"]:unit["end"]].decode("utf-8")
