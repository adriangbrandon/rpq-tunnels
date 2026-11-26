#!/bin/bash
# ALL SHORTEST WALKS against the simulation of PathFinder (MillenniumDB): the comparison of the paper.
# Usage: tunnels/run_pathfinder.sh <out dir of run_wikidata.sh> [extra ring-tunnels options]
# Reuses the out dir of run_wikidata.sh (<out>/basic/wd.dat) and the adjacency-list index of
# run_backends.sh (<out>/adj/wd.dat.adj). Writes under <out>:
#   sp_early.csv, sp_early_adj.csv (+ .log)    ring-tunnels and adj-tunnels with --shortest --early-stop
#   pathfinder.md, pathfinder_adj.md           pathfinder_tables.py of each CSV (sections P0-P6; P5 of the
#                                              Plain one uses <out>/adj/cv.csv, "no data" if it does not exist)
#   summary_pathfinder.md                      ring_summary.py --pathfinder
#   pathfinder_paper.md                        the comparison with PathFinder of the paper (pathfinder_tables.py --paper)
# The two runs go in sequence (adj-tunnels takes ~61 GB on Wikidata). Do NOT change their options:
# the rng is global and --vv-samples consumes it, so other options would draw other c-to-c pairs.
# Environment: BUILD_DIR (default <repo>/build); WD_QUERIES (default tunnels/data/paths-final.tsv);
# MDB_RESULTS, MDB_RAW (optional, both or none): results.csv of join_pf.py and raw.csv of run_pf.py
# of the MillDB-PathFinder repo; they add the t_MDB columns to the time table.
set -e
OUT=$(readlink -m "${1:?out dir of run_wikidata.sh}"); shift; EXTRA="$*"
HERE=$(cd "$(dirname "$0")" && pwd); B=$(cd "${BUILD_DIR:-$HERE/../build}" && pwd); DATA=$HERE/data
Q=${WD_QUERIES:-$DATA/paths-final.tsv}
[ -f "$OUT/.ring-tunnels" ] && [ -L "$OUT/basic/wd.dat" ] || { echo "$OUT is not an out dir of run_wikidata.sh"; exit 1; }
[ -f "$OUT/adj/wd.dat" ] && [ -f "$OUT/adj/wd.dat.adj" ] || { echo "$OUT/adj/wd.dat.adj does not exist: run run_backends.sh $OUT adj first"; exit 1; }
if [ -n "${MDB_RESULTS:-}" ] || [ -n "${MDB_RAW:-}" ]; then
  [ -f "${MDB_RESULTS:-}" ] && [ -f "${MDB_RAW:-}" ] || { echo "MDB_RESULTS and MDB_RAW must both be files (or both empty)"; exit 1; }
fi
BUD="--uniform-cost --budgets 0,1000,10000,100000,inf --rel 1,4,16 --rel-slack 10 $EXTRA"
PF="--shortest --early-stop --pairs 5 --pair-reverse --vv-derive --vv-samples 5"
"$B/ring-tunnels" "$OUT/basic/wd.dat" "$Q" --out "$OUT/sp_early.csv" $BUD $PF 2> "$OUT/sp_early.log"
"$B/adj-tunnels" "$OUT/adj/wd.dat" "$Q" --out "$OUT/sp_early_adj.csv" $BUD $PF 2> "$OUT/sp_early_adj.log"

T=$HERE/pathfinder_tables.py
python3 "$T" "$OUT" --sp sp_early.csv > "$OUT/pathfinder.md"
# Plain: <out>/adj is its dir, so that P5 compares with the cv.csv of adj-tunnels (if any), not the Ring one
python3 "$T" "$OUT/adj" --sp "$OUT/sp_early_adj.csv" > "$OUT/pathfinder_adj.md"
python3 "$HERE/ring_summary.py" "$OUT/sp_early.csv" --pathfinder > "$OUT/summary_pathfinder.md"
MDB=()
[ -z "${MDB_RESULTS:-}" ] || MDB=(--mdb "$MDB_RESULTS" --mdb-raw "$MDB_RAW")
python3 "$T" "$OUT" --sp sp_early.csv --paper --adj sp_early_adj.csv "${MDB[@]}" > "$OUT/pathfinder_paper.md"
echo "done: $OUT"
