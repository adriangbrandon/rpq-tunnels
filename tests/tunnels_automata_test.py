#!/usr/bin/env python3
"""Tests of the --automata mode of adj-tunnels (src/ring-tunnels.cpp), on tests/data/metro.

  T1  hand-counted sizes of the three automata (tests/tunnels/automata_queries.tsv)
  T2  the same number of answers in the three automata, equal to the oracle's (rpq_oracle.py)
  T3  structure of the output: header, three rows per valid c-to-v / v-to-c query, invariants
  T4  limits (--max-nodes) and the options that are rejected

Usage: tests/tunnels_automata_test.py --build-dir build
"""

import argparse
import csv
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, 'tools'))
import rpq_oracle  # noqa: E402

METRO = os.path.join(HERE, 'data', 'metro')
OWN_QUERIES = os.path.join(HERE, 'tunnels', 'automata_queries.tsv')
HEADER = ('qid,line,kind,shape,m,src,automaton,states,trans,eps_trans,status,R_nodes,R_edges,'
          'R_eps_edges,lookups,scanned,H_nodes,H_edges,answers,t_product,t_live')
AUTOMATA = ['glushkov', 'rglushkov', 'thompson']

PRED = 'http://example.org/pred/'
A, B, C = (f'<{PRED}{x}>' for x in ('l1', 'l2', 'l5'))
NODE1 = '<http://example.org/node/id1>'

# T1: expression -> (m, {automaton: (states, trans, eps_trans)}), the same for c-to-v and v-to-c
T1 = {
    f'{A}': (1, (2, 1, 0), (2, 1, 0), (2, 1, 0)),
    f'{A}/{B}': (2, (3, 2, 0), (3, 2, 0), (3, 2, 0)),
    f'({A})*': (1, (2, 2, 0), (2, 2, 0), (4, 1, 4)),
    f'({A})+': (1, (2, 2, 0), (2, 2, 0), (4, 1, 3)),
    f'({A})?': (1, (2, 1, 0), (2, 1, 0), (4, 1, 3)),
    f'({A}|{B})/{C}': (2, (3, 3, 0), (3, 3, 0), (3, 3, 0)),
    f'({A}/{B})|{C}': (3, (4, 3, 0), (4, 3, 0), (7, 3, 4)),
    f'^{A}/{B}': (2, (3, 2, 0), (3, 2, 0), (3, 2, 0)),
}

failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)
        print('FAIL', msg)


def run(build, binary, dat, queries, extra=(), expect_rc=0):
    out = os.path.join(os.path.dirname(dat), f'{binary}-{abs(hash((queries, tuple(extra))))}.csv')
    proc = subprocess.run([os.path.join(build, binary), dat, queries, '--out', out] + list(extra),
                          stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    check(proc.returncode == expect_rc,
          f'{binary} {" ".join(extra)}: exit code {proc.returncode}, expected {expect_rc}\n{proc.stderr[-300:]}')
    return proc, out


def read_csv(path):
    with open(path, newline='') as f:
        text = f.read()
    return text.splitlines()[0], list(csv.DictReader(text.splitlines()))


def file_lines(path):
    with open(path, newline='') as f:
        return f.read().split('\n')


def by_query(rows):
    q = {}
    for r in rows:
        q.setdefault(int(r['qid']), []).append(r)
    return q


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--build-dir', required=True)
    args = ap.parse_args()
    build = os.path.abspath(args.build_dir)

    work = tempfile.mkdtemp(prefix='ring-tunnels-automata-')
    try:
        dat = os.path.join(work, 'graph.dat')
        for name in ('graph.dat', 'graph.dat.SO', 'graph.dat.P'):
            shutil.copy(os.path.join(METRO, name), work)
        metro_q = os.path.join(METRO, 'queries.tsv')

        graph = rpq_oracle.Graph(dat)
        nodes = rpq_oracle.load_dict(dat + '.SO')
        preds = rpq_oracle.load_dict(dat + '.P')

        # ------------------------------------------------------------ own queries
        _, out = run(build, 'adj-tunnels', dat, OWN_QUERIES, ['--automata'])
        header, rows = read_csv(out)
        own_lines = file_lines(OWN_QUERIES)

        # T3: structure
        check(header == HEADER, f'T3 header: {header}')
        groups = by_query(rows)
        for qid, g in groups.items():
            check([r['automaton'] for r in g] == AUTOMATA, f'T3 q{qid}: automata {[r["automaton"] for r in g]}')
            check(len({r['line'] for r in g}) == 1, f'T3 q{qid}: different lines')
        valid_lines = set()
        for r in rows:
            valid_lines.add(int(r['line']))
        for ln, text in enumerate(own_lines, start=1):
            if not text.strip():
                check(ln not in valid_lines, f'T3 line {ln} (blank) has rows')
                continue
            subj, _, obj = text.split(' ')[0], None, text.split(' ')[-1]
            cv = not subj.startswith('?') and obj.startswith('?')
            vc = subj.startswith('?') and not obj.startswith('?')
            known = 'unknown' not in text and 'id99' not in text
            check((ln in valid_lines) == ((cv or vc) and known),
                  f'T3 line {ln}: rows={ln in valid_lines}, expected {(cv or vc) and known}: {text}')
        # the file has at least one of each ignored kind
        check(any(not t.strip() for t in own_lines[:-1]), 'T3: no blank line in the queries file')
        check(any(t.startswith('?x') and t.endswith('?y') for t in own_lines), 'T3: no VV in the queries file')

        # T1: hand counts
        seen = set()
        for r in rows:
            text = own_lines[int(r['line']) - 1]
            expr = text[text.index(' ') + 1:text.rindex(' ')]
            if expr not in T1:
                continue
            m, *expected = T1[expr]
            seen.add((expr, r['kind']))
            k = AUTOMATA.index(r['automaton'])
            got = (int(r['states']), int(r['trans']), int(r['eps_trans']))
            check(int(r['m']) == m, f'T1 {expr} {r["kind"]}: m={r["m"]}, expected {m}')
            check(got == expected[k], f'T1 {expr} {r["kind"]} {r["automaton"]}: {got}, expected {expected[k]}')
        check(len(seen) == 2 * len(T1), f'T1: only {len(seen)} of {2 * len(T1)} (expression, kind) pairs found')

        # T2 and the invariants of T3, on a set of queries
        def t2_t3_rows(rows, lines, label):
            n_t2 = 0
            for qid, g in by_query(rows).items():
                text = lines[int(g[0]['line']) - 1]
                for r in g:
                    m = int(r['m'])
                    check(int(r['states']) == m + 1 or r['automaton'] == 'thompson',
                          f'T3 {label} q{qid} {r["automaton"]}: states {r["states"]} != m+1 = {m + 1}')
                    if r['automaton'] != 'thompson':
                        check(int(r['R_eps_edges']) == 0, f'T3 {label} q{qid} {r["automaton"]}: eps edges')
                    check(int(r['H_nodes']) <= int(r['R_nodes']), f'T3 {label} q{qid} {r["automaton"]}: H_nodes > R_nodes')
                    check(int(r['H_edges']) <= int(r['R_edges']), f'T3 {label} q{qid} {r["automaton"]}: H_edges > R_edges')
                if not all(r['status'] == 'ok' for r in g):
                    continue
                answers = {r['answers'] for r in g}
                check(len(answers) == 1, f'T2 {label} q{qid}: answers differ {[r["answers"] for r in g]}')
                pairs = rpq_oracle.evaluate(graph, nodes, preds, text)
                check(pairs is not None, f'T2 {label} q{qid}: the oracle rejects {text}')
                if pairs is None:
                    continue
                want = len({y for _, y in pairs}) if g[0]['kind'] == 'CV' else len({x for x, _ in pairs})
                check(all(int(r['answers']) == want for r in g),
                      f'T2 {label} q{qid} ({text}): answers {[r["answers"] for r in g]}, oracle {want}')
                n_t2 += 1
            return n_t2

        n = t2_t3_rows(rows, own_lines, 'own')
        check(n >= 40, f'T2 own: only {n} queries compared')

        # ------------------------------------------------------------ metro
        _, out = run(build, 'adj-tunnels', dat, metro_q, ['--automata'])
        header, mrows = read_csv(out)
        metro_lines = file_lines(metro_q)
        check(header == HEADER, f'T3 metro header: {header}')
        _, nout = run(build, 'adj-tunnels', dat, metro_q, ['--reps', '1'])
        _, nrows = read_csv(nout)
        expected_q = {int(r['qid']) for r in nrows
                      if r['kind'] in ('CV', 'VC') and r['status'] not in ('cc_in_file', 'unknown_iri')}
        check(len(mrows) == 3 * len(expected_q),
              f'T3 metro: {len(mrows)} rows, expected 3 x {len(expected_q)}')
        check({int(r['qid']) for r in mrows} == expected_q, 'T3 metro: different set of queries from the normal mode')
        check(len(expected_q) > 0, 'T3 metro: no c-to-v / v-to-c queries')
        for qid, g in by_query(mrows).items():
            check([r['automaton'] for r in g] == AUTOMATA, f'T3 metro q{qid}: automata order')
        n = t2_t3_rows(mrows, metro_lines, 'metro')
        check(n >= 50, f'T2 metro: only {n} queries compared')

        # ------------------------------------------------------------ T4
        _, lout = run(build, 'adj-tunnels', dat, metro_q, ['--automata', '--max-nodes', '1'])
        _, lrows = read_csv(lout)
        full = {(r['qid'], r['automaton']): int(r['R_nodes']) for r in mrows if r['status'] == 'ok'}
        check(len(lrows) == len(mrows), f'T4: {len(lrows)} rows with --max-nodes 1, expected {len(mrows)}')
        n_big = 0
        for r in lrows:
            if full.get((r['qid'], r['automaton']), 0) > 1:
                n_big += 1
                check(r['status'] == 'too_large', f'T4 q{r["qid"]} {r["automaton"]}: status {r["status"]}')
                check(int(r['H_nodes']) == 0 and int(r['H_edges']) == 0 and int(r['answers']) == 0,
                      f'T4 q{r["qid"]} {r["automaton"]}: H or answers not 0')
        check(n_big > 0, 'T4: no query reaches more than 1 node')
        check(not any(r['status'] == 'ok' and int(r['R_nodes']) > 1 for r in lrows), 'T4: ok row with > 1 node')

        for binary in ('ring-tunnels', 'ring-tunnels-split'):
            proc, _ = run(build, binary, dat, metro_q, ['--automata'], expect_rc=1)
            check('adj-tunnels' in proc.stderr, f'T4 {binary}: stderr does not mention adj-tunnels')
        run(build, 'adj-tunnels', dat, metro_q, ['--automata', '--shortest'], expect_rc=1)
    finally:
        shutil.rmtree(work, ignore_errors=True)

    if failures:
        print(f'{len(failures)} failure(s)')
        return 1
    print('ok: T1-T4')
    return 0


if __name__ == '__main__':
    sys.exit(main())
