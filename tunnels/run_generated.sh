#!/bin/bash
# Queries generated from the expression shapes of Hammerer & Martens (GRADES-NDA 2025), run like
# run_wikidata.sh but reusing the indexes it built.
# Usage: tunnels/run_generated.sh <out dir of run_wikidata.sh> <shapes.csv> [N=1000] [seed=1] [extra ring-tunnels options]
#   shapes.csv: e.g. WD_organic.csv from https://zenodo.org/records/15363430
# Writes everything under <out dir>/gen-<N>-<seed>/.
# Environment: BUILD_DIR (default <repo>/build); WD_QUERIES (default tunnels/data/paths-final.tsv):
# the query log whose predicates form the pool.
set -e
# parallel commands: remember each PID and fail, saying which command, if any of them fails
PIDS=(); NAMES=()
track() { PIDS+=($!); NAMES+=("$1"); }
waitall() { local i rc=0; for i in "${!PIDS[@]}"; do wait "${PIDS[$i]}" || { echo "failed: ${NAMES[$i]}" >&2; rc=1; }; done; PIDS=(); NAMES=(); [ $rc = 0 ] || exit 1; }
OUT=$(readlink -m "${1:?out dir of run_wikidata.sh}"); SHAPES=$(readlink -f "${2:?shapes csv}"); N=${3:-1000}; SEED=${4:-1}
shift 2; [ $# -gt 0 ] && shift; [ $# -gt 0 ] && shift; EXTRA="$*"
HERE=$(cd "$(dirname "$0")" && pwd); B=$(cd "${BUILD_DIR:-$HERE/../build}" && pwd)
[ -f "$OUT/.ring-tunnels" ] && [ -f "$OUT/basic/wd.dat.bwts.L" ] && [ -f "$OUT/split/wd.dat.wt_pred_s" ] || { echo "$OUT has no indexes built by run_wikidata.sh"; exit 1; }
G=$OUT/gen-$N-$SEED; mkdir -p "$G"
# predicate pool: the predicates of the Wikidata query log, weighted by their occurrences there
"$B/gen-queries" "$OUT/basic/wd.dat" "$SHAPES" "${WD_QUERIES:-$HERE/data/paths-final.tsv}" "$N" --seed "$SEED" > "$G/queries.tsv" 2> "$G/gen.log"
Q=$G/queries.tsv
BUD="--uniform-cost --budgets 0,1000,10000,100000,inf --rel 1,4,16 --rel-slack 10 $EXTRA"
BUDV="--uniform-cost --budgets 0,1000,inf --rel 4,16 --rel-slack 10 --vv-reps 1 $EXTRA"
"$B/ring-tunnels" "$OUT/basic/wd.dat" "$Q" --out "$G/cv.csv" $BUD --pairs 5 --pair-reverse --vv none 2> "$G/cv.log" & track "ring-tunnels $G/cv.csv"
"$B/ring-tunnels" "$OUT/basic/wd.dat" "$Q" --out "$G/vv_bridge.csv" $BUDV --vv bridge --vv-derive --vv-only --vv-samples 0 --vv-union 2> "$G/vv_bridge.log" & track "ring-tunnels $G/vv_bridge.csv"
"$B/ring-tunnels-split" "$OUT/split/wd.dat" "$Q" --out "$G/vv_ring.csv" $BUDV --vv ring --vv-derive --vv-only --vv-samples 0 2> "$G/vv_ring.log" & track "ring-tunnels-split $G/vv_ring.csv"
waitall
"$B/ring-tunnels" "$OUT/basic/wd.dat" "$Q" --out "$G/sp.csv" $BUD --shortest --pairs 5 --pair-reverse --vv-derive --vv-samples 5 2> "$G/sp.log"
S=$HERE/ring_summary.py
python3 "$S" "$G/cv.csv" > "$G/summary_cv.md"
cat "$G/vv_bridge.csv" <(tail -n +2 "$G/vv_ring.csv") > "$G/vv_all.csv"
python3 "$S" "$G/vv_all.csv" --vv > "$G/summary_vv.md"
python3 "$S" "$G/sp.csv" --pathfinder > "$G/summary_pathfinder.md"
echo "done: $G"
