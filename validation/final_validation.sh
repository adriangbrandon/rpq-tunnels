#!/usr/bin/env bash
# Repeats the whole validation on Wikidata with the final version, reusing the
# indexes and the times of the original code of a previous run (validation/run.sh
# with validation-out-60 and validation-out-600): only the candidate runs.
#   Ring    basic on the lexicographic dataset, paths.tsv, limit of 60 s
#   RingA   basic on the BFS dataset, paths.tsv, limit of 60 s
#   RingAB  split on the BFS dataset, paths.tsv, limit of 60 s
#   RingA and RingAB on the 73 complex RPQs, limit of 600 s
# A step that finished is not repeated when the script runs again.
#
# Usage: validation/final_validation.sh <wikidata-dir> <wikidata-bfs-dir> [options]
#   --previous-60 DIR   previous run with the limit of 60 s (default: validation-out-60)
#   --previous-600 DIR  previous run with the limit of 600 s (default: validation-out-600)
#   --sdsl-root DIR     install prefix of the extended SDSL (default: $HOME)
#   --out DIR           working directory (default: ./validation-out-final)
#   --reps N            runs of the candidate (default: 1)
#   --skip-complex      skip the 73 complex RPQs
#   --queries-60 FILE   queries with the limit of 60 s (default: data/wikidata/paths.tsv)
#   --queries-600 FILE  queries with the limit of 600 s (default: data/wikidata/paths-split-73.tsv)
#
# Send: <out>/results-final.tar.gz
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="$PWD/validation-out-final"
PREVIOUS_60="$PWD/validation-out-60"
PREVIOUS_600="$PWD/validation-out-600"
SDSL_ROOT="$HOME"
REPS=1
COMPLEX=1
QUERIES_60="$REPO/data/wikidata/paths.tsv"
QUERIES_600="$REPO/data/wikidata/paths-split-73.tsv"
DATASETS=()

while [ $# -gt 0 ]; do
    case "$1" in
        --previous-60) PREVIOUS_60="$2"; shift 2 ;;
        --previous-600) PREVIOUS_600="$2"; shift 2 ;;
        --sdsl-root) SDSL_ROOT="$2"; shift 2 ;;
        --out) OUT="$2"; shift 2 ;;
        --reps) REPS="$2"; shift 2 ;;
        --skip-complex) COMPLEX=0; shift ;;
        --queries-60) QUERIES_60="$2"; shift 2 ;;
        --queries-600) QUERIES_600="$2"; shift 2 ;;
        -h|--help) sed -n '2,22p' "$0"; exit 0 ;;
        -*) echo "unknown option: $1" >&2; exit 1 ;;
        *) DATASETS+=("$1"); shift ;;
    esac
done

log() { echo "[final $(date +%H:%M:%S)] $*"; }
die() { echo "[final] ERROR: $*" >&2; exit 1; }

[ "${#DATASETS[@]}" = 2 ] && [ -d "${DATASETS[0]}" ] && [ -d "${DATASETS[1]}" ] \
    || die "usage: $0 <wikidata-dir> <wikidata-bfs-dir> [options] (see --help)"
LEX="$(cd "${DATASETS[0]}" && pwd)"
BFS="$(cd "${DATASETS[1]}" && pwd)"
LEX_NAME="$(basename "$LEX")"
BFS_NAME="$(basename "$BFS")"

# Everything that is needed from the previous runs, before spending any time.
require() { [ -e "$1" ] || die "$1 not found"; }
require "$PREVIOUS_60/work/$LEX_NAME/index-basic"
require "$PREVIOUS_60/work/$BFS_NAME/index-basic"
require "$PREVIOUS_60/work/$BFS_NAME/index-split"
Q60="$(basename "$QUERIES_60" .tsv)"
Q600="$(basename "$QUERIES_600" .tsv)"
require "$PREVIOUS_60/results/$LEX_NAME/digests-basic-$Q60-baseline.txt"
require "$PREVIOUS_60/results/$BFS_NAME/digests-basic-$Q60-baseline.txt"
require "$PREVIOUS_60/results/$BFS_NAME/digests-split-$Q60-baseline.txt"
if [ "$COMPLEX" = 1 ]; then
    require "$PREVIOUS_600/results/$BFS_NAME/digests-basic-$Q600-baseline.txt"
    require "$PREVIOUS_600/results/$BFS_NAME/digests-split-$Q600-baseline.txt"
fi

mkdir -p "$OUT"
OUT="$(cd "$OUT" && pwd)"

step_done() { [ -f "$OUT/.done-$1" ]; }
mark_done() { : > "$OUT/.done-$1"; }

# Links the index of a previous run and copies the times of the original code.
reuse() {  # <out> <previous> <name> <variant> <query-name>
    local res="$1/results/$3" work="$1/work/$3" index
    index="$(cd "$2/work/$3/index-$4" && pwd -P)"
    mkdir -p "$res" "$work"
    ln -sfn "$index" "$work/index-$4"
    cp "$2/results/$3/queries-$4-$5-baseline-run"*.* "$2/results/$3/digests-$4-$5-baseline.txt" "$res/"
    echo "$4 $5: original code from $2/results/$3 ($(awk '$1 == "baseline:" {print $2, $3}' "$2/results/$3/build-info.txt"))" \
        >> "$res/baseline-source.txt"
}

run_step() {  # <limit> <dataset-dir> <variant> <queries> <previous>
    local out="$OUT/$1" name qname
    name="$(basename "$2")"
    qname="$(basename "$4" .tsv)"
    if step_done "$1-$name-$3-$qname"; then log "already done: $name / $3 / $qname"; return; fi
    log "$name / $3 / $qname, limit of $1 s ($REPS runs)"
    reuse "$out" "$5" "$name" "$3" "$qname"
    "$REPO/validation/run.sh" "$2" --out "$out" --variants "$3" --queries "$4" \
        --reps "$REPS" --reuse-indexes --candidate-only
    mark_done "$1-$name-$3-$qname"
}

if ! step_done build; then
    log "building, with the tests"
    "$REPO/validation/build.sh" --out "$OUT/60" --sdsl-root "$SDSL_ROOT" --timeout 60
    [ "$COMPLEX" = 0 ] || "$REPO/validation/build.sh" --out "$OUT/600" --sdsl-root "$SDSL_ROOT" --skip-tests
    mark_done build
fi

run_step 60 "$LEX" basic "$QUERIES_60" "$PREVIOUS_60"
run_step 60 "$BFS" basic "$QUERIES_60" "$PREVIOUS_60"
run_step 60 "$BFS" split "$QUERIES_60" "$PREVIOUS_60"
if [ "$COMPLEX" = 1 ]; then
    run_step 600 "$BFS" basic "$QUERIES_600" "$PREVIOUS_600"
    run_step 600 "$BFS" split "$QUERIES_600" "$PREVIOUS_600"
fi

(cd "$OUT" && tar -czf results-final.tar.gz 60/results $( [ "$COMPLEX" = 0 ] || echo 600/results ))
log "done. Send: $OUT/results-final.tar.gz"
