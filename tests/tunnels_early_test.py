#!/usr/bin/env python3
"""Tests of --early-stop of the tunnels binaries (src/ring-tunnels.cpp, ALL SHORTEST WALKS).

On tests/data/metro and tests/data/rand-small, with adj-tunnels and ring-tunnels, the same run with and
without --early-stop (--shortest --pairs 3 --pair-reverse --vv-derive --vv-samples 2, budgets 0,inf and
4x+10, --uniform-cost), rows paired by (qid, kind, src, dst, model, budget):

  E3a  the same row keys (the pairs do not change)
  E3b  CV, VC, VVs rows and those with status != ok: equal in every column that does not start with t_
  E3c  pair rows with status ok: same H_*, Hplain_bits, answers, pf_answers, pf_paths, pf_dfa_states,
       pf_status; pf_check = verify = 1 in both; R_nodes, R_edges and the pf_* work/space columns <=
  E3d  not vacuous: some pair has a strictly smaller R_nodes and some a strictly smaller pf_states
  E3e  --early-stop without --shortest, and with --automata --shortest: exit code 1
  E3f  pathfinder_tables.py --sp sp_early.csv: exit code 0 and P0 without pf_check / verify failures;
       --sp to a file that does not exist: exit code 1
  E4   (metro, adj-tunnels) the rule of the stop by hand: for each pair row the walk length d of the
       shortest accepted walks and their number are computed in Python (Thompson NFA of
       tests/tools/rpq_oracle.py, subset product by layers, no pruning) and pf_paths must be that number;
       pf_states with the stop is between the number of graph nodes within d steps and the number of
       (node, set of NFA states) pairs within d steps. Some pair must have d >= 2.

Usage: tests/tunnels_early_test.py --build-dir build
"""

import argparse
import csv
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(HERE, 'tools'))
import rpq_oracle  # noqa: E402

TABLES = os.path.join(ROOT, 'tunnels', 'pathfinder_tables.py')
BINARIES = {'adj-tunnels': None, 'ring-tunnels': 'build-index-basic'}
DATASETS = ['metro', 'rand-small']
BUD = ['--budgets', '0,inf', '--rel', '4', '--rel-slack', '10', '--uniform-cost']
OPTS = BUD + ['--shortest', '--pairs', '3', '--pair-reverse', '--vv-derive', '--vv-samples', '2', '--reps', '1']
PAIR_KINDS = ('CVp', 'CVr', 'VCp', 'VCr')
EQUAL = ['H_nodes', 'H_edges', 'H_bits', 'Hplain_bits', 'answers', 'pf_answers', 'pf_paths', 'pf_dfa_states', 'pf_status']
LESS = ['R_nodes', 'R_edges', 'pf_states', 'pf_transitions', 'pf_edges', 'pf_idx_searches', 'pf_bytes_structs', 'pf_bytes']

failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)
        print('FAIL', msg)


def run(cmd, expect_rc=0, **kw):
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, **kw)
    check(proc.returncode == expect_rc,
          f'{" ".join(os.path.basename(c) for c in cmd[:2])}: exit code {proc.returncode}, expected {expect_rc}\n{proc.stderr[-400:]}')
    return proc


def read(path):
    with open(path, newline='') as f:
        return list(csv.DictReader(f))


def key(r):
    return (r['qid'], r['kind'], r['src'], r['dst'], r['model'], r['budget'])


def group(rows):
    g = defaultdict(list)
    for r in rows:
        g[key(r)].append(r)
    return g


def stable(r):
    return {k: v for k, v in r.items() if not k.startswith('t_')}


# ---- E4: independent computation with the Thompson NFA of the oracle
def shortest_walks(graph, ast, src, dst, limit=40):
    """(d, number of shortest accepted walks, nodes within d steps, (node, state set) pairs within d steps)
    from src to dst, or None if there is no walk of at most `limit` steps."""
    nfa = rpq_oracle.NFA()
    start, accept = nfa.build(ast)
    cur = {(src, frozenset(nfa.closure([start]))): 1}
    seen = set(cur)
    for d in range(limit + 1):
        paths = sum(c for (v, s), c in cur.items() if v == dst and accept in s)
        if paths:
            return d, paths, len({v for v, _ in seen}), len(seen)
        nxt = defaultdict(int)
        for (v, s), c in cur.items():
            for lab, ws in graph.out[v].items():
                t = {r for q in s for l2, r in nfa.trans[q] if l2 == lab}
                if not t:
                    continue
                s2 = frozenset(nfa.closure(t))
                for w in ws:
                    nxt[(w, s2)] += c
        cur = nxt
        seen |= set(cur)
    return None


def e4(data, rows_early, rows_full):
    graph = rpq_oracle.Graph(os.path.join(data, 'graph.dat'))
    preds = rpq_oracle.load_dict(os.path.join(data, 'graph.dat.P'))
    with open(os.path.join(data, 'queries.tsv')) as f:
        lines = f.read().split('\n')
    full = group(rows_full)
    deep = checked = 0
    done = set()
    for r in rows_early:
        if r['kind'] not in PAIR_KINDS or r['status'] != 'ok' or r['pf_status'] != 'ok' or key(r)[:4] in done:
            continue
        done.add(key(r)[:4])
        _, rpq, _ = rpq_oracle.split_query(lines[int(r['line']) - 1])
        ast = rpq_oracle.Parser(rpq_oracle.tokenize(rpq), preds).parse()
        if r['kind'] in ('VCp', 'CVr'):   # walked from the object: the inverse path
            ast = rpq_oracle.invert(ast)
        got = shortest_walks(graph, ast, int(r['src']), int(r['dst']))
        label = f'E4 q{r["qid"]} {r["kind"]} {r["src"]}->{r["dst"]}'
        check(got is not None, f'{label}: no walk in the oracle')
        if got is None:
            continue
        d, paths, nodes, pairs = got
        checked += 1
        deep += d >= 2
        check(float(r['pf_paths']) == paths, f'{label}: pf_paths {r["pf_paths"]}, walks of length {d}: {paths}')
        states = int(r['pf_states'])
        check(nodes <= states <= pairs, f'{label}: pf_states {states} outside [{nodes}, {pairs}] (d = {d})')
        # the run without the stop explores at least as much
        check(int(full[key(r)][0]['pf_states']) >= states, f'{label}: pf_states grows with the stop')
    check(deep > 0, 'E4: no pair with d >= 2')
    print(f'E4 metro: {checked} pair rows checked, {deep} with d >= 2')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--build-dir', required=True)
    args = ap.parse_args()
    build = os.path.abspath(args.build_dir)
    work = tempfile.mkdtemp(prefix='ring-tunnels-early-')
    reduced_R, reduced_pf, pairs_seen = set(), set(), set()
    example = None
    try:
        for ds in DATASETS:
            data = os.path.join(HERE, 'data', ds)
            for binary, builder in BINARIES.items():
                wd = os.path.join(work, ds, binary)
                os.makedirs(wd)
                for name in ('graph.dat', 'graph.dat.SO', 'graph.dat.P'):
                    shutil.copy(os.path.join(data, name), wd)
                dat = os.path.join(wd, 'graph.dat')
                if builder:
                    subprocess.run([os.path.join(build, builder), dat], check=True,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                q = os.path.join(data, 'queries.tsv')
                outs = {}
                for tag, extra in (('full', []), ('early', ['--early-stop'])):
                    outs[tag] = os.path.join(wd, f'sp_{tag}.csv')
                    run([os.path.join(build, binary), dat, q, '--out', outs[tag]] + OPTS + extra)
                full, early = group(read(outs['full'])), group(read(outs['early']))
                name = f'{ds}/{binary}'
                check(sorted(full) == sorted(early) and all(len(full[k]) == len(early[k]) for k in full),
                      f'E3a {name}: row keys differ')
                npair = 0
                for k in full:
                    for a, b in zip(full[k], early.get(k, [])):
                        if a['kind'] not in PAIR_KINDS or a['status'] != 'ok' or b['status'] != 'ok':
                            check(stable(a) == stable(b), f'E3b {name} {k}: rows differ')
                            continue
                        npair += 1
                        pk = k[:4]
                        pairs_seen.add((ds, binary) + pk)
                        for c in EQUAL:
                            check(a[c] == b[c], f'E3c {name} {k}: {c} {a[c]} -> {b[c]}')
                        check(a['pf_check'] == '1' and b['pf_check'] == '1', f'E3c {name} {k}: pf_check')
                        check(a['verify'] == '1' and b['verify'] == '1', f'E3c {name} {k}: verify')
                        for c in LESS:
                            check(int(b[c]) <= int(a[c]), f'E3c {name} {k}: {c} grows {a[c]} -> {b[c]}')
                        if int(b['R_nodes']) < int(a['R_nodes']):
                            reduced_R.add((ds, binary) + pk)
                            if example is None and int(b['pf_states']) < int(a['pf_states']):
                                example = (name, k[:4], a['R_nodes'], b['R_nodes'], a['pf_states'], b['pf_states'])
                        if int(b['pf_states']) < int(a['pf_states']):
                            reduced_pf.add((ds, binary) + pk)
                check(npair > 0, f'E3c {name}: no pair rows with status ok')
                if ds == 'metro' and binary == 'adj-tunnels':
                    e4(data, read(outs['early']), read(outs['full']))
                    # E3f: the tables on the run with the stop
                    td = os.path.join(wd, 'tables')
                    os.makedirs(td)
                    shutil.copy(outs['early'], os.path.join(td, 'sp_early.csv'))
                    p = run([sys.executable, TABLES, td, '--sp', 'sp_early.csv', '--min-h-edges', '1'])
                    m = re.search(r'^## P0\. .*?(?=^## P1)', p.stdout, re.S | re.M)
                    rows = [l for l in (m.group(0).splitlines() if m else []) if l.startswith('|') and not l.startswith('|---')][1:]
                    check(len(rows) >= 4, 'E3f: P0 without rows')
                    for l in rows:
                        cells = [c.strip() for c in l.strip('|').split('|')]
                        check(cells[4] == '0' and cells[5] == '0', f'E3f: P0 row with failures: {l}')
                    run([sys.executable, TABLES, td, '--sp', 'missing.csv'], expect_rc=1)
                    run([sys.executable, TABLES, td, '--sp', os.path.join(td, 'sp_early.csv')])

        # E3d: counted over distinct pairs (a pair has one row per configuration)
        check(reduced_R, 'E3d: no pair with a smaller R_nodes')
        check(reduced_pf, 'E3d: no pair with a smaller pf_states')
        print(f'E3d: {len(pairs_seen)} pairs, {len(reduced_R)} with a smaller R_nodes, {len(reduced_pf)} with a smaller pf_states; '
              f'example (run, pair, R_nodes before/after, pf_states before/after): {example}')

        # E3e: the options
        wd = os.path.join(work, 'metro', 'adj-tunnels')
        dat = os.path.join(wd, 'graph.dat')
        q = os.path.join(HERE, 'data', 'metro', 'queries.tsv')
        run([os.path.join(build, 'adj-tunnels'), dat, q, '--out', os.path.join(wd, 'x.csv'), '--early-stop'], expect_rc=1)
        run([os.path.join(build, 'adj-tunnels'), dat, q, '--out', os.path.join(wd, 'x.csv'), '--automata', '--shortest',
             '--early-stop'], expect_rc=1)
        run([os.path.join(build, 'ring-tunnels'), os.path.join(work, 'metro', 'ring-tunnels', 'graph.dat'), q,
             '--out', os.path.join(wd, 'x.csv'), '--early-stop'], expect_rc=1)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    print(f'{len(failures)} failures' if failures else 'ok')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
