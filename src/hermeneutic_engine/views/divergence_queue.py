"""The divergence queue for the lenses of the run, and no others.

    PYTHONPATH=src python3 -m hermeneutic_engine.views.divergence_queue work/wiki --codebook MEMO --out DIR [--top 60]

Writes DIR/divergence-queue.md.

The engine's divergence view compares every focused-coding lens on a codebook.
Its `render` takes `lens_ids`, which the command line does not offer. This
wrapper finds the lenses of the run (run_lenses.py) and calls that view with
exactly those. The page is the view's own, untouched; when the run is not
complete, one status line saying so is put above it.

Reads the ledger and writes only to DIR. Makes no model call.
"""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import argparse
import time
from pathlib import Path

from hermeneutic_engine.views.run_lenses import EXPECT, Run, open_project
from hermeneutic_engine.views import divergence

NAME = "divergence-queue"


def page(project, run: Run, top: int = 60) -> str:
    text = divergence.render(project, run.codebook, top=top, lens_ids=run.lens_ids)
    status = run.status_line()
    return (f"> {status}\n\n" if status else "") + text


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="The engine's divergence view, for the lenses of the run only.")
    parser.add_argument("project")
    parser.add_argument("--codebook", required=True, help="ID of the codebook memo the run applied")
    parser.add_argument("--out", required=True, help=f"a directory; writes {NAME}.md there")
    parser.add_argument("--top", type=int, default=60, help="how many of the most divergent units to show (default 60)")
    parser.add_argument("--expect", type=int, default=EXPECT, help="how many readers a whole run has (default 3)")
    args = parser.parse_args(argv)
    project = open_project(args.project)
    run = Run(project, args.codebook, expect=args.expect)
    if not run.readers:
        print(f"no lens of the run is in the ledger for codebook {args.codebook}", file=sys.stderr)
        return 2
    started = time.time()
    text = page(project, run, args.top)
    seconds = time.time() - started
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    target = out / f"{NAME}.md"
    target.write_bytes(text.encode("utf-8"))
    if run.status_line():
        print(run.status_line().replace("**", ""))
    print(f"divergence view over {len(run.lens_ids)} lenses ({', '.join(run.lens_ids)}) took {seconds:.1f}s")
    print(f"wrote {target} ({len(text):,} characters)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
