#!/usr/bin/env python3
"""Tests of tunnels/pathfinder_tables.py (tables P0-P6, ALL SHORTEST WALKS vs the PathFinder simulation).

  T1  real run on tests/data/metro (sp.csv with --shortest, cv.csv in ALL WALKS), --min-h-edges 1:
      the sections, and P0-P3, P1b, P5, P6 recomputed independently from the same CSVs
  T2  without sp.csv: exit code 1
  T3  sp.csv without sem=shortest rows (the cv.csv of T1 as sp.csv): exit code 1
  T4  without cv.csv: exit code 0, warning, "no data: ... missing" in P5, the rest equal to T1
  T5  hand-written CSVs: formulas and edge cases with exact numbers
  T6  a CSV with only CV rows: no rows of the other classes
  T7  without --paper the output is the one of the script of commit a5bd785 (main before --paper) (real run and synthetic CSVs)
  T8  --paper on hand-written CSVs: space table, time table with and without --mdb, tb_key (4 cases), exclusions,
      first cold row, missing rows, a repeated key with another t_build (exit code 1), other configurations
  T9  --paper with --mdb on a real run on metro (Ring and adj-tunnels) against an independent copy of the
      MillDB join script, with synthetic results.csv and raw.csv

Usage: tests/tunnels_pathfinder_test.py --build-dir build
"""

import argparse
import csv
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
METRO = os.path.join(HERE, 'data', 'metro')
SCRIPT = os.path.join(ROOT, 'tunnels', 'pathfinder_tables.py')

BUD = ['--uniform-cost', '--budgets', '0,1000,10000,100000,inf', '--rel', '1,4,16', '--rel-slack', '10']
CV_OPTS = BUD + ['--pairs', '2', '--pair-reverse', '--vv', 'none', '--reps', '1']
SP_OPTS = BUD + ['--shortest', '--pairs', '2', '--pair-reverse', '--vv-derive', '--vv-samples', '2', '--reps', '1']

# COLS of src/ring-tunnels.cpp
COLS = ["qid", "line", "kind", "src", "dst", "shape", "Q", "status", "R_nodes", "R_edges", "H_nodes", "H_edges",
        "leaves", "answers", "ring_answers", "t_ring", "t_product", "t_live", "t_cost", "H_bits", "H_bits_global", "t_H_expand",
        "model", "budget", "S_nodes", "S_edges", "S_list", "S_bits", "replay_model", "max_region_work", "t_select", "t_encode",
        "t_build", "t_expand", "dec_visited", "dec_expanded", "dec_children", "verify", "seeds", "live_seeds", "explored_seeds",
        "seed_bits", "split", "first", "t_Hplain_expand", "Hplain_bits",
        "sem", "pf_status", "pf_dfa_states", "pf_states", "pf_transitions", "pf_edges", "pf_idx_searches", "pf_answers",
        "pf_paths", "pf_bytes_structs", "pf_bytes", "pf_check", "t_pf"]

CLASSES = [('c-to-v', ('CV', 'VC')), ('c-to-c', ('CVp', 'VCp')), ('c-to-c (reverse)', ('CVr', 'VCr')),
           ('v-to-v (sampled sources)', ('VVs',))]
CLASS_OF = {k: n for n, ks in CLASSES for k in ks}

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


def tables(d, *extra, expect_rc=0):
    return run([sys.executable, SCRIPT, d] + list(extra), expect_rc)


# ---- independent reading of the CSVs and of the Markdown (does not use the code under test)
def read(path):
    with open(path, newline='') as f:
        return list(csv.DictReader(f))


def val(r, k):
    return float(r[k]) if r[k] != '' else 0.0


def f(x, d):
    """format of a number: d decimals and a thousands separator"""
    return 'n/a' if x is None else f'{x:,.{d}f}'


def div(a, b):
    return a / b if b else None


def fx(x, d=2):
    return 'n/a' if x is None else f(x, d) + 'x'


def section(text, name):
    m = re.search(rf'^## {name}\. .*?(?=^## P|\Z)', text, re.S | re.M)
    return m.group(0) if m else ''


def split_cells(line):
    return [c.strip().replace('\\|', '|') for c in re.split(r'(?<!\\)\|', line.strip())[1:-1]]


def tab(text, name):
    """(header, rows) of the first Markdown table of a section"""
    rows = []
    for l in section(text, name).splitlines():
        if l.startswith('|'):
            if not l.startswith('|---'):
                rows.append(split_cells(l))
        elif rows:
            break
    return (rows[0], rows[1:]) if rows else ([], [])


def col(head, name):
    return head.index(name)


def num(s):
    return float(s.replace(',', ''))


def close(cell, expect, d):
    """a cell with d decimals equals expect within half a unit of the last decimal"""
    return cell != 'n/a' and abs(num(cell.rstrip('x')) - expect) <= 0.5 * 10 ** -d + 1e-9


def cfg_name(r):
    return f"{r['model']} {r['budget']}"


class Run:
    """sp.csv read and classified as the spec says"""

    def __init__(self, rows):
        self.every = rows   # any sem: P0 counts the rows with status != ok, which have sem empty
        self.all = [r for r in rows if r['sem'] == 'shortest']
        self.valid = [r for r in self.all if r['status'] == 'ok' and r['pf_status'] == 'ok']
        self.cfgs = []
        for r in self.valid:
            if cfg_name(r) not in self.cfgs:
                self.cfgs.append(cfg_name(r))

    def of(self, rows, cls):
        return [r for r in rows if CLASS_OF.get(r['kind']) == cls]

    def classes(self, rows):
        return [n for n, _ in CLASSES if self.of(rows, n)]

    @staticmethod
    def per_query(rows):
        seen = {}
        for r in rows:
            seen.setdefault((r['qid'], r['kind'], r['src'], r['dst']), r)
        return list(seen.values())


def tot(rows, k):
    return sum(val(r, k) for r in rows)


def make_results(build, W):
    for f_ in ('graph.dat', 'graph.dat.SO', 'graph.dat.P'):
        shutil.copy(os.path.join(METRO, f_), W)
    dat = os.path.join(W, 'graph.dat')
    run([os.path.join(build, 'build-index-basic'), dat])
    q = os.path.join(METRO, 'queries.tsv')
    run([os.path.join(build, 'ring-tunnels'), dat, q, '--out', os.path.join(W, 'sp.csv')] + SP_OPTS)
    run([os.path.join(build, 'ring-tunnels'), dat, q, '--out', os.path.join(W, 'cv.csv')] + CV_OPTS)


def by_class_cfg(head, rows, cls_col=0, cfg_col=1):
    return {(r[cls_col], r[cfg_col]): r for r in rows}


# ------------------------------------------------------------------ T1
def t1(text, sp, cv):
    R = Run(sp)
    secs = [m.group(1) for m in re.finditer(r'^## (P\w+)\. ', text, re.M)]
    check(secs == ['P0', 'P1', 'P1b', 'P2', 'P2b', 'P3', 'P4', 'P5', 'P6'], f'T1: sections {secs}')
    check(len(R.cfgs) == 8, f'T1: {len(R.cfgs)} configurations in metro, expected 8')
    for c in ('c-to-v', 'v-to-v (sampled sources)'):
        check(c in R.classes(R.all), f'T1: class {c} is not in metro')

    # P0
    h, rows = tab(text, 'P0')
    p0 = {r[0]: r for r in rows}
    check(list(p0) == R.classes(R.every), f'T1 P0: classes {list(p0)}')
    check(h == ['class', 'queries', 'status != ok', 'pf_status != ok', 'pf_check != 1', 'verify != 1'], f'T1 P0: header {h}')
    for cls in R.classes(R.every):
        rs = R.of(R.every, cls)
        qs = R.per_query(rs)
        ok = [q for q in qs if q['status'] == 'ok']
        vq = [q for q in ok if q['pf_status'] == 'ok']
        vr = [r for r in R.of(R.valid, cls)]
        exp = [str(len(qs)), str(len(qs) - len(ok)), str(len(ok) - len(vq)),
               str(sum(1 for q in vq if q['pf_check'] != '1')), str(sum(1 for r in vr if r['verify'] != '1'))]
        check(p0[cls][1:] == exp, f'T1 P0 {cls}: {p0[cls][1:]}, expected {exp}')
        check(p0[cls][4] == '0' and p0[cls][5] == '0', f'T1 P0 {cls}: pf_check or verify failures in metro: {p0[cls]}')
    # T0b: the rows with status != ok (sem empty) are counted, and metro has some
    bad = {cls: len(R.per_query([r for r in R.of(R.every, cls) if r['status'] != 'ok'])) for cls in R.classes(R.every)}
    check(any(bad.values()), 'T0b: metro has no status != ok row (the check of P0 is not exercised)')
    check(all(p0[c][2] == str(n) for c, n in bad.items()), f'T0b: P0 status != ok {[(c, p0[c][2]) for c in bad]}, expected {bad}')
    check('pf_status values: {' in section(text, 'P0'), 'T1 P0: no pf_status values line')

    # P1
    h, rows = tab(text, 'P1')
    p1 = {r[0]: r for r in rows}
    for cls in R.classes(R.valid):
        rs = R.of(R.valid, cls)
        qs = R.per_query(rs)
        r = p1[cls]
        check(r[col(h, 'queries')] == str(len(qs)), f'T1 P1 {cls}: queries {r[1]}')
        for name, exp in (('PathFinder', tot(qs, 'pf_bytes') / 1e6), ('PathFinder structs', tot(qs, 'pf_bytes_structs') / 1e6),
                          ('plain H_sp', tot(qs, 'Hplain_bits') / 8e6), ('differential H_sp', tot(qs, 'H_bits') / 8e6)):
            check(close(r[col(h, name)], exp, 3), f'T1 P1 {cls} {name}: {r[col(h, name)]}, expected {exp}')
        for c in R.cfgs:
            exp = tot([x for x in rs if cfg_name(x) == c], 'S_bits') / 8e6
            cell = r[col(h, f'S ({c})')]
            check(close(cell, exp, 3), f'T1 P1 {cls} S ({c}): {cell}, expected {exp}')
    sec = section(text, 'P1').split('PathFinder / S:')[1]
    lines = [split_cells(l) for l in sec.splitlines() if l.startswith('|') and not l.startswith('|---')]
    check(len(lines) - 1 == len(R.classes(R.valid)) * len(R.cfgs), f'T1 P1: {len(lines) - 1} rows in the ratio table')
    rat = {(r[0], r[1]): r for r in lines[1:]}
    for cls in R.classes(R.valid):
        for c in R.cfgs:
            rs = [x for x in R.of(R.valid, cls) if cfg_name(x) == c]
            s = tot(rs, 'S_bits') / 8
            exp = [fx(div(tot(rs, 'pf_bytes'), s), 1), fx(div(tot(rs, 'pf_bytes_structs'), s), 1)]
            check(rat[(cls, c)][2:4] == exp, f'T1 P1 ratios {cls} {c}: {rat[(cls, c)][2:4]}, expected {exp}')
            ms_ = sorted(val(x, 'pf_bytes') / (val(x, 'S_bits') / 8) for x in rs if val(x, 'H_edges') >= 1 and val(x, 'S_bits') > 0)
            m = (ms_[(len(ms_) - 1) // 2] + ms_[len(ms_) // 2]) / 2 if ms_ else None
            check(rat[(cls, c)][4] == fx(m, 1), f'T1 P1 median {cls} {c}: {rat[(cls, c)][4]}, expected {fx(m, 1)}')

    # P1b
    h, rows = tab(text, 'P1b')
    check(h == ['class', 'queries', 'PathFinder states', 'R nodes', 'H_sp nodes', 'automaton: states / R nodes',
                'pruning: R / H_sp nodes', 'states / H_sp nodes', 'PathFinder transitions', 'H_sp edges',
                'transitions / H_sp edges', 'plain H_sp / S (W=0)', 'plain H_sp / S (4x+10)'], f'T1 P1b: header {h}')
    check('H_sp and R use the reverse Glushkov NFA; PathFinder uses the minimal DFA of the same language.' in section(text, 'P1b'),
          'T1 P1b: note missing')
    p1b = {r[0]: r for r in rows}
    for cls in R.classes(R.valid):
        rs = R.of(R.valid, cls)
        qs = R.per_query(rs)
        st, rn, hn = tot(qs, 'pf_states'), tot(qs, 'R_nodes'), tot(qs, 'H_nodes')
        r = p1b[cls]
        exp = {'queries': str(len(qs)), 'PathFinder states': f(st, 0), 'R nodes': f(rn, 0), 'H_sp nodes': f(hn, 0),
               'automaton: states / R nodes': f(div(st, rn), 2), 'pruning: R / H_sp nodes': f(div(rn, hn), 2),
               'states / H_sp nodes': f(div(st, hn), 2), 'PathFinder transitions': f(tot(qs, 'pf_transitions'), 0),
               'H_sp edges': f(tot(qs, 'H_edges'), 0),
               'transitions / H_sp edges': f(div(tot(qs, 'pf_transitions'), tot(qs, 'H_edges')), 2)}
        for k, e in exp.items():
            check(r[col(h, k)] == e, f'T1 P1b {cls} {k}: {r[col(h, k)]}, expected {e}')
        for name, c in (('plain H_sp / S (W=0)', 'region 0'), ('plain H_sp / S (4x+10)', 'region_rel 4x+10')):
            x = [y for y in rs if cfg_name(y) == c]
            e = f(div(tot(x, 'Hplain_bits'), tot(x, 'S_bits')), 2)
            check(r[col(h, name)] == e, f'T1 P1b {cls} {name}: {r[col(h, name)]}, expected {e}')

    # P2, P2b, P3
    def us(r, k):
        return round(val(r, k) * 1e6)

    h2, r2 = tab(text, 'P2')
    h2b, r2b = tab(text, 'P2b')
    h3, r3 = tab(text, 'P3')
    d2, d2b, d3 = by_class_cfg(h2, r2), by_class_cfg(h2b, r2b), by_class_cfg(h3, r3)
    n = len(R.classes(R.valid)) * len(R.cfgs)
    check(len(d2) == len(d2b) == len(d3) == n, f'T1 P2/P2b/P3: {len(d2)}, {len(d2b)}, {len(d3)} rows, expected {n}')
    for cls in R.classes(R.valid):
        for c in R.cfgs:
            rs = [x for x in R.of(R.valid, cls) if cfg_name(x) == c]
            tag = f'{cls} {c}'
            pf, bd, ex = sum(us(x, 't_pf') for x in rs), sum(us(x, 't_build') for x in rs), sum(us(x, 't_expand') for x in rs)
            check(d2[(cls, c)][col(h2, 'queries')] == str(len(rs)), f'T1 P2 {tag}: queries')
            check(d2[(cls, c)][col(h2, 'build / BFS')] == fx(div(bd, pf)), f'T1 P2 {tag}: build / BFS {d2[(cls, c)][5]}, expected {fx(div(bd, pf))}')
            check(d2b[(cls, c)][col(h2b, 'queries')] == str(len(rs)), f'T1 P2b {tag}: queries')
            check(d2b[(cls, c)][col(h2b, 'decode / BFS')] == fx(div(ex, pf)), f'T1 P2b {tag}: decode / BFS {d2b[(cls, c)][5]}, expected {fx(div(ex, pf))}')
            faster = f(100 * sum(1 for x in rs if us(x, 't_expand') < us(x, 't_pf')) / len(rs), 1)
            check(d2b[(cls, c)][col(h2b, 'queries with t_expand < t_pf (%)')] == faster, f'T1 P2b {tag}: % {d2b[(cls, c)][-1]}, expected {faster}')
            extra, saving = bd - pf, pf - ex
            be = '0' if extra <= 0 else ('never' if saving <= 0 else str(-(-extra // saving)))
            check(d3[(cls, c)][col(h3, 'break-even reuses')] == be, f'T1 P3 {tag}: {d3[(cls, c)][4]}, expected {be}')
            check(d3[(cls, c)][col(h3, 'extra build (ms)')] == f(extra / 1e3, 3), f'T1 P3 {tag}: extra {d3[(cls, c)][2]}, expected {f(extra / 1e3, 3)}')
    check("t_pf is the simulation of PathFinder's BFS on the same index (not MillenniumDB)." in section(text, 'P2'), 'T1 P2: note')
    check('Reusing S k times costs t_build + k·t_expand; re-running costs (k+1)·t_pf.' in section(text, 'P3'), 'T1 P3: note')

    # P4: every row has as many cells as the header; ordered by number of queries
    h, rows = tab(text, 'P4')
    check(bool(rows) and all(len(r) == len(h) for r in rows), 'T1 P4: rows with a different number of cells')
    check('(config: region_rel 4x+10)' in section(text, 'P4').splitlines()[0], 'T1 P4: config in the title')
    cnt = {}
    for q in R.per_query([r for r in R.valid if r['kind'] in ('CV', 'VC') and r['shape']]):
        cnt[q['shape']] = cnt.get(q['shape'], 0) + 1
    top = sorted(cnt, key=lambda s: (-cnt[s], s))[:12]
    check([r[0] for r in rows] == top and [int(r[1]) for r in rows] == [cnt[s] for s in top], f'T1 P4: shapes {[r[:2] for r in rows]}, expected {top}')

    # P5
    h, rows = tab(text, 'P5')
    p5 = by_class_cfg(h, rows)
    cvv = [r for r in cv if r['status'] == 'ok']
    ck = {}
    for r in cvv:
        ck.setdefault((r['qid'], r['kind'], r['src'], r['dst'], r['model'], r['budget']), r)
    sk = {}
    for r in R.valid:
        sk.setdefault((r['qid'], r['kind'], r['src'], r['dst'], r['model'], r['budget']), r)
    names = [n for n, _ in CLASSES[:3]]
    exp_keys = {}
    for k in sk:
        if k in ck and CLASS_OF[k[1]] in names:
            exp_keys.setdefault((CLASS_OF[k[1]], f'{k[4]} {k[5]}'), []).append(k)
    check(set(p5) == set(exp_keys), f'T1 P5: rows {sorted(p5)}')
    for key, ks in exp_keys.items():
        r = p5.get(key)
        if r is None:
            continue
        ha, hs = sum(val(ck[k], 'H_edges') for k in ks), sum(val(sk[k], 'H_edges') for k in ks)
        check(r[col(h, 'matched queries')] == str(len(ks)), f'T1 P5 {key}: matched {r[2]}, expected {len(ks)}')
        check(r[col(h, 'H_sp / H edges')] == f(div(hs, ha), 3), f'T1 P5 {key}: ratio {r[5]}, expected {f(div(hs, ha), 3)}')
        check(hs <= ha, f'T1 P5 {key}: H_sp edges > H edges')
        check(num(r[col(h, 'H_sp edges')]) <= num(r[col(h, 'H edges (all)')]), f'T1 P5 {key}: H_sp edges > H edges in the table')
    ua = sum(1 for k in sk if CLASS_OF[k[1]] in names and k not in ck)
    ub = sum(1 for k in ck if CLASS_OF.get(k[1]) in names and k not in sk)
    check(f'unmatched: {ua} rows of sp.csv and {ub} rows of cv.csv' in text, f'T1 P5: unmatched line, expected {ua}/{ub}')

    # P6
    h, rows = tab(text, 'P6')
    p6 = {r[0]: r for r in rows}
    for cls in R.classes(R.valid):
        qs = R.per_query(R.of(R.valid, cls))
        mx = max(val(q, 'pf_paths') for q in qs)
        check(p6[cls][col(h, 'queries')] == str(len(qs)), f'T1 P6 {cls}: queries')
        check(p6[cls][col(h, 'max shortest walks')] == '%.3g' % mx, f'T1 P6 {cls}: max {p6[cls][3]}, expected {"%.3g" % mx}')


# ------------------------------------------------------------------ T5 / T6: hand-written CSVs
def srow(**kw):
    base = dict(sem='shortest', status='ok', pf_status='ok', pf_check='1', verify='1', model='region', budget='0', shape='a',
                H_edges='6', H_nodes='5', R_nodes='20', pf_states='10', pf_transitions='12', Hplain_bits='8000000',
                H_bits='4000000', S_bits='1600000', pf_bytes='2000000', pf_bytes_structs='500000', pf_paths='4', t_pf='0.010',
                t_build='0.030', t_expand='0.002', src='1', dst='2')
    base.update(kw)
    return [base.get(c, '') for c in COLS]


def write_csv(d, rows, name='sp.csv'):
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, name), 'w', newline='') as fh:
        w = csv.writer(fh)
        w.writerow(COLS)
        w.writerows(rows)
    return d


C1 = dict(model='region', budget='0')
C2 = dict(model='region_rel', budget='4x+10')


def synthetic(d, cfg2=C2):
    rows = [
        # CV: break-even ceil(20/8) = 3 and ceil(21/6) = 4; the shape has a |
        srow(qid='1', kind='CV', shape='(a|b)*', **C1, t_build='0.030', t_expand='0.002', S_bits='1600000'),
        srow(qid='1', kind='CV', shape='(a|b)*', **cfg2, t_build='0.031', t_expand='0.004', S_bits='800000'),
        # VCp: t_expand >= t_pf and t_build > t_pf: never
        srow(qid='2', kind='VCp', **C1, t_build='0.020', t_expand='0.012'),
        srow(qid='2', kind='VCp', **cfg2, t_build='0.020', t_expand='0.012'),
        # VCr: t_build <= t_pf: 0
        srow(qid='3', kind='VCr', **C1, t_build='0.008', t_expand='0.003'),
        srow(qid='3', kind='VCr', **cfg2, t_build='0.008', t_expand='0.003'),
        # VVs: a timeout, a pf_status=too_many_states, pf_check=0 with 1e+12 paths, verify=0 and S_bits=0
        srow(qid='4', kind='VVs', status='timeout', **C1),
        srow(qid='5', kind='VVs', pf_status='too_many_states', **C1),
        srow(qid='6', kind='VVs', pf_check='0', pf_paths='1e+12', **C1, S_bits='0'),
        srow(qid='6', kind='VVs', pf_check='0', pf_paths='1e+12', **cfg2, S_bits='800000'),
        srow(qid='7', kind='VVs', pf_paths='10', **C1, S_bits='0'),
        srow(qid='7', kind='VVs', pf_paths='10', **cfg2, S_bits='800000', verify='0'),
    ]
    return write_csv(d, rows)


def t5(work):
    d = synthetic(os.path.join(work, 'syn'))
    p = tables(d, '--min-h-edges', '1')
    text = p.stdout
    check(any(l.startswith('warning:') and 'cv.csv' in l for l in p.stderr.splitlines()), f'T5: no warning about cv.csv:\n{p.stderr}')
    check(any(l.startswith('warning:') and 'pf_check' in l for l in p.stderr.splitlines()), f'T5: no warning for pf_check:\n{p.stderr}')
    check(any(l.startswith('warning:') and 'verify' in l for l in p.stderr.splitlines()), f'T5: no warning for verify:\n{p.stderr}')
    check('no data: ' in section(text, 'P5') and 'cv.csv missing' in section(text, 'P5'), 'T5 P5: no "no data ... missing"')

    # P0
    h, rows = tab(text, 'P0')
    p0 = {r[0]: r[1:] for r in rows}
    check(p0.get('c-to-v') == ['1', '0', '0', '0', '0'], f'T5 P0 c-to-v: {p0.get("c-to-v")}')
    check(p0.get('c-to-c') == ['1', '0', '0', '0', '0'], f'T5 P0 c-to-c: {p0.get("c-to-c")}')
    # VVs: queries 4 (qid 4, 5, 6, 7), 1 timeout, 1 pf_status, pf_check 1 (q6), verify 1 row (q7, cfg 2)
    check(p0.get('v-to-v (sampled sources)') == ['4', '1', '1', '1', '1'], f'T5 P0 VVs: {p0.get("v-to-v (sampled sources)")}')
    check("pf_status values: {'ok': 5, 'too_many_states': 1}" in text, 'T5 P0: pf_status values')

    # P1: timeout / too_many_states queries are not there
    h, rows = tab(text, 'P1')
    p1 = {r[0]: r for r in rows}
    check(p1['v-to-v (sampled sources)'][1] == '2', f'T5 P1 VVs queries: {p1["v-to-v (sampled sources)"][1]}')
    r = p1['c-to-v']
    check(r[2:6] == ['2.000', '0.500', '1.000', '0.500'], f'T5 P1 c-to-v totals: {r[2:6]}')
    check(r[col(h, 'S (region 0)')] == '0.200' and r[col(h, 'S (region_rel 4x+10)')] == '0.100', f'T5 P1 S columns: {r[6:]}')
    sec = section(text, 'P1').split('PathFinder / S:')[1]
    rat = {(l[0], l[1]): l[2:] for l in (split_cells(x) for x in sec.splitlines() if x.startswith('|') and not x.startswith('|---'))}
    check(rat[('c-to-v', 'region 0')] == ['10.0x', '2.5x', '10.0x'], f'T5 P1 ratios: {rat[("c-to-v", "region 0")]}')
    check(rat[('c-to-v', 'region_rel 4x+10')] == ['20.0x', '5.0x', '20.0x'], f'T5 P1 ratios 2: {rat[("c-to-v", "region_rel 4x+10")]}')
    # S_bits = 0 in region 0 for both queries of VVs: n/a, no exception
    check(rat[('v-to-v (sampled sources)', 'region 0')] == ['n/a', 'n/a', 'n/a'], f'T5 P1 S=0: {rat[("v-to-v (sampled sources)", "region 0")]}')
    check(rat[('v-to-v (sampled sources)', 'region_rel 4x+10')] == ['20.0x', '5.0x', '20.0x'], f'T5 P1 VVs cfg2: {rat[("v-to-v (sampled sources)", "region_rel 4x+10")]}')

    # P1b: states 10, R 20, H nodes 5, transitions 12, H edges 6; plain / S = 8e6 / 1.6e6 and 8e6 / 8e5
    h, rows = tab(text, 'P1b')
    r = {x[0]: x for x in rows}['c-to-v']
    got = {k: r[col(h, k)] for k in h}
    exp = {'automaton: states / R nodes': '0.50', 'pruning: R / H_sp nodes': '4.00', 'states / H_sp nodes': '2.00',
           'transitions / H_sp edges': '2.00', 'plain H_sp / S (W=0)': '5.00', 'plain H_sp / S (4x+10)': '10.00'}
    for k, e in exp.items():
        check(got[k] == e, f'T5 P1b {k}: {got[k]}, expected {e}')
    r = {x[0]: x for x in rows}['v-to-v (sampled sources)']
    check(r[col(h, 'plain H_sp / S (W=0)')] == 'n/a', f'T5 P1b S=0: {r[col(h, "plain H_sp / S (W=0)")]}')

    # P2, P2b, P3
    def get(n, cls, cfg):
        h, rows = tab(text, n)
        return {k: v for k, v in zip(h, by_class_cfg(h, rows)[(cls, cfg)])}

    g = get('P2', 'c-to-v', 'region 0')
    check(g['queries'] == '1' and g['PathFinder BFS (t_pf)'] == '10.000' and g['build S (t_build)'] == '30.000' and g['build / BFS'] == '3.00x', f'T5 P2: {g}')
    g = get('P2', 'c-to-v', 'region_rel 4x+10')
    check(g['build S (t_build)'] == '31.000' and g['build / BFS'] == '3.10x', f'T5 P2 cfg2: {g}')
    g = get('P2b', 'c-to-v', 'region 0')
    check(g['decode S (t_expand)'] == '2.000' and g['decode / BFS'] == '0.20x' and g['queries with t_expand < t_pf (%)'] == '100.0', f'T5 P2b: {g}')
    g = get('P2b', 'c-to-v', 'region_rel 4x+10')
    check(g['decode / BFS'] == '0.40x', f'T5 P2b cfg2: {g}')
    g = get('P3', 'c-to-v', 'region 0')
    check((g['extra build (ms)'], g['saving per reuse (ms)'], g['break-even reuses']) == ('20.000', '8.000', '3'), f'T5 P3: {g}')
    g = get('P3', 'c-to-v', 'region_rel 4x+10')
    check((g['extra build (ms)'], g['saving per reuse (ms)'], g['break-even reuses']) == ('21.000', '6.000', '4'), f'T5 P3 cfg2: {g}')
    g = get('P3', 'c-to-c', 'region 0')
    check((g['break-even reuses'], g['queries where S never pays off (%)']) == ('never', '100.0'), f'T5 P3 never: {g}')
    g = get('P3', 'c-to-c (reverse)', 'region 0')
    check((g['extra build (ms)'], g['break-even reuses'], g['queries where S never pays off (%)']) == ('-2.000', '0', '0.0'), f'T5 P3 zero: {g}')

    # P4: the | in the shape is escaped, the table has the right number of columns
    sec = section(text, 'P4')
    check('(config: region_rel 4x+10)' in sec.splitlines()[0], 'T5 P4: title')
    check('(a\\|b)\\*' in sec or '(a\\|b)*' in sec, f'T5 P4: shape not escaped:\n{sec}')
    h, rows = tab(text, 'P4')
    check(len(rows) == 1 and len(rows[0]) == 8 and len(h) == 8, f'T5 P4: {rows}')
    check(rows and rows[0][:5] == ['(a|b)*', '1', '2,000.00', '100.00', '20.00x'], f'T5 P4 row: {rows}')

    # P6: 1e+12 is read; S = 800000 + 800000 bits = 200 KB in 4x+10
    h, rows = tab(text, 'P6')
    r = {x[0]: x for x in rows}['v-to-v (sampled sources)']
    check(r[col(h, 'queries')] == '2' and r[col(h, 'max shortest walks')] == '1e+12' and
          r[col(h, 'queries with > 100,000 walks')] == '1' and r[col(h, 'median shortest walks')] == '5e+11', f'T5 P6: {r}')
    check(r[col(h, 'total S (KB)')] == '200.00', f'T5 P6 S: {r}')

    # without region_rel 4x+10: n/a in P1b and the title says which configuration is used
    d2 = synthetic(os.path.join(work, 'syn2'), cfg2=dict(model='region', budget='100'))
    text2 = tables(d2, '--min-h-edges', '1').stdout
    h, rows = tab(text2, 'P1b')
    check(rows and all(r[col(h, 'plain H_sp / S (4x+10)')] == 'n/a' for r in rows), f'T5 P1b without 4x+10: {rows}')
    check('(config: region 100)' in section(text2, 'P4').splitlines()[0], f'T5 P4 title without 4x+10: {section(text2, "P4").splitlines()[0]}')
    check('region 100' in section(text2, 'P6').splitlines()[0], f'T5 P6 title without 4x+10: {section(text2, "P6").splitlines()[0]}')

    # --top-shapes and the default threshold (H_edges = 6 < 1000: no median, n/a)
    text3 = tables(d).stdout
    rat3 = section(text3, 'P1').split('PathFinder / S:')[1]
    check('| c-to-v | region 0 | 10.0x | 2.5x | n/a |' in rat3, f'T5 P1 default threshold: {rat3[:300]}')
    h, rows = tab(tables(d, '--top-shapes', '0', '--min-h-edges', '1').stdout, 'P4')
    check(rows == [], f'T5 --top-shapes 0: {rows}')


def t6b(work):
    # no valid configuration: every row has pf_status != ok
    d = write_csv(os.path.join(work, 'novalid'), [srow(qid='1', kind='CV', pf_status='too_many_states', **C1),
                                                  srow(qid='2', kind='VVs', status='too_large', pf_status='', **C1)])
    text = tables(d).stdout
    check(tab(text, 'P0')[1] != [] and 'no data' in section(text, 'P1') and 'no data' in section(text, 'P4') and
          'no data' in section(text, 'P6'), f'T6b: tables without valid rows:\n{text}')
    p = tables(d)
    check('Traceback' not in p.stderr, f'T6b: traceback:\n{p.stderr}')
    # an empty class x configuration: VVs has only region 0; and a cv.csv that shares nothing / only a part
    rows = [srow(qid='1', kind='CV', **C1), srow(qid='1', kind='CV', **C2, S_bits='800000'),
            srow(qid='2', kind='VVs', **C1)]
    d = write_csv(os.path.join(work, 'partial'), rows)
    write_csv(d, [srow(qid='9', kind='CV', sem='', **C1)], 'cv.csv')
    p = tables(d, '--min-h-edges', '1')
    check('Traceback' not in p.stderr, f'T6b: traceback:\n{p.stderr}')
    h, rows_ = tab(p.stdout, 'P1')
    r = {x[0]: x for x in rows_}['v-to-v (sampled sources)']
    check(r[col(h, 'S (region_rel 4x+10)')] == '0.000', f'T6b P1 empty class x config: {r}')
    sec = section(p.stdout, 'P1').split('PathFinder / S:')[1]
    check(not any(l.startswith('| v-to-v') and 'region_rel' in l for l in sec.splitlines()), 'T6b P1: row of an empty class x config')
    check('no data' in section(p.stdout, 'P5') and 'unmatched: 2 rows of sp.csv and 1 rows of cv.csv' in p.stdout,
          f'T6b P5:\n{section(p.stdout, "P5")}')
    write_csv(d, [srow(qid='1', kind='CV', sem='', **C1)], 'cv.csv')   # matches one of the two rows of CV
    p = tables(d, '--min-h-edges', '1')
    h, rows_ = tab(p.stdout, 'P5')
    check([r[:3] for r in rows_] == [['c-to-v', 'region 0', '1']], f'T6b P5 partial match: {rows_}')
    check('unmatched: 1 rows of sp.csv and 0 rows of cv.csv' in p.stdout, f'T6b P5 unmatched:\n{section(p.stdout, "P5")}')


def t6(work):
    d = write_csv(os.path.join(work, 'only-cv'), [srow(qid='1', kind='CV', **C1), srow(qid='1', kind='CV', **C2, t_build='0.031')])
    text = tables(d).stdout
    for n in ('P0', 'P1', 'P1b', 'P2', 'P2b', 'P3', 'P6'):
        h, rows = tab(text, n)
        check(bool(rows) and all(r[0] == 'c-to-v' for r in rows), f'T6 {n}: rows {[r[0] for r in rows]}')
    check('c-to-c' not in text and 'v-to-v' not in text, 'T6: c-to-c or v-to-v in the output')
    # shape empty everywhere: no data in P4
    d = write_csv(os.path.join(work, 'noshape'), [srow(qid='1', kind='CV', shape='', **C1)])
    check('no data' in section(tables(d).stdout, 'P4'), 'T6: P4 without shapes does not say "no data"')


# ------------------------------------------------------------------ T7
BASE_COMMIT = 'a5bd785'   # main before --paper: the output without --paper must stay byte by byte


def t7(work, W):
    r = subprocess.run(['git', 'show', BASE_COMMIT + ':tunnels/pathfinder_tables.py'], cwd=ROOT, stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, text=True)
    if r.returncode != 0:   # never skipped in silence
        check(False, f'T7: commit {BASE_COMMIT} (script without --paper) is not available: {r.stderr.strip()}')
        return
    old = os.path.join(work, 'pathfinder_tables_main.py')
    with open(old, 'w') as fh:
        fh.write(r.stdout)
    dirs = [(W, ['--min-h-edges', '1']), (W, []), (synthetic(os.path.join(work, 'syn7')), [])]
    for d, extra in dirs:
        a = subprocess.run([sys.executable, old, d] + extra, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        b = subprocess.run([sys.executable, SCRIPT, d] + extra, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        check(a.returncode == b.returncode and a.stdout == b.stdout and a.stderr == b.stderr,
              f'T7: {d} {extra}: the output differs from the one of main')


# ------------------------------------------------------------------ T8
def prow(qid, kind, src, dst, tb, tp, cfg=C2, **kw):
    return srow(qid=str(qid), kind=kind, src=str(src), dst=str(dst), t_build=tb, t_pf=tp, **cfg, **kw)


def write_mdb(d, results, raw):
    with open(os.path.join(d, 'results.csv'), 'w', newline='') as fh:
        w = csv.writer(fh)
        w.writerow(['qid', 'kind', 'src', 'dst', 'dir', 'mdb_status', 'n', 'sim_t_pf'])
        w.writerows(results)
    with open(os.path.join(d, 'raw.csv'), 'w', newline='') as fh:
        w = csv.writer(fh)
        w.writerow(['n', 'warmup', 'pf_status', 'error', 'ns'])
        w.writerows(raw)
    return os.path.join(d, 'results.csv'), os.path.join(d, 'raw.csv')


def md_tables(text):
    """[(header, rows)] of every Markdown table of the text"""
    out, cur = [], []
    for l in text.splitlines() + ['']:
        if l.startswith('|'):
            if not l.startswith('|---'):
                cur.append(split_cells(l))
        elif cur:
            out.append((cur[0], cur[1:]))
            cur = []
    return out


def table_of(text, first_cell, second=None):
    for h, rows in md_tables(text):
        if h[0] == first_cell and (second is None or h[1] == second):
            return h, rows
    return [], []


def t8(work):
    d = os.path.join(work, 'paper')
    ring = [
        # c-to-v: means (KB) 3,000 / 1,000 / 1,500 / 750 / 300
        prow(1, 'CV', 1, 2, '0.030', '0.010', pf_bytes='2000000', pf_bytes_structs='500000', Hplain_bits='8000000',
             H_bits='4000000', S_bits='1600000', pf_states='10', H_nodes='5'),
        prow(2, 'VC', 3, 4, '0.010', '0.010', pf_bytes='4000000', pf_bytes_structs='1500000', Hplain_bits='16000000',
             H_bits='8000000', S_bits='3200000', pf_states='20', H_nodes='5'),
        prow(3, 'CVp', 5, 6, '0.045', '0.030', H_edges='6', Hplain_bits='8000000', pf_states='100', H_nodes='10'),
        prow(4, 'VCp', 7, 8, '0.015', '0.030', H_edges='10', Hplain_bits='16000000', pf_states='300', H_nodes='10'),
        # not used: another configuration (same key, other t_build), the same row again, status, pf_status, sem, VVs, CVr
        prow(1, 'CV', 1, 2, '0.999', '0.999', cfg=C1, pf_bytes='900000000'),
        prow(1, 'CV', 1, 2, '0.030', '0.010', pf_bytes='2000000', pf_bytes_structs='500000', Hplain_bits='8000000',
             H_bits='4000000', S_bits='1600000', pf_states='10', H_nodes='5'),
        prow(8, 'CV', 1, 2, '0.5', '0.5', status='timeout', pf_bytes='900000000'),
        prow(9, 'CV', 1, 2, '0.5', '0.5', pf_status='too_many_states', pf_bytes='900000000'),
        prow(10, 'CV', 1, 2, '0.5', '0.5', sem='all', pf_bytes='900000000'),
        prow(11, 'VVs', 1, 2, '0.5', '0.5', pf_bytes='900000000'),
        prow(12, 'CVr', 6, 5, '0.5', '0.5', pf_bytes='900000000'),
    ]
    plain = [prow(1, 'CV', 1, 2, '0.020', '0.004'), prow(2, 'VC', 3, 4, '0.010', '0.006'),
             prow(3, 'CVp', 5, 6, '0.012', '0.003'), prow(4, 'VCp', 7, 8, '0.006', '0.003')]
    write_csv(d, ring, 'sp_early.csv')
    write_csv(d, plain, 'sp_early_adj.csv')
    p = tables(d, '--sp', 'sp_early.csv', '--paper', '--adj', 'sp_early_adj.csv')
    out = p.stdout
    check(out.startswith('## Comparison with PathFinder\n') and out.count('\n## ') == 0 and
          [l for l in out.splitlines() if l.startswith('### ')] ==
          ['### Space held after evaluation (c-to-v)', '### Construction time relative to PathFinder (ratios of sums)'],
          'T8: --paper prints only the comparison with PathFinder, with the titles by content')
    check(not re.search(r'Table \d|Sec\. \d|P7', out), 'T8: a title with a number of the paper')
    h, rows = table_of(out, '', 'PathFinder (total)')
    check(h == ['', 'PathFinder (total)', 'PathFinder (structs)', 'plain H_sp', 'differential H_sp', 'S'], f'T8: header of Table 8 {h}')
    check(rows == [['KB per query', '3,000', '1,000', '1,500', '750', '300'],
                   ['vs S', '10.0×', '3.3×', '5.0×', '2.5×', '1×']], f'T8: Table 8 {rows}')
    for line in ('- PathFinder / plain H_sp: 2.0×', '- PathFinder structs / plain H_sp: 0.667×',
                 '- plain H_sp / differential H_sp: 2.0×', '- PathFinder / differential H_sp: 4.0×',
                 '- PathFinder structs / differential H_sp: 1.3×', '- differential H_sp / S: 2.5×',
                 '- PathFinder states / H_sp nodes, c-to-v: 3.00',
                 '- c-to-c (CVp, VCp), 2 pairs: PathFinder states / H_sp nodes 20.00; mean H_sp edges per pair 8.0; '
                 'mean plain H_sp per pair 1,500,000.0 B'):
        check(line in out.splitlines(), f'T8: missing line {line!r}\n{out}')
    h, rows = table_of(out, '', 't_b/t_pf c-to-v')
    check(rows == [['Ring', '2.00', '1.00', 'n/a', 'n/a'], ['Plain', '3.00', '3.00', 'n/a', 'n/a']], f'T8: Table 9 without MDB {rows}')
    check('- c-to-v: t_pf Ring / t_pf Plain 2.00; t_MDB / t_pf Plain n/a; t_pf Ring / t_MDB n/a' in out.splitlines() and
          '- c-to-c: t_pf Ring / t_pf Plain 10.00; t_MDB / t_pf Plain n/a; t_pf Ring / t_MDB n/a' in out.splitlines(),
          f'T8: the lines of Table 9\n{out}')
    check('\\begin{tabular}{@{}l rr rr@{}}' in out and 'Ring & 2.00 & 1.00 & -- & -- \\\\' in out and
          'Plain & 3.00 & 3.00 & -- & -- \\\\' in out and '\\end{tabular}' in out, f'T8: LaTeX block\n{out}')
    # without --adj: Plain is n/a, with a warning
    p = tables(d, '--sp', 'sp_early.csv', '--paper')
    check(table_of(p.stdout, '', 't_b/t_pf c-to-v')[1][1] == ['Plain', 'n/a', 'n/a', 'n/a', 'n/a'], 'T8: without --adj')
    check('warning: no --adj' in p.stderr, 'T8: no warning without --adj')
    # --adj, --mdb without --paper, and --mdb alone: usage errors
    check(tables(d, '--sp', 'sp_early.csv', '--adj', 'sp_early_adj.csv', expect_rc=2).returncode == 2, 'T8: --adj without --paper')
    check(tables(d, '--sp', 'sp_early.csv', '--paper', '--mdb', 'x.csv', expect_rc=2).returncode == 2, 'T8: --mdb without --mdb-raw')
    check(tables(d, '--sp', 'sp_early.csv', '--paper', '--adj', 'nope.csv', expect_rc=1).returncode == 1, 'T8: --adj does not exist')

    # ---- with MillenniumDB: the four cases of tb_key, the exclusions, the missing rows
    x = os.path.join(work, 'cross')
    ring = [prow(1, 'CV', 1, 2, '0.030', '0.010'), prow(2, 'VC', 3, 4, '0.010', '0.010'),
            prow(3, 'CVp', 5, 6, '0.045', '0.030'), prow(4, 'VCp', 7, 8, '0.015', '0.030'),
            prow(5, 'CVr', 10, 9, '0.100', '0.050'), prow(6, 'VCr', 12, 11, '0.200', '0.100'),
            prow(7, 'CV', 13, 14, '0.040', '0.020'),                      # only in Ring
            prow(1, 'CV', 1, 2, '0.777', '0.777', cfg=C1)]                # other configuration: ignored
    plain = [prow(1, 'CV', 1, 2, '0.020', '0.004'), prow(2, 'VC', 3, 4, '0.010', '0.006'),
             prow(3, 'CVp', 5, 6, '0.012', '0.003'), prow(4, 'VCp', 7, 8, '0.006', '0.003'),
             prow(5, 'CVr', 10, 9, '0.050', '0.025'), prow(6, 'VCr', 12, 11, '0.084', '0.040')]
    write_csv(x, ring, 'sp_early.csv')
    write_csv(x, plain, 'sp_early_adj.csv')
    ok = 'ok'
    res = [
        (1, 'CV', 1, 2, 'subj', ok, 1, '0.001'),
        (2, 'VC', 3, 4, 'obj', ok, 2, '0.001'),
        (3, 'CVp', 5, 6, 'subj', ok, 3, '0.001'),    # CVp, dir=subj: (3, CVp, 5, 6)
        (5, 'CVp', 9, 10, 'obj', ok, 4, '0.001'),    # CVp, dir=obj:  (5, CVr, 10, 9)
        (4, 'VCp', 7, 8, 'obj', ok, 5, '0.001'),     # VCp, dir=obj:  (4, VCp, 7, 8)
        (6, 'VCp', 11, 12, 'subj', ok, 6, '0.001'),  # VCp, dir=subj: (6, VCr, 12, 11)
        (1, 'CV', 1, 2, 'unknown', ok, 7, '0.001'),  # unknown: out
        (1, 'CV', 1, 2, 'subj', 'timeout', 8, '0.001'),   # mdb_status != ok: out
        (1, 'CV', 1, 2, 'subj', ok, 9, '0.001'),     # no cold time: out
        (99, 'CV', 1, 2, 'subj', ok, 10, '0.001'),   # in neither CSV: missing in both
        (7, 'CV', 13, 14, 'subj', ok, 11, '0.001'),  # only in Ring: missing in Plain
        (2, 'VC', 3, 4, 'obj', ok, 12, ''),          # without sim_t_pf: out (as the MillDB script)
        (7, 'CVr', 14, 13, 'subj', ok, 1, '0.001'),  # kind outside the four: out
    ]
    raw = [
        (1, 1, 'ok', '', 20000000), (1, 1, 'ok', '', 5000000000),   # n=1: the first warmup row counts
        (2, 0, 'ok', '', 9000000000), (2, 1, 'ok', '', 70000000),   # n=2: the first one is not warmup
        (3, 1, 'ok', 'boom', 1000000000), (3, 1, 'ok', '', 40000000),   # n=3: the first one has an error
        (4, 1, 'ok', '', 80000000), (5, 1, 'ok', '', 10000000), (6, 1, 'ok', '', 30000000),
        (7, 1, 'ok', '', 10000000), (8, 1, 'ok', '', 10000000), (9, 1, 'timeout', '', 10000000),
        (10, 1, 'ok', '', 10000000), (11, 1, 'ok', '', 50000000), (12, 1, 'ok', '', 10000000),
    ]
    mres, mraw = write_mdb(x, res, raw)
    p = tables(x, '--sp', 'sp_early.csv', '--paper', '--adj', 'sp_early_adj.csv', '--mdb', mres, '--mdb-raw', mraw)
    h, rows = table_of(p.stdout, '', 't_b/t_pf c-to-v')
    check(rows == [['Ring', '2.00', '1.71', '0.57', '2.25'], ['Plain', '3.00', '2.14', '0.33', '0.95']],
          f'T8: Table 9 with MDB {rows}')
    h, rows = table_of(p.stdout, 'system')
    got = {(r[0], r[1]): (r[2], r[3]) + tuple(float(v) for v in r[4:]) for r in rows}
    exp = {('Ring', 'c-to-v'): ('3', '1', 0.080, 0.040, 0.140), ('Ring', 'c-to-c'): ('4', '0', 0.360, 0.210, 0.160),
           ('Plain', 'c-to-v'): ('2', '2', 0.030, 0.010, 0.090), ('Plain', 'c-to-c'): ('4', '0', 0.152, 0.071, 0.160)}
    check(set(got) == set(exp) and all(got[k][:2] == exp[k][:2] and all(abs(a - b) < 1e-9 for a, b in zip(got[k][2:], exp[k][2:]))
                                       for k in exp), f'T8: rows used {got}')
    for line in ('- c-to-v: t_pf Ring / t_pf Plain 4.00; t_MDB / t_pf Plain 9.00; t_pf Ring / t_MDB 0.29',
                 '- c-to-c: t_pf Ring / t_pf Plain 2.96; t_MDB / t_pf Plain 2.25; t_pf Ring / t_MDB 1.31'):
        check(line in p.stdout.splitlines(), f'T8: missing line {line!r}\n{p.stdout}')
    check('Ring & 2.00 & 1.71 & 0.57 & 2.25 \\\\' in p.stdout, 'T8: LaTeX with MDB')

    # a repeated key with another t_build (or t_pf): error; the same values: fine
    for col, v in (('t_build', '0.031'), ('t_pf', '0.011')):
        dup = os.path.join(work, 'dup-' + col)
        rr = [prow(1, 'CV', 1, 2, '0.030', '0.010'), prow(1, 'CV', 1, 2, '0.030', '0.010')]
        rr[1][COLS.index(col)] = v
        write_csv(dup, rr, 'sp_early.csv')
        p = tables(dup, '--sp', 'sp_early.csv', '--paper', expect_rc=1)
        check('error:' in p.stderr and '## P7' not in p.stdout, f'T8: repeated key with another {col}: {p.stderr!r}')
    # the same key in the Plain CSV
    write_csv(dup, [prow(1, 'CV', 1, 2, '0.030', '0.010')], 'sp_early.csv')
    write_csv(dup, [prow(1, 'CV', 1, 2, '0.030', '0.010'), prow(1, 'CV', 1, 2, '0.031', '0.010')], 'adj.csv')
    p = tables(dup, '--sp', 'sp_early.csv', '--paper', '--adj', 'adj.csv', expect_rc=1)
    check('Plain' in p.stderr, f'T8: repeated key in Plain: {p.stderr!r}')
    # a row without t_build or without t_pf does not count (as in the MillDB script)
    write_csv(dup, [prow(1, 'CV', 1, 2, '0.030', '0.010'), prow(2, 'CV', 3, 4, '', '0.010'),
                    prow(3, 'VC', 5, 6, '0.020', '')], 'sp_early.csv')
    p = tables(dup, '--sp', 'sp_early.csv', '--paper')
    h, rows = table_of(p.stdout, 'system')
    check(rows and rows[0][:3] == ['Ring', 'c-to-v', '1'], f'T8: rows with an empty t_build or t_pf: {rows}')
    # without valid rows: no data and exit code 0
    write_csv(dup, [prow(1, 'CV', 1, 2, '0.030', '0.010', cfg=C1)], 'sp_early.csv')
    p = tables(dup, '--sp', 'sp_early.csv', '--paper')
    check('no data' in p.stdout and 'n/a' in p.stdout, 'T8: no valid rows')


# ------------------------------------------------------------------ T9
def millb_join(res_rows, raw_rows, sp_rows, tcol, filt):
    """the ad hoc script of MillDB (tb_join.py), as functions over rows: {group: (n, missing, mean mdb, mean pf, mean tb, pf/m, b/m)}"""
    tb = {}
    for r in sp_rows:
        if all(r[c] == v for c, v in filt.items()) and r['status'] == 'ok' and r[tcol] != '':
            try:
                key = (r['qid'], r['kind'], int(r['src']), int(r['dst']))
            except ValueError:
                continue
            tb[key] = r[tcol]
    cold = {}
    for r in raw_rows:
        if r['warmup'] == '1' and r['pf_status'] == 'ok' and r['error'] == '':
            cold.setdefault(r['n'], float(r['ns']) / 1e9)

    def tb_key(r):
        q, k, s, d = r['qid'], r['kind'], int(r['src']), int(r['dst'])
        if k in ('CV', 'VC'):
            return (q, k, s, d)
        own_dir = 'subj' if k == 'CVp' else 'obj'
        return (q, k, s, d) if r['dir'] == own_dir else (q, {'CVp': 'CVr', 'VCp': 'VCr'}[k], d, s)
    out = {}
    for g, ks in (('c-to-v', ['CV', 'VC']), ('c-to-c', ['CVp', 'VCp'])):
        rs = [r for r in res_rows if r['kind'] in ks and r['mdb_status'] == 'ok' and r['sim_t_pf'] and r['n'] in cold
              and r['dir'] in ('subj', 'obj')]
        ok = [(r, tb[tb_key(r)]) for r in rs if tb_key(r) in tb]
        m = sum(cold[r['n']] for r, _ in ok)
        pf = sum(float(r['sim_t_pf']) for r, _ in ok)
        b = sum(float(t) for _, t in ok)
        out[g] = (len(ok), len(rs) - len(ok), m, pf, b)
    return out


def t9(build, work):
    import random
    W = os.path.join(work, 'metro9')
    os.makedirs(os.path.join(W, 'adj'))
    make_results(build, W)
    for f_ in ('graph.dat', 'graph.dat.SO', 'graph.dat.P'):
        shutil.copy(os.path.join(METRO, f_), os.path.join(W, 'adj'))
    q = os.path.join(METRO, 'queries.tsv')
    EARLY = BUD + ['--shortest', '--early-stop', '--pairs', '5', '--pair-reverse', '--vv-derive', '--vv-samples', '5', '--reps', '1']
    run([os.path.join(build, 'ring-tunnels'), os.path.join(W, 'graph.dat'), q, '--out', os.path.join(W, 'sp_early.csv')] + EARLY)
    run([os.path.join(build, 'adj-tunnels'), os.path.join(W, 'adj', 'graph.dat'), q, '--out', os.path.join(W, 'sp_early_adj.csv')] + EARLY)
    ours = {'Ring': read(os.path.join(W, 'sp_early.csv')), 'Plain': read(os.path.join(W, 'sp_early_adj.csv'))}
    check(all(any(r['kind'] == 'CVp' for r in v) for v in ours.values()), 'T9: no CVp rows in the real run')
    filt = {'model': 'region_rel', 'budget': '4x+10', 'sem': 'shortest', 'pf_status': 'ok'}
    # synthetic MillDB rows from ours: random directions (unknown included), some timeouts, some without a cold time
    rng = random.Random(20260101)
    res, raw, n = [], [], 0
    pool = [r for r in ours['Ring'] if r['kind'] in ('CV', 'VC', 'CVp', 'VCp') and r['model'] == 'region_rel' and r['budget'] == '4x+10']
    for r in pool:
        n += 1
        d = 'obj' if r['kind'] in ('VC',) else 'subj'
        if r['kind'] in ('CVp', 'VCp'):
            d = rng.choice(['subj', 'obj', 'obj', 'subj', 'unknown'])
        st = rng.choice(['ok'] * 9 + ['timeout'])
        res.append(dict(qid=r['qid'], kind=r['kind'], src=r['src'], dst=r['dst'], dir=d, mdb_status=st, n=str(n),
                        sim_t_pf=r['t_pf']))
        t = int(float(r['t_build']) * 1e9 * rng.uniform(0.3, 3))
        if rng.random() < 0.9:
            if rng.random() < 0.2:
                raw.append(dict(n=str(n), warmup='1', pf_status='ok', error='x', ns=str(t * 7)))
            raw.append(dict(n=str(n), warmup='0', pf_status='ok', error='', ns=str(t * 5)))
            raw.append(dict(n=str(n), warmup='1', pf_status='ok', error='', ns=str(t)))
            raw.append(dict(n=str(n), warmup='1', pf_status='ok', error='', ns=str(t * 3)))
    for i in range(10):   # rows of MDB that we do not have
        n += 1
        res.append(dict(qid='no-q%d' % i, kind='CV', src='1', dst='2', dir='subj', mdb_status='ok', n=str(n), sim_t_pf='0.1'))
        raw.append(dict(n=str(n), warmup='1', pf_status='ok', error='', ns='1000'))
    check(any(r['dir'] == 'unknown' for r in res) and any(r['mdb_status'] != 'ok' for r in res), 'T9: synthetic rows without variety')

    def res_for(sysname):   # sim_t_pf of MillDB's results.csv = the t_pf of our row tb_key
        tp = {(r['qid'], r['kind'], r['src'], r['dst']): r['t_pf'] for r in ours[sysname] if all(r[c] == v for c, v in filt.items())}
        out = []
        for r in res:
            k = r['kind']
            key = ((r['qid'], k, r['src'], r['dst']) if k in ('CV', 'VC') or r['dir'] == ('subj' if k == 'CVp' else 'obj')
                   else (r['qid'], {'CVp': 'CVr', 'VCp': 'VCr'}.get(k, k), r['dst'], r['src']))
            out.append(dict(r, sim_t_pf=tp.get(key, '0.1')))
        return out
    mres, mraw = os.path.join(W, 'results.csv'), os.path.join(W, 'raw.csv')
    for path, rows in ((mres, res), (mraw, raw)):
        with open(path, 'w', newline='') as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()) if path == mraw else
                               ['qid', 'kind', 'src', 'dst', 'dir', 'mdb_status', 'n', 'sim_t_pf'])
            w.writeheader()
            w.writerows(rows)
    p = tables(W, '--sp', 'sp_early.csv', '--paper', '--adj', 'sp_early_adj.csv', '--mdb', mres, '--mdb-raw', mraw)
    h, rows = table_of(p.stdout, 'system')
    got = {(r[0], r[1]): r[2:] for r in rows}
    t9row = {r[0]: r[1:] for r in table_of(p.stdout, '', 't_b/t_pf c-to-v')[1]}
    for si, sysname in enumerate(('Ring', 'Plain')):
        exp = millb_join(res_for(sysname), raw, ours[sysname], 't_build', filt)
        for ci, g in enumerate(('c-to-v', 'c-to-c')):
            n_, miss, m, pf, b = exp[g]
            check(n_ > 0, f'T9: {sysname} {g}: no matched rows')
            gn, gm, gb, gp, gt = got[(sysname, g)]
            check((int(gn), int(gm)) == (n_, miss), f'T9: {sysname} {g}: n/missing {gn}/{gm}, expected {n_}/{miss}')
            for name, a, b_ in (('t_build', float(gb), b), ('t_pf', float(gp), pf), ('t_MDB', float(gt), m)):
                check(abs(a - b_) <= 1e-5 * abs(b_), f'T9: {sysname} {g}: sum {name} {a} vs {b_}')
            check(t9row[sysname][ci] == f'{b / pf:.2f}' and t9row[sysname][2 + ci] == f'{b / m:.2f}',
                  f'T9: {sysname} {g}: Table 9 {t9row[sysname]}, expected {b / pf:.2f} and {b / m:.2f}')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--build-dir', required=True)
    args = ap.parse_args()
    build = os.path.abspath(args.build_dir)
    t0 = time.time()

    work = tempfile.mkdtemp(prefix='ring-tunnels-pathfinder-')
    try:
        W = os.path.join(work, 'metro')
        os.makedirs(W)
        make_results(build, W)
        sp, cv = read(os.path.join(W, 'sp.csv')), read(os.path.join(W, 'cv.csv'))

        # T1
        p = tables(W, '--min-h-edges', '1')
        check(not any(l.startswith('warning:') for l in p.stderr.splitlines()), f'T1: unexpected warnings:\n{p.stderr}')
        t1_out = p.stdout
        t1(t1_out, sp, cv)

        # T2
        empty = os.path.join(work, 'empty')
        os.makedirs(empty)
        p = tables(empty, expect_rc=1)
        check(p.stderr.strip() != '' and '## P' not in p.stdout, f'T2: stderr {p.stderr!r}, stdout {p.stdout[:80]!r}')

        # T3
        d = os.path.join(work, 'nosp')
        os.makedirs(d)
        shutil.copy(os.path.join(W, 'cv.csv'), os.path.join(d, 'sp.csv'))
        p = tables(d, expect_rc=1)
        check(p.stderr.strip() != '' and '## P' not in p.stdout, f'T3: stderr {p.stderr!r}')

        # T4
        d = os.path.join(work, 'nocv')
        os.makedirs(d)
        shutil.copy(os.path.join(W, 'sp.csv'), d)
        p4 = tables(d, '--min-h-edges', '1')
        check(any(l.startswith('warning:') and 'cv.csv' in l for l in p4.stderr.splitlines()), f'T4: no warning about cv.csv:\n{p4.stderr}')
        s5 = section(p4.stdout, 'P5')
        check('no data: ' in s5 and 'cv.csv missing' in s5, f'T4: P5: {s5}')
        for n in ('P0', 'P1', 'P1b', 'P2', 'P2b', 'P3', 'P4', 'P6'):   # same sp.csv: identical
            check(section(p4.stdout, n) == section(t1_out, n), f'T4: section {n} differs from T1')
        check([m.group(1) for m in re.finditer(r'^## (P\w+)\. ', p4.stdout, re.M)] ==
              ['P0', 'P1', 'P1b', 'P2', 'P2b', 'P3', 'P4', 'P5', 'P6'], 'T4: sections')

        t5(work)
        t6(work)
        t6b(work)
        t7(work, W)
        t8(work)
        t9(build, work)
        check(time.time() - t0 < 60, f'duration {time.time() - t0:.0f} s >= 60 s')
    finally:
        shutil.rmtree(work, ignore_errors=True)

    if failures:
        print(f'{len(failures)} failure(s)')
        return 1
    print(f'OK: T1-T9 ({time.time() - t0:.1f} s)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
