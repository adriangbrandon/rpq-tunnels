# Tests

Safety net for the refactoring of Ring-RPQ. It needs Python 3 and a build of the
project (`cmake -S . -B build && cmake --build build`).

## Regression suite

```bash
python3 tests/regression.py --build-dir build
```

For every dataset in `tests/data` and both index variants (`basic`, `split`) it
builds the index, runs the queries and compares the output with the golden
files in `tests/golden`:

- `<dataset>.<variant>.golden`: `query count digest` per query (the digest is a
  SHA-256 of the sorted solutions, duplicates included) and the exit code.
- `<dataset>.oracle`: the same for the correct answers, computed by
  `tools/rpq_oracle.py`, a naive product-graph evaluator independent from the
  C++ code.

`--details` re-runs the oracle on the queries that disagree with it and tells
missing, extra, reversed and duplicate pairs apart.

The golden files record the current behaviour, including known bugs (see
`docs/REFACTORING.md`). A refactoring step must not change them. A step that
fixes a bug re-records them with `--update`, and the diff of `tests/golden`
must show exactly the queries that now agree with the oracle.

Differences with the oracle are listed for each run. `--strict-oracle` makes
them fail the run (the goal once the known bugs are fixed).

## Datasets

`tests/data` is generated deterministically by

```bash
python3 tests/tools/gen_dataset.py --out tests/data \
    --logs data/wikidata/paths.tsv data/wikidata/paths-split-73.tsv data/yago2s/paths.tsv
```

- `metro`: the running example of the paper (Fig. 1), plus one query of each shape.
- `rand-small`, `rand-medium`, `rand-dense`: random graphs with skewed degrees.

The query shapes are the distinct patterns of the benchmark logs in `data/`,
instantiated with the dataset's own predicates and nodes. A few extra shapes
cover cases the logs do not contain (e.g. constant-to-constant queries).

## Performance baseline

```bash
python3 tests/tools/gen_dataset.py --bench --out /tmp/ring-data --logs data/*/*.tsv
python3 tests/benchmark.py --build-dir build --data /tmp/ring-data/bench \
    --variant basic --compare tests/baseline/bench-basic.json
```

`tests/baseline/bench-{basic,split}.json` hold the per-query median times (5
runs) and result counts of the code before the refactoring, measured on an
Apple M1 Pro (about 16 s and 8.5 s per run; run-to-run noise is about 1 %).
`--compare` fails if the counts change or if the total time or the geometric
mean of the per-query ratios grows more than 3 %.

Absolute times depend on the machine and, on a laptop, on power management
(battery level, low power mode). The reliable check is to run a reference
build interleaved with the current one under the same conditions:

```bash
git worktree add /tmp/ring-p0 phase0-baseline
cmake -S /tmp/ring-p0 -B /tmp/ring-p0/build && cmake --build /tmp/ring-p0/build
python3 tests/benchmark.py --build-dir build --against-build /tmp/ring-p0/build \
    --data /tmp/ring-data/bench --variant basic
```

Time ratios only use the queries whose result count did not change (a bug
fix changes the work done); the others are listed separately. The same script
works with the Wikidata and YAGO2s datasets from Zenodo.
