#!/bin/bash
# The same experiments on the other graph representations: navGraph (Navarro & Robert, SPIRE 2024)
# and plain adjacency lists. Reuses the out dir of run_wikidata.sh (and its gen-* dirs, if any).
# Usage: tunnels/run_backends.sh <out dir of run_wikidata.sh> [nav|adj|both] [extra ring-tunnels options]
# Builds (once) <out>/nav/ (navGraph index) and <out>/adj/ (<dat>.adj, the adjacency lists), then
# writes cv.csv and vv_bridge.csv under <out>/nav and <out>/adj, and under <out>/gen-*/nav, adj.
# The .dat and the dictionaries are only read: they are linked (absolute paths), not copied.
# Environment: BUILD_DIR (default <repo>/build); WD_DICT_P, WD_DICT_SO (default tunnels/data/
# wikidata-unsorted-enumerated.dat.P / .SO); WD_QUERIES (default tunnels/data/paths-final.tsv).
set -e
# parallel commands: remember each PID and fail, saying which command, if any of them fails
PIDS=(); NAMES=()
track() { PIDS+=($!); NAMES+=("$1"); }
waitall() { local i rc=0; for i in "${!PIDS[@]}"; do wait "${PIDS[$i]}" || { echo "failed: ${NAMES[$i]}" >&2; rc=1; }; done; PIDS=(); NAMES=(); [ $rc = 0 ] || exit 1; }
OUT=$(readlink -m "${1:?out dir of run_wikidata.sh}"); WHICH=${2:-both}; shift; [ $# -gt 0 ] && shift; EXTRA="$*"
HERE=$(cd "$(dirname "$0")" && pwd); B=$(cd "${BUILD_DIR:-$HERE/../build}" && pwd); DATA=$HERE/data
[ -f "$OUT/.ring-tunnels" ] && [ -L "$OUT/basic/wd.dat" ] || { echo "$OUT is not an out dir of run_wikidata.sh"; exit 1; }
DAT=$(readlink -f "$OUT/basic/wd.dat")
# the dictionaries are linked, not copied (they are large): resolve them now, before building anything
DICT_P=$(readlink -m "${WD_DICT_P:-$DATA/wikidata-unsorted-enumerated.dat.P}")
DICT_SO=$(readlink -m "${WD_DICT_SO:-$DATA/wikidata-unsorted-enumerated.dat.SO}")
for f in "$DICT_P" "$DICT_SO"; do [ -f "$f" ] || { echo "no such dictionary: $f"; exit 1; }; done
BUD="--uniform-cost --budgets 0,1000,10000,100000,inf --rel 1,4,16 --rel-slack 10 $EXTRA"
BUDV="--uniform-cost --budgets 0,1000,inf --rel 4,16 --rel-slack 10 --vv-reps 1 $EXTRA"
TIME=""; [ -x /usr/bin/time ] && TIME="/usr/bin/time -v"
run() {   # backend binary, index file, query file, out dir
  mkdir -p "$4"; PIDS=(); NAMES=()   # (runs in a subshell)
  "$B/$1" "$2" "$3" --out "$4/cv.csv" $BUD --pairs 5 --pair-reverse --vv none 2> "$4/cv.log" & track "$1 $4/cv.csv"
  "$B/$1" "$2" "$3" --out "$4/vv_bridge.csv" $BUDV --vv bridge --vv-derive --vv-only --vv-samples 0 --vv-union 2> "$4/vv_bridge.log" & track "$1 $4/vv_bridge.csv"
  waitall
  python3 "$HERE/ring_summary.py" "$4/cv.csv" > "$4/summary_cv.md"
  python3 "$HERE/ring_summary.py" "$4/vv_bridge.csv" --vv > "$4/summary_vv.md"
}
for be in nav adj; do
  [ "$WHICH" = both ] || [ "$WHICH" = $be ] || continue
  D=$OUT/$be; mkdir -p "$D"
  ln -sfn "$DAT" "$D/wd.dat"; ln -sfn "$DICT_P" "$D/wd.dat.P"; ln -sfn "$DICT_SO" "$D/wd.dat.SO"
  if [ $be = nav ]; then
    [ -x "$B/nav-tunnels" ] || { echo "nav-tunnels not built: cmake -DNAVGRAPH_DIR=... .."; exit 1; }
    [ -f "$D/wd.dat.sizes" ] || (cd "$D" && $TIME "$B/nav-build-index" wd.dat > index.log 2>&1)
    BIN=nav-tunnels
  else
    BIN=adj-tunnels   # builds wd.dat.adj on its first run
    [ -f "$D/wd.dat.adj" ] || $TIME "$B/adj-tunnels" "$D/wd.dat" "${WD_QUERIES:-$DATA/paths-final.tsv}" --max-queries 0 --out /dev/null > "$D/index.log" 2>&1
  fi
  ls -lL "$D"/wd.dat.* > "$D/index_sizes.txt"
  run $BIN "$D/wd.dat" "${WD_QUERIES:-$DATA/paths-final.tsv}" "$D" & track "$be $D"
  for G in "$OUT"/gen-*; do [ -f "$G/queries.tsv" ] || continue; run $BIN "$D/wd.dat" "$G/queries.tsv" "$G/$be" & track "$be $G/$be"; done
done
waitall
echo "done: $OUT"
