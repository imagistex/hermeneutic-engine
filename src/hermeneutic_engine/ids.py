"""Hashing, identifiers and timestamps.

IDs are the SHA-256 of sorted-key JSON. This is deliberately simple and is not
strict canonical JSON (RFC 8785). Hashed content must avoid floats so that the
simple form stays stable across languages.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone


def canonical(obj) -> bytes:
    """Sorted-key, compact, UTF-8 JSON."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def content_id(prefix: str, obj) -> str:
    """An ID derived from content, so writing the same thing twice is harmless."""
    return f"{prefix}:{sha256_hex(canonical(obj))[:20]}"


def event_id(prefix: str) -> str:
    """An ID for something that happened once (a reading, a judgment)."""
    return f"{prefix}:{uuid.uuid4().hex[:20]}"


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
