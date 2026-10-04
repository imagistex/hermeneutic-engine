#!/bin/zsh
# The morning kit: turn a finished overnight run into pages a researcher can read.
#
#   zsh scripts/morning.sh OUTDIR [--anyway] [--page-out FILE] [--site-out DIR]
#
# OUTDIR takes the pages (in the morning, the vault folder). The steps run in
# order. Each logs its start, its end and its exit status, and a step that
# fails does not stop the ones after it. Everything printed is also kept in
# OUTDIR/morning-kit.log.
#
#   1  the run log must end with "=== all lanes ended ... ===" (or pass --anyway)
#   2  verify
#   3  the readers of the run        -> OUTDIR/run-readers.md and .json
#   4  the reader page               -> work/wiki/views/reader.html (or --page-out FILE)
#   5  names and content             -> OUTDIR/2026-10-03-names-and-content.md
#   6  the divergence queue          -> OUTDIR/divergence-queue.md
#      passages with no place        -> OUTDIR/passages-with-no-place.md and .json
#      words that stood out          -> OUTDIR/words-that-stood-out.md and .json
#   7  the public site, if site-builder/build_site.py is there -> site/ (or --site-out DIR)
#   8  reconcile: the two pages of step 6 counted again from the raw ledger
#
# Step 5 writes over the page of that name if OUTDIR already has one; the page
# says of itself that it is overwritten on each run. MK_NAMES_PAGE names another.
#
# --page-out and --site-out are for a rehearsal while the run is still going:
# they keep a partial reader page and a partial site out of their usual places.
#
# Nothing here writes to the ledger, sources, lenses or runs of the project, and
# nothing calls a model. Python is told not to write bytecode, so importing the
# engine leaves nothing new under src/.

set -u
zmodload zsh/datetime

usage() {
  print -u2 -- "usage: zsh scripts/morning.sh OUTDIR [--anyway] [--page-out FILE] [--site-out DIR]"
  exit 2
}

OUTDIR="" ANYWAY=0 PAGE_OUT="" SITE_OUT=""
while (( $# > 0 )); do
  case "$1" in
    --anyway) ANYWAY=1 ;;
    --page-out) (( $# > 1 )) || usage; PAGE_OUT="${2:a}"; shift ;;
    --site-out) (( $# > 1 )) || usage; SITE_OUT="${2:a}"; shift ;;
    -h|--help) usage ;;
    -*) print -u2 -- "unknown option: $1"; usage ;;
    *) [[ -z "$OUTDIR" ]] || usage; OUTDIR="${1:a}" ;;
  esac
  shift
done
[[ -n "$OUTDIR" ]] || usage

# Paths given on the command line were made absolute above, before this.
cd "${0:A:h}/.." || exit 2

# What the run was. Each can be set from the environment (the tests do).
PROJECT="${MK_PROJECT:-work/wiki}"
RUN_LOG="${MK_RUN_LOG:-$PROJECT/runs/full-run.log}"
CODEBOOK="${MK_CODEBOOK:?Set MK_CODEBOOK to a memo ID in your project}"
SAMPLE="${MK_SAMPLE:?Set MK_SAMPLE to a memo ID in your project}"
TERMS="${MK_TERMS:-codebooks/wiki-terms-v0.txt}"
NAMES_PAGE="${MK_NAMES_PAGE:-2026-10-03-names-and-content.md}"
SITE_BUILDER="${MK_SITE_BUILDER:-site-builder/build_site.py}"
SITE_COPY="${MK_SITE_COPY:-site-builder/site.json}"
PY="${MK_PYTHON:-python3}"
: "${PAGE_OUT:=$PROJECT/views/reader.html}"
: "${SITE_OUT:=site}"

export PYTHONPATH="src${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONDONTWRITEBYTECODE=1

mkdir -p "$OUTDIR" || exit 2

typeset -a STEP_NAMES STEP_STATUS STEP_SECONDS

# step NAME COMMAND...: run one step, log its start, end and status, and go on.
step() {
  local name="$1"
  shift
  local t0=$EPOCHREALTIME st dt
  print -r -- "=== $name: started $(date '+%Y-%m-%d %H:%M:%S') ==="
  "$@"
  st=$?
  dt="$(printf '%.1f' $(( EPOCHREALTIME - t0 )))"
  print -r -- "=== $name: ended $(date '+%Y-%m-%d %H:%M:%S') with status $st after ${dt}s ==="
  print
  STEP_NAMES+=("$name")
  STEP_STATUS+=("$st")
  STEP_SECONDS+=("$dt")
  return 0
}

check_log() {
  if [[ ! -r "$RUN_LOG" ]]; then
    print -r -- "the run log is not there: $RUN_LOG"
    return 1
  fi
  local last
  last="$(grep -v '^[[:space:]]*$' "$RUN_LOG" | tail -n 1)"
  if [[ "$last" == "=== all lanes ended "*" ===" ]]; then
    print -r -- "the run log ends as it should: $last"
    return 0
  fi
  print -r -- "the run log does not end with the 'all lanes ended' line. Its last line is:"
  print -r -- "    $last"
  return 1
}

do_verify() {
  local tmp st
  tmp="$(mktemp -t morning-kit-verify)" || return 1
  "$PY" -m hermeneutic_engine verify "$PROJECT" > "$tmp" 2>&1
  st=$?
  if (( st == 0 )); then tail -n 4 "$tmp"; else tail -n 45 "$tmp"; fi
  rm -f "$tmp"
  return $st
}

main() {
  print -r -- "morning kit, $(date '+%Y-%m-%d %H:%M:%S %Z')"
  print -r -- "project $PROJECT, codebook $CODEBOOK, pages to $OUTDIR"
  print

  step "1 run log" check_log
  if (( STEP_STATUS[-1] != 0 )); then
    if (( ! ANYWAY )); then
      print -r -- "Stopping here: the lanes have not all ended. Pass --anyway to make the pages from what the ledger holds now."
      return 2
    fi
    print -r -- "--anyway: going on. The pages will say which readers are not complete."
    print
  fi
  local lanes
  lanes="$(pgrep -f 'hermeneutic_engine focused' | tr '\n' ' ')"
  if [[ -n "$lanes" ]]; then
    print -r -- "Note: a lane is still running and writing to the ledger (pid ${lanes% }). A step of the engine's own may catch a half-written line; running it again fixes that."
    print
  fi

  step "2 verify" do_verify
  step "3 readers of the run" "$PY" -m hermeneutic_engine.views.run_lenses "$PROJECT" --codebook "$CODEBOOK" --out "$OUTDIR"
  step "4 reader page" "$PY" -m hermeneutic_engine page "$PROJECT" --terms "$TERMS" --out "$PAGE_OUT" \
    --sample "$SAMPLE" --codebook "$CODEBOOK"
  step "5 names and content" "$PY" -m hermeneutic_engine names-content "$PROJECT" --terms "$TERMS" \
    --out "$OUTDIR/$NAMES_PAGE" --codebook "$CODEBOOK"
  step "6a divergence queue" "$PY" -m hermeneutic_engine.views.divergence_queue "$PROJECT" --codebook "$CODEBOOK" --out "$OUTDIR"
  step "6b passages with no place" "$PY" -m hermeneutic_engine.views.candidates "$PROJECT" --codebook "$CODEBOOK" --out "$OUTDIR"
  step "6c words that stood out" "$PY" -m hermeneutic_engine.views.standout "$PROJECT" --codebook "$CODEBOOK" --out "$OUTDIR"
  if [[ -f "$SITE_BUILDER" ]]; then
    step "7 public site" "$PY" "$SITE_BUILDER" "$PROJECT" --copy "$SITE_COPY" --out "$SITE_OUT"
  else
    print -r -- "7 public site: skipped, $SITE_BUILDER is not there"
    print
  fi
  step "8 reconcile" "$PY" -m hermeneutic_engine.views.reconcile "$PROJECT" --out "$OUTDIR"

  print -r -- "=== summary ==="
  local n failed=0
  for (( n = 1; n <= ${#STEP_NAMES}; n++ )); do
    printf '  status %s  %7ss  %s\n' "${STEP_STATUS[n]}" "${STEP_SECONDS[n]}" "${STEP_NAMES[n]}"
    if (( n > 1 && STEP_STATUS[n] != 0 )); then failed=$(( failed + 1 )); fi
  done
  if (( STEP_STATUS[1] != 0 )); then
    print -r -- "The run log did not show that all lanes had ended (step 1); --anyway was given."
  fi
  "$PY" -m hermeneutic_engine.views.run_lenses "$PROJECT" --codebook "$CODEBOOK" --status
  if (( failed > 0 )); then
    print -r -- "$failed step(s) after the first ended with a status other than 0."
    return 1
  fi
  print -r -- "Every step after the first ended with status 0."
  return 0
}

main 2>&1 | tee "$OUTDIR/morning-kit.log"
exit ${pipestatus[1]}
