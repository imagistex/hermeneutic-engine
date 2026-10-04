"""Command line for hermeneutic-engine."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from .store import PREFIX, FRAME_FILE, Project
from .verify import verify


def cmd_init(args) -> int:
    Project.init(args.project, {"name": args.name, "question": args.question})
    print(f"created {args.project}")
    return 0


def cmd_verify(args) -> int:
    with Project(args.project) as project:
        report = verify(project)
    counts = ", ".join(f"{n} {kind}" for kind, n in report["counts"].items() if n)
    print(f"records: {counts}")
    print(f"source files hashed: {report['blobs']}; anchors checked: {report['anchors_checked']}; "
          f"model calls checked: {report['runs_checked']}; {report['seconds']}s")
    print(f"ledger digest: {report['digest']}")
    if report["n_warnings"]:
        print(f"{report['n_warnings']} warnings, e.g. {report['warnings'][0][0]}: {report['warnings'][0][1]}")
    if report["ok"]:
        print(f"OK. This shows {report['shows']}. It does not show {report['does_not_show']}.")
        return 0
    print(f"FAILED: {report['n_errors']} problems")
    for rid, why in report["errors"][:40]:
        print(f"  {rid}: {why}")
    return 1


def cmd_stats(args) -> int:
    with Project(args.project) as project:
        for kind in PREFIX:
            records = project.records(kind)
            if not records:
                continue
            line = f"{kind}: {len(records)}"
            if kind == "unit":
                line += "  " + json.dumps(dict(Counter(r["unit_kind"] for r in records).most_common()))
            print(line)
    return 0


def cmd_wiki(args) -> int:
    from .corpora import wiki
    if args.action == "fetch":
        for name, info in wiki.fetch(args.data, force=args.force).items():
            print(f"{info['sha256'][:16]}  {info['bytes']:>9}  {name}")
        return 0
    if args.action == "sample":
        from .methods import sampling
        with Project(args.project) as project:
            memo, units = sampling.stratified(project, name=args.name, seed=args.seed, strata=wiki.pilot_strata())
        print(memo["body"])
        print(f"\nsample memo: {memo['id']}")
        return 0
    root = Path(args.project)
    if (root / FRAME_FILE).exists():
        project = Project(root)
    else:
        frame = wiki.frame(args.data)
        frame["question"] = args.question
        project = Project.init(root, frame)
    with project:
        stats = wiki.ingest(project, args.data)
    print(json.dumps(stats, indent=2))
    return 0


def _frame_text(args) -> str:
    if args.frame == "none":
        return ""
    if args.frame_file:
        return Path(args.frame_file).read_text(encoding="utf-8")
    from .corpora import wiki
    return wiki.reader_frame()


def cmd_read(args) -> int:
    from .methods import open_coding, sampling
    from .reader_presets import preset
    spec = preset(args.reader)
    with Project(args.project) as project:
        units = sampling.units_of(project, args.sample)
        if args.limit:
            units = sorted(units, key=lambda u: u["id"])[:args.limit]
        print(f"{args.reader}: reading {len(units)} units in batches of {args.batch}")
        totals = open_coding.run(project, units, spec, frame=_frame_text(args), batch_size=args.batch,
                                 parallel=args.parallel, seed=args.seed, instructions=args.instructions)
    print(json.dumps(totals, indent=2))
    return 0 if not totals["batches_failed"] else 1


def cmd_view(args) -> int:
    from .views import pilot
    with Project(args.project) as project:
        text = pilot.render(project, args.sample, show=args.show)
    out = Path(args.out) if args.out else Path(args.project) / "views" / "pilot.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(f"wrote {out} ({len(text):,} characters)")
    return 0


def cmd_lexicon(args) -> int:
    from .views import lexicon
    terms = lexicon.parse_terms(Path(args.terms).read_text(encoding="utf-8"))
    with Project(args.project) as project:
        results = lexicon.trace(project, terms)
    out = Path(args.out) if args.out else Path(args.project) / "views" / "lexicon.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(lexicon.render(results, title=args.title), encoding="utf-8")
    (Path(args.project) / "views").mkdir(exist_ok=True)
    slim = [{k: v for k, v in r.items() if k != "first"} | ({"first": {k: v for k, v in r["first"].items() if k not in ("text", "span")}}
            if r.get("first") else {}) for r in results]
    (Path(args.project) / "views" / "lexicon.json").write_text(json.dumps(slim, indent=1, ensure_ascii=False), encoding="utf-8")
    present = sum(1 for r in results if r["units"])
    print(f"{present} terms found, {len(results) - present} absent; wrote {out}")
    return 0


def cmd_concordance(args) -> int:
    from .views import concordance
    with Project(args.project) as project:
        text = concordance.render(project, Path(args.terms).read_text(encoding="utf-8"), args.title, args.per_term)
    Path(args.out).write_text(text, encoding="utf-8")
    print(f"wrote {args.out} ({len(text):,} characters)")
    return 0


def cmd_names(args) -> int:
    from .views import names
    with Project(args.project) as project:
        text = names.render(project, args.title)
    Path(args.out).write_text(text, encoding="utf-8")
    print(f"wrote {args.out} ({len(text):,} characters)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hermeneutic", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="create an empty project")
    p.add_argument("project")
    p.add_argument("--name", default=None)
    p.add_argument("--question", default=None)
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("verify", help="check bytes, anchors, lineage and authorship")
    p.add_argument("project")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("stats", help="count records")
    p.add_argument("project")
    p.set_defaults(func=cmd_stats)

    p = sub.add_parser("wiki", help="the collusion.wiki corpus adapter")
    wiki_sub = p.add_subparsers(dest="action", required=True)
    f = wiki_sub.add_parser("fetch", help="download the publisher's files")
    f.add_argument("data")
    f.add_argument("--force", action="store_true")
    f.set_defaults(func=cmd_wiki)
    i = wiki_sub.add_parser("ingest", help="store revisions as sources and statements as units")
    i.add_argument("project")
    i.add_argument("data")
    i.add_argument("--question", default=None)
    i.set_defaults(func=cmd_wiki)
    s = wiki_sub.add_parser("sample", help="draw the seeded, stratified pilot sample")
    s.add_argument("project")
    s.add_argument("--name", default="pilot")
    s.add_argument("--seed", type=int, default=20261003)
    s.set_defaults(func=cmd_wiki)

    p = sub.add_parser("read", help="have one reader open-code a sample")
    p.add_argument("project")
    p.add_argument("--sample", required=True, help="ID of a sample memo")
    p.add_argument("--reader", required=True, help="a preset: opus, sol, muse, opus55, fable, astra")
    p.add_argument("--batch", type=int, default=20)
    p.add_argument("--parallel", type=int, default=3)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--limit", type=int, default=0, help="read only the first N units (for a smoke test)")
    p.add_argument("--instructions", choices=["0", "1"], default="0",
                   help="version of the coding instructions (1 follows the pilot review)")
    p.add_argument("--frame", choices=["corpus", "none"], default="corpus",
                   help="'none' tells the reader nothing about the material (a comparison lens)")
    p.add_argument("--frame-file", default=None, help="text telling readers what the material is")
    p.set_defaults(func=cmd_read)

    p = sub.add_parser("lexicon", help="trace the writers' own terms through the corpus")
    p.add_argument("project")
    p.add_argument("--terms", required=True, help="a file of terms, one per line")
    p.add_argument("--out", default=None)
    p.add_argument("--title", default="In vivo lexicon")
    p.set_defaults(func=cmd_lexicon)

    p = sub.add_parser("concordance", help="list every use of each term in its line of context")
    p.add_argument("project")
    p.add_argument("--terms", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--title", default="Concordance")
    p.add_argument("--per-term", type=int, default=40)
    p.set_defaults(func=cmd_concordance)

    p = sub.add_parser("names", help="survey the names writers chose and how names are used")
    p.add_argument("project")
    p.add_argument("--out", required=True)
    p.add_argument("--title", default="Names")
    p.set_defaults(func=cmd_names)

    p = sub.add_parser("view", help="render a review page")
    p.add_argument("which", choices=["pilot"])
    p.add_argument("--show", default=None, help="'all', or an instructions version; default is the latest")
    p.add_argument("project")
    p.add_argument("--sample", required=True)
    p.add_argument("--out", default=None)
    p.set_defaults(func=cmd_view)

    # Method adapters bring their own commands.
    for module in ('consolidate', 'focused_coding', 'board'):
        try:
            adapter = __import__(f"hermeneutic_engine.methods.{module}", fromlist=["register_cli"])
        except ImportError:
            continue
        if hasattr(adapter, "register_cli"):
            adapter.register_cli(sub)
    for module in ("divergence", "reader_page", "names_content"):
        try:
            view = __import__(f"hermeneutic_engine.views.{module}", fromlist=["register_cli"])
        except ImportError:
            continue
        if hasattr(view, "register_cli"):
            view.register_cli(sub)

    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
