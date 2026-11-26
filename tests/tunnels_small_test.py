#!/usr/bin/env python3
"""Test of tunnels/reproduce_small.sh (the whole reproduction on tests/data/rand-medium).

  T1  --dataset rand-medium (the default): exit code 0, tables.md and pathfinder_paper.md (with the
      Ring and Plain rows of the time table) non-empty, the data dir and paper.env mounted
  T2  an OUT that exists and is not an output of the script: exit code 2 and nothing touched
  T2b --keep with another dataset than the one of the output: exit code 2, nothing touched
  T3  an unknown dataset or a missing OUT: exit code 2
  T4  total time under 3 minutes

Usage: tests/tunnels_small_test.py --build-dir build
"""

import argparse
import os
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SCRIPT = os.path.join(ROOT, 'tunnels', 'reproduce_small.sh')

failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)
        print('FAIL', msg)


def run(args, build):
    return subprocess.run(['bash', SCRIPT] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=ROOT,
                          env=dict(os.environ, BUILD_DIR=build))


def tree(d):
    return sorted(os.path.join(r, n) for r, ds, fs in os.walk(d) for n in ds + fs)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--build-dir', required=True)
    build = os.path.abspath(ap.parse_args().build_dir)
    t0 = time.time()
    with tempfile.TemporaryDirectory() as tmp:
        tmp = os.path.realpath(tmp)
        out = os.path.join(tmp, 'small')
        p = run([out], build)
        check(p.returncode == 0, f'T1: exit code {p.returncode}\n{p.stdout[-1500:]}\n{p.stderr[-1500:]}')
        for f in ('out/tables.md', 'out/pathfinder_paper.md', 'out/sp_early.csv', 'out/sp_early_adj.csv', 'paper.env',
                  'data/wd-small.dat', 'data/predicates.txt'):
            path = os.path.join(out, f)
            check(os.path.isfile(path) and os.path.getsize(path) > 0, f'T1: {f} missing or empty')
        pp = os.path.join(out, 'out', 'pathfinder_paper.md')
        if os.path.isfile(pp):
            with open(pp) as fh:
                txt = fh.read()
            check('## Comparison with PathFinder' in txt and '| Ring |' in txt and '| Plain |' in txt, 'T1: pathfinder_paper.md lacks the Ring/Plain rows')
            plain = [l for l in txt.splitlines() if l.startswith('| Plain |')]
            cells = [c.strip() for c in plain[0].strip('|').split('|')] if plain else []
            check(len(cells) == 5 and all(c != 'n/a' for c in cells[1:3]), f'T1: the Plain row of the time table has no numbers: {plain}')
        with open(os.path.join(out, '.reproduce-small')) as fh:
            check(fh.read().strip() == 'rand-medium', 'T1: .reproduce-small does not hold the dataset')
        if os.path.isfile(os.path.join(out, 'paper.env')):
            with open(os.path.join(out, 'paper.env')) as fh:
                env = fh.read()
            check('MDB_RESULTS=""' in env and 'NAVGRAPH_DIR=""' in env, 'T1: paper.env without MDB_* or NAVGRAPH_DIR empty')

        # T2: a foreign directory
        foreign = os.path.join(tmp, 'foreign')
        os.makedirs(foreign)
        with open(os.path.join(foreign, 'mine.txt'), 'w') as fh:
            fh.write('x')
        before = tree(foreign)
        p = run(['--dataset', 'metro', foreign], build)
        check(p.returncode == 2 and 'not an output' in p.stderr, f'T2: exit code {p.returncode}\n{p.stderr}')
        check(tree(foreign) == before, 'T2: the foreign directory was touched')

        # T2b: --keep with another dataset is refused without touching the output
        before = tree(out)
        p = run(['--keep', '--dataset', 'metro', out], build)
        check(p.returncode == 2 and 'rand-medium' in p.stderr, f'T2b: exit code {p.returncode}\n{p.stderr}')
        check(tree(out) == before, 'T2b: the output was touched')

        # T3
        p = run(['--dataset', 'nope', os.path.join(tmp, 'x')], build)
        check(p.returncode == 2 and not os.path.exists(os.path.join(tmp, 'x')), f'T3: unknown dataset: exit code {p.returncode}')
        p = run([], build)
        check(p.returncode == 2, f'T3: without OUT: exit code {p.returncode}')

    dt = time.time() - t0
    check(dt < 180, f'T4: {dt:.0f} s (limit 180 s)')
    if failures:
        print(f'{len(failures)} failure(s)')
        return 1
    print(f'tunnels_small: ok ({dt:.0f} s)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
