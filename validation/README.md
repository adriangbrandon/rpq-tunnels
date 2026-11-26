# Validation on the datasets of the paper

These scripts compare, on a server, the original code with the refactored
one on the real datasets (Wikidata and YAGO2s): results of every query,
query times, index construction time, memory and index size.

- **baseline**: tag `phase0-baseline`, the published algorithm with only a
  working Release build (the original `CMakeLists.txt` always compiled
  without `-O3`) and the option to dump the solutions.
- **candidate**: the refactored code (by default, the commit checked out).

Both versions are run interleaved (alternating which one goes first) so
that the state of the machine affects both alike.

## Requirements

- Linux, `git`, `cmake` >= 3.12, a C++17 compiler, `python3` and GNU time
  (`/usr/bin/time -v`, package `time`) for the peak memory.
- The repository with the tag `phase0-baseline` and the refactoring
  branch (push them, or copy this directory to the server).
- The extended SDSL (github.com/adriangbrandon/sdsl-lite); `build.sh` can
  install it.
- Memory: the paper built the Wikidata index with 73 GB of RAM (104 GB with
  the splitting support); the query drivers also load the node dictionary
  (`.dat.SO`, 13 GB of strings for Wikidata) into memory.

## Datasets

From https://zenodo.org/record/7254968. Each one comes as `<name>.tar.gz`
(identifiers in lexicographic order) and `<name>.bfs.tar.gz` (BFS order,
Section 5.1). Extract each archive in its own directory; it must contain
`<name>.dat`, `<name>.dat.SO` and `<name>.dat.P`. The systems of the paper
are:

| System | Dataset | Variant |
|---|---|---|
| Ring | `<name>` | `basic` |
| RingA | `<name>.bfs` | `basic` |
| RingAB | `<name>.bfs` | `split` |

## Time limit per query

The limit is a compile-time constant of the query drivers (600 s in the
code). The paper used 60 s for the Wikidata benchmark of Table 2 and 600 s
for the 73 complex RPQs and for YAGO2s. `build.sh --timeout N` sets the
same value in both versions; each value needs its own `--out` directory.

With 600 s the Wikidata benchmark takes much longer than in the paper:
`paths.tsv` has 130 variable-to-variable RPQs with `*` or `?`, whose
answers include every node with itself (hundreds of millions of pairs on
Wikidata), and they run until the limit. The paper only used the queries
with fewer than one million results.

## Running

```bash
# 1. Build both versions twice, with the limits of the paper.
#    --install-sdsl clones and installs the extended SDSL if it is not there.
validation/build.sh --sdsl-root "$HOME/sdsl" --install-sdsl --timeout 60 --out validation-out-60
validation/build.sh --sdsl-root "$HOME/sdsl" --out validation-out-600

# 2. Wikidata, 2,110 queries (Table 2 and Fig. 12), 60 s per query
validation/run.sh /data/wikidata --out validation-out-60 --variants basic     # Ring
validation/run.sh /data/wikidata.bfs --out validation-out-60                  # RingA and RingAB

# 3. Wikidata, the 73 complex RPQs of Fig. 13, 600 s per query (slow: ~2 h
#    per run in the paper, so a single run)
validation/run.sh /data/wikidata.bfs --out validation-out-600 --reps 1 \
    --queries data/wikidata/paths-split-73.tsv

# 4. YAGO2s (55 queries), 600 s per query
validation/run.sh /data/yago2s.bfs --out validation-out-600                   # RingA and RingAB
```

Steps 2 and 3 build the indexes of `wikidata.bfs` once each (one per
`--out`); to save that hour, the index directory of step 2
(`validation-out-60/work/wikidata.bfs`) can be copied to
`validation-out-600/work/` and step 3 run with `--reuse-indexes`.

Options of `run.sh` (`--help`): `--reps N` (runs of each version, default
3), `--variants basic,split`, `--queries F1,F2`, `--name NAME` (label of the
results, default the directory name), `--build-both` (also build the
indexes with the baseline, to compare construction time and memory and
check that the files are identical), `--reuse-indexes`, `--no-digests`
(times only), `--keep-solutions`.

The results of both versions are compared in the first run: the solutions
of each query stream through a pipe into a digest (count and SHA-256), so
neither memory nor disk grows with the number of results.

The indexes are built once per variant with the candidate and used by both
versions: their files are byte-for-byte identical (checked by the
refactoring tests; `--build-both` checks it on the real data).

Rough duration of step 2 with the default 3 runs and the 60 s limit: the
paper reports 0.6-1.4 s per query on average on Wikidata, so 20-50 minutes
per run and version (more if the queries with huge results dominate),
plus the index construction (about 1 hour each).

## Checking the hash of the answer sets

`validation/hash_check.sh <bfs-dataset-dir> --index-from <work-dir>` compares
the hash of the answer sets (`31a+b`) with the original code and with the
previous refactored version on the 103 queries that use those sets and have
at least one million answers, plus the peak memory on 3 queries that reach
the limit. See `--help`; it writes `results-hash-check.tar.gz`.

`validation/disjunction_check.sh <bfs-dataset-dir> --index-from <work-dir>` finds
out whether the hash causes the slowdown of the 29 disjunctions: it compares XOR
with `31a+b` on the same code (tag `phase5-xor-hash`), and the original code with
XOR. See `--help`; it writes `results-disjunction-check.tar.gz`.

`validation/noexcept_check.sh <bfs-dataset-dir> --index-from <work-dir>` compares
the hash with `noexcept` with the same code without it (tag `phase5-cached-hash`):
time on the 103 queries and peak memory on 4 queries that reach the limit. See
`--help`; it writes `results-noexcept-check.tar.gz`.

## Repeating the validation with the final version

`validation/final_validation.sh <wikidata-dir> <wikidata-bfs-dir>` runs only the
candidate on `paths.tsv` and on the 73 complex RPQs, reusing the indexes and the
times of the original code of `validation-out-60` and `validation-out-600`. It is
safe to run it again after a failure: finished steps are skipped. See `--help`;
it writes `results-final.tar.gz`.

## What to send

For each dataset, `validation-out/results-<name>.tar.gz` (a few MB: times,
memory, logs and one digest per query; not the solutions). The
`summary.md` inside gives a first look:

- index construction time, peak memory and size;
- for each variant and query file: times of both versions, the
  candidate/baseline time ratio on the queries with the same results,
  timeouts, and the queries whose results differ, each one labelled with
  the bug fix of `docs/REFACTORING.md` that explains it (B3, B4, B5, ...)
  or as UNEXPLAINED.

`summary.json` has the same data in full, including times by query shape.

## Expected differences

- The baseline ends with exit status 134 (bug B1: it aborts after the last
  query); all its results are printed before.
- Results differ where the baseline was wrong: constant-to-constant queries
  (B3), `?x ^p <c>` and `<c> p ?y` (B4), and many variable-to-variable
  queries of the split variant (B5, B6). Any UNEXPLAINED difference needs a
  look.
- Queries whose results differ do different work, so they are left out of
  the time ratios and listed apart.
- A query that reaches the time limit returns the results found so far,
  which depend on the speed of each version: such differences are labelled
  `timeout` (not comparable).
