#!/usr/bin/env python3
"""Tests of tunnels/paper_figures.py and tunnels/paper_tables.py on a results directory built from
tests/data/metro (same runs as run_wikidata.sh, without nav/).

  T1  paper_figures.py: numbers.md, warnings (nav yes; the old cv_rev / vv_union files no), the sections
      and rows of numbers.md, the PDFs (or the "matplotlib not available" warning)
  T2  paper_figures.py without cv.csv: exit code 1
  T3  paper_tables.py R --generated G: the six tables, recomputed independently from the CSVs
  T4  paper_tables.py without --generated: "no data" in the two tables of generated queries

Usage: tests/tunnels_paper_test.py --build-dir build
"""

import argparse
import csv
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
METRO = os.path.join(HERE, 'data', 'metro')
FIGURES = os.path.join(ROOT, 'tunnels', 'paper_figures.py')
TABLES = os.path.join(ROOT, 'tunnels', 'paper_tables.py')

BUD = ['--uniform-cost', '--budgets', '0,1000,10000,100000,inf', '--rel', '1,4,16', '--rel-slack', '10']
BUDV = ['--uniform-cost', '--budgets', '0,1000,inf', '--rel', '4,16', '--rel-slack', '10', '--vv-reps', '1']
CV_OPTS = BUD + ['--pairs', '2', '--pair-reverse', '--vv', 'none', '--reps', '1']
VVB_OPTS = BUDV + ['--vv', 'bridge', '--vv-derive', '--vv-only', '--vv-samples', '0', '--vv-union']
VVR_OPTS = BUDV + ['--vv', 'ring', '--vv-derive', '--vv-only', '--vv-samples', '0']

TIME_V = ('\tCommand being timed: "build-index-basic wd.dat"\n'
          '\tElapsed (wall clock) time (h:mm:ss or m:ss): 1:12:00\n'
          '\tMaximum resident set size (kbytes): 73400320\n')

failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)
        print('FAIL', msg)


def run(cmd, expect_rc=0):
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    check(proc.returncode == expect_rc,
          f'{" ".join(os.path.basename(c) for c in cmd[:3])}: exit code {proc.returncode}, expected {expect_rc}\n{proc.stderr[-400:]}')
    return proc


def tool(build, name, dat, out, opts):
    run([os.path.join(build, name), dat, os.path.join(METRO, 'queries.tsv'), '--out', out] + opts)


def make_results(build, R):
    for sub in ('basic', 'split', 'adj'):
        os.makedirs(os.path.join(R, sub))
        for f in ('graph.dat', 'graph.dat.SO', 'graph.dat.P'):
            shutil.copy(os.path.join(METRO, f), os.path.join(R, sub))
    for name in ('basic', 'split'):
        run([os.path.join(build, f'build-index-{name}'), os.path.join(R, name, 'graph.dat')])
    tool(build, 'ring-tunnels', os.path.join(R, 'basic', 'graph.dat'), os.path.join(R, 'cv.csv'), CV_OPTS)
    tool(build, 'ring-tunnels', os.path.join(R, 'basic', 'graph.dat'), os.path.join(R, 'vv_bridge.csv'), VVB_OPTS)
    tool(build, 'ring-tunnels-split', os.path.join(R, 'split', 'graph.dat'), os.path.join(R, 'vv_ring.csv'), VVR_OPTS)
    with open(os.path.join(R, 'vv_all.csv'), 'w') as out:   # as run_wikidata.sh: cat vv_bridge <(tail -n +2 vv_ring)
        out.write(open(os.path.join(R, 'vv_bridge.csv')).read())
        out.writelines(open(os.path.join(R, 'vv_ring.csv')).readlines()[1:])
    tool(build, 'adj-tunnels', os.path.join(R, 'adj', 'graph.dat'), os.path.join(R, 'adj', 'cv.csv'), CV_OPTS)
    tool(build, 'adj-tunnels', os.path.join(R, 'adj', 'graph.dat'), os.path.join(R, 'adj', 'vv_bridge.csv'), VVB_OPTS)
    with open(os.path.join(R, 'basic', 'index.log'), 'w') as f:
        f.write('  Index built 16.3 bytes per triple\n' + TIME_V)


# ---- independent reading of the CSVs (does not use the code under test)
def rows_of(path):
    with open(path, newline='') as f:
        return [r for r in csv.DictReader(f) if r['status'] == 'ok']


def pick(rows, kinds, model=None, budget=None):
    return [r for r in rows if r['kind'] in kinds and (model is None or (r['model'], r['budget']) == (model, budget))]


def val(r, k):
    return float(r[k]) if r[k] != '' else 0.0


# the sections of paper_tables.py, by content; the keys keep the order of the output (the old numbers of the tables)
TITLES = {2: 'Graph representations: space, construction time and peak memory',
          3: 'Size of H and S per query class',
          4: 'Construction time of R, H and S',
          5: 'c-to-v on the Ring: space of S relative to H',
          6: 'Generated queries relative to the explicit H',
          7: 'Generated c-to-v queries by shape'}


def section(text, n):
    m = re.search(rf'^## {re.escape(TITLES[n])}.*?(?=^## |\Z)', text, re.S | re.M)
    return m.group(0) if m else ''


def split_cells(line):
    """cells of a Markdown table row, respecting the escape \\|"""
    return [c.strip().replace('\\|', '|') for c in re.split(r'(?<!\\)\|', line.strip())[1:-1]]


def all_rows(sec):
    return [split_cells(l) for l in sec.splitlines() if l.startswith('|') and not l.startswith('|---')]


def table_rows(sec):
    return all_rows(sec)[1:]   # without the header


def close(a, b, tol):
    return abs(a - b) <= tol


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--build-dir', required=True)
    args = ap.parse_args()
    build = os.path.abspath(args.build_dir)
    t0 = time.time()

    work = tempfile.mkdtemp(prefix='ring-tunnels-paper-')
    try:
        R = os.path.join(work, 'R')
        os.makedirs(R)
        make_results(build, R)
        G = os.path.join(work, 'gen-test')
        shutil.copytree(R, G)

        # T1
        out = os.path.join(work, 'out')
        proc = run([sys.executable, FIGURES, R, out])
        check(os.path.isfile(os.path.join(out, 'numbers.md')), 'T1: numbers.md does not exist')
        warns = [l for l in proc.stderr.splitlines() if l.startswith('warning:')]
        check(any('nav' in l for l in warns), f'T1: no warning about nav:\n{proc.stderr}')
        check(not any('cv_rev' in l or 'vv_union' in l for l in proc.stderr.splitlines()),
              f'T1: warning about cv_rev / vv_union:\n{proc.stderr}')
        if os.path.isfile(os.path.join(out, 'numbers.md')):
            nm = open(os.path.join(out, 'numbers.md')).read()
            check('Ring, best orientation' in nm, 'T1: no "Ring, best orientation" in numbers.md')
            ctov = nm.split('## c-to-c')[0]
            for cfg in ('100000', '16x+10'):
                check(f'| {cfg} |' in ctov, f'T1: no row "| {cfg} |" in c-to-v')
        try:
            import matplotlib  # noqa: F401
            have_mpl = True
        except ImportError:
            have_mpl = False
        if have_mpl:
            for f in ('ctov.pdf', 'ctoc.pdf', 'vtov.pdf'):
                check(os.path.isfile(os.path.join(out, f)), f'T1: {f} does not exist')
        else:
            check('matplotlib not available' in proc.stderr, 'T1: no "matplotlib not available" warning')
            print('note: matplotlib is not installed, PDFs not checked')

        # T2
        empty = os.path.join(work, 'empty')
        os.makedirs(empty)
        proc = run([sys.executable, FIGURES, empty, os.path.join(work, 'out2')], expect_rc=1)
        check(proc.stderr.strip() != '', 'T2: no message on stderr')

        # T3
        tp = run([sys.executable, TABLES, R, '--generated', G])
        text = tp.stdout
        for n in range(2, 8):
            check(f'## {TITLES[n]}' in text, f'T3: no "## {TITLES[n]}"')
        check([l for l in text.splitlines() if l.startswith('## ')] == [l for n in range(2, 8) for l in text.splitlines()
              if l.startswith(f'## {TITLES[n]}')], 'T3: the sections are not the six titles in order')
        check(not re.search(r'Table \d', text), 'T3: a title with a number of the paper')
        cv = rows_of(os.path.join(R, 'cv.csv'))
        # Table 2
        t2 = {r[0]: r[1:] for r in table_rows(section(text, 2))}
        check(t2.get('Ring') is not None and close(float(t2['Ring'][0]), 16.3, 0.05), f'T3 table 2, Ring bytes/edge: {t2.get("Ring")}')
        check(t2.get('Ring') is not None and close(float(t2['Ring'][1]), 1.2, 0.05), f'T3 table 2, Ring hours: {t2.get("Ring")}')
        check(t2.get('Ring') is not None and close(float(t2['Ring'][2]), 70, 0.5), f'T3 table 2, Ring GB: {t2.get("Ring")}')
        check(t2.get('Compact') == ['n/a'] * 3, f'T3 table 2, Compact: {t2.get("Compact")}')
        # Table 3: mean thousands of H nodes, c-to-v
        h = pick(cv, ('CV', 'VC'), 'region', '0')
        expect = sum(val(r, 'H_nodes') for r in h) / len(h) / 1000
        t3 = table_rows(section(text, 3))
        row = next((r for r in t3 if r[0].startswith('c-to-v (')), None)
        check(row is not None and close(float(row[1]), expect, 0.001 + 0.001 * expect), f'T3 table 3, H nodes: {row}, expected {expect}')
        check(row is not None and row[0] == f'c-to-v ({len(h)} queries)', f'T3 table 3, label: {row}')
        # Table 5, with the default threshold (no query of metro has 1000 H edges) and with --min-h-edges 1
        big = {(r['qid'], r['kind'], r['src'], r['dst']) for r in h if val(r, 'H_edges') >= 1000}
        check(not big, 'T3: some metro query has >= 1000 H edges, the n/a check of table 5 is not valid')
        t5 = {r[0]: r[1:] for r in table_rows(section(text, 5))}
        check(t5.get('4x+10') == ['n/a'] * 4, f'T3 table 5 (default threshold): {t5.get("4x+10")}')
        tp1 = run([sys.executable, TABLES, R, '--generated', G, '--min-h-edges', '1'])
        big = {(r['qid'], r['kind'], r['src'], r['dst']) for r in h if val(r, 'H_edges') >= 1}
        rs = [r for r in pick(cv, ('CV', 'VC'), 'region_rel', '4x+10') if (r['qid'], r['kind'], r['src'], r['dst']) in big]
        expect = 100 * sum(val(r, 'S_bits') for r in rs) / sum(val(r, 'H_bits') for r in rs)
        t5 = {r[0]: r[1:] for r in table_rows(section(tp1.stdout, 5))}
        check(t5.get('4x+10') is not None and close(float(t5['4x+10'][0]), expect, 0.006), f'T3 table 5: {t5.get("4x+10")}, expected {expect}')
        # Table 7: n of the most frequent shape
        cnt = Counter(r['shape'] for r in h)
        top = min(cnt, key=lambda s: (-cnt[s], s))
        t7 = table_rows(section(text, 7))
        check(len(t7) == min(12, len(cnt)), f'T3 table 7: {len(t7)} rows, expected {min(12, len(cnt))}')
        check(bool(t7) and t7[0][0] == top and int(t7[0][1]) == cnt[top], f'T3 table 7: first row {t7[:1]}, expected {top} n={cnt[top]}')
        # every row of every table has as many cells as its header; a shape with | is among them
        for n in range(2, 8):
            rows = all_rows(section(text, n))
            check(bool(rows) and all(len(r) == len(rows[0]) for r in rows), f'T3 table {n}: rows with a different number of cells')
        check(any('|' in r[0] for r in t7), 'T3 table 7: no shape with | among the rows')
        check(text.count('no data:') == 2 and 'nav/cv.csv missing' in text, 'T3: unexpected "no data" lines')

        def variant(name, drop=()):
            d = os.path.join(work, name)
            shutil.copytree(R, d)
            for f in drop:
                os.remove(os.path.join(d, f))
            return d

        # T5: warning paths of paper_figures.py
        def figures(r, name):
            return run([sys.executable, FIGURES, r, os.path.join(work, name)])

        d = variant('R-norev')
        kind = open(os.path.join(R, 'cv.csv')).readline().split(',').index('kind')
        with open(os.path.join(R, 'cv.csv')) as f:
            lines = f.read().split('\n')
        with open(os.path.join(d, 'cv.csv'), 'w') as f:
            f.write('\n'.join(l for l in lines if l and l.split(',')[kind] not in ('CVr', 'VCr')))
        proc = figures(d, 'out-norev')
        check(any(l.startswith('warning:') and 'CVr' in l for l in proc.stderr.splitlines()), f'T5: no warning without CVr/VCr:\n{proc.stderr}')
        check('Ring, best orientation' not in open(os.path.join(work, 'out-norev', 'numbers.md')).read(), 'T5: best orientation without CVr/VCr')

        d = variant('R-noplain')
        ip = lines[0].split(',').index('Hplain_bits')
        with open(os.path.join(d, 'cv.csv'), 'w') as f:
            f.write('\n'.join(l if i == 0 or not l else ','.join(c if j != ip else '' for j, c in enumerate(l.split(',')))
                              for i, l in enumerate(lines)))
        proc = figures(d, 'out-noplain')
        check(any(l.startswith('warning:') and 'fixed' in l for l in proc.stderr.splitlines()), f'T5: no warning about fixed plain H:\n{proc.stderr}')

        d = variant('R-novvall', drop=('vv_all.csv',))
        proc = figures(d, 'out-novvall')
        check(any(l.startswith('warning:') and 'vv_all.csv' in l for l in proc.stderr.splitlines()), f'T5: no warning without vv_all.csv:\n{proc.stderr}')
        a1 = open(os.path.join(out, 'numbers.md')).read().split('## v-to-v')[1]
        a2 = open(os.path.join(work, 'out-novvall', 'numbers.md')).read().split('## v-to-v')[1]
        check(a1 == a2, 'T5: v-to-v numbers differ when built from vv_bridge.csv + vv_ring.csv')

        # T4
        tn = run([sys.executable, TABLES, R])
        for n in (6, 7):
            check('no data' in section(tn.stdout, n), f'T4: no "no data" in table {n}')
        run([sys.executable, TABLES, empty], expect_rc=1)
        d = variant('R-novv', drop=('vv_all.csv', 'vv_bridge.csv', 'vv_ring.csv'))
        t4 = section(run([sys.executable, TABLES, d]).stdout, 4)
        for f in ('vv_all.csv', 'vv_bridge.csv'):
            check(t4.count(f'{os.path.join(d, f)} missing') == 1, f'T4: {f}: "no data" lines: {t4.count(f + " missing")}, expected 1')

        check(time.time() - t0 < 60, f'duration {time.time() - t0:.0f} s >= 60 s')
    finally:
        shutil.rmtree(work, ignore_errors=True)

    if failures:
        print(f'{len(failures)} failure(s)')
        return 1
    print('ok: T1-T4')
    return 0


if __name__ == '__main__':
    sys.exit(main())
