"""Lenses and activities.

A lens says who is reading and how: the reader (a model, a human, or code), the
method, and the exact prompt stack, hashed part by part. Changing any part makes
a new lens, so two readings can always be told apart and compared.

A reading is either reproducible (a headless reader with a pinned stack) or
situated (a person, or a model in open conversation). The lens records which.
"""

from __future__ import annotations

from . import __version__
from .ids import content_id, event_id, utc_now
from .store import PREFIX, Project


def make_lens(project: Project, *, reader: dict, method: dict, stack: list[tuple[str, str, str]] = (),
              theory: str = "withheld", priors: list[str] = (), reproducible: bool = True) -> dict:
    """Declare a lens. `stack` is an ordered list of (role, name, text).

    `theory` says whether an analytic theory was given to the reader
    ("withheld", or the name of a declared facet). No reading is free of
    priors, so `priors` lists the ones this lens knowingly carries: what the
    instructions direct attention to and what the reader was told about the
    material.
    """
    parts = []
    for role, name, text in stack:
        data = text.encode("utf-8")
        parts.append({"role": role, "name": name, "sha256": project.put_part(data), "bytes": len(data)})
    body = {
        "reader": reader,
        "method": method,
        "stack": parts,
        "theory": theory,
        "priors": list(priors),
        "reproducible": reproducible,
    }
    lens_id = content_id(PREFIX["lens"], body)
    # A lens declares itself: it is its own author and needs no activity.
    return project.append("lens", body, by=lens_id, activity="declared", rid=lens_id)


def code_lens(project: Project, component: str, method: str) -> dict:
    """The lens for deterministic work done by this library (ingest, counting)."""
    return make_lens(
        project,
        reader={"kind": "code", "id": component, "version": __version__},
        method={"name": method, "version": "0"},
        theory="none",
        reproducible=True,
    )


def human_lens(project: Project, name: str, method: str = "reading") -> dict:
    return make_lens(
        project,
        reader={"kind": "human", "id": name},
        method={"name": method, "version": "0"},
        theory="situated",
        reproducible=False,
    )


class Activity:
    """One act of ingesting, reading, verifying or judging.

    The ID exists from the start so records can point at it; the activity
    record itself is appended when the work ends. `verify` reports any record
    whose activity never got written, which is what a crash looks like.
    """

    def __init__(self, project: Project, kind: str, lens_id: str, used=None):
        self.project = project
        self.kind = kind
        self.lens_id = lens_id
        self.used = used or []
        self.id = event_id(PREFIX["activity"])
        self.started = utc_now()

    def finish(self, status: str = "ok", **extra) -> dict:
        body = {
            "type": self.kind,
            "lens": self.lens_id,
            "used": self.used,
            "started": self.started,
            "ended": utc_now(),
            "status": status,
        }
        body.update(extra)
        return self.project.append("activity", body, by=self.lens_id, activity=self.id, rid=self.id)
