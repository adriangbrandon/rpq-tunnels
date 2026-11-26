#!/bin/bash
# Product graphs of Glushkov, reverse Glushkov (ours) and Thompson automata, on adjacency lists,
# for the c-to-v / v-to-c queries of the log and of the generated sets.
# Usage: tunnels/run_automata.sh <out dir of run_wikidata.sh> [extra ring-tunnels options]
# Environment: BUILD_DIR (default <repo>/build); WD_QUERIES (default tunnels/data/paths-final.tsv).
# Needs <out>/adj/wd.dat.adj (built by run_backends.sh). Writes <out>/automata/*.csv and summaries.
set -e
# parallel commands: remember each PID and fail, saying which command, if any of them fails
PIDS=(); NAMES=()
track() { PIDS+=($!); NAMES+=("$1"); }
waitall() { local i rc=0; for i in "${!PIDS[@]}"; do wait "${PIDS[$i]}" || { echo "failed: ${NAMES[$i]}" >&2; rc=1; }; done; PIDS=(); NAMES=(); [ $rc = 0 ] || exit 1; }
OUT=$(readlink -m "${1:?out dir of run_wikidata.sh}"); shift; EXTRA="$*"
HERE=$(cd "$(dirname "$0")" && pwd); B=$(cd "${BUILD_DIR:-$HERE/../build}" && pwd); DATA=$HERE/data
[ -f "$OUT/adj/wd.dat.adj" ] || { echo "no $OUT/adj/wd.dat.adj: run run_backends.sh adj first"; exit 1; }
D=$OUT/automata; mkdir -p "$D"
"$B/adj-tunnels" "$OUT/adj/wd.dat" "${WD_QUERIES:-$DATA/paths-final.tsv}" --automata --out "$D/log.csv" $EXTRA 2> "$D/log.log" & track "adj-tunnels $D/log.csv"
for G in "$OUT"/gen-*; do [ -f "$G/queries.tsv" ] || continue
  n=$(basename "$G"); "$B/adj-tunnels" "$OUT/adj/wd.dat" "$G/queries.tsv" --automata --out "$D/$n.csv" $EXTRA 2> "$D/$n.log" & track "adj-tunnels $D/$n.csv"
done
waitall
for f in "$D"/*.csv; do python3 "$HERE/automata_summary.py" "$f" > "${f%.csv}.md"; done
echo "done: $D"
