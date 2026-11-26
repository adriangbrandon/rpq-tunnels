#!/usr/bin/env bash
# Checks the hash of the answer sets (31a+b) on the real data, with the queries
# that showed the regression of RingAB: those that use the answer set and have
# at least one million answers (validation/queries/answer-set-wikidata.tsv).
# All of them run on the split variant with a limit of 600 s per query:
#   1. against the original code (XOR), with digests of the results;
#   2. against the previous refactored version (mixed hash, tag phase5-mixed-hash);
#   3. peak memory on three queries that reach the limit, against the original.
#
# Usage: validation/hash_check.sh <dataset-dir> [options]
#   <dataset-dir>      the BFS dataset, e.g. /data/wikidata.bfs
#   --index-from DIR   reuse an index: the work directory of a previous run,
#                      e.g. validation-out-600/work/wikidata.bfs
#   --sdsl-root DIR    install prefix of the extended SDSL (default: $HOME)
#   --out DIR          working directory (default: ./validation-out-hash)
#   --reps N           runs of step 1 (default: 3; step 2 does one)
#   --skip-mixed       skip step 2
#   --skip-memory      skip step 3
#   --queries FILE     queries of steps 1 and 2 (default: the file above)
#   --memory-queries FILE  queries of step 3
#
# Send: <out>/results-hash-check.tar.gz
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="$PWD/validation-out-hash"
SDSL_ROOT="$HOME"
INDEX_FROM=""
REPS=3
MIXED=1
MEMORY=1
QUERIES="$REPO/validation/queries/answer-set-wikidata.tsv"
MEMORY_QUERIES="$REPO/validation/queries/answer-set-memory-wikidata.tsv"
DATASET_DIR=""

while [ $# -gt 0 ]; do
    case "$1" in
        --index-from) INDEX_FROM="$2"; shift 2 ;;
        --sdsl-root) SDSL_ROOT="$2"; shift 2 ;;
        --out) OUT="$2"; shift 2 ;;
        --reps) REPS="$2"; shift 2 ;;
        --skip-mixed) MIXED=0; shift ;;
        --skip-memory) MEMORY=0; shift ;;
        --queries) QUERIES="$2"; shift 2 ;;
        --memory-queries) MEMORY_QUERIES="$2"; shift 2 ;;
        -h|--help) sed -n '2,23p' "$0"; exit 0 ;;
        -*) echo "unknown option: $1" >&2; exit 1 ;;
        *) DATASET_DIR="$1"; shift ;;
    esac
done

log() { echo "[hash-check $(date +%H:%M:%S)] $*"; }
die() { echo "[hash-check] ERROR: $*" >&2; exit 1; }

[ -n "$DATASET_DIR" ] && [ -d "$DATASET_DIR" ] || die "usage: $0 <dataset-dir> [options] (see --help)"
DATASET_DIR="$(cd "$DATASET_DIR" && pwd)"
NAME="$(basename "$DATASET_DIR")"
mkdir -p "$OUT"
OUT="$(cd "$OUT" && pwd)"
if [ -n "$INDEX_FROM" ]; then
    INDEX_FROM="$(cd "$INDEX_FROM" && pwd)"
    [ -d "$INDEX_FROM/index-split" ] || die "$INDEX_FROM/index-split not found (a split index is needed)"
fi

link_index() {  # <out-dir> <index-dir>
    mkdir -p "$1/work/$NAME"
    ln -sfn "$2" "$1/work/$NAME/index-split"
}

log "1/5 building the original code and the candidate"
"$REPO/validation/build.sh" --out "$OUT/original" --sdsl-root "$SDSL_ROOT"

REUSE=()
if [ -n "$INDEX_FROM" ]; then
    link_index "$OUT/original" "$INDEX_FROM/index-split"
    REUSE=(--reuse-indexes)
fi
log "2/5 candidate against the original code ($REPS runs)"
"$REPO/validation/run.sh" "$DATASET_DIR" --out "$OUT/original" --variants split \
    --queries "$QUERIES" --reps "$REPS" ${REUSE[@]+"${REUSE[@]}"}
INDEX="$(cd "$OUT/original/work/$NAME/index-split" && pwd -P)"

if [ "$MEMORY" = 1 ]; then
    log "3/5 peak memory on queries that reach the limit"
    "$REPO/validation/run.sh" "$DATASET_DIR" --out "$OUT/original" --variants split \
        --queries "$MEMORY_QUERIES" --reps 1 --reuse-indexes
fi

if [ "$MIXED" = 1 ]; then
    log "4/5 building the previous refactored version"
    "$REPO/validation/build.sh" --out "$OUT/mixed" --sdsl-root "$SDSL_ROOT" \
        --baseline phase5-mixed-hash --skip-tests
    link_index "$OUT/mixed" "$INDEX"
    log "5/5 candidate against the mixed hash (1 run)"
    "$REPO/validation/run.sh" "$DATASET_DIR" --out "$OUT/mixed" --variants split \
        --queries "$QUERIES" --reps 1 --reuse-indexes
fi

(cd "$OUT" && tar -czf results-hash-check.tar.gz original/results*/ ${MIXED:+$( [ "$MIXED" = 1 ] && echo mixed/results*/ )})
log "done. Send: $OUT/results-hash-check.tar.gz"
