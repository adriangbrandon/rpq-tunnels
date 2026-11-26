#!/usr/bin/env bash
# Finds out whether the hash causes the slowdown of the disjunctions on RingAB
# (validation/queries/answer-set-disjunctions-wikidata.tsv), with the same code
# and only the hash changed (tag phase5-xor-hash). On the split variant:
#   1. XOR against 31a+b;
#   2. the original code against XOR: what the rest of the refactoring costs.
#
# Usage: validation/disjunction_check.sh <dataset-dir> --index-from DIR [options]
#   <dataset-dir>      the BFS dataset, e.g. /data/wikidata.bfs
#   --index-from DIR   the work directory of a previous run with a split index,
#                      e.g. validation-out-600/work/wikidata.bfs
#   --sdsl-root DIR    install prefix of the extended SDSL (default: $HOME)
#   --out DIR          working directory (default: ./validation-out-disjunctions)
#   --reps N           runs of step 1 (default: 3; step 2 does one)
#   --skip-original    skip step 2
#   --queries FILE     queries (default: the file above)
#
# Send: <out>/results-disjunction-check.tar.gz
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="$PWD/validation-out-disjunctions"
SDSL_ROOT="$HOME"
INDEX_FROM=""
REPS=3
ORIGINAL=1
QUERIES="$REPO/validation/queries/answer-set-disjunctions-wikidata.tsv"
DATASET_DIR=""

while [ $# -gt 0 ]; do
    case "$1" in
        --index-from) INDEX_FROM="$2"; shift 2 ;;
        --sdsl-root) SDSL_ROOT="$2"; shift 2 ;;
        --out) OUT="$2"; shift 2 ;;
        --reps) REPS="$2"; shift 2 ;;
        --skip-original) ORIGINAL=0; shift ;;
        --queries) QUERIES="$2"; shift 2 ;;
        -h|--help) sed -n '2,19p' "$0"; exit 0 ;;
        -*) echo "unknown option: $1" >&2; exit 1 ;;
        *) DATASET_DIR="$1"; shift ;;
    esac
done

log() { echo "[disjunction-check $(date +%H:%M:%S)] $*"; }
die() { echo "[disjunction-check] ERROR: $*" >&2; exit 1; }

[ -n "$DATASET_DIR" ] && [ -d "$DATASET_DIR" ] || die "usage: $0 <dataset-dir> --index-from DIR [options] (see --help)"
[ -n "$INDEX_FROM" ] && [ -d "$INDEX_FROM/index-split" ] || die "--index-from must be a directory with index-split"
DATASET_DIR="$(cd "$DATASET_DIR" && pwd)"
NAME="$(basename "$DATASET_DIR")"
INDEX="$(cd "$INDEX_FROM/index-split" && pwd -P)"
mkdir -p "$OUT"
OUT="$(cd "$OUT" && pwd)"

compare() {  # <out-dir> <baseline-ref> <candidate-ref> <reps>
    "$REPO/validation/build.sh" --out "$1" --sdsl-root "$SDSL_ROOT" \
        --baseline "$2" --candidate "$3" --skip-tests
    mkdir -p "$1/work/$NAME"
    ln -sfn "$INDEX" "$1/work/$NAME/index-split"
    "$REPO/validation/run.sh" "$DATASET_DIR" --out "$1" --variants split \
        --queries "$QUERIES" --reps "$4" --reuse-indexes
}

log "1/2 XOR against 31a+b ($REPS runs)"
compare "$OUT/hash" phase5-xor-hash HEAD "$REPS"
RESULTS=(hash/results)

if [ "$ORIGINAL" = 1 ]; then
    log "2/2 the original code against XOR (1 run)"
    compare "$OUT/original" phase0-baseline phase5-xor-hash 1
    RESULTS+=(original/results)
fi

(cd "$OUT" && tar -czf results-disjunction-check.tar.gz "${RESULTS[@]}")
log "done. Send: $OUT/results-disjunction-check.tar.gz"
