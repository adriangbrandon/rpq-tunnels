# Tunnels over Ring-RPQ

Implementation of tunnels (a compressed representation of the solution subgraph H of an RPQ) on top of
the Ring of Ring-RPQ (`adriangbrandon/ring-rpq`, on the refactored version: `ring_rpq.hpp` and its
planner). It measures real construction and
expansion times, and space in bits.

## What changes in the repo

- `src/ring-tunnels.cpp` (new): the program. It builds two binaries:
  - `ring-tunnels` on the basic Ring (`build-index-basic`);
  - `ring-tunnels-split` on RingAB (`build-index-split`), which also does the Ring-RPQ split for
    v-to-v.
- `src/tunnel_backends.hpp` (new): the graph representations (Ring, RingAB, navGraph,
  adjacency lists) behind the same interface.
- `src/gen-queries.cpp` (new): generates RPQs from expression shapes.
- `src/ring_rpq.hpp`: only public access methods are added (`subjects_bwt`, `predicates_bwt`,
  `max_subject`, `max_object`) and `reaches_marked`, the c-to-c search with an already marked automaton.
  The RingAB split is the one of `plan_split`. The Ring-RPQ evaluation does not change.
- `src/bwt-C-nose.hpp`: `operator[]` and `count_distinct_values` on `L_S` are back.
- `src/query_config.hpp`: `TIME_OUT` can be redefined at compile time.
- `CMakeLists.txt`: the targets. `cmake -DRING_TIMEOUT=60 ..` sets the timeout (s) of the Ring's own
  evaluations that are used as the baseline (60 by default).

## What it does, per query

1. **Ring** (a baseline that only delivers answers): the original Ring-RPQ evaluation, with the same
   strategy that its planner chooses (`solve` of `src/planner.hpp`), like `query-index-basic` /
   `query-index-split`.
2. **Product graph R**: the same traversal, but with one product node per (node, state) instead of
   the bit-parallel sets of states. Expanding (v, j) uses the same Ring primitives:
   - the active labels of v for j (wavelet tree of `L_P` marked with `B_array`);
   - the range of `L_S` of each label, whose distinct values are the neighbors.
3. **H**: the nodes of R that reach an accepting node ((t, 0) for pairs).
4. **Selection of S** (region model of the prototype) for each budget. The best one in bits is
   kept among three valid candidates:
   - the region pass;
   - the W = 0 set;
   - the pass with the expensive shared nodes stored beforehand.

   The bound per region adds up the children, so in DAGs a shared node is counted several times;
   the third candidate avoids that.
5. **Encoding in bits**:
   - the keys of S go in Elias-Fano (`sd_vector`);
   - each node stores its list of live children or of dead ones, whichever is shorter, as local indexes
     coded by gaps with delta codes;
   - the local index of a child is the position of the neighbor in the range of `L_S` (which is sorted)
     by |next| plus the rank of the state.
6. **Expansion**: BFS from the root.
   - A stored node reads its list: with a live list it accesses `L_S[...]` directly, without enumerating; with
     a dead list it enumerates and skips the dead ones.
   - A node that is not stored is re-expanded with the Ring and explores its dead branches.
   - At the end comes the liveness pass.
   - It is **verified** that what was expanded is exactly H (nodes, edges, hash); column `verify`.

Baselines in each row:
- **Explicit H**: the same format, with all the internal nodes of H and their live lists
  (`H_bits`, `t_H_expand`).
- `H_bits_global`: size with global ids.
- **Recompute**: the row `budget=inf` (nothing stored).
- `t_ring`: time of the Ring.

### v-to-v

The expression is cut at one point with seed nodes C.
- `--vv ring` (RingAB): the `split_rpq` of Ring-RPQ (`h_sum_path_intersection`, source/target/intersection
  cuts) gives `rpq_l`, `rpq_r`, `elements` and `first_left`.
- `--vv bridge` (basic Ring): among the mandatory predicates (`RpqTree::getMandatoryData`, which
  are bridges of the NFA) the cut at the source or at the target with fewer distinct nodes is chosen. The
  seeds are those nodes: it is a cut by nodes, not by edges as in the prototype. The side that does
  not start with the cut label is run first.

Each side is a multi-source product graph:
- side 1 is built from all the seeds;
- side 2, from those that have a solution on side 1 (as in Ring-RPQ);
- L are the seeds that are live on both sides.

Representation: the S of each side plus a list of seeds.
- With a budget, the decoder explores the cheapest dead seeds. Their cost is the
  closure on side 1, or the extra work on side 1 plus the closure on side 2.
- The remaining dead seeds go as an exclusion list, or the list of live ones is stored if it is
  shorter.
- The decoder regenerates C with the Ring, decodes the list, expands side 1 and then side 2
  from the seeds that are live on side 1, and restricts to L.
- It is verified against the H of each side. When the number of pairs is manageable (`--pair-limit`), it
  is also compared with the answers of Ring-RPQ (`answers` against `ring_answers`).

**Per-source baseline** (rows `VVs`, with `--vv-samples K`):
- K random sources of S0, the nodes that can start the expression;
- each one is processed as a full c-to-v;
- the summary estimates |S0| × the mean (`seeds` = |S0|).

Row types: `CV`, `VC`, `CVp`/`VCp` (pairs, with `--pairs K`), `VVr` (Ring cut), `VVb` (bridge
cut), `VVs` (per-source samples).

## Reproducing the paper

The whole flow is launched with `tunnels/reproduce_paper.sh`, which uses the `run_*.sh` scripts and the
Python scripts below without duplicating them. The data paths are configured **in a single file**; the
scripts assume no server path.

1. Copy the template and fill it in (`tunnels/paper.env` is not versioned: it is in `.gitignore`):

   ```bash
   cp tunnels/paper.env.example tunnels/paper.env
   $EDITOR tunnels/paper.env     # at least DATA_DIR and OUT_DIR
   ```

   | Variable | What it is |
   |---|---|
   | `DATA_DIR` | required: directory of the data on the server |
   | `WD_DAT` | Wikidata `.dat` (default `$DATA_DIR/wikidata-enumerated.dat`); read only |
   | `WD_DICT_P`, `WD_DICT_SO` | dictionaries (default `$DATA_DIR/wikidata-unsorted-enumerated.dat.{P,SO}`) |
   | `WD_QUERIES` | log of 1,589 RPQs (default `$DATA_DIR/paths-final.tsv`) |
   | `SHAPES_CSV` | `WD_organic.csv` by Hammerer & Martens (https://zenodo.org/records/15363430) |
   | `OUT_DIR` | required: output; it cannot be inside the directory of `WD_DAT` |
   | `BUILD_DIR` | binaries; empty: `<repo>/build` |
   | `SDSL_ROOT` | prefix of the extended SDSL, for the `build` step |
   | `NAVGRAPH_DIR` | navGraph source; empty: Compact (navGraph) is skipped and `backends` only does `adj` |
   | `MDB_RESULTS`, `MDB_RAW` | optional, both or neither: `results.csv` from `join_pf.py` and `raw.csv` from `run_pf.py` of the MillDB-PathFinder repo (MillenniumDB times, see "MillenniumDB times"); if empty, the time table has no t_MDB columns |
   | `RING_TIMEOUT` | time limit (s) of the Ring baselines (default 60) |
   | `GEN_N`, `GEN_SEED` | generated queries: number and seed (default 1000 and 1) |
   | `EXTRA_OPTS` | extra options for `ring-tunnels` (`--max-queries 50` for a test) |

2. See what it is going to do, without running anything:

   ```bash
   tunnels/reproduce_paper.sh --list        # the steps
   tunnels/reproduce_paper.sh --dry-run     # validates the configuration and prints each command ("+ ...")
   ```

   Validation runs before executing and lists **all** the problems together (exit code
   2, nothing created): empty variables, missing inputs, missing binaries, `OUT_DIR` inside
   the directory of `WD_DAT`. It warns (`warning:`) without blocking if `/usr/bin/time` or matplotlib is missing.

3. Launch it (`nohup` is advisable; the output of the steps also goes to `$OUT_DIR/reproduce.log`):

   ```bash
   nohup tunnels/reproduce_paper.sh > reproduce.out 2>&1 &
   tunnels/reproduce_paper.sh --only figures,tables        # only some steps, in their order
   tunnels/reproduce_paper.sh --config other.env           # another configuration
   ```

   Each step prints `== step: start` and `== step: ok (N s)`; if one fails, `== step: FAILED` and the
   script ends with a non-zero exit code without going on. Runs can be resumed: the indexes already
   built are not rebuilt, although the CSVs are rewritten.

### Steps and what each one produces

| Step | Does | Produces |
|---|---|---|
| `build` | `cmake -S <repo> -B $BUILD_DIR -DSDSL_ROOT=… -DRING_TIMEOUT=… [-DNAVGRAPH_DIR=…]` and `cmake --build` | — |
| `log` | `run_wikidata.sh $WD_DAT $OUT_DIR $EXTRA_OPTS` | graph representations (Ring, RingAB), size and construction time of H and S, c-to-v on the Ring; figures |
| `generated` | `run_generated.sh $OUT_DIR $SHAPES_CSV $GEN_N $GEN_SEED $EXTRA_OPTS` | generated queries against the explicit H and generated c-to-v by shape |
| `backends` | `run_backends.sh $OUT_DIR both $EXTRA_OPTS` (`adj` if `NAVGRAPH_DIR` is empty) | graph representations (Compact, Plain), construction times; Compact and Plain series of the figures |
| `pathfinder` | `run_pathfinder.sh $OUT_DIR $EXTRA_OPTS` (with `MDB_RESULTS` and `MDB_RAW` exported) | comparison with PathFinder in `pathfinder_paper.md` |
| `automata` | `run_automata.sh $OUT_DIR $EXTRA_OPTS` | `--automata` (outside the paper) |
| `figures` | `paper_figures.py $OUT_DIR $OUT_DIR/figures` and, if it exists, the same with `gen-$GEN_N-$GEN_SEED` into `$OUT_DIR/figures-gen` | `ctov.pdf`, `ctoc.pdf`, `vtov.pdf` and `numbers.md` |
| `tables` | `paper_tables.py $OUT_DIR --generated $OUT_DIR/gen-$GEN_N-$GEN_SEED > $OUT_DIR/tables.md` (if the script does not exist, it warns and is skipped) | the six tables of `tables.md` |

Outputs, by content (the scripts do not number them): "Graph representations: space, construction time and
peak memory" = bytes per edge, construction and peak memory (from `index.log` and `index_sizes.txt`);
"Size of H and S per query class"; "Construction time of R, H and S"; `ctov.pdf` and "c-to-v on the Ring:
space of S relative to H" = c-to-v on three representations; `ctoc.pdf` = c-to-c with the best
orientation; `vtov.pdf` = v-to-v; "Generated queries relative to the explicit H" and "Generated c-to-v
queries by shape" = generated queries; "Comparison with PathFinder" = PathFinder against H_sp and S, from
`pathfinder_paper.md`. `run_wikidata.sh` no longer launches `--shortest`: `sp.csv` is not generated (without
`--early-stop` it does not appear in the paper, and the CV/VC rows are identical in `sp_early.csv`). The PDFs of the
figures are not versioned.

The order of the steps matters: `pathfinder` needs the adjacency-list index
(`$OUT_DIR/adj/wd.dat.adj`) that `backends` builds. If it is launched alone (`--only pathfinder`) without
having run `backends` before, validation says so (exit code 2). Its two runs go in sequence
(`adj-tunnels` takes about 61 GB on Wikidata).

### MillenniumDB times (time table of `pathfinder_paper.md`)

The MillenniumDB times are not measured by this repo: they come from the MillDB-PathFinder repo (`pf/README.md`:
image with the *build-only* patch, `translate.py`, `run_pf.py` and `join_pf.py`). Its two files are
passed as `MDB_RESULTS` (`results.csv` from `join_pf.py`) and `MDB_RAW` (`raw.csv` from `run_pf.py`);
`pathfinder_tables.py --paper` does the join (see "Comparison with PathFinder").

### Small-scale test: `reproduce_small.sh`

```bash
tunnels/reproduce_small.sh [--dataset metro|rand-small|rand-medium] [--keep] <out>
```

It sets up in `<out>/data` a `DATA_DIR` with a dataset of `tests/data` (`rand-medium` by default; the
predicates are renamed to `/prop/direct/`, as in `tests/tunnels_repro_test.py`), writes
`<out>/paper.env` (`GEN_N=10`, `EXTRA_OPTS="--reps 1"`, no navGraph, `MDB_*` empty) and launches
`reproduce_paper.sh` with all the steps except `build` (the binaries must be in `BUILD_DIR`, by
default `<repo>/build`). It leaves in `<out>/out` `tables.md`, `pathfinder_paper.md` and, with matplotlib,
the figures. It takes about 50 s with `rand-medium` in Release. If `<out>` exists and is not an output of
this script, it ends with exit code 2 without touching anything; if it is an output of its own, it deletes it first, except with
`--keep`, which reuses what is there. It is covered by the `tunnels_small` test of `ctest`.

### Resources and dependencies

- **Resources.** The representations table of the paper reaches **103 GB of RAM and 2.4 h per index**. The runs of
  `run_wikidata.sh` launch three processes in parallel, each with its index loaded (about 30 GB each).
  It needs a large server; it is not reproducible on a laptop.
- **Extended SDSL** (Adrián's), in `SDSL_ROOT`; **CMake**, g++ with C++17 and Python 3.
- **navGraph** (github.com/j-rparra/navGraph), only for Compact; without `NAVGRAPH_DIR` it is skipped and the
  representations table and the figures lack that series.
- **`/usr/bin/time`** (GNU time): without it, `index.log` does not record peak memory or build time
  and the representations table lacks those columns. It does not fail silently: the script warns.
- **matplotlib**, to draw the figures; without it, the `figures` step does not draw.

### What to expect from the results

- **Space: deterministic.** The bits of H, S, plain H and the indexes (`H_bits`, `S_bits`,
  `Hplain_bits`, `*_nodes`, `*_edges`, `index_sizes.txt`) come out the same in every run with the same
  code and the same data.
- **Times: they vary.** The paper reports the minimum of 3 repetitions (1 in v-to-v). With several runs
  sharing a machine (the three of `run_wikidata.sh` go in parallel), the same command does not give the
  same times: it depends on the CPU, the cache, the load and the compiler (the paper uses g++ 14.2). The
  approximate ratios and the order between representations are reproduced, not the seconds.
- **The code of the paper is `main`:** the version with tunnels of the `tunnels` branch is the one that was ported to the
  refactored index, and `verify` and the tests check that the result is the same.

### Queries that the paper excludes

The paper excludes (note `docs/notes/tunnels.md`, §7.8) 5 generated c-to-v queries that exceed the time
limit on Plain, out of the generated queries, and 72 v-to-v of the log that accept the empty path. The code has no
exclusion list: each query comes out in the CSV with its `status`.
- The v-to-v queries that accept the empty path come out with `status=eps_source`, without measurements.
- Those that exceed `--timeout`, `--max-nodes` or `--max-edges` come out with `status=timeout` or `too_large`.
- The summaries and figures (`ring_summary.py`, `paper_figures.py`) only aggregate the rows with
  `status=ok`.

The scripts of `tunnels/` do not cross queries between representations: a query that fails on Plain
still counts on the Ring unless whoever builds the table excludes it. Which `--timeout` or `--max-nodes`
left those 5 queries out in the paper remains open.

## Building

The extended SDSL (Adrián's, `sdsl-lite` from the tar.gz) is needed, installed in a prefix of its own,
and CMake. With `reproduce_paper.sh` the `build` step compiles everything: it reads `SDSL_ROOT`, `RING_TIMEOUT` and
`NAVGRAPH_DIR` from `tunnels/paper.env` (see "Reproducing the paper"). By hand:

```bash
cmake -S . -B build -DSDSL_ROOT=<prefix of the extended SDSL> -DRING_TIMEOUT=60 [-DNAVGRAPH_DIR=<navGraph>]
cmake --build build -j
```

The `~/ring-tunnels/...` paths below are just an example installation (the SDSL in
`~/ring-tunnels/deps`, without touching `~/include` or `~/lib`):

```bash
cd ~/ring-tunnels/sdsl-lite && ./install.sh ~/ring-tunnels/deps
cd ~/ring-tunnels/Ring-RPQ && mkdir -p build && cd build
cmake -DSDSL_ROOT=$HOME/ring-tunnels/deps -DRING_TIMEOUT=60 .. && make -j8 build-index-basic build-index-split ring-tunnels ring-tunnels-split
```

## Running on Wikidata

The data is not in the repo. The scripts take it from the following environment variables, or from
`tunnels/data/` if they are not defined (as it used to work); `reproduce_paper.sh` fills them in from
`tunnels/paper.env`:

| Variable | Default | What it is |
|---|---|---|
| `BUILD_DIR` | `<repo>/build` | directory of the binaries (the five scripts) |
| `WD_DICT_P` | `tunnels/data/wikidata-unsorted-enumerated.dat.P` | predicate dictionary (`run_wikidata.sh`, `run_backends.sh`) |
| `WD_DICT_SO` | `tunnels/data/wikidata-unsorted-enumerated.dat.SO` | subject and object dictionary (idem); both are linked, not copied, with absolute paths (a relative value is resolved first) |
| `WD_QUERIES` | `tunnels/data/paths-final.tsv` | RPQ log (all five; in `run_generated.sh`, the predicate *pool*) |

From the graph it only **reads** the `.dat` and the dictionaries (it leaves symlinks to them in the output directory; nothing is copied) and writes everything under the
output directory, which cannot be inside the directory of the `.dat` nor be a foreign directory
that already exists:

```bash
nohup tunnels/run_wikidata.sh <DATA_DIR>/wikidata-enumerated.dat ~/tunnels-out > ~/tunnels-out.log 2>&1 &
```

The script:
- builds both indexes (once) in `~/tunnels-out/{basic,split}`;
- runs, with the paper configuration (`--uniform-cost`), c-to-v with 5 c-to-c pairs per query
  solved from both ends, v-to-v bridge together with the PMR of all the sources, and v-to-v split;
- does not run the comparison with PathFinder (`--shortest`): that is the `pathfinder` step
  (`run_pathfinder.sh`, see "Comparison with PathFinder");
- leaves the summaries in `summary_*.md`.

It is advisable to launch it with `nohup`. The three runs are independent and can be launched in parallel
if there is memory: each index takes about 12–16 B per triple and each query uses on the order of 100 B per
product node.

All the options are described in "Options of `ring-tunnels`". The most used:
`--max-queries N` and `--only q1,q2` (subsets), `--reps 3` (repetitions of each expansion; the
minimum is reported), `--uniform-cost` (cost model of the paper: without it, the Ring uses the labels
that w has and the results are not comparable between representations) and `--rel 1,4,16 --rel-slack 10`
(relative budgets c·out+W₀).

The figures of the paper (averages per query) come from `tunnels/paper_figures.py`, from
the CSVs of these runs and those of `run_backends.sh`. The whole flow is in "Reproducing the paper".

## Options of `ring-tunnels`

The four binaries (`ring-tunnels`, `ring-tunnels-split`, `adj-tunnels`, `nav-tunnels`) share the
same argument parser of `main()` (`src/ring-tunnels.cpp`): `<index> <queries> [options]`.
An unknown option, or one without a value, prints the usage and ends with exit code 1. This table lists **all** of them,
including those that `usage()` does not show (`--baseline-timeout`, `--no-per-source`). In "Binaries", "all"
are the four. The default values are those of the code, which are not those of the paper (the scripts pass
their own). In "Paper", "yes" means that the `run_*.sh` use it with the paper configuration; "no", that
it exists but the paper does not use it; "later", that it was added after the paper.

| Option | Default | What it does | Generates | Binaries | Paper |
|---|---|---|---|---|---|
| `--out f.csv` | `ring_tunnels.csv` | output CSV | the file | all | yes |
| `--budgets b1,b2,..` | `0,10,100,1000,inf` | absolute work budgets of the region model; `none` omits them; `inf` = store nothing (recompute) | one row per budget, `model=region`, `budget=<b>` | all | yes (`0,1000,10000,100000,inf`; in v-to-v `0,1000,inf`) |
| `--rel c1,c2,..` | `4` | relative budgets c·out+W₀; `none` omits them | rows `model=region_rel`, `budget=<c>x+<W₀>` | all | yes (`1,4,16`; in v-to-v `4,16`) |
| `--rel-slack w1,..` | `10` | the constant W₀ of the relative ones | idem | all | yes (`10`) |
| `--pairs K` | `0` | K c-to-c pairs per c-to-v / v-to-c query, taken from their answers | rows `CVp` / `VCp` | all | yes (`5`) |
| `--pair-reverse` | off | also solves each pair from its target | rows `CVr` / `VCr` (the paper uses the best orientation per pair) | all | yes |
| `--vv-union` | off | v-to-v baseline: a single multi-source PMR from all the valid sources. Works with `--vv none` | rows `VVu` | all | yes |
| `--uniform-cost` | off | analysis cost of (w,q) = labels leaving q, also on the Ring (by default, those that w has) | changes `t_cost`, `S_*`, `replay_model` | all | yes (always) |
| `--shortest` | off | ALL SHORTEST WALKS: H becomes H_sp and each row carries the PathFinder simulation. It does not allow `--vv ring/bridge/both`, `--vv-union` or `--automata` | `sem=shortest` and columns `pf_*`, `t_pf` | all | no (later; it is `sp_early.csv` of `run_pathfinder.sh`) |
| `--early-stop` | off | with `--shortest`, in the rows with a fixed target (`CVp`, `CVr`, `VCp`, `VCr`) the three BFSs (product R, PathFinder simulation and S decoder) stop expanding at the layer where the target is first found. Without `--shortest`: exit code 1. It does not change the columns (the run is told apart by the file name, `sp_early.csv`); H_sp does not change, R, S and `pf_*` can be smaller | same columns | all | no (later) |
| `--automata` | off | only compares the Glushkov, reverse Glushkov and Thompson products on each c-to-v / v-to-c | its own CSV (columns of `--automata`, below): 3 rows per query | only `adj-tunnels` (in the others: exit code 1) | no (later) |
| `--vv ring\|bridge\|both\|none` | `ring` with `ring-tunnels-split`; `bridge` in the others | cut for v-to-v | rows `VVr` (ring), `VVb` (bridge) | `ring` and `both` only in `ring-tunnels-split` | yes (`bridge` with the basic one, `ring` with the split one, `none` in c-to-v) |
| `--vv-derive` | off | also runs the v-to-v obtained by removing the constant of each c-to-v / v-to-c | rows `VV*` with `src=derived`; repeated ones come out with `status=duplicate` | all | yes |
| `--vv-only` | off | only v-to-v queries (with `--vv-derive`, only the derived ones) | omits the c-to-v rows | all | yes |
| `--vv-samples K` | `5` | sources sampled for the v-to-v per-source baseline; 0 omits it | rows `VVs` | all | yes (`0`; with `--shortest`, `5`) |
| `--no-per-source` | — | equivalent to `--vv-samples 0` | — | all | no |
| `--reps R` | `3` | repetitions of each expansion; the minimum is reported | times `t_expand`, `t_H_expand`, `t_Hplain_expand` | all | yes (3) |
| `--vv-reps R` | the value of `--reps` | repetitions in the v-to-v cuts | idem in v-to-v | all | yes (`1`) |
| `--timeout s` | `600` | maximum time (s) to build a product | `status=timeout` | all | no |
| `--max-nodes N` | `200000000` | maximum nodes of the product | `status=too_large` | all | no |
| `--max-edges N` | `1000000000` | maximum edges of the product | `status=too_large` | all | no |
| `--cost-cap N` | `10000` | cap on the work when exploring a dead branch; a node with a branch that reaches it is always stored | changes the selection of S | all | no (appendix A of the paper raises the cap to 10⁷ for one query) |
| `--pair-limit N` | `20000000` | maximum number of pairs to compare the v-to-v answers with those of Ring-RPQ | `answers` against `ring_answers` | all | no |
| `--only q1,q2,..` | all | only the queries with those `qid` | — | all | no |
| `--max-queries N` | all | only the first N queries (with 0 none is run: `run_backends.sh` uses it to build `<dat>.adj`) | — | all | no |
| `--seed s` | `1` | seed of the sampling of pairs and sources | `dst` of `CVp`, `src` of `VVs` | all | no |
| `--no-ring` | off | does not run the Ring baseline | `ring_answers`, `t_ring` empty | all (no effect in `adj-tunnels`, which has no baseline) | no |
| `--no-ring-vv` | off | does not run the baseline in the v-to-v queries | idem in v-to-v | idem | no |
| `--ring-vv-derived` | off | also times Ring-RPQ on the derived v-to-v queries (can be very slow) | `t_ring`, `ring_answers` | those with a baseline (Ring, RingAB, navGraph) | no |
| `--baseline-timeout s` | navGraph: 60; Ring and RingAB: `RING_TIMEOUT` of the build; `adj-tunnels`: no limit | threshold from which the baseline is marked `timeout`. Only `nav-tunnels` applies it to the baseline itself; on the Ring the limit is set at compile time | `ring_answers=timeout` | all | no |

Notes:
- The `run_*.sh` pass their own options and append the extra ones at the end (`EXTRA_OPTS` from `paper.env`,
  or the arguments you give them by hand). If an option is repeated, the last one wins, so `EXTRA_OPTS`
  can override (for example `--max-queries 50` for a quick test).

## CSV columns

One row per query and configuration; fields that do not apply are left empty. Order and names of
`COLS` in `src/ring-tunnels.cpp`.

| Column | What it is |
|---|---|
| `qid`, `line` | number of the query and line of the queries file |
| `kind` | `CV`, `VC` (c-to-v and v-to-c), `CVp`, `VCp` (c-to-c pair), `CVr`, `VCr` (pair solved from the target), `VVr`, `VVb` (v-to-v with ring / bridge cut), `VVs` (per-source sample), `VVu` (PMR of all the sources), `VV` (repeated v-to-v) |
| `src`, `dst` | ids of the constants (`dst=-1`: there is none; `src=derived`: v-to-v derived from a c-to-v) |
| `shape` | shape of the expression |
| `Q` | states of the Glushkov automaton |
| `status` | `ok`; otherwise, the reason: `parse_error`, `unknown_iri`, `cc_in_file`, `id_out_of_range`, `too_many_states`, `too_large`, `timeout`, `eps_source` (the expression accepts the empty path), `duplicate` |
| `R_nodes`, `R_edges` | nodes and edges of the product graph R |
| `H_nodes`, `H_edges` | nodes and edges of H (those of R that reach acceptance) |
| `leaves` | accepting nodes of H |
| `answers`, `ring_answers` | answers of the tunnels and of Ring-RPQ (or `timeout`); they must match |
| `t_ring`, `t_product`, `t_live`, `t_cost` | seconds: Ring baseline, construction of R, computation of H (liveness) and cost analysis |
| `H_bits`, `H_bits_global` | size in bits of the differential explicit H (local ids) and with global ids |
| `t_H_expand` | seconds to expand the explicit H |
| `Hplain_bits`, `t_Hplain_expand` | size in bits and seconds to expand the plain H (dense numbering) |
| `model`, `budget` | `region` or `region_rel` and its budget (`0`, `1000`, `inf`, `4x+10`, ...); empty in baseline rows |
| `S_nodes`, `S_edges`, `S_list`, `S_bits` | stored nodes of S, edges, list entries and size in bits |
| `replay_model`, `max_region_work` | work of the region model when decoding and the largest region |
| `t_select`, `t_encode` | seconds to select S and to encode it |
| `t_build` | `t_product + t_live + t_cost + t_select + t_encode` |
| `t_expand` | seconds to expand S (minimum of `--reps`) |
| `dec_visited`, `dec_expanded`, `dec_children` | nodes visited, re-expanded with the Ring and child edges of the decoder |
| `verify` | 1 if what was expanded is exactly H (nodes, edges and hash) |
| `seeds`, `live_seeds`, `explored_seeds`, `seed_bits` | v-to-v: seeds of the cut, live ones, ones explored by the decoder and bits of their list (in `VVs` and `VVu`, `seeds` = \|S0\|) |
| `split`, `first` | v-to-v: chosen cut and side that is run first |
| `sem` | `all` or `shortest` |
| `pf_status`, `pf_dfa_states`, `pf_states`, `pf_transitions`, `pf_edges`, `pf_idx_searches`, `pf_answers`, `pf_paths`, `pf_bytes_structs`, `pf_bytes`, `pf_check`, `t_pf` | PathFinder simulation (only with `--shortest`; see its section) |

Columns of the `--automata` CSV (another format, see its section):
`qid, line, kind, shape, m, src, automaton, states, trans, eps_trans, status, R_nodes, R_edges,
R_eps_edges, lookups, scanned, H_nodes, H_edges, answers, t_product, t_live`.

## Summarizing

```bash
python3 tunnels/ring_summary.py cv.csv                  # c-to-v / v-to-c and pairs
python3 tunnels/ring_summary.py vv_all.csv --vv         # v-to-v against the per-source baseline
python3 tunnels/ring_summary.py sp_early.csv --pathfinder    # --shortest: PathFinder vs H_sp vs S
```

## Comparison with PathFinder (`--shortest`)

PathFinder, the path engine of MillenniumDB, does not support ALL WALKS (the paths can be infinite).
Its comparable mode is ALL SHORTEST WALKS, so the comparison is made under that semantics:

- **H_sp**: with `--shortest`, H becomes the subgraph of the shortest paths. These are the edges
  (x, y) of R with dist(y) = dist(x) + 1 that lead to an accepting node (dist = BFS layer
  from the constant). The selection of S, the encoding and `verify` do not change. The decoder
  computes the layers in its own BFS and discards the edges that are not shortest. The columns
  `H_*`, `Hplain_*`, `S_*` and `verify` then refer to H_sp, and `sem` is `shortest`.
- **PathFinder (columns `pf_*`)**: an exact simulation of what ALL SHORTEST WALKS stores
  (MillenniumDB `1d928c3`, `src/query/executor/binding_iter/paths/all_shortest_walks`):
  - A BFS from the constant over (node, minimal-DFA state) that explores everything
    reachable. `BFSEnum` uses it for c-to-v / v-to-c and `BFSCheck` for c-to-c, which also empties
    its queue. v-to-v runs it from every start node (`UnfixedComposite`); here, from the
    sampled sources (rows `VVs`).
  - The minimal DFA of a language is unique: building it from our automaton (subsets + Moore,
    with the same rule as `RPQ_DFA::optimize`) gives the states of PathFinder's.
  - `pf_states`: `SearchState` (40 B) stored. `pf_transitions`: `TransitionLinkedList` (32 B), one
    per arrival at a state with its optimal distance. `pf_edges`: edges traversed.
    `pf_idx_searches`: iterators opened. `pf_paths`: shortest paths that it would enumerate (its protocol
    cuts at 100,000). `pf_dfa_states`: states of the minimal DFA.
  - `pf_bytes_structs` = states × 40 + transitions × 32 is a lower bound. `pf_bytes` adds the
    containers:
    - each `SearchState` in its own node of `boost::unordered_node_set` (48 B with glibc malloc);
    - the pointer table (groups of 15 slots + 16 B, power of 2, load ≤ 0.875);
    - the arena of blocks of 4096 transitions (the first block, 128 KB, is always reserved);
    - with several final states in the DFA, the `unordered_flat_map` of optimal distances.
  - `pf_check` = 1 if the answers, the minimal distance of each one and the nodes of the graph of
    H_sp match those of PathFinder. They are two different automata (Glushkov NFA and minimal DFA)
    for the same language.
- Only c-to-v / v-to-c, pairs (`--pairs`, `--pair-reverse`) and per-source samples of v-to-v
  (`--vv-derive --vv-samples K`). `--shortest` does not allow `--vv ring|bridge|both` or `--vv-union`,
  which have no equivalent in PathFinder.
- **`--early-stop`**: in pairs (fixed target) all the shortest paths have the length d at which
  the target is discovered, and MillenniumDB's `BFSCheck` empties its queue anyway. With the option,
  the three BFSs (the product R, `simulatePF` and the decoder of S) do not expand any node of the
  layer >= d; the nodes of layer d already inserted stay as leaves. In `CVp`/`VCp` each pair builds
  its own R from the constant (it does not reuse that of the c-to-v query) and in `CVr`/`VCr` it stops at
  the original constant. The `CV`, `VC` and `VVs` rows do not change, nor does H_sp. For the server:
  `tunnels/run_pathfinder.sh $OUT` (see below).
- **`run_pathfinder.sh <out> [extra ring-tunnels options]`**: reuses the `$OUT` of
  `run_wikidata.sh` and the index of `run_backends.sh` (`adj/wd.dat.adj`; without it, error and exit code 1).
  It runs in sequence, with the `BUD` of `run_wikidata.sh` and exactly
  `--shortest --early-stop --pairs 5 --pair-reverse --vv-derive --vv-samples 5` (do not change them: the
  `rng` is global and `--vv-samples` consumes it, so other options would pick other c-to-c pairs):
  `ring-tunnels` on `basic/wd.dat` (`sp_early.csv`, `sp_early.log`) and `adj-tunnels` on
  `adj/wd.dat` (`sp_early_adj.csv`, `sp_early_adj.log`). Then it writes `pathfinder.md` and
  `pathfinder_adj.md` (`pathfinder_tables.py` of each CSV; the one for Plain is run with `$OUT/adj` as
  the directory, so its P5 uses the `cv.csv` of `adj-tunnels` and says "no data" if it does not exist), `summary_pathfinder.md`
  (`ring_summary.py --pathfinder`) and `pathfinder_paper.md` (comparison with PathFinder, `--paper`; with the
  t_MDB columns if `MDB_RESULTS` and `MDB_RAW` are defined).
- It does not measure MillenniumDB times: it compares space and work.
- `tunnels/check_pathfinder.py` recomputes in Python, from scratch, the `pf_*` columns of each row
  (Thompson NFA, subsets, Moore and PathFinder's BFS) on small graphs like those in
  `tests/data`.
- `tunnels/pathfinder_tables.py <dir> [--sp FILE] [--min-h-edges N] [--top-shapes K] > pathfinder.md` summarizes in
  Markdown tables the run in `<dir>` (`sp.csv`, or `FILE` with `--sp`, path relative to `<dir>` or absolute, and, if it exists, `cv.csv`; it works for the `$OUT` of
  `run_wikidata.sh`, with `--sp sp_early.csv` (it no longer generates `sp.csv`) and for a `gen-N-S`). Note: `t_pf` is the simulation of PathFinder's BFS on the
  same index, not a MillenniumDB time. It covers the BFS and the answers, distances and number of
  paths; it excludes the `pf_check` verification (except for storing the transitions in `tl` during the BFS, a small
  bias in favor of our method that is left in). Tables:
  - P0: validity of the data.
  - P1: space that each one keeps.
  - P1b: pruning versus compression (states of the automaton, R, H_sp).
  - P2: cost of building S versus PathFinder's BFS.
  - P2b: cost of decompressing S versus repeating the BFS.
  - P3: number of reuses from which S pays off.
  - P4: by query shape (c-to-v).
  - P5: ALL WALKS (`cv.csv`) versus ALL SHORTEST WALKS.
  - P6: number of shortest paths versus the size of H_sp and S.
- **`pathfinder_tables.py <dir> --sp FILE --paper [--adj FILE] [--mdb results.csv --mdb-raw raw.csv]`**
  prints only `Comparison with PathFinder` (Markdown); without `--paper` the output does not change. Valid rows of
  each CSV (Ring = `--sp`, Plain = `--adj`, path as with `--sp`): `sem=shortest`, `status=ok`,
  `pf_status=ok`, `region_rel 4x+10`, one per `(qid, kind, src, dst)`; if a key is repeated with
  another `t_build` or `t_pf`, error (exit code 1). It contains:
  - `Space held after evaluation (c-to-v)` (Ring only, c-to-v = CV+VC): mean per query in KB of `pf_bytes`, `pf_bytes_structs`,
    `Hplain_bits/8`, `H_bits/8` and `S_bits/8`, and the ratio of totals against S; below it, the figures
    of the text (PF/plain, plain/differential, PF states / H_sp nodes in c-to-v and c-to-c, edges and
    plain bytes per pair in c-to-c).
  - `Construction time relative to PathFinder (ratios of sums)`: ratios of sums t_b/t_pf and t_b/t_MDB per class (c-to-v = CV+VC, c-to-c = CVp+VCp)
    for Ring and Plain, with two decimals, the rows used (n and `missing`), the ratios t_pf Ring /
    t_pf Plain, t_MDB / t_pf Plain and t_pf Ring / t_MDB, and a LaTeX block (the `tabular` of the paper).
    Without `--mdb` the t_MDB columns are `n/a` and c-to-c uses our own CVp/VCp rows.
  - With `--mdb`/`--mdb-raw` it replicates the MillDB join: cold MillenniumDB time per `n` =
    the first row of `raw.csv` with `warmup=1`, `pf_status=ok` and no `error` (`ns/1e9`); rows of
    `results.csv` with `kind` CV/VC/CVp/VCp, `mdb_status=ok`, `dir` `subj` or `obj` (`unknown` is left out) and
    `sim_t_pf` non-empty if the column exists. Each one is paired with our row `tb_key`: CV/VC,
    the same; CVp with `dir=subj`, the same, and otherwise the CVr with `src` and `dst` swapped; VCp with
    `dir=obj`, the same, and otherwise the VCr swapped. If that row does not exist (or is not valid) in a CSV,
    the MDB row does not enter that CSV (`missing`). The three ratios of a class use the same
    rows, and `t_pf` comes from our CSV (the `results_adj.csv` of Plain is not needed).

## Queries generated from the shapes of Hammerer & Martens

`gen-queries` instantiates the regular expression shapes of "A Compendium of Regular Expression
Shapes in SPARQL Queries" (GRADES-NDA 2025; https://zenodo.org/records/15363430). `WD_organic.csv` is
used, for example.

How it generates each query:
- It picks a shape with probability proportional to its number of distinct RPQs.
- It binds its letters to predicates with a random walk on the graph, which follows a random word
  of the expression. Each letter takes a predicate available at the node, weighted by its frequency in
  the query log. Distinct letters get distinct predicates.
- By construction, each query has at least one answer.
- Half are c-to-v and half v-to-c. The constants are written `<id:N>`.

```bash
wget -O ~/WD_organic.csv 'https://zenodo.org/records/15363430/files/WD_organic.csv?download=1'
tunnels/run_generated.sh ~/tunnels-out ~/WD_organic.csv 1000 1     # N=1000, seed 1
```

It reuses the indexes of `run_wikidata.sh` and writes into `~/tunnels-out/gen-1000-1/`.

## Inverses

Ring-RPQ expects inverses as `<%p>`; with `^<p>`, the `^` stays as a literal symbol of the
automaton that never matches. `paths-final.tsv` uses `^`, so `ring-tunnels` rewrites the paths:
- `^<p>` becomes `<%p>`;
- groups are inverted by pushing the `^` down to the predicates: `^(a/b)` = `^b/^a`.

## Other graph representations: navGraph and adjacency lists

`src/tunnel_backends.hpp` separates the tunnels from the index. The same `ring-tunnels.cpp` compiles
against four representations:

| binary | representation | index |
|---|---|---|
| `ring-tunnels` | Ring (Ring-RPQ) | `build-index-basic` |
| `ring-tunnels-split` | RingAB (Ring-RPQ), with its split for v-to-v | `build-index-split` |
| `nav-tunnels` | navGraph (Navarro & Robert, SPIRE 2024; github.com/j-rparra/navGraph) | `nav-build-index` |
| `adj-tunnels` | uncompressed adjacency lists (the "classic" representation) | built from the `.dat` and left in `<dat>.adj` |

The four produce the same product graph R and the same H; the `verify` column checks it, and in the
tests R, H and answers match row by row. What changes between them:
- The analysis cost a(w,q). The Ring only pays for the labels of q that w has (it finds them
  in its wavelet tree). navGraph and the adjacency lists check every label of q.
- The times.
- Each representation's own baseline: navGraph uses its evaluation (`rpq_one_const` /
  `rpq_no_const`, forward navigation, timeout `--baseline-timeout`, 60 s by default). The
  adjacency lists have none.

```bash
cd ~/ring-tunnels/Ring-RPQ/build
cmake -DSDSL_ROOT=$HOME/ring-tunnels/deps -DNAVGRAPH_DIR=$HOME/ring-tunnels/navGraph ..
make adj-tunnels nav-tunnels nav-build-index
cd .. && nohup tunnels/run_backends.sh ~/tunnels-out both > ~/tunnels-backends.log 2>&1 &
```

`run_backends.sh`:
- builds (once) `~/tunnels-out/nav/` and `~/tunnels-out/adj/`;
- runs c-to-v with pairs and v-to-v bridge on the log;
- runs the same on each existing `gen-*`, in `gen-*/nav` and `gen-*/adj`.

## Automata comparison (`--automata`)

It measures how much the product graph changes depending on the automaton of the same expression. Only in `adj-tunnels`
(in `ring-tunnels` and `ring-tunnels-split` it ends with exit code 1) and it is not combined with `--shortest`.
For each c-to-v or v-to-c query it builds the product from the constant with three automata:
`glushkov` (standard Glushkov), `rglushkov` (reverse Glushkov, the one Ring-RPQ uses) and `thompson`
(Dragon book, with ε transitions). The v-to-v and c-to-c queries, blank lines and those with an unknown IRI or
predicate produce no rows. No column or row of the normal mode is written.

```bash
build/adj-tunnels <graph.dat> <queries> --automata [--out f.csv] [--timeout s] [--max-nodes N] [--max-edges N] [--only ...] [--max-queries N]
python3 tunnels/automata_summary.py f.csv > f.md
```

Three rows per query, in the order `glushkov`, `rglushkov`, `thompson`. Columns:
- `qid,line,kind,shape`: as in the normal mode. `m`: positions of the expression (an `(a|b)` of
  plain predicates is a single position). `src`: constant node.
- `automaton,states,trans,eps_trans`: states, labeled transitions (one per label) and ε
  transitions.
- `status`: `ok`, `too_large` (exceeds `--max-nodes` or `--max-edges`) or `timeout`. If it is not `ok`, the
  product columns are those of the partial product and `H_*` and `answers` stay at 0.
- `R_nodes,R_edges,R_eps_edges`: nodes (v, q) reached, edges of the product and how many are ε.
- `lookups,scanned`: queries (node, distinct label) to the lists and neighbors returned.
- `H_nodes,H_edges`: nodes of R from which a final state is reached and edges of R that end
  in them. `answers`: distinct nodes v with some (v, q) reached and q final.
- `t_product,t_live`: seconds.

### Getting the results

`--automata` is not part of the normal run or of the paper. There are two ways to get it:

```bash
tunnels/reproduce_paper.sh --only backends,automata     # with the configuration of paper.env
tunnels/run_automata.sh <out of run_wikidata.sh> [extra options]   # by hand
```

It needs `<out>/adj/wd.dat.adj` (built by `run_backends.sh adj`, hence `backends,automata`) and the
query log (`WD_QUERIES`). `run_automata.sh` runs the log and each `gen-*` in parallel with
`adj-tunnels --automata` and writes into `<out>/automata/`:
- `log.csv` and `gen-<N>-<seed>.csv`: one CSV per query set (columns above);
- `*.log`: the error output of each run;
- `*.md`: the summary of each CSV, generated with `tunnels/automata_summary.py`.

### Reading `automata_summary.py`

```bash
python3 tunnels/automata_summary.py f.csv > f.md
```

The summary is computed only over the queries in which the three automata finished (`ok`). It contains:
- how many queries there are and how many finished with each automaton (`ok`, `too_large`, `timeout`);
- **totals** per automaton of states, transitions, ε transitions, nodes and edges of R, queries
  to the lists (`lookups`, `scanned`), nodes and edges of H and times;
- **ratios per query** against `rglushkov` (the one Ring-RPQ uses): median, mean of the ratios
  and maximum of `R_nodes`, `R_edges`, `H_nodes`, `H_edges` and `t_product`, over the queries where
  `rglushkov` is greater than 0. A ratio below 1 is a product smaller than Ring-RPQ's;
- totals per expression shape.

The tests are in `tests/tunnels_automata_test.py` (ctest: `tunnels_automata`).
