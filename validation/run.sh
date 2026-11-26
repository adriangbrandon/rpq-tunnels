#!/usr/bin/env bash
# Runs the validation on one dataset: builds its indexes and runs the query
# benchmarks of the paper with the baseline and the candidate built by
# validation/build.sh, interleaving both versions.
#
# Usage: validation/run.sh <dataset-dir> [options]
#   <dataset-dir>      directory with one dataset as distributed in Zenodo:
#                      <name>.dat, <name>.dat.SO and <name>.dat.P
#   --out DIR          working directory of build.sh (default: ./validation-out)
#   --name NAME        label of the dataset (default: the directory name)
#   --queries F1,F2    query files (default: chosen by the dataset name:
#                      wikidata -> data/wikidata/paths.tsv,
#                      yago     -> data/yago2s/paths.tsv)
#   --variants V1,V2   basic (Ring / RingA) and/or split (RingAB) (default: both)
#   --reps N           runs of each version and query file (default: 3)
#   --build-both       also build the indexes with the baseline, to compare
#                      construction time and memory and check the files match
#   --keep-solutions   keep the solutions of every query (can be very large);
#                      by default only a digest per query is kept
#   --no-digests       do not compare the results of both versions (times only)
#   --reuse-indexes    use the indexes built by a previous run on this dataset
#                      (e.g. to run another query file); results are added
#   --candidate-only   do not run the baseline: its results must already be in
#                      <out>/results/<name>/ (validation/final_validation.sh)
#
# Results: <out>/results/<name>/ (send the archive <out>/results-<name>.tar.gz)
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="$PWD/validation-out"
NAME=""
QUERIES=""
VARIANTS="basic,split"
REPS=3
BUILD_BOTH=0
KEEP_SOLUTIONS=0
DIGESTS=1
REUSE_INDEXES=0
CANDIDATE_ONLY=0
DATASET_DIR=""

while [ $# -gt 0 ]; do
    case "$1" in
        --out) OUT="$2"; shift 2 ;;
        --name) NAME="$2"; shift 2 ;;
        --queries) QUERIES="$2"; shift 2 ;;
        --variants) VARIANTS="$2"; shift 2 ;;
        --reps) REPS="$2"; shift 2 ;;
        --build-both) BUILD_BOTH=1; shift ;;
        --keep-solutions) KEEP_SOLUTIONS=1; shift ;;
        --reuse-indexes) REUSE_INDEXES=1; shift ;;
        --candidate-only) CANDIDATE_ONLY=1; shift ;;
        --no-digests) DIGESTS=0; shift ;;
        -h|--help) sed -n '2,30p' "$0"; exit 0 ;;
        -*) echo "unknown option: $1" >&2; exit 1 ;;
        *) DATASET_DIR="$1"; shift ;;
    esac
done

log() { echo "[run $(date +%H:%M:%S)] $*"; }
die() { echo "[run] ERROR: $*" >&2; exit 1; }

[ -n "$DATASET_DIR" ] || die "usage: $0 <dataset-dir> [options] (see --help)"
[ -d "$DATASET_DIR" ] || die "$DATASET_DIR is not a directory"
DATASET_DIR="$(cd "$DATASET_DIR" && pwd)"
OUT="$(cd "$OUT" 2>/dev/null && pwd)" || die "$OUT does not exist: run validation/build.sh first"
BUILD="$OUT/build"
for v in baseline candidate; do
    [ -x "$BUILD/$v/query-index-basic" ] || die "$BUILD/$v is missing: run validation/build.sh first"
done

shopt -s nullglob
dats=("$DATASET_DIR"/*.dat)
shopt -u nullglob
[ "${#dats[@]}" = 1 ] || die "expected exactly one .dat file in $DATASET_DIR, found ${#dats[@]}"
DAT="${dats[0]}"
for ext in SO P; do [ -f "$DAT.$ext" ] || die "missing $DAT.$ext"; done
[ -n "$NAME" ] || NAME="$(basename "$DATASET_DIR")"

if [ -z "$QUERIES" ]; then
    case "$(echo "$NAME $(basename "$DAT")" | tr '[:upper:]' '[:lower:]')" in
        *wikidata*) QUERIES="$REPO/data/wikidata/paths.tsv" ;;
        *yago*) QUERIES="$REPO/data/yago2s/paths.tsv" ;;
        *) die "cannot guess the query files from the name '$NAME': use --queries" ;;
    esac
fi
IFS=',' read -r -a QUERY_FILES <<< "$QUERIES"
IFS=',' read -r -a VARIANT_LIST <<< "$VARIANTS"
for q in "${QUERY_FILES[@]}"; do [ -f "$q" ] || die "query file $q not found"; done

RES="$OUT/results/$NAME"
WORK="$OUT/work/$NAME"
mkdir -p "$RES" "$WORK"

# Runs a command recording its elapsed time and peak memory in <prefix>.time
if /usr/bin/time -v true >/dev/null 2>&1; then
    TIME_STYLE=gnu
elif /usr/bin/time -l true >/dev/null 2>&1; then
    TIME_STYLE=bsd
else
    TIME_STYLE=none
fi
measured() {
    local prefix="$1"; shift
    local status=0 start end
    start=$(python3 -c 'import time; print(time.time())')
    case "$TIME_STYLE" in
        gnu) /usr/bin/time -v -o "$prefix.timev" "$@" || status=$? ;;
        bsd) /usr/bin/time -l "$@" 2> "$prefix.timev" || status=$?; cat "$prefix.timev" >&2 ;;
        none) "$@" || status=$? ;;
    esac
    end=$(python3 -c 'import time; print(time.time())')
    {
        echo "exit_status: $status"
        echo "wall_seconds: $(python3 -c "print(round($end - $start, 3))")"
        if [ -f "$prefix.timev" ]; then
            if [ "$TIME_STYLE" = gnu ]; then
                echo "max_rss_kb: $(awk -F': ' '/Maximum resident set size/ {print $2}' "$prefix.timev")"
            else
                echo "max_rss_kb: $(awk '/maximum resident set size/ {print int($1 / 1024)}' "$prefix.timev")"
            fi
        fi
    } > "$prefix.time"
    rm -f "$prefix.timev"
    return 0
}

# --- environment ---------------------------------------------------------------
{
    echo "dataset: $NAME"
    echo "dataset_dir: $DATASET_DIR"
    echo "dat: $(basename "$DAT")"
    echo "triples: $(wc -l < "$DAT")"
    echo "nodes: $(wc -l < "$DAT.SO")"
    echo "predicates: $(wc -l < "$DAT.P")"
    echo "queries: ${QUERY_FILES[*]}"
    echo "variants: ${VARIANT_LIST[*]}"
    echo "reps: $REPS"
    echo "time_style: $TIME_STYLE"
    echo "uname: $(uname -a)"
    if command -v lscpu >/dev/null; then lscpu | grep -E "Model name|^CPU\(s\)|MHz|L3" | sed 's/^/cpu: /'; fi
    if [ -r /proc/meminfo ]; then grep MemTotal /proc/meminfo | sed 's/^/mem: /'; fi
    sed 's/^/build_/' "$BUILD/info.txt"
} > "$RES/environment.new"
# Keep the query files of previous runs on this dataset (--reuse-indexes).
if [ -f "$RES/environment.txt" ]; then
    previous=$(awk -F': ' '$1 == "queries" {print $2}' "$RES/environment.txt")
    for q in $previous; do
        case " ${QUERY_FILES[*]} " in *" $q "*) ;; *) sed -i.bak "s|^queries: .*|& $q|" "$RES/environment.new"; rm -f "$RES/environment.new.bak" ;; esac
    done
fi
mv "$RES/environment.new" "$RES/environment.txt"
cp "$BUILD/info.txt" "$RES/build-info.txt"

# --- indexes ---------------------------------------------------------------------
build_index() {  # <version> <variant> <dir>
    local version="$1" variant="$2" dir="$3"
    rm -rf "$dir"; mkdir -p "$dir"
    for f in "$DAT" "$DAT.SO" "$DAT.P"; do ln -s "$f" "$dir/"; done
    log "building the $variant index with the $version"
    measured "$RES/index-$variant-$version" \
        "$BUILD/$version/build-index-$variant" "$dir/$(basename "$DAT")" > "$RES/index-$variant-$version.log" 2>&1
    # Index files only (the dataset files are symlinks).
    (cd "$dir" && find . -type f -exec wc -c {} + | grep -v " total$") > "$RES/index-$variant-$version.sizes" || true
}

for variant in "${VARIANT_LIST[@]}"; do
    case "$variant" in basic|split) ;; *) die "unknown variant $variant" ;; esac
    if [ "$REUSE_INDEXES" = 1 ]; then
        [ -f "$WORK/index-$variant/$(basename "$DAT").nTriples" ] || die "no $variant index to reuse in $WORK"
        log "reusing the $variant index"
        continue
    fi
    build_index candidate "$variant" "$WORK/index-$variant"
    if [ "$BUILD_BOTH" = 1 ]; then
        build_index baseline "$variant" "$WORK/index-$variant-baseline"
        log "comparing the index files of both versions"
        (
            cd "$WORK/index-$variant" && for f in "$(basename "$DAT")".*; do
                [ -L "$f" ] && continue
                if cmp -s "$f" "../index-$variant-baseline/$f"; then echo "same $f"; else echo "DIFFERENT $f"; fi
            done
        ) > "$RES/index-$variant-comparison.txt"
        rm -rf "$WORK/index-$variant-baseline"
    fi
done

# --- queries ---------------------------------------------------------------------
for variant in "${VARIANT_LIST[@]}"; do
    INDEX="$WORK/index-$variant/$(basename "$DAT")"
    for qfile in "${QUERY_FILES[@]}"; do
        qname="$(basename "$qfile" .tsv)"
        for rep in $(seq 1 "$REPS"); do
            # Alternate which version goes first.
            if [ $((rep % 2)) = 1 ]; then order="baseline candidate"; else order="candidate baseline"; fi
            [ "$CANDIDATE_ONLY" = 1 ] && order="candidate"
            for version in $order; do
                prefix="$RES/queries-$variant-$qname-$version-run$rep"
                log "$variant / $qname / $version / run $rep"
                if [ "$rep" = 1 ] && [ "$DIGESTS" = 1 ]; then
                    # The solutions go through a FIFO to the digest, never to disk
                    # (a query can have hundreds of millions of results).
                    fifo="$WORK/solutions-$variant-$qname-$version.fifo"
                    rm -f "$fifo"; mkfifo "$fifo"
                    if [ "$KEEP_SOLUTIONS" = 1 ]; then
                        tee "$RES/solutions-$variant-$qname-$version.txt" < "$fifo" \
                            | python3 -I "$REPO/validation/summarize.py" digest - > "$RES/digests-$variant-$qname-$version.txt" &
                    else
                        python3 -I "$REPO/validation/summarize.py" digest "$fifo" > "$RES/digests-$variant-$qname-$version.txt" &
                    fi
                    digest_pid=$!
                    measured "$prefix" "$BUILD/$version/query-index-$variant" "$INDEX" "$qfile" "$fifo" \
                        > "$prefix.out" 2> "$prefix.err"
                    # If the driver died before opening the FIFO, unblock the reader.
                    unblock=""
                    if kill -0 "$digest_pid" 2>/dev/null; then ( : > "$fifo" ) 2>/dev/null & unblock=$!; fi
                    wait "$digest_pid" || true
                    [ -n "$unblock" ] && kill "$unblock" 2>/dev/null || true
                    rm -f "$fifo"
                else
                    measured "$prefix" "$BUILD/$version/query-index-$variant" "$INDEX" "$qfile" \
                        > "$prefix.out" 2> "$prefix.err"
                fi
            done
        done
    done
done

python3 -I "$REPO/validation/summarize.py" summary "$RES" > "$RES/summary.md"
tar -czf "$OUT/results-$NAME.tar.gz" -C "$OUT/results" "$NAME"
log "done. Summary: $RES/summary.md"
log "send: $OUT/results-$NAME.tar.gz"
