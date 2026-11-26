#!/usr/bin/env python3
"""Regression suite for Ring-RPQ.

For every dataset in tests/data and every index variant (basic, split) it
builds the index, runs the dataset's queries and compares the results with the
golden files in tests/golden:

  <dataset>.<variant>.golden   "query count digest" per query, plus the exit code
  <dataset>.oracle             the same for the correct answers, computed by
                               tools/rpq_oracle.py

The digest is a SHA-256 of the sorted solutions of the query (duplicates
included for the engine, so they are detected too).  The golden files record
the behaviour of the code as it is, including its known bugs, so refactoring
steps can be checked to preserve it exactly.  Differences against the oracle
are reported but only fail the run with --strict-oracle; --details re-runs the
oracle on those queries and tells missing, extra, reversed and duplicate pairs
apart.

Usage:
  tests/regression.py --build-dir build            # check
  tests/regression.py --build-dir build --update   # re-record golden files
"""

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, 'tools'))
import rpq_oracle  # noqa: E402

VARIANTS = ('basic', 'split')


def digest(pairs):
    h = hashlib.sha256()
    for s, o in sorted(pairs):
        h.update(f'{s} {o}\n'.encode())
    return h.hexdigest()[:16]


def summary_lines(solutions, n_queries):
    """'query count digest' for every query, solutions: query -> [(s, o)]."""
    return [f'{q} {len(solutions.get(q, ()))} {digest(solutions.get(q, ()))}'
            for q in range(1, n_queries + 1)]


def read_solutions(path):
    sols = defaultdict(list)
    with open(path) as f:
        for line in f:
            q, s, o = line.split()
            sols[int(q)].append((int(s), int(o)))
    return sols


def read_summary(path):
    with open(path) as f:
        return f.read().splitlines()


def queries_of(dataset_dir):
    with open(os.path.join(dataset_dir, 'queries.tsv'), newline='') as f:
        return [line for line in f if line.strip()]


def oracle_solutions(dataset_dir, queries):
    dat = os.path.join(dataset_dir, 'graph.dat')
    graph = rpq_oracle.Graph(dat)
    nodes = rpq_oracle.load_dict(dat + '.SO')
    preds = rpq_oracle.load_dict(dat + '.P')
    return {q: rpq_oracle.evaluate(graph, nodes, preds, line) or set()
            for q, line in queries.items()}


def run_variant(build_dir, dataset_dir, variant, work, queries=None):
    os.makedirs(work, exist_ok=True)
    for name in ('graph.dat', 'graph.dat.SO', 'graph.dat.P'):
        shutil.copy(os.path.join(dataset_dir, name), work)
    dat = os.path.join(work, 'graph.dat')
    subprocess.run([os.path.join(build_dir, f'build-index-{variant}'), dat],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    sol_path = os.path.join(work, 'solutions.txt')
    proc = subprocess.run([os.path.join(build_dir, f'query-index-{variant}'), dat,
                           queries or os.path.join(dataset_dir, 'queries.tsv'), sol_path],
                          stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    return read_solutions(sol_path), proc.returncode


def describe(expected, got):
    exp, gotset = set(expected), set(got)
    parts = [f'expected {len(exp)}, got {len(got)}']
    if len(got) != len(gotset):
        parts.append(f'{len(got) - len(gotset)} duplicates')
    missing, extra = exp - gotset, gotset - exp
    reversed_pairs = {(o, s) for s, o in extra} & missing
    if reversed_pairs:
        parts.append(f'{len(reversed_pairs)} reversed')
    if len(missing) - len(reversed_pairs):
        parts.append(f'{len(missing) - len(reversed_pairs)} missing')
    if len(extra) - len(reversed_pairs):
        parts.append(f'{len(extra) - len(reversed_pairs)} extra')
    return ', '.join(parts)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--build-dir', required=True)
    ap.add_argument('--data', default=os.path.join(HERE, 'data'))
    ap.add_argument('--golden', default=os.path.join(HERE, 'golden'))
    ap.add_argument('--work', help='working directory (default: a temporary one)')
    ap.add_argument('--update', action='store_true', help='re-record the golden files')
    ap.add_argument('--strict-oracle', action='store_true',
                    help='fail when the engine disagrees with the oracle')
    ap.add_argument('--details', action='store_true',
                    help='explain every disagreement with the oracle')
    ap.add_argument('--changes', action='store_true',
                    help='for every query that differs from the golden files, tell '
                         'whether it now agrees with the oracle')
    ap.add_argument('--datasets', nargs='*', help='subset of datasets to run')
    args = ap.parse_args()

    build_dir = os.path.abspath(args.build_dir)
    work_root = args.work or tempfile.mkdtemp(prefix='ring-rpq-regression-')
    os.makedirs(args.golden, exist_ok=True)
    datasets = args.datasets or sorted(
        d for d in os.listdir(args.data)
        if os.path.exists(os.path.join(args.data, d, 'queries.tsv')))

    golden_failures = oracle_failures = 0
    for ds in datasets:
        dataset_dir = os.path.join(args.data, ds)
        queries = dict(enumerate(queries_of(dataset_dir), start=1))
        n = len(queries)

        oracle_path = os.path.join(args.golden, f'{ds}.oracle')
        stale = not os.path.exists(oracle_path) or len(read_summary(oracle_path)) != n
        if args.update or stale:
            # The oracle only depends on the dataset: recompute it when the queries change.
            with open(oracle_path, 'w') as f:
                f.write('\n'.join(summary_lines(oracle_solutions(dataset_dir, queries), n)) + '\n')
        oracle = read_summary(oracle_path)

        for variant in VARIANTS:
            sols, code = run_variant(build_dir, dataset_dir, variant,
                                     os.path.join(work_root, ds, variant))
            summary = summary_lines(sols, n) + [f'exit {code}']
            golden_path = os.path.join(args.golden, f'{ds}.{variant}.golden')
            if args.update:
                with open(golden_path, 'w') as f:
                    f.write('\n'.join(summary) + '\n')
                status = 'recorded'
            else:
                expected = read_summary(golden_path)
                changed = [e.split()[0] for e, g in zip(expected, summary) if e != g]
                if len(expected) != len(summary):
                    changed.append('(number of lines)')
                status = 'ok' if not changed else f'FAIL in queries {", ".join(changed[:20])}'
                golden_failures += bool(changed)
                if args.changes and changed:
                    fixed = [q for q in changed if q.isdigit()
                             and summary[int(q) - 1].split()[1:] == oracle[int(q) - 1].split()[1:]
                             and len(sols.get(int(q), ())) == len(set(sols.get(int(q), ())))]
                    still = [q for q in changed if q not in fixed]
                    status += (f'\n    changed and now correct: {", ".join(fixed) or "-"}'
                               f'\n    changed and still wrong: {", ".join(still) or "-"}')

            # The oracle digest is computed on sets, so compare on unique pairs.
            unique = summary_lines({q: set(v) for q, v in sols.items()}, n)
            disagree = [q for q in range(1, n + 1)
                        if unique[q - 1] != oracle[q - 1]
                        or len(sols.get(q, ())) != len(set(sols.get(q, ())))]
            oracle_failures += len(disagree)

            if variant == 'basic':
                # The same queries with LF line endings must give the same results.
                lf_path = os.path.join(work_root, ds, 'queries-lf.tsv')
                with open(lf_path, 'w', newline='\n') as f:
                    f.writelines(line.rstrip('\r\n') + '\n' for line in queries.values())
                lf_sols, lf_code = run_variant(build_dir, dataset_dir, variant,
                                               os.path.join(work_root, ds, 'basic-lf'), lf_path)
                if summary_lines(lf_sols, n) + [f'exit {lf_code}'] != summary:
                    status += '; FAIL with LF line endings'
                    golden_failures += 1
            print(f'{ds:12s} {variant:5s} golden: {status}; '
                  f'oracle: {n - len(disagree)}/{n} queries agree')
            if args.details and disagree:
                truth = oracle_solutions(dataset_dir, {q: queries[q] for q in disagree})
                for q in disagree:
                    print(f'    q{q}: {describe(truth[q], sols.get(q, []))}   '
                          f'{queries[q].strip()}')

    # Expressions with more than 64 NFA states must be rejected, not answered
    # wrongly (B7). 64 predicates -> 65 states.
    if 'metro' in datasets:
        dat = os.path.join(work_root, 'metro', 'basic', 'graph.dat')
        long_query = os.path.join(work_root, 'long-query.tsv')
        with open(long_query, 'w') as f:
            f.write('?x ' + '/'.join(['<http://example.org/pred/l1>'] * 64) + ' ?y\n')
        proc = subprocess.run([os.path.join(build_dir, 'query-index-basic'), dat, long_query],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        ok = '1;0;0' in proc.stdout and 'skipped' in proc.stderr
        print(f'long expressions rejected: {"ok" if ok else "FAIL"}')
        golden_failures += not ok

    if golden_failures:
        print(f'\n{golden_failures} golden mismatch(es); working files in {work_root}')
    if args.strict_oracle and oracle_failures:
        print(f'\n{oracle_failures} query result(s) disagree with the oracle')
    sys.exit(1 if golden_failures or (args.strict_oracle and oracle_failures) else 0)


if __name__ == '__main__':
    main()
