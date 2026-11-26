#!/bin/bash
# Reproduces the experiments of the Tunnels paper with the existing scripts of tunnels/.
# Usage: tunnels/reproduce_paper.sh [--config FILE] [--only step1,step2,...] [--dry-run] [--list]
#   --config FILE   configuration (default tunnels/paper.env; copy tunnels/paper.env.example)
#   --only STEPS    comma-separated subset of the steps, run in their usual order
#   --dry-run       validate the configuration and print the commands ("+ ..."), run nothing
#   --list          print the steps and exit
# Exit codes: 0 ok, 2 invalid usage or configuration (nothing is created), other: a step failed.
# All the paths come from the configuration file; nothing is assumed about the server.
set -euo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO=$(dirname "$HERE")
STEPS=(build log generated backends pathfinder automata figures tables)
describe() {
  case $1 in
    build)     echo "cmake configure and build of the binaries (SDSL_ROOT, RING_TIMEOUT, NAVGRAPH_DIR)";;
    log)       echo "run_wikidata.sh: indexes + Wikidata log. Graph representations (Ring, RingAB), size and construction time of H and S, c-to-v on the Ring; the figures";;
    generated) echo "run_generated.sh: queries generated from the shapes. Generated queries relative to H, c-to-v by shape";;
    backends)  echo "run_backends.sh: navGraph (Compact) and adjacency lists (Plain). Graph representations (Compact, Plain), construction time; series of the figures";;
    pathfinder) echo "run_pathfinder.sh: ALL SHORTEST WALKS vs PathFinder (Ring and Plain). Comparison with PathFinder (pathfinder_paper.md)";;
    automata)  echo "run_automata.sh: --automata (outside the paper)";;
    figures)   echo "paper_figures.py: ctov.pdf, ctoc.pdf, vtov.pdf and numbers.md";;
    tables)    echo "paper_tables.py -> tables.md. The six tables of the representations and of the generated queries";;
  esac
}

CONFIG=$HERE/paper.env; ONLY=""; DRY=0; LIST=0
while [ $# -gt 0 ]; do
  case $1 in
    --config) [ $# -ge 2 ] || { echo "error: --config needs a file" >&2; exit 2; }; CONFIG=$2; shift 2;;
    --only)   [ $# -ge 2 ] || { echo "error: --only needs a list of steps" >&2; exit 2; }; ONLY=$2; shift 2;;
    --dry-run) DRY=1; shift;;
    --list)   LIST=1; shift;;
    -h|--help) sed -n '2,9p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0;;
    *) echo "error: unknown option: $1" >&2; exit 2;;
  esac
done

if [ "$LIST" = 1 ]; then
  for s in "${STEPS[@]}"; do printf '%-10s %s\n' "$s" "$(describe "$s")"; done
  exit 0
fi

# steps selected
SELECTED=()
if [ -z "$ONLY" ]; then
  SELECTED=("${STEPS[@]}")
else
  IFS=',' read -ra want <<< "$ONLY"
  for w in "${want[@]}"; do
    ok=0; for s in "${STEPS[@]}"; do [ "$s" = "$w" ] && ok=1; done
    [ $ok = 1 ] || { echo "error: unknown step '$w' (see --list)" >&2; exit 2; }
  done
  for s in "${STEPS[@]}"; do for w in "${want[@]}"; do if [ "$s" = "$w" ]; then SELECTED+=("$s"); break; fi; done; done
fi
selected() { local s; for s in "${SELECTED[@]}"; do [ "$s" = "$1" ] && return 0; done; return 1; }

if [ ! -f "$CONFIG" ]; then
  echo "error: no configuration file: $CONFIG" >&2
  echo "copy tunnels/paper.env.example to tunnels/paper.env and fill it in (or use --config FILE)" >&2
  exit 2
fi
# the configuration: KEY=value lines
if ! LOADERR=$(bash -ec '. "$1"' _ "$CONFIG" 2>&1 > /dev/null); then   # own process: -e acts there
  echo "error: cannot load $CONFIG (KEY=value lines; quote values with spaces):" >&2
  echo "$LOADERR" | sed 's/^/  /' >&2
  exit 2
fi
# shellcheck source=/dev/null
. "$CONFIG"

DATA_DIR=${DATA_DIR:-}; OUT_DIR=${OUT_DIR:-}
if [ -n "$DATA_DIR" ]; then
  WD_DAT=${WD_DAT:-$DATA_DIR/wikidata-enumerated.dat}
  WD_DICT_P=${WD_DICT_P:-$DATA_DIR/wikidata-unsorted-enumerated.dat.P}
  WD_DICT_SO=${WD_DICT_SO:-$DATA_DIR/wikidata-unsorted-enumerated.dat.SO}
  WD_QUERIES=${WD_QUERIES:-$DATA_DIR/paths-final.tsv}
  SHAPES_CSV=${SHAPES_CSV:-$DATA_DIR/WD_organic.csv}
fi
WD_DAT=${WD_DAT:-}; WD_DICT_P=${WD_DICT_P:-}; WD_DICT_SO=${WD_DICT_SO:-}
WD_QUERIES=${WD_QUERIES:-}; SHAPES_CSV=${SHAPES_CSV:-}
BUILD_DIR=${BUILD_DIR:-$REPO/build}
SDSL_ROOT=${SDSL_ROOT:-}; NAVGRAPH_DIR=${NAVGRAPH_DIR:-}
MDB_RESULTS=${MDB_RESULTS:-}; MDB_RAW=${MDB_RAW:-}
RING_TIMEOUT=${RING_TIMEOUT:-60}; GEN_N=${GEN_N:-1000}; GEN_SEED=${GEN_SEED:-1}
EXTRA_OPTS=${EXTRA_OPTS:-}
EXTRA=(); [ -z "$EXTRA_OPTS" ] || read -ra EXTRA <<< "$EXTRA_OPTS"

# ---- validation: every problem is listed, and nothing is created if there is any
PROBLEMS=(); WARNINGS=()
problem() { PROBLEMS+=("$1"); }
need_file() {   # variable name, value, step(s) that need it
  if [ -z "$2" ]; then problem "$1 is empty (needed by: $3)"
  elif [ ! -f "$2" ]; then problem "$1: no such file: $2 (needed by: $3)"; fi
}
need_bin() {    # binary, step(s)
  [ -x "$BUILD_DIR/$1" ] || problem "$BUILD_DIR/$1 does not exist (needed by: $2; run the 'build' step)"
}
[ -n "$DATA_DIR" ] || problem "DATA_DIR is empty"
[ -n "$OUT_DIR" ] || problem "OUT_DIR is empty"
if selected log; then
  need_file WD_DAT "$WD_DAT" log; need_file WD_DICT_P "$WD_DICT_P" log
  need_file WD_DICT_SO "$WD_DICT_SO" log; need_file WD_QUERIES "$WD_QUERIES" log
fi
if selected generated; then
  need_file SHAPES_CSV "$SHAPES_CSV" generated; need_file WD_QUERIES "$WD_QUERIES" generated
fi
if selected backends; then
  need_file WD_QUERIES "$WD_QUERIES" backends; need_file WD_DICT_P "$WD_DICT_P" backends
  need_file WD_DICT_SO "$WD_DICT_SO" backends
fi
if selected pathfinder; then need_file WD_QUERIES "$WD_QUERIES" pathfinder; fi
if selected automata; then need_file WD_QUERIES "$WD_QUERIES" automata; fi
if [ -n "$MDB_RESULTS$MDB_RAW" ] && selected pathfinder; then   # MillenniumDB times: both or none
  need_file MDB_RESULTS "$MDB_RESULTS" "pathfinder (MDB_RAW is set)"; need_file MDB_RAW "$MDB_RAW" "pathfinder (MDB_RESULTS is set)"
fi
if ! selected build; then
  if selected log; then need_bin build-index-basic log; need_bin build-index-split log; fi
  if selected log || selected generated; then need_bin ring-tunnels "log, generated"; need_bin ring-tunnels-split "log, generated"; fi
  if selected generated; then need_bin gen-queries generated; fi
  if selected pathfinder; then need_bin ring-tunnels pathfinder; fi
  if selected backends || selected pathfinder || selected automata; then need_bin adj-tunnels "backends, pathfinder, automata"; fi
  if [ -n "$NAVGRAPH_DIR" ] && selected backends; then need_bin nav-tunnels backends; need_bin nav-build-index backends; fi
fi
if [ -n "$NAVGRAPH_DIR" ] && [ ! -d "$NAVGRAPH_DIR" ]; then problem "NAVGRAPH_DIR is not a directory: $NAVGRAPH_DIR"; fi
if [ -n "$OUT_DIR" ]; then
  OUT=$(readlink -m "$OUT_DIR")
  if [ -n "$WD_DAT" ]; then
    case "$OUT/" in "$(dirname "$(readlink -m "$WD_DAT")")/"*) problem "OUT_DIR ($OUT) is inside the directory of WD_DAT";; esac
  fi
  if selected log && [ -e "$OUT" ] && [ ! -f "$OUT/.ring-tunnels" ]; then
    problem "OUT_DIR exists and is not an output of these scripts: $OUT"
  fi
  if [ "$DRY" = 0 ]; then   # outputs of steps that are not run now
    if ! selected log && { selected generated || selected backends || selected pathfinder; }; then
      [ -f "$OUT/.ring-tunnels" ] || problem "$OUT has no indexes built by the 'log' step"
    fi
    if ! selected log && selected generated; then
      [ -f "$OUT/basic/wd.dat.bwts.L" ] && [ -f "$OUT/split/wd.dat.wt_pred_s" ] || problem "$OUT has no indexes for 'generated'; run the 'log' step"
    fi
    if ! selected log && { selected figures || selected tables; }; then
      [ -f "$OUT/cv.csv" ] || problem "$OUT/cv.csv does not exist (run the 'log' step before 'figures' or 'tables')"
    fi
    if ! selected backends && selected automata; then
      [ -f "$OUT/adj/wd.dat.adj" ] || problem "$OUT/adj/wd.dat.adj does not exist (run the 'backends' step before 'automata')"
    fi
    if ! selected backends && selected pathfinder; then
      [ -f "$OUT/adj/wd.dat.adj" ] || problem "$OUT/adj/wd.dat.adj does not exist (run the 'backends' step before 'pathfinder')"
    fi
  fi
fi
command -v python3 > /dev/null || problem "python3 not found"
if selected build; then command -v cmake > /dev/null || problem "cmake not found"; fi
[ -x /usr/bin/time ] || WARNINGS+=("/usr/bin/time not found: the table of graph representations will have no memory or time of the index builds")
if selected figures && ! python3 -c 'import matplotlib' 2> /dev/null; then
  WARNINGS+=("matplotlib not found: the 'figures' step will not draw")
fi
for w in "${WARNINGS[@]+"${WARNINGS[@]}"}"; do echo "warning: $w" >&2; done
if [ ${#PROBLEMS[@]} -gt 0 ]; then
  echo "error: invalid configuration ($CONFIG):" >&2
  for p in "${PROBLEMS[@]}"; do echo "  - $p" >&2; done
  exit 2
fi

# ---- commands
export BUILD_DIR WD_DICT_P WD_DICT_SO WD_QUERIES MDB_RESULTS MDB_RAW
GEN=$OUT/gen-$GEN_N-$GEN_SEED
LOG=$OUT/reproduce.log
BACKEND=both; [ -n "$NAVGRAPH_DIR" ] || BACKEND=adj

run_cmd() {
  if [ "$DRY" = 1 ]; then echo "+ $*"; else "$@"; fi
}
step_build() {
  local args=(-S "$REPO" -B "$BUILD_DIR" "-DRING_TIMEOUT=$RING_TIMEOUT")
  [ -z "$SDSL_ROOT" ] || args+=("-DSDSL_ROOT=$SDSL_ROOT")
  [ -z "$NAVGRAPH_DIR" ] || args+=("-DNAVGRAPH_DIR=$NAVGRAPH_DIR")
  run_cmd cmake "${args[@]}" || return 1
  run_cmd cmake --build "$BUILD_DIR" -j || return 1
}
step_log() { run_cmd "$HERE/run_wikidata.sh" "$WD_DAT" "$OUT" ${EXTRA[@]+"${EXTRA[@]}"}; }
step_generated() { run_cmd "$HERE/run_generated.sh" "$OUT" "$SHAPES_CSV" "$GEN_N" "$GEN_SEED" ${EXTRA[@]+"${EXTRA[@]}"}; }
step_backends() { run_cmd "$HERE/run_backends.sh" "$OUT" "$BACKEND" ${EXTRA[@]+"${EXTRA[@]}"}; }
step_pathfinder() { run_cmd "$HERE/run_pathfinder.sh" "$OUT" ${EXTRA[@]+"${EXTRA[@]}"}; }
step_automata() { run_cmd "$HERE/run_automata.sh" "$OUT" ${EXTRA[@]+"${EXTRA[@]}"}; }
step_figures() {
  run_cmd python3 "$HERE/paper_figures.py" "$OUT" "$OUT/figures" || return 1
  if [ "$DRY" = 1 ] || [ -d "$GEN" ]; then
    run_cmd python3 "$HERE/paper_figures.py" "$GEN" "$OUT/figures-gen" || return 1
  fi
}
step_tables() {
  if [ ! -f "$HERE/paper_tables.py" ]; then
    local msg="warning: tunnels/paper_tables.py does not exist yet: step 'tables' skipped"
    if [ "$DRY" = 1 ]; then echo "$msg" >&2; else echo "$msg" | tee -a "$LOG" >&3; fi   # fd 3: the real stderr
    return 0
  fi
  local gen=()
  if [ "$DRY" = 1 ] || [ -d "$GEN" ]; then gen=(--generated "$GEN"); fi
  if [ "$DRY" = 1 ]; then echo "+ python3 $HERE/paper_tables.py $OUT ${gen[*]} > $OUT/tables.md"
  else python3 "$HERE/paper_tables.py" "$OUT" "${gen[@]}" > "$OUT/tables.md"; fi
}

# ---- outputs that each step must leave (at least one data row)
need_rows() {   # file, minimum number of lines (default 2: header + one row)
  [ -f "$1" ] && [ "$(wc -l < "$1")" -ge "${2:-2}" ] || { echo "missing or empty output: $1" >&2; return 1; }
}
check_build() { return 0; }
check_log() { local f; for f in cv vv_bridge vv_ring; do need_rows "$OUT/$f.csv" || return 1; done; }
check_generated() { need_rows "$GEN/queries.tsv" 1 && need_rows "$GEN/cv.csv"; }
check_backends() {
  local b; local bs=(adj); [ -z "$NAVGRAPH_DIR" ] || bs+=(nav)
  for b in "${bs[@]}"; do need_rows "$OUT/$b/cv.csv" || return 1; need_rows "$OUT/$b/vv_bridge.csv" || return 1; done
}
check_pathfinder() {
  need_rows "$OUT/sp_early.csv" && need_rows "$OUT/sp_early_adj.csv" && need_rows "$OUT/pathfinder_paper.md" 1
}
check_automata() { need_rows "$OUT/automata/log.csv"; }
check_figures() { return 0; }
check_tables() { return 0; }

# ---- execution
if [ "$DRY" = 1 ]; then
  for s in "${SELECTED[@]}"; do echo "== $s: dry-run"; "step_$s"; done
  exit 0
fi
exec 3>&2
mkdir -p "$OUT"
if selected log; then touch "$OUT/.ring-tunnels"; fi   # run_wikidata.sh accepts a marked out dir
for s in "${SELECTED[@]}"; do
  echo "== $s: start" | tee -a "$LOG"
  SECONDS=0
  if ( "step_$s" && "check_$s" ) 2>&1 | tee -a "$LOG"; then
    echo "== $s: ok ($SECONDS s)" | tee -a "$LOG"
  else
    echo "== $s: FAILED" | tee -a "$LOG"
    exit 1
  fi
done
