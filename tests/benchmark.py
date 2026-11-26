#!/usr/bin/env python3
"""Performance baseline for Ring-RPQ.

Builds the index of a dataset (graph.dat + dictionaries + queries.tsv), runs
its queries several times and stores the per-query median time and result
count as JSON.  With --compare it checks a new run against a stored baseline:
result counts must be identical and the time ratios are reported.

The synthetic benchmark dataset is generated with
  tests/tools/gen_dataset.py --bench --out <dir> --logs data/*/*.tsv
but any dataset of the paper (Zenodo) can be used the same way.

Examples:
  tests/benchmark.py --build-dir build --data <dir>/bench --variant basic \
      --out baseline-basic.json
  tests/benchmark.py --build-dir build --data <dir>/bench --variant basic \
      --compare baseline-basic.json
"""

import argparse
import json
import math
import os
import shutil
import statistics
import subprocess
import sys
import tempfile

# Queries faster than this are dominated by noise and excluded from ratios.
MIN_NS = 1_000_000


def build_index(build_dir, data, variant, work):
    dat = os.path.join(work, 'graph.dat')
    if not os.path.exists(dat + '.nTriples'):
        os.makedirs(work, exist_ok=True)
        for name in ('graph.dat', 'graph.dat.SO', 'graph.dat.P'):
            shutil.copy(os.path.join(data, name), work)
        subprocess.run([os.path.join(build_dir, f'build-index-{variant}'), dat],
                       check=True, stdout=subprocess.DEVNULL)
    return dat


def run_once(build_dir, variant, dat, queries):
    proc = subprocess.run([os.path.join(build_dir, f'query-index-{variant}'), dat, queries],
                          stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    res = {}
    for line in proc.stdout.splitlines():
        parts = line.split(';')
        if len(parts) == 3 and parts[0].isdigit():
            res[parts[0]] = (int(parts[1]), int(parts[2]))
    return res


def medians(runs):
    results = {}
    for q in runs[0]:
        counts = {r[q][0] for r in runs if q in r}
        if len(counts) != 1:
            print(f'warning: q{q} returns different counts across runs: {counts}', file=sys.stderr)
        results[q] = {'count': runs[0][q][0],
                      'ns': int(statistics.median(r[q][1] for r in runs if q in r))}
    return results


def geomean(values):
    return math.exp(sum(math.log(v) for v in values) / len(values)) if values else float('nan')


def summarize(results):
    times = [r['ns'] for r in results.values()]
    return sum(times) / 1e9, statistics.median(times) / 1e6


def compare(baseline, current, tolerance):
    """Time ratios are computed on the queries whose result count did not change:
    a query whose results changed (e.g. a bug fix) does different work."""
    ok = True
    common = [q for q in baseline if q in current]
    changed = [q for q in common if baseline[q]['count'] != current[q]['count']]
    same = [q for q in common if baseline[q]['count'] == current[q]['count']]
    if changed:
        ok = False
        t_b = sum(baseline[q]['ns'] for q in changed) / 1e9
        t_c = sum(current[q]['ns'] for q in changed) / 1e9
        print(f'result counts differ in {len(changed)} queries ({t_b:.2f} s -> {t_c:.2f} s), '
              f'e.g. {changed[:10]}')
    ratios = [current[q]['ns'] / baseline[q]['ns'] for q in same if baseline[q]['ns'] >= MIN_NS]
    total_ratio = sum(current[q]['ns'] for q in same) / sum(baseline[q]['ns'] for q in same)
    gm = geomean(ratios)
    print(f'queries with the same results: {len(same)}')
    print(f'  total time ratio (new/baseline): {total_ratio:.3f}')
    print(f'  geometric mean of per-query ratios ({len(ratios)} queries >= {MIN_NS / 1e6:g} ms): {gm:.3f}')
    worst = sorted(((current[q]['ns'] / baseline[q]['ns'], q) for q in same
                    if baseline[q]['ns'] >= MIN_NS), reverse=True)[:5]
    print('  slowest relative queries: ' + ', '.join(f'q{q} x{r:.2f}' for r, q in worst))
    short = [current[q]['ns'] / baseline[q]['ns'] for q in same if 0 < baseline[q]['ns'] < MIN_NS]
    if short:
        print(f'  geometric mean of per-query ratios ({len(short)} queries < {MIN_NS / 1e6:g} ms, '
              f'dominated by per-query setup): {geomean(short):.3f}')
    if total_ratio > 1 + tolerance or gm > 1 + tolerance:
        print(f'SLOWER than baseline beyond tolerance ({tolerance:.0%})')
        ok = False
    return ok


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--build-dir', required=True)
    ap.add_argument('--data', required=True, help='dataset directory')
    ap.add_argument('--variant', choices=('basic', 'split'), default='basic')
    ap.add_argument('--queries', help='query file (default: <data>/queries.tsv)')
    ap.add_argument('--reps', type=int, default=3)
    ap.add_argument('--work', help='where to keep the index (default: temporary directory)')
    ap.add_argument('--out', help='write the results as JSON')
    ap.add_argument('--compare', help='baseline JSON to compare against')
    ap.add_argument('--against-build',
                    help='build directory of a reference version: both versions are run '
                         'interleaved under the same conditions (recommended on laptops, '
                         'where power management changes absolute times)')
    ap.add_argument('--tolerance', type=float, default=0.03)
    args = ap.parse_args()

    build_dir = os.path.abspath(args.build_dir)
    work = args.work or tempfile.mkdtemp(prefix='ring-rpq-bench-')
    dat = build_index(build_dir, args.data, args.variant, os.path.join(work, args.variant))
    queries = args.queries or os.path.join(args.data, 'queries.tsv')

    ref_build = os.path.abspath(args.against_build) if args.against_build else None
    ref_dat = build_index(ref_build, args.data, args.variant,
                          os.path.join(work, 'reference-' + args.variant)) if ref_build else None
    runs, ref_runs = [], []
    for i in range(args.reps):
        runs.append(run_once(build_dir, args.variant, dat, queries))
        if ref_build:
            ref_runs.append(run_once(ref_build, args.variant, ref_dat, queries))
        print(f'run {i + 1}/{args.reps}: {sum(t for _, t in runs[-1].values()) / 1e9:.2f} s',
              file=sys.stderr)
    results = medians(runs)

    total, median = summarize(results)
    print(f'{len(results)} queries, total {total:.2f} s, median {median:.3f} ms')
    if args.out:
        with open(args.out, 'w') as f:
            json.dump({'variant': args.variant, 'reps': args.reps, 'queries': results}, f, indent=0)
    if ref_build:
        sys.exit(0 if compare(medians(ref_runs), results, args.tolerance) else 1)
    if args.compare:
        with open(args.compare) as f:
            baseline = json.load(f)['queries']
        sys.exit(0 if compare(baseline, results, args.tolerance) else 1)


if __name__ == '__main__':
    main()
