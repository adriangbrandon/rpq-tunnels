#!/bin/bash
# Tunnels on Ring-RPQ over Wikidata.
# Usage: tunnels/run_wikidata.sh <path to wikidata-enumerated.dat> <out dir> [extra ring-tunnels options]
# (e.g. --max-queries 50 for a quick trial)
# The .dat is only READ (a symlink to it is placed in the out dir). The dictionaries and the
# queries come from tunnels/data unless the environment says otherwise:
#   BUILD_DIR   directory of the binaries           (default: <repo>/build)
#   WD_DICT_P   predicate dictionary (.P)           (default: tunnels/data/wikidata-unsorted-enumerated.dat.P)
#   WD_DICT_SO  subject/object dictionary (.SO)     (default: tunnels/data/wikidata-unsorted-enumerated.dat.SO)
#   WD_QUERIES  query log                           (default: tunnels/data/paths-final.tsv)
# Everything is written under the out dir, which must not exist yet
# or must be a previous output of this script.
set -e
# parallel commands: remember each PID and fail, saying which command, if any of them fails
PIDS=(); NAMES=()
track() { PIDS+=($!); NAMES+=("$1"); }
waitall() { local i rc=0; for i in "${!PIDS[@]}"; do wait "${PIDS[$i]}" || { echo "failed: ${NAMES[$i]}" >&2; rc=1; }; done; PIDS=(); NAMES=(); [ $rc = 0 ] || exit 1; }
DAT=$(readlink -f "${1:?path to wikidata-enumerated.dat}"); OUT=$(readlink -m "${2:?out dir}"); shift 2; EXTRA="$*"
HERE=$(cd "$(dirname "$0")" && pwd); B=$(cd "${BUILD_DIR:-$HERE/../build}" && pwd); DATA=$HERE/data
Q=${WD_QUERIES:-$DATA/paths-final.tsv}
[ -f "$DAT" ] || { echo "no such file: $DAT"; exit 1; }
case "$OUT/" in "$(dirname "$DAT")/"*) echo "the out dir must not be inside $(dirname "$DAT")"; exit 1;; esac
if [ -e "$OUT" ] && [ ! -f "$OUT/.ring-tunnels" ]; then echo "$OUT exists and is not an output of this script"; exit 1; fi
mkdir -p "$OUT"; touch "$OUT/.ring-tunnels"
mkdir -p "$OUT/basic" "$OUT/split"
for v in basic split; do
  ln -sfn "$DAT" "$OUT/$v/wd.dat"
  cp "${WD_DICT_P:-$DATA/wikidata-unsorted-enumerated.dat.P}" "$OUT/$v/wd.dat.P"
  cp "${WD_DICT_SO:-$DATA/wikidata-unsorted-enumerated.dat.SO}" "$OUT/$v/wd.dat.SO"
done
TIME=""; [ -x /usr/bin/time ] && TIME="/usr/bin/time -v"
[ -f "$OUT/basic/wd.dat.bwts.L" ] || $TIME "$B/build-index-basic" "$OUT/basic/wd.dat" > "$OUT/basic/index.log" 2>&1
[ -f "$OUT/split/wd.dat.wt_pred_s" ] || $TIME "$B/build-index-split" "$OUT/split/wd.dat" > "$OUT/split/index.log" 2>&1
ls -l "$OUT"/basic/wd.dat.* "$OUT"/split/wd.dat.* > "$OUT/index_sizes.txt"

# Configuration of the paper: uniform cost model on every backend.
BUD="--uniform-cost --budgets 0,1000,10000,100000,inf --rel 1,4,16 --rel-slack 10 $EXTRA"
# v-to-v: fewer budgets and one expansion each (their H have tens of millions of nodes)
BUDV="--uniform-cost --budgets 0,1000,inf --rel 4,16 --rel-slack 10 --vv-reps 1 $EXTRA"
# The three runs are independent and run in parallel (each loads its own index, ~30 GB).
# 1. c-to-v / v-to-c, with 5 constant-to-constant pairs each, solved from both endpoints (basic Ring)
"$B/ring-tunnels" "$OUT/basic/wd.dat" "$Q" --out "$OUT/cv.csv" $BUD --pairs 5 --pair-reverse --vv none 2> "$OUT/cv.log" & track "ring-tunnels $OUT/cv.csv"
# 2. v-to-v: bridge split on the basic Ring, plus the multi-source PMR baseline (rows VVu)
"$B/ring-tunnels" "$OUT/basic/wd.dat" "$Q" --out "$OUT/vv_bridge.csv" $BUDV --vv bridge --vv-derive --vv-only --vv-samples 0 --vv-union 2> "$OUT/vv_bridge.log" & track "ring-tunnels $OUT/vv_bridge.csv"
# 3. v-to-v: Ring-RPQ split on RingAB
"$B/ring-tunnels-split" "$OUT/split/wd.dat" "$Q" --out "$OUT/vv_ring.csv" $BUDV --vv ring --vv-derive --vv-only --vv-samples 0 2> "$OUT/vv_ring.log" & track "ring-tunnels-split $OUT/vv_ring.csv"
waitall
# (ALL SHORTEST WALKS vs PathFinder: run_pathfinder.sh, after run_backends.sh)

S=$(dirname "$0")/ring_summary.py
python3 "$S" "$OUT/cv.csv" > "$OUT/summary_cv.md"
python3 "$S" "$OUT/vv_bridge.csv" --vv > "$OUT/summary_vv_bridge.md"
cat "$OUT/vv_bridge.csv" <(tail -n +2 "$OUT/vv_ring.csv") > "$OUT/vv_all.csv"
python3 "$S" "$OUT/vv_all.csv" --vv > "$OUT/summary_vv.md"
echo "done: $OUT"
