"""Check the morning pages against the ledger, by a different road.

    python3 -m hermeneutic_engine.views.reconcile work/wiki --out DIR

Reads DIR/passages-with-no-place.{md,json} and DIR/words-that-stood-out.{md,json}
and counts again from the raw ledger files. It shares no code with the scripts
that made the pages and does not import the engine: it reads the JSONL lines
and the stored source bytes itself. For each page it checks that

  - the lenses the page used are exactly the lenses of the run in the ledger;
  - each reader's records (unfit memos, or codings with the code) number what
    the ledger holds for the batches the page saw;
  - the records listed under the passages add up to the same numbers, and each
    says what its ledger record says (its unit, its offsets, the reader's own
    reason and suggested name, or its note);
  - the passages by tier add up to the passages;
  - every passage, and every reader's own quotation, is byte for byte what the
    stored source holds between its offsets;
  - every passage is in the Markdown page, byte for byte.

The ledger may have grown since the page was made. A page records how many
finished read activities it saw for each reader; the count here is over those
same activities (the first that many in the ledger, which only ever grows at
its end).

Prints one line for each check and ends with status 1 if any failed.
"""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import argparse
import json
from collections import Counter
from pathlib import Path

PAGES = (("passages-with-no-place", "unfit memos"), ("words-that-stood-out", "codings"))


def lines_of(root: Path, kind: str):
    """The records of one ledger file. A last line that does not parse is skipped."""
    path = root / "ledger" / f"{kind}.jsonl"
    if not path.exists():
        return
    lines = [line for line in path.read_bytes().split(b"\n") if line.strip()]
    for n, line in enumerate(lines):
        try:
            yield json.loads(line.decode("utf-8"))
        except ValueError:
            if n != len(lines) - 1:
                raise


class Sources:
    def __init__(self, root: Path):
        self.root = root
        self.blob = {rec["id"]: rec["blob"] for rec in lines_of(root, "source")}
        self.cache: dict[str, bytes] = {}

    def bytes_of(self, source_id: str) -> bytes:
        if source_id not in self.cache:
            if len(self.cache) > 2000:
                self.cache.clear()
            sha = self.blob[source_id]
            self.cache[source_id] = (self.root / "sources" / sha[:2] / sha).read_bytes()
        return self.cache[source_id]


def check_page(root: Path, out: Path, name: str, what: str, sources: Sources, say) -> None:
    data = json.loads((out / f"{name}.json").read_text(encoding="utf-8"))
    page = (out / f"{name}.md").read_bytes()
    readers = data["readers"]
    lens_reader = {lid: n for n, reader in enumerate(readers) for lid in reader["lens_ids"]}

    # The lenses of the run, found again from the ledger by what they declare.
    in_ledger = {lens["id"] for lens in lines_of(root, "lens")
                 if (lens.get("reader") or {}).get("codebook") == data["codebook"]
                 and (lens.get("method") or {}).get("name") == "focused-coding"
                 and str((lens.get("method") or {}).get("version")) == "1"
                 and (lens.get("method") or {}).get("code_types") == ["analytic"]}
    say(in_ledger == set(lens_reader), f"{name}: the page's lenses are the run's lenses in the ledger "
        f"({len(lens_reader)} on the page, {len(in_ledger)} in the ledger)")

    # The finished read activities the page saw: the first N of each reader, in ledger order.
    seen, taken = {}, Counter()
    for activity in lines_of(root, "activity"):
        index = lens_reader.get(activity.get("lens"))
        if index is None or activity.get("type") != "read" or activity.get("status") != "ok":
            continue
        if taken[index] < readers[index]["activities_ok"]:
            taken[index] += 1
            seen[activity["id"]] = activity["lens"]
    for index, reader in enumerate(readers):
        say(taken[index] == reader["activities_ok"],
            f"{name}: {reader['label']}: {reader['activities_ok']:,} finished read activities on the page, "
            f"{taken[index]:,} found in the ledger")

    # The records, counted again, and kept so that what the page says of each can be compared.
    counts = Counter()
    ledger = {}  # record id -> (unit, anchor start, anchor end, the reader's own words)
    if what == "unfit memos":
        for memo in lines_of(root, "memo"):
            if memo.get("memo_type") == "unfit" and seen.get(memo.get("activity")) == memo.get("by"):
                counts[lens_reader[memo["by"]]] += 1
                about = memo.get("about") or []
                anchor = next((ref for ref in about if isinstance(ref, dict)), {})
                ledger[memo["id"]] = (next((ref for ref in about if isinstance(ref, str)), None),
                                      anchor.get("start"), anchor.get("end"),
                                      {"reason": memo.get("body") or "", "suggested_name": memo.get("suggested_name") or "",
                                       "quote_not_found": memo.get("quote_not_found")})
    else:
        code_id = data["code"]["id"]
        for coding in lines_of(root, "coding"):
            if coding.get("code") == code_id and seen.get(coding.get("activity")) == coding.get("by"):
                counts[lens_reader[coding["by"]]] += 1
                ledger[coding["id"]] = (coding.get("unit"), coding["anchor"].get("start"), coding["anchor"].get("end"),
                                        {"note": coding.get("note") or ""})
    listed = Counter(entry["reader"] for passage in data["passages"] for entry in passage["entries"])
    differs = 0
    for passage in data["passages"]:
        for entry in passage["entries"]:
            unit, start, end, words = ledger.get(entry["id"], (None, None, None, {}))
            same = unit == passage["unit"] and all(entry.get(key) == value for key, value in words.items())
            if entry.get("exact") is not None:  # an anchor the page quoted: its offsets are the ledger's
                same = same and (start, end) == (entry["start"], entry["end"])
            differs += not same
    say(differs == 0 and len(ledger) == sum(listed.values()),
        f"{name}: every record listed says what its ledger record says (unit, offsets, the reader's own words)"
        + (f" ({differs:,} do not)" if differs else ""))
    for index, reader in enumerate(readers):
        say(counts[index] == reader["entries"] == listed[reader["label"]],
            f"{name}: {reader['label']}: {what} in the ledger {counts[index]:,}, in the page's table "
            f"{reader['entries']:,}, listed under passages {listed[reader['label']]:,}")
    say(sum(counts.values()) == data["totals"]["entries"],
        f"{name}: all readers: {what} in the ledger {sum(counts.values()):,}, on the page {data['totals']['entries']:,}")

    passages = data["passages"]
    by_tier = Counter(passage["tier"] for passage in passages)
    say(len(passages) == data["totals"]["passages"] == sum(row["passages"] for row in data["tiers"])
        and all(by_tier[row["tier"]] == row["passages"] for row in data["tiers"]),
        f"{name}: {len(passages):,} passages listed; the tiers say "
        + ", ".join(f"{row['passages']:,} by {row['name']}" for row in data["tiers"]))
    say(all(passage["tier"] == len({entry["family"] for entry in passage["entries"]}) for passage in passages),
        f"{name}: every passage's tier is the number of families among its records")
    keys = [(-passage["tier"], passage["first_seen"]) for passage in passages]
    say(keys == sorted(keys), f"{name}: the passages stand tier by tier, and inside a tier by day and time first seen")

    # Grouping, worked out again: inside a passage the quotations chain by overlap, a record with no
    # anchor is alone, and between two passages of one post no bytes are shared.
    broken, spans_of = 0, {}
    for passage in passages:
        if not passage["found"]:
            broken += len(passage["entries"]) != 1
            continue
        spans_of.setdefault(passage["unit"], []).append((passage["start"], passage["end"]))
        reach = None
        for start, end in sorted((entry["start"], entry["end"]) for entry in passage["entries"]):
            broken += reach is not None and start >= reach
            reach = end if reach is None else max(reach, end)
    for spans in spans_of.values():
        spans.sort()
        broken += sum(1 for a, b in zip(spans, spans[1:]) if b[0] < a[1])
    say(broken == 0, f"{name}: records are grouped by overlap, and no two passages of one post share a byte"
        + (f" ({broken:,} exceptions)" if broken else ""))

    # Bytes.
    wrong_text = wrong_entry = missing = quoted = 0
    for passage in passages:
        if passage["found"]:
            quoted += 1
            raw = sources.bytes_of(passage["source"])[passage["start"]:passage["end"]]
            wrong_text += raw != passage["text"].encode("utf-8")
            missing += passage["text"].encode("utf-8") not in page
            starts = [entry["start"] for entry in passage["entries"]]
            ends = [entry["end"] for entry in passage["entries"]]
            wrong_text += (min(starts), max(ends)) != (passage["start"], passage["end"])
        for entry in passage["entries"]:
            if entry.get("exact") is not None:
                raw = sources.bytes_of(passage["source"])[entry["start"]:entry["end"]]
                wrong_entry += raw != entry["exact"].encode("utf-8")
            elif str(entry.get("quote_not_found") or "").strip():
                missing += entry["quote_not_found"].encode("utf-8") not in page
    say(wrong_text == 0, f"{name}: {quoted:,} passages are the source bytes between their offsets"
        + (f" ({wrong_text:,} are not)" if wrong_text else ""))
    say(wrong_entry == 0, f"{name}: every reader's own quotation is the source bytes between its offsets"
        + (f" ({wrong_entry:,} are not)" if wrong_entry else ""))
    say(missing == 0, f"{name}: every passage is in the Markdown page byte for byte"
        + (f" ({missing:,} are not)" if missing else ""))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Check the morning pages against the ledger.")
    parser.add_argument("project")
    parser.add_argument("--out", required=True, help="the directory the pages were written to")
    args = parser.parse_args(argv)
    root, out = Path(args.project), Path(args.out)
    failed = []

    def say(ok: bool, text: str) -> None:
        print(("ok    " if ok else "FAIL  ") + text)
        if not ok:
            failed.append(text)

    sources = Sources(root)
    for name, what in PAGES:
        if not (out / f"{name}.json").exists() or not (out / f"{name}.md").exists():
            say(False, f"{name}: the page or its JSON is not in {out}")
            continue
        check_page(root, out, name, what, sources, say)
    print(f"{len(failed)} check{'' if len(failed) == 1 else 's'} failed" if failed else "all checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
