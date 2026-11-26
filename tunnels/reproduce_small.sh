#!/bin/bash
# Dry run of the whole reproduction (tunnels/reproduce_paper.sh) on a small test dataset of tests/data.
# Usage: tunnels/reproduce_small.sh [--dataset metro|rand-small|rand-medium] [--keep] <out dir>
#   --dataset D   dataset of tests/data (default rand-medium; about 1-2 min in Release)
#   --keep        keep what a previous run left in <out dir> (indexes and CSVs are reused; the dataset
#                 must be the same, else exit code 2); without it, a previous output is deleted first
# Under <out dir>: data/ (the dataset as a DATA_DIR), paper.env (the configuration) and out/ (the
# output: tables.md, pathfinder_paper.md, figures/ if matplotlib is installed, ...).
# Needs the binaries in BUILD_DIR (default <repo>/build; the 'build' step is not run).
# The MillenniumDB columns of the time table are left out (MDB_RESULTS and MDB_RAW empty).
# Exit codes: 0 ok, 2 invalid usage or <out dir> exists and is not an output of this script
# (nothing is touched), other: a step failed.
set -euo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO=$(dirname "$HERE")
DATASET=rand-medium; KEEP=0; OUT=""
while [ $# -gt 0 ]; do
  case $1 in
    --dataset) [ $# -ge 2 ] || { echo "error: --dataset needs a name" >&2; exit 2; }; DATASET=$2; shift 2;;
    --keep) KEEP=1; shift;;
    -h|--help) sed -n '2,12p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0;;
    -*) echo "error: unknown option: $1" >&2; exit 2;;
    *) [ -z "$OUT" ] || { echo "error: more than one out dir" >&2; exit 2; }; OUT=$1; shift;;
  esac
done
[ -n "$OUT" ] || { echo "error: out dir needed (see --help)" >&2; exit 2; }
case $DATASET in metro|rand-small|rand-medium) ;; *) echo "error: unknown dataset '$DATASET' (metro, rand-small, rand-medium)" >&2; exit 2;; esac
SRC=$REPO/tests/data/$DATASET
[ -f "$SRC/graph.dat" ] || { echo "error: no dataset in $SRC" >&2; exit 2; }
OUT=$(readlink -m "$OUT")
if [ -e "$OUT" ] && [ ! -f "$OUT/.reproduce-small" ]; then
  echo "error: $OUT exists and is not an output of this script" >&2; exit 2
fi
BUILD_DIR=${BUILD_DIR:-$REPO/build}
[ -d "$BUILD_DIR" ] || { echo "error: no build directory: $BUILD_DIR (set BUILD_DIR)" >&2; exit 2; }

if [ "$KEEP" = 1 ] && [ -f "$OUT/.reproduce-small" ] && [ "$(cat "$OUT/.reproduce-small")" != "$DATASET" ]; then
  echo "error: $OUT holds the dataset '$(cat "$OUT/.reproduce-small")', not '$DATASET': --keep cannot reuse it" >&2; exit 2
fi
[ "$KEEP" = 1 ] || rm -rf "$OUT"
D=$OUT/data
mkdir -p "$D"; echo "$DATASET" > "$OUT/.reproduce-small"   # the mark of the outputs of this script: the dataset
cp "$SRC/graph.dat" "$D/wd-small.dat"
# gen-queries only takes the predicates of the form .../prop/direct/... from the log for its pool
for f in graph.dat.P:predicates.txt graph.dat.SO:nodes.txt queries.tsv:log.tsv; do
  sed 's#/pred/#/prop/direct/#g' "$SRC/${f%%:*}" > "$D/${f##*:}"
done
cp "$REPO/tests/tunnels/shapes_small.csv" "$D/shapes.csv"
cat > "$OUT/paper.env" <<ENV
DATA_DIR="$D"
WD_DAT="$D/wd-small.dat"
WD_DICT_P="$D/predicates.txt"
WD_DICT_SO="$D/nodes.txt"
WD_QUERIES="$D/log.tsv"
SHAPES_CSV="$D/shapes.csv"
OUT_DIR="$OUT/out"
BUILD_DIR="$BUILD_DIR"
NAVGRAPH_DIR=""
GEN_N="10"
GEN_SEED="1"
EXTRA_OPTS="--reps 1"
MDB_RESULTS=""
MDB_RAW=""
ENV
SECONDS=0
"$HERE/reproduce_paper.sh" --config "$OUT/paper.env" --only log,generated,backends,pathfinder,automata,figures,tables
echo "done ($SECONDS s): $OUT/out/tables.md, $OUT/out/pathfinder_paper.md"
