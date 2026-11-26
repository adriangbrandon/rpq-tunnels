#!/usr/bin/env bash
# Builds the two versions compared by the validation:
#   baseline  - the original algorithm (tag phase0-baseline: the published
#               code with a working Release build and the option to dump
#               the solutions, nothing else changed)
#   candidate - the refactored code (default: the commit checked out here)
#
# Usage: validation/build.sh [options]
#   --out DIR          working directory (default: ./validation-out)
#   --sdsl-root DIR    install prefix of the extended SDSL (default: $HOME)
#   --install-sdsl     clone and install the extended SDSL into --sdsl-root
#                      if it is not there
#   --baseline REF     git ref of the baseline (default: phase0-baseline)
#   --candidate REF    git ref of the candidate (default: HEAD)
#   --timeout SECONDS  time limit per query of both versions (default: 600,
#                      as in the code; the paper used 60 for the Wikidata
#                      benchmark of Table 2). Use another --out per value.
#   --jobs N           parallel compilation jobs (default: all cores)
#   --skip-tests       do not run the test suite of the candidate
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="$PWD/validation-out"
SDSL_ROOT="$HOME"
INSTALL_SDSL=0
BASELINE_REF="phase0-baseline"
CANDIDATE_REF="HEAD"
JOBS="$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 4)"
RUN_TESTS=1
TIMEOUT=""

while [ $# -gt 0 ]; do
    case "$1" in
        --out) OUT="$2"; shift 2 ;;
        --sdsl-root) SDSL_ROOT="$2"; shift 2 ;;
        --install-sdsl) INSTALL_SDSL=1; shift ;;
        --baseline) BASELINE_REF="$2"; shift 2 ;;
        --candidate) CANDIDATE_REF="$2"; shift 2 ;;
        --jobs) JOBS="$2"; shift 2 ;;
        --timeout) TIMEOUT="$2"; shift 2 ;;
        --skip-tests) RUN_TESTS=0; shift ;;
        -h|--help) sed -n '2,23p' "$0"; exit 0 ;;
        *) echo "unknown option: $1" >&2; exit 1 ;;
    esac
done

log() { echo "[build] $*"; }
die() { echo "[build] ERROR: $*" >&2; exit 1; }

for tool in git cmake python3; do
    command -v "$tool" >/dev/null || die "$tool is required"
done
mkdir -p "$OUT"
OUT="$(cd "$OUT" && pwd)"
SDSL_ROOT="$(mkdir -p "$SDSL_ROOT" && cd "$SDSL_ROOT" && pwd)"

# --- extended SDSL -----------------------------------------------------------
if [ ! -f "$SDSL_ROOT/include/sdsl/wm_int.hpp" ] || [ ! -f "$SDSL_ROOT/lib/libsdsl.a" ]; then
    [ "$INSTALL_SDSL" = 1 ] || die "extended SDSL not found in $SDSL_ROOT (use --sdsl-root or --install-sdsl)"
    log "installing the extended SDSL into $SDSL_ROOT"
    rm -rf "$OUT/sdsl-lite"
    git clone --recursive https://github.com/adriangbrandon/sdsl-lite.git "$OUT/sdsl-lite"
    (cd "$OUT/sdsl-lite" && ./install.sh "$SDSL_ROOT")
fi
grep -q "all_values_in_range" "$SDSL_ROOT/include/sdsl/wm_int.hpp" \
    || die "$SDSL_ROOT has the original SDSL, the extended one is needed (github.com/adriangbrandon/sdsl-lite)"

# --- sources -------------------------------------------------------------------
cd "$REPO"
git rev-parse --verify -q "$BASELINE_REF^{commit}" >/dev/null || die "unknown ref $BASELINE_REF (fetch the tags?)"
git rev-parse --verify -q "$CANDIDATE_REF^{commit}" >/dev/null || die "unknown ref $CANDIDATE_REF"

build_version() {
    local name="$1" ref="$2"
    local src="$OUT/src/$name" bld="$OUT/build/$name"
    log "$name: checking out $ref"
    if [ -e "$src" ]; then git worktree remove --force "$src" 2>/dev/null || rm -rf "$src"; fi
    git worktree prune
    mkdir -p "$OUT/src"
    git worktree add -q --detach "$src" "$ref"
    if [ -n "$TIMEOUT" ]; then
        # The same change in both versions: the limit is a compile-time constant.
        sed -i.orig "s/^#define TIME_OUT [0-9]*/#define TIME_OUT $TIMEOUT/" "$src/src/query_config.hpp"
        grep -q "^#define TIME_OUT $TIMEOUT\$" "$src/src/query_config.hpp" || die "$name: could not set TIME_OUT"
        rm -f "$src/src/query_config.hpp.orig"
        log "$name: TIME_OUT set to $TIMEOUT s"
    fi
    log "$name: building"
    rm -rf "$bld"
    cmake -S "$src" -B "$bld" -DCMAKE_BUILD_TYPE=Release -DSDSL_ROOT="$SDSL_ROOT" > "$OUT/build-$name.log" 2>&1
    cmake --build "$bld" -j "$JOBS" >> "$OUT/build-$name.log" 2>&1 || die "$name failed to build (see $OUT/build-$name.log)"
    for exe in build-index-basic build-index-split query-index-basic query-index-split; do
        [ -x "$bld/$exe" ] || die "$name: $exe was not built"
    done
}

build_version baseline "$BASELINE_REF"
build_version candidate "$CANDIDATE_REF"

if [ "$RUN_TESTS" = 1 ]; then
    log "candidate: running the test suite"
    (cd "$OUT/build/candidate" && ctest --output-on-failure) > "$OUT/tests-candidate.log" 2>&1 \
        || die "the test suite of the candidate failed (see $OUT/tests-candidate.log)"
fi

# --- description of the build ---------------------------------------------------------
{
    echo "date: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "host: $(hostname)"
    echo "baseline: $BASELINE_REF $(git rev-parse "$BASELINE_REF^{commit}")"
    echo "candidate: $CANDIDATE_REF $(git rev-parse "$CANDIDATE_REF^{commit}")"
    echo "sdsl_root: $SDSL_ROOT"
    echo "timeout_seconds: $(awk '/^#define TIME_OUT/ {print $3}' "$OUT/src/candidate/src/query_config.hpp")"
    echo "compiler: $(c++ --version 2>/dev/null | head -1)"
    echo "cmake: $(cmake --version | head -1)"
    echo "flags_baseline: $(grep -h 'CXX_FLAGS' "$OUT/build/baseline/CMakeFiles/query-index-basic.dir/flags.make" | head -1)"
    echo "flags_candidate: $(grep -h 'CXX_FLAGS' "$OUT/build/candidate/CMakeFiles/query-index-basic.dir/flags.make" | head -1)"
} > "$OUT/build/info.txt"

log "done: $OUT/build/{baseline,candidate}"
cat "$OUT/build/info.txt"
