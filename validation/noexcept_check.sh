#!/usr/bin/env bash
# Checks the noexcept of the hash of the answer sets on the real data, against
# the same code without it (tag phase5-cached-hash). On the split variant:
#   1. time on the queries with answer sets and at least one million answers
#      (validation/queries/answer-set-wikidata.tsv);
#   2. peak memory on queries that reach the limit, one process per query so
#      that each peak can be told apart
#      (validation/queries/peak-memory-wikidata.tsv).
#
# Usage: validation/noexcept_check.sh <dataset-dir> --index-from DIR [options]
#   <dataset-dir>      the BFS dataset, e.g. /data/wikidata.bfs
#   --index-from DIR   the work directory of a previous run with a split index,
#                      e.g. validation-out-600/work/wikidata.bfs
#   --sdsl-root DIR    install prefix of the extended SDSL (default: $HOME)
#   --out DIR          working directory (default: ./validation-out-noexcept)
#   --reps N           runs of step 1 (default: 2)
#   --skip-memory      skip step 2
#   --queries FILE     queries of step 1 (default: the file above)
#   --memory-queries FILE  queries of step 2 (default: the file above)
#
# Send: <out>/results-noexcept-check.tar.gz
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="$PWD/validation-out-noexcept"
SDSL_ROOT="$HOME"
INDEX_FROM=""
REPS=2
MEMORY=1
QUERIES="$REPO/validation/queries/answer-set-wikidata.tsv"
MEMORY_QUERIES="$REPO/validation/queries/peak-memory-wikidata.tsv"
DATASET_DIR=""

while [ $# -gt 0 ]; do
    case "$1" in
        --index-from) INDEX_FROM="$2"; shift 2 ;;
        --sdsl-root) SDSL_ROOT="$2"; shift 2 ;;
        --out) OUT="$2"; shift 2 ;;
        --reps) REPS="$2"; shift 2 ;;
        --skip-memory) MEMORY=0; shift ;;
        --queries) QUERIES="$2"; shift 2 ;;
        --memory-queries) MEMORY_QUERIES="$2"; shift 2 ;;
        -h|--help) sed -n '2,22p' "$0"; exit 0 ;;
        -*) echo "unknown option: $1" >&2; exit 1 ;;
        *) DATASET_DIR="$1"; shift ;;
    esac
done

log() { echo "[noexcept-check $(date +%H:%M:%S)] $*"; }
die() { echo "[noexcept-check] ERROR: $*" >&2; exit 1; }

[ -n "$DATASET_DIR" ] && [ -d "$DATASET_DIR" ] || die "usage: $0 <dataset-dir> --index-from DIR [options] (see --help)"
[ -n "$INDEX_FROM" ] && [ -d "$INDEX_FROM/index-split" ] || die "--index-from must be a directory with index-split"
DATASET_DIR="$(cd "$DATASET_DIR" && pwd)"
NAME="$(basename "$DATASET_DIR")"
INDEX="$(cd "$INDEX_FROM/index-split" && pwd -P)"
mkdir -p "$OUT"
OUT="$(cd "$OUT" && pwd)"

log "1/3 building both versions and running the tests"
"$REPO/validation/build.sh" --out "$OUT" --sdsl-root "$SDSL_ROOT" --baseline phase5-cached-hash
mkdir -p "$OUT/work/$NAME"
ln -sfn "$INDEX" "$OUT/work/$NAME/index-split"

log "2/3 time ($REPS runs)"
"$REPO/validation/run.sh" "$DATASET_DIR" --out "$OUT" --variants split \
    --queries "$QUERIES" --reps "$REPS" --reuse-indexes

if [ "$MEMORY" = 1 ]; then
    log "3/3 peak memory, one process per query"
    mkdir -p "$OUT/memory-queries"
    i=0
    while IFS= read -r line || [ -n "$line" ]; do
        i=$((i + 1))
        printf '%s\n' "$line" > "$OUT/memory-queries/memory-$i.tsv"
        "$REPO/validation/run.sh" "$DATASET_DIR" --out "$OUT" --variants split \
            --queries "$OUT/memory-queries/memory-$i.tsv" --reps 1 --reuse-indexes
    done < "$MEMORY_QUERIES"
fi

(cd "$OUT" && tar -czf results-noexcept-check.tar.gz results)
log "done. Send: $OUT/results-noexcept-check.tar.gz"
