# Tunnels over the Ring

This repository holds the prototype of **tunnels**: a compact representation of the solution
subgraph H of a regular path query (RPQ) over a labeled graph. This README explains, step by step,
how to build everything and how to regenerate the experiments, from the graph files to the tables
and figures.

The graph is stored in four representations, all behind the same interface:

| Name | What it is | Binary of the experiments |
|---|---|---|
| Ring | the Ring index (two wavelet matrices over the edges) | `ring-tunnels` |
| RingAB | the Ring built for splitting an RPQ in two (needed for the v-to-v cut of the Ring) | `ring-tunnels-split` |
| Compact | the navGraph compact graph | `nav-tunnels` |
| Plain | plain adjacency lists | `adj-tunnels` |

Query classes: **c-to-v** and **v-to-c** (one constant endpoint), **c-to-c** (both constants) and
**v-to-v** (both variables).

> The detailed documentation of every option, CSV column and script is in `tunnels/README.md`.
> This file is the guided tour.

## 1. Requirements

| Needed for | Requirement |
|---|---|
| everything | the extended SDSL, [github.com/adriangbrandon/sdsl-lite](https://github.com/adriangbrandon/sdsl-lite) |
| everything | CMake (3.12 or newer), g++ with C++17, Python 3 |
| time and memory of the index builds | GNU `time` (`/usr/bin/time`). Without it `index.log` has no peak memory or build time, and the index-space table has `n/a` in those columns |
| figures | matplotlib. Without it the figures are not drawn (their numbers are still written) |
| Compact only | [navGraph](https://github.com/j-rparra/navGraph). Without it the Compact series is left out |

Install the extended SDSL in a prefix of your own (`<sdsl-prefix>` below). Follow the instructions
of its repository; it comes with an `install.sh` that takes the prefix:

```bash
git clone https://github.com/adriangbrandon/sdsl-lite.git
cd sdsl-lite && ./install.sh <sdsl-prefix>
```

**Resources on Wikidata (approximate).** The full experiment needs a large server:

- Building one index takes up to about 103 GB of RAM and 2.4 hours.
- `run_wikidata.sh` runs three query runs in parallel, each with its own index loaded
  (about 30 GB each).
- `adj-tunnels` (Plain) takes about 61 GB. Its PathFinder run goes alone, after the other runs.
- The Compact and Plain runs of `run_backends.sh` are launched in parallel as well.

It is not reproducible on a laptop. To check the whole pipeline on a small graph, see
[section 8](#8-one-script-that-does-it-all).

## 2. Data

Three kinds of input. All paths below are examples: put the files anywhere and point `DATA` to them.

**The graph.** Three files:

| File | Content |
|---|---|
| `wikidata-enumerated.dat` | one edge per line, `s p o`: three integers (subject, predicate, object) |
| `wikidata-unsorted-enumerated.dat.SO` | dictionary of subjects and objects, one `id string` per line |
| `wikidata-unsorted-enumerated.dat.P` | dictionary of predicates, one `id string` per line |

These are the names the scripts look for by default (section 8 shows how to give other names).
The index builders need the three files side by side under one prefix, so the commands in section 4
link the `.dat` and the dictionaries as `wd.dat`, `wd.dat.SO` and `wd.dat.P`. The scripts only read these files and never write next to them.

> **TODO:** download link of the graph pending (to be added by the author).

**The query log.** `paths-final.tsv`: 1,589 RPQs of the Wikidata log, one per line,
`<subject> <rpq> <object>`, where each endpoint is an IRI or a variable (`?x`).

> **TODO:** location of `paths-final.tsv` in the repository (or download link) pending.

**The expression shapes.** `WD_organic.csv`, from Hammerer and Martens
([zenodo.org/records/15363430](https://zenodo.org/records/15363430)). It is only needed to
generate extra queries (section 5).

Variables used in the rest of this README. Run every command from the root of the repository:

```bash
B=$PWD/build                             # directory of the binaries
DATA=$HOME/tunnels-data                  # holds the files above
OUT=$HOME/tunnels-out                    # all results; must not be inside $DATA
WD=$DATA/wikidata-enumerated.dat
WD_P=$DATA/wikidata-unsorted-enumerated.dat.P
WD_SO=$DATA/wikidata-unsorted-enumerated.dat.SO
Q=$DATA/paths-final.tsv
SHAPES=$DATA/WD_organic.csv
```

## 3. Compile

```bash
cmake -S . -B $B -DSDSL_ROOT=<sdsl-prefix> -DRING_TIMEOUT=60
cmake --build $B -j
```

Options:

- `-DSDSL_ROOT=<prefix>`: where the extended SDSL is installed (default `$HOME`).
- `-DRING_TIMEOUT=<s>`: time limit, in seconds, of the Ring's own evaluation that is used as a
  baseline (default 60).
- `-DNAVGRAPH_DIR=<navGraph source>`: adds the Compact binaries. Without it they are not built.

Binaries used by the experiments:

| Binary | What it does |
|---|---|
| `build-index-basic <dat>` | builds the Ring next to the `.dat` |
| `build-index-split <dat>` | builds RingAB next to the `.dat` |
| `nav-build-index <dat>` | builds the navGraph index (only with `NAVGRAPH_DIR`) |
| `ring-tunnels <index> <queries>` | tunnels on the Ring |
| `ring-tunnels-split <index> <queries>` | tunnels on RingAB (adds the Ring's v-to-v cut) |
| `nav-tunnels <index> <queries>` | tunnels on Compact (only with `NAVGRAPH_DIR`) |
| `adj-tunnels <index> <queries>` | tunnels on Plain; it builds its adjacency lists the first time |
| `gen-queries <index> <shapes> <log> <N>` | generates RPQs from expression shapes |

The four `*-tunnels` programs are the same source with different graph representations. They share
all their options (the full list is in `tunnels/README.md`).

## 4. Build each graph representation

Every representation lives in its own directory under `$OUT`, with the three graph files in it
(all three as symbolic links to the originals, nothing is copied):

```bash
for v in basic split adj; do
  mkdir -p $OUT/$v
  ln -sfn $(readlink -f $WD) $OUT/$v/wd.dat
  ln -sfn $(readlink -f $WD_P) $OUT/$v/wd.dat.P
  ln -sfn $(readlink -f $WD_SO) $OUT/$v/wd.dat.SO
done
touch $OUT/.ring-tunnels       # marks $OUT as an output directory of these scripts
```

The commands below put the build log in `index.log`. That file is what the index-space table
reads: it has the line `... bytes per triple` printed by the builder and, with GNU `time -v`, the
elapsed time and the peak memory. Put `/usr/bin/time -v` in front of every build command (as in
the examples); if you do not have it, omit it.

### Ring

```bash
/usr/bin/time -v $B/build-index-basic $OUT/basic/wd.dat > $OUT/basic/index.log 2>&1
```

Leaves, next to `$OUT/basic/wd.dat`: `wd.dat.bwts.L`, `wd.dat.bwts.Ca`, `wd.dat.bwtp.L`,
`wd.dat.bwtp.C`, `wd.dat.nTriples` and `index.log`.

### RingAB

```bash
/usr/bin/time -v $B/build-index-split $OUT/split/wd.dat > $OUT/split/index.log 2>&1
```

Leaves the same files in `$OUT/split/`, plus `wd.dat.wt_pred_s`.

### Compact (navGraph)

Only if you compiled with `-DNAVGRAPH_DIR`. The builder reads `wd.dat` from the current directory:

```bash
mkdir -p $OUT/nav
ln -sfn $(readlink -f $WD) $OUT/nav/wd.dat
ln -sfn $(readlink -f $WD_P) $OUT/nav/wd.dat.P; ln -sfn $(readlink -f $WD_SO) $OUT/nav/wd.dat.SO
(cd $OUT/nav && /usr/bin/time -v $B/nav-build-index wd.dat > index.log 2>&1)
```

Leaves `wd.dat.sizes` and the navGraph files in `$OUT/nav/`, and `index.log`.

### Plain

`adj-tunnels` builds its adjacency lists from the `.dat` the first time it runs. Run it with no
queries (`--max-queries 0`) to build and time them:

```bash
/usr/bin/time -v $B/adj-tunnels $OUT/adj/wd.dat $Q --max-queries 0 --out /dev/null > $OUT/adj/index.log 2>&1
```

Leaves `wd.dat.adj` and `index.log` in `$OUT/adj/`.

### Sizes

The sizes of the index files of every representation, as a list of files:

```bash
ls -lL $OUT/basic/wd.dat.* $OUT/split/wd.dat.* > $OUT/index_sizes.txt
ls -lL $OUT/adj/wd.dat.* > $OUT/adj/index_sizes.txt
```

`tunnels/run_wikidata.sh` and `tunnels/run_backends.sh` (section 8) do all of this, and skip an
index that already exists.

## 5. Build R, H and S

Each `*-tunnels` program reads the queries and, for each one, does:

- **R**, the product graph: one node per (graph node, automaton state), built with the primitives
  of the representation. Its construction is timed (`t_product`).
- **H**, the solution subgraph: the nodes of R that reach an accepting node. This is what a
  conventional engine would materialize (`H_bits`, `t_H_expand`).
- **S**, the set of nodes that are stored with tunnels, chosen for a **budget** of work (`--budgets`
  absolute, `--rel` relative to the output). Nodes outside S are re-expanded with the graph
  representation when H is decoded. S is encoded in bits (`S_bits`), decoded again (`t_expand`) and
  checked to be exactly H (`verify=1`).

Each run writes one CSV with one row per query and configuration. The key columns are `kind` (the
class), `status` (`ok` or why the query was left out), `H_nodes`, `H_edges`, `H_bits`, `S_bits`,
`model`/`budget` (the configuration), `t_build` (time to build S) and `t_expand` (time to decode it),
`answers`/`ring_answers` and `verify`. The full list is in `tunnels/README.md`.

Options of the paper's configuration, used in all the commands below:

```bash
BUD="--uniform-cost --budgets 0,1000,10000,100000,inf --rel 1,4,16 --rel-slack 10"
BUDV="--uniform-cost --budgets 0,1000,inf --rel 4,16 --rel-slack 10 --vv-reps 1"
```

`--uniform-cost` makes the cost model comparable across representations. `inf` stores nothing (the
decoder recomputes everything).

### c-to-v and v-to-c, with their c-to-c pairs

```bash
$B/ring-tunnels $OUT/basic/wd.dat $Q --out $OUT/cv.csv $BUD --pairs 5 --pair-reverse --vv none 2> $OUT/cv.log
```

Writes `$OUT/cv.csv` with these `kind` values:

- `CV`, `VC`: the c-to-v and v-to-c queries;
- `CVp`, `VCp`: for each one, 5 c-to-c queries (`--pairs 5`) taken from its answers, solved from the
  constant of the original query;
- `CVr`, `VCr`: the same pairs solved from the other end (`--pair-reverse`).

`cv.csv` also carries rows of kind `CC` and `VV`; they come out with their `status` (`cc_in_file`,
`too_many_states`) and the tables leave them out.

### v-to-v

The v-to-v queries are the ones that come from removing the constant of each c-to-v or v-to-c query
(`--vv-derive`). The expression is cut at a point, with its nodes as seeds, and each side is a
multi-source product graph. Two cuts exist:

- the **bridge** cut, on the Ring, picks the cut among the mandatory predicates of the expression.
  The same command also runs the baseline with one multi-source product graph from all the sources
  (`--vv-union`, rows `VVu`):

  ```bash
  $B/ring-tunnels $OUT/basic/wd.dat $Q --out $OUT/vv_bridge.csv $BUDV --vv bridge --vv-derive --vv-only --vv-samples 0 --vv-union 2> $OUT/vv_bridge.log
  ```

  Rows `VVb` and `VVu` in `$OUT/vv_bridge.csv`.

- the **Ring cut**, on RingAB, is the cut of the Ring's own query planner:

  ```bash
  $B/ring-tunnels-split $OUT/split/wd.dat $Q --out $OUT/vv_ring.csv $BUDV --vv ring --vv-derive --vv-only --vv-samples 0 2> $OUT/vv_ring.log
  ```

  Rows `VVr` in `$OUT/vv_ring.csv`.

Join both files for the summaries and figures, and summarize each CSV:

```bash
cat $OUT/vv_bridge.csv <(tail -n +2 $OUT/vv_ring.csv) > $OUT/vv_all.csv
python3 tunnels/ring_summary.py $OUT/cv.csv > $OUT/summary_cv.md
python3 tunnels/ring_summary.py $OUT/vv_all.csv --vv > $OUT/summary_vv.md
```

`ring_summary.py` prints, for each class and configuration, the size of S relative to the explicit
H and the build and expansion times.

### The other representations

Plain (`adj-tunnels`) and Compact (`nav-tunnels`) take the same arguments and options. They write
into their own directory, and only c-to-v/c-to-c and the bridge cut apply to them:

```bash
mkdir -p $OUT/adj
$B/adj-tunnels $OUT/adj/wd.dat $Q --out $OUT/adj/cv.csv $BUD --pairs 5 --pair-reverse --vv none 2> $OUT/adj/cv.log
$B/adj-tunnels $OUT/adj/wd.dat $Q --out $OUT/adj/vv_bridge.csv $BUDV --vv bridge --vv-derive --vv-only --vv-samples 0 --vv-union 2> $OUT/adj/vv_bridge.log
```

With Compact, use `nav-tunnels $OUT/nav/wd.dat` and write into `$OUT/nav/` (`cv.csv`,
`vv_bridge.csv`) the same way. `tunnels/run_backends.sh` runs both (section 8).

### Generated queries

Besides the log, queries are generated from the expression shapes. `gen-queries` draws a shape
with probability proportional to how many RPQs it has, binds its letters to predicates by a random
walk on the graph (so that the query has at least one answer) and writes half c-to-v and half
v-to-c queries. The predicates are taken from the log, weighted by their frequency. Its output is a
query file for the `*-tunnels` programs:

```bash
N=1000; SEED=1
G=$OUT/gen-$N-$SEED
mkdir -p $G
$B/gen-queries $OUT/basic/wd.dat $SHAPES $Q $N --seed $SEED > $G/queries.tsv 2> $G/gen.log
$B/ring-tunnels $OUT/basic/wd.dat $G/queries.tsv --out $G/cv.csv $BUD --pairs 5 --pair-reverse --vv none 2> $G/cv.log
```

The script does the whole set at once. It needs the indexes of the Ring and RingAB (section 4)
and reads the log from `WD_QUERIES`:

```bash
WD_QUERIES=$Q tunnels/run_generated.sh $OUT $SHAPES $N $SEED
```

It writes, in `$OUT/gen-$N-$SEED/`:

| File | Content |
|---|---|
| `queries.tsv`, `gen.log` | the queries and the shapes drawn |
| `cv.csv` | c-to-v, v-to-c and c-to-c with the Ring |
| `vv_bridge.csv`, `vv_ring.csv`, `vv_all.csv` | v-to-v with the bridge cut, the Ring cut and both |
| `sp.csv` | the same queries with `--shortest` (see section 6) |
| `summary_cv.md`, `summary_vv.md`, `summary_pathfinder.md` | the summaries of the above |
| `*.log` | the standard error of each run |

## 6. PathFinder: ALL SHORTEST WALKS

PathFinder is a path-query engine. It does not support all walks (they can
be infinite), so the comparison is made with ALL SHORTEST WALKS: with `--shortest`, H becomes
**H_sp**, the subgraph of the shortest walks, and S, its encoding and `verify` refer to H_sp. Each
row also carries the **simulation of PathFinder's search**, run on the same graph representation:
the number of states and transitions it stores, their bytes (`pf_bytes`, `pf_bytes_structs`) and its
time (`t_pf`).

`--early-stop` makes the search of the c-to-c queries stop at the first layer where the target
appears. The options of these two runs are fixed: do not change them (the random number generator
is global, so a different option set draws other c-to-c pairs and the two CSVs would no longer
refer to the same queries).

```bash
PF="--shortest --early-stop --pairs 5 --pair-reverse --vv-derive --vv-samples 5"
$B/ring-tunnels $OUT/basic/wd.dat $Q --out $OUT/sp_early.csv $BUD $PF 2> $OUT/sp_early.log
$B/adj-tunnels $OUT/adj/wd.dat $Q --out $OUT/sp_early_adj.csv $BUD $PF 2> $OUT/sp_early_adj.log
```

| Run | Representation | Output |
|---|---|---|
| `ring-tunnels` | Ring | `$OUT/sp_early.csv` |
| `adj-tunnels` | Plain | `$OUT/sp_early_adj.csv` |

Run them one after the other (Plain takes about 61 GB on Wikidata). Plain needs `$OUT/adj/wd.dat.adj`
from section 4. The script runs both and then the tables of section 7:

```bash
WD_QUERIES=$Q tunnels/run_pathfinder.sh $OUT
```

## 7. From the CSV files to tables and figures

All the scripts are in `tunnels/`, read only CSV and logs, and write to standard output or to the
directory you give. A missing input is reported as `warning:` and left out of the table, never as a
silent zero.

**Tables of the space and time of the representations.** `paper_tables.py` reads `cv.csv`,
`vv_bridge.csv`, `vv_ring.csv` (or `vv_all.csv`), the `index.log` of each representation and the
CSVs of `nav/` and `adj/`; with `--generated` it also reads the `gen-*` directory.

```bash
python3 tunnels/paper_tables.py $OUT --generated $G > $OUT/tables.md
```

`tables.md` has six tables, each under a title that says its content:

- **Graph representations: space, construction time and peak memory**: bytes per edge, construction time and peak memory of
  Ring, RingAB, Compact and Plain;
- **Size of H and S per query class** (c-to-v, c-to-c, v-to-v, v-to-v from all sources): nodes
  and edges of H, plain H, differential H and S, in KB;
- **Construction time of R, H and S** per class and representation;
- **c-to-v on the Ring: space of S relative to H**, for the queries with at least 1,000 H edges, per configuration: S relative
  to H (total, median, share of queries with S at most 1% of H) and the expansion time of S relative
  to the explicit H;
- **Generated queries relative to the explicit H** (space and time per class and
  configuration), and **Generated c-to-v queries by shape** for the most frequent shapes.

**Figures of the space/time trade-off.** `paper_figures.py` plots, per query class, the average
space against the average expansion time for each representation and budget:

```bash
python3 tunnels/paper_figures.py $OUT $OUT/figures
python3 tunnels/paper_figures.py $G $OUT/figures-gen     # the generated queries
```

It writes `ctov.pdf` (c-to-v), `ctoc.pdf` (c-to-c; the Ring as built and from the better
endpoint) and `vtov.pdf` (v-to-v; bridge and Ring cuts and the all-sources baseline), and
`numbers.md`, the same data as text. Without matplotlib only `numbers.md` is written.

**Space and time against PathFinder.** `pathfinder_tables.py --paper` reads `sp_early.csv` and
`sp_early_adj.csv`:

```bash
python3 tunnels/pathfinder_tables.py $OUT --sp sp_early.csv --paper --adj sp_early_adj.csv > $OUT/pathfinder_paper.md
```

It prints one section, `## Comparison with PathFinder`, with two tables:

- **Space held after evaluation (c-to-v)**, per query (KB): PathFinder's structures (total, and the structs alone),
  the plain and differential H_sp, and S, with the ratio to S;
- **Construction time relative to PathFinder** for the Ring and Plain: the build time of S over the time of PathFinder's
  simulation, in c-to-v and c-to-c (c-to-c forward, rows `CVp` and `VCp`). The two columns on the
  right show `n/a` in this setup.

Without `--paper`, `pathfinder_tables.py` prints a longer report (`$OUT/pathfinder.md`);
`ring_summary.py --pathfinder` gives a short one. Both are written by `run_pathfinder.sh`.

Space values are deterministic: they come out the same in every run with the same code and data.
Times vary with the machine and its load; compare ratios and the order between representations,
not the seconds.

## 8. One script that does it all

`tunnels/reproduce_paper.sh` runs sections 3 to 7 in order with the scripts of `tunnels/`
(`run_wikidata.sh`, `run_generated.sh`, `run_backends.sh`, `run_pathfinder.sh`,
`paper_figures.py`, `paper_tables.py`). All the paths of your machine go in one configuration file (`tunnels/paper.env` by default, another one with `--config FILE`).

```bash
cp tunnels/paper.env.example tunnels/paper.env     # not versioned
$EDITOR tunnels/paper.env
```

Minimum to set, on their own lines of the file: `DATA_DIR` (where the three graph files,
`paths-final.tsv` and `WD_organic.csv` are) and `OUT_DIR` (where everything is written; it cannot be
inside the directory of the `.dat`). The input files are looked up from `DATA_DIR`, so set it where
it appears in the file, not at the end. The others have defaults or can be left empty:

| Variable | Meaning |
|---|---|
| `SDSL_ROOT` | prefix of the extended SDSL, for the `build` step |
| `NAVGRAPH_DIR` | navGraph source; empty: no Compact |
| `BUILD_DIR` | binaries; empty: `<repo>/build` |
| `WD_DAT`, `WD_DICT_P`, `WD_DICT_SO`, `WD_QUERIES`, `SHAPES_CSV` | the input files; by default they are looked up in `DATA_DIR` as `wikidata-enumerated.dat`, `wikidata-unsorted-enumerated.dat.P` and `.SO`, `paths-final.tsv` and `WD_organic.csv` |
| `RING_TIMEOUT` | time limit (s) of the Ring baseline (default 60) |
| `GEN_N`, `GEN_SEED` | generated queries: how many and the seed (default 1000 and 1) |
| `EXTRA_OPTS` | extra options for the `*-tunnels` programs, e.g. `--max-queries 50` for a quick test |

Inspect it first, then launch it:

```bash
tunnels/reproduce_paper.sh --list         # the steps
tunnels/reproduce_paper.sh --dry-run      # checks the configuration, prints each command ("+ ...")
nohup tunnels/reproduce_paper.sh > reproduce.out 2>&1 &
tunnels/reproduce_paper.sh --only figures,tables      # only some steps, in their usual order
```

Validation runs first and lists every problem at once (exit code 2, nothing created). Each step
prints `== step: start` and `== step: ok (N s)`; if one fails it prints `== step: FAILED` and the
script stops with a non-zero code. The output of every step is also in `$OUT_DIR/reproduce.log`.
Runs can be resumed: the indexes already built are not rebuilt, but the CSVs are rewritten.

| Step | Does | Needs |
|---|---|---|
| `build` | section 3 | |
| `log` | `run_wikidata.sh`: Ring and RingAB indexes, the runs of section 5 on the log | |
| `generated` | `run_generated.sh`: generated queries | `log` |
| `backends` | `run_backends.sh`: Compact (if `NAVGRAPH_DIR`) and Plain, on the log and the generated queries | `log` |
| `pathfinder` | `run_pathfinder.sh`: section 6 and its tables | `log`, `backends` |
| `figures` | `paper_figures.py` | `log` |
| `tables` | `paper_tables.py` | `log` |

There is one more step, `automata`, which is not needed for any table or figure; leave it out
with `--only` (for example `--only build,log,generated,backends,pathfinder,figures,tables`).

**What it leaves under `OUT_DIR`:**

```text
$OUT_DIR/
  .ring-tunnels                 marks the directory as an output
  reproduce.log                 the output of every step
  basic/  split/  adj/  nav/    one directory per representation: wd.dat and the dictionaries (links),
                                the index files, index.log, index_sizes.txt; adj/ and nav/ also hold
                                cv.csv, vv_bridge.csv, their logs and summaries
  cv.csv  vv_bridge.csv  vv_ring.csv  vv_all.csv      sections 5 (+ .log files)
  summary_cv.md  summary_vv_bridge.md  summary_vv.md
  gen-<N>-<seed>/               queries.tsv, gen.log, cv.csv, vv_*.csv, sp.csv, summaries,
                                and adj/, nav/ with their CSVs
  sp_early.csv  sp_early_adj.csv                      section 6 (+ .log files)
  pathfinder.md  pathfinder_adj.md  summary_pathfinder.md  pathfinder_paper.md
  tables.md                     section 7 tables
  figures/  figures-gen/        ctov.pdf  ctoc.pdf  vtov.pdf  numbers.md
```

A full run also leaves an `automata/` directory (the optional step above); nothing else reads it.

### Try it on a small graph

```bash
tunnels/reproduce_small.sh [--dataset metro|rand-small|rand-medium] [--keep] /tmp/tunnels-small
```

It builds a `DATA_DIR` in `<out>/data` from one of the small graphs in `tests/data` (default
`rand-medium`), writes `<out>/paper.env` (10 generated queries, one repetition, no navGraph) and
runs every step except `build`, so the binaries must already be in `build/` (set `BUILD_DIR` for
another place). Everything is left in `<out>/out`, with the layout above: `tables.md`,
`pathfinder_paper.md` and, with matplotlib, the figures. It takes about a minute with the Release
build. If `<out>` exists and is not an output of this script, it exits with code 2 and touches
nothing; if it is, it deletes it first, unless you pass `--keep`.

## 9. More

- `tunnels/README.md`: every option of the `*-tunnels` programs, every CSV column,
  the details of the decoders, of the v-to-v cuts and of the PathFinder simulation.
- `tests/README.md`: the test suite (`ctest --test-dir build --output-on-failure`).
