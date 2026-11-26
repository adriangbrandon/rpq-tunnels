#!/usr/bin/env python3
"""Summary of run_automata.sh output: product graph sizes per automaton, over the queries where the
three automata finished, and per-query ratios with respect to the reverse Glushkov automaton."""
import csv, sys, statistics as st
from collections import defaultdict
rows = defaultdict(dict)
for r in csv.DictReader(open(sys.argv[1])):
    rows[r['qid']][r['automaton']] = r
AUT = ['rglushkov', 'glushkov', 'thompson']
status = defaultdict(lambda: defaultdict(int))
for q in rows.values():
    for a in AUT:
        if a in q: status[a][q[a]['status']] += 1
ok = [q for q in rows.values() if all(a in q and q[a]['status'] == 'ok' for a in AUT)]
print(f"# {sys.argv[1]}\n\nqueries: {len(rows)}; all three ok: {len(ok)}")
for a in AUT: print(f"- {a}: " + ", ".join(f"{k} {v}" for k, v in sorted(status[a].items())))
F = ['states', 'trans', 'eps_trans', 'R_nodes', 'R_edges', 'R_eps_edges', 'lookups', 'scanned', 'H_nodes', 'H_edges', 't_product', 't_live']
print("\n## Totals over the common queries\n")
print("| automaton | " + " | ".join(F) + " |"); print("|---" * (len(F) + 1) + "|")
for a in AUT:
    s = {f: sum(float(q[a][f]) for q in ok) for f in F}
    print(f"| {a} | " + " | ".join((f"{s[f]:.1f}" if f.startswith('t_') else f"{s[f]:,.0f}") for f in F) + " |")
print("\n## Per-query ratio to rglushkov (median, mean of ratios, max) over queries with rglushkov > 0\n")
print("| automaton | measure | median | mean | max | queries |"); print("|---|---|---|---|---|---|")
for a in ['glushkov', 'thompson']:
    for f in ['R_nodes', 'R_edges', 'H_nodes', 'H_edges', 't_product']:
        r = [float(q[a][f]) / float(q['rglushkov'][f]) for q in ok if float(q['rglushkov'][f]) > 0]
        if r: print(f"| {a} | {f} | {st.median(r):.2f} | {st.mean(r):.2f} | {max(r):.2f} | {len(r)} |")
print("\n## By shape (common queries): R_nodes total per automaton\n")
by = defaultdict(lambda: defaultdict(float)); cnt = defaultdict(int)
for q in ok:
    sh = q['rglushkov']['shape']; cnt[sh] += 1
    for a in AUT: by[sh][a] += float(q[a]['R_nodes'])
print("| shape | queries | " + " | ".join(AUT) + " |"); print("|---" * (len(AUT) + 2) + "|")
for sh in sorted(by, key=lambda s: -by[s]['rglushkov'])[:25]:
    print(f"| `{sh}` | {cnt[sh]} | " + " | ".join(f"{by[sh][a]:,.0f}" for a in AUT) + " |")
