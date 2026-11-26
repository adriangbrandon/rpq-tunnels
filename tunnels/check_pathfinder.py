"""Independent check of ring-tunnels --shortest (the PathFinder simulation).

For every ok row it recomputes, in Python and from scratch, what PathFinder's ALL SHORTEST WALKS
keeps: Thompson NFA of the path (tests/tools/rpq_oracle.py), subset construction, Moore
minimization of the partial DFA, then a BFS over (node, DFA state) from the row's source.
Compared columns: pf_dfa_states, pf_states, pf_transitions, pf_edges, pf_answers, pf_paths.

usage: python3 tunnels/check_pathfinder.py <repo> <dir with graph.dat, .SO, .P> <queries.tsv> <--shortest csv>
Only for small graphs (e.g. tests/data); it relies on tests/tools/rpq_oracle.py.
"""
import csv
import sys
from collections import deque

repo, ddir, qfile, rowsfile = sys.argv[1:5]
sys.path.insert(0, repo + "/tests/tools")
import rpq_oracle as O  # noqa: E402

graph = O.Graph(ddir + "/graph.dat")
nodes = O.load_dict(ddir + "/graph.dat.SO")
preds = O.load_dict(ddir + "/graph.dat.P")
lines = open(qfile).read().split("\n")


def min_dfa(ast):
    nfa = O.NFA()
    start, accept = nfa.build(ast)
    init = frozenset(nfa.closure([start]))
    labels = sorted({lab for q in range(nfa.n) for lab, _ in nfa.trans[q]})
    subs, ids, tab = [init], {init: 0}, []
    i = 0
    while i < len(subs):
        row = []
        for lab in labels:
            nxt = set()
            for q in subs[i]:
                for l2, r in nfa.trans[q]:
                    if l2 == lab:
                        nxt.add(r)
            if not nxt:
                row.append(-1)
                continue
            fs = frozenset(nfa.closure(nxt))
            if fs not in ids:
                ids[fs] = len(subs)
                subs.append(fs)
            row.append(ids[fs])
        tab.append(row)
        i += 1
    part = [1 if accept in s else 0 for s in subs]
    count = 0
    while True:
        sig = {}
        new = []
        for k in range(len(subs)):
            key = (part[k],) + tuple(-1 if t < 0 else part[t] for t in tab[k])
            new.append(sig.setdefault(key, len(sig)))
        part = new
        if len(sig) == count:
            break
        count = len(sig)
    trans = [None] * count
    fin = [False] * count
    for k in range(len(subs)):
        c = part[k]
        if trans[c] is None:
            fin[c] = accept in subs[k]
            trans[c] = [(lab, part[t]) for lab, t in zip(labels, tab[k]) if t >= 0]
    return part[0], fin, trans


def pathfinder(dfa, src, dst):
    start, fin, trans = dfa
    if dst is not None and dst == src and fin[start]:
        return dict(states=1, trans=0, edges=0, answers=1, paths=1.0)
    idx = {(src, start): 0}
    order = [(src, start)]
    dist, cnt = [0], [1.0]
    ntrans = edges = 0
    i = 0
    while i < len(order):
        v, c = order[i]
        for lab, to in trans[c]:
            for w in sorted(graph.out[v].get(lab, ())):
                edges += 1
                k = (w, to)
                j = idx.get(k)
                if j is None:
                    idx[k] = len(order)
                    order.append(k)
                    dist.append(dist[i] + 1)
                    cnt.append(cnt[i])
                    ntrans += 1
                elif dist[j] == dist[i] + 1:
                    cnt[j] += cnt[i]
                    ntrans += 1
        i += 1
    best = {}
    for k, (v, c) in enumerate(order):
        if fin[c]:
            best[v] = min(best.get(v, dist[k]), dist[k])
    paths = sum(cnt[k] for k, (v, c) in enumerate(order)
                if fin[c] and best[v] == dist[k] and (dst is None or v == dst))
    answers = len(best) if dst is None else int(dst in best)
    return dict(states=len(order), trans=ntrans, edges=edges, answers=answers, paths=paths)


cache, bad, n = {}, 0, 0
for r in csv.DictReader(open(rowsfile)):
    if r["status"] != "ok" or r["sem"] != "shortest":
        continue
    kind = r["kind"]
    line = lines[int(r["line"]) - 1]
    subj, rpq, obj = O.split_query(line)
    ast = O.Parser(O.tokenize(rpq), preds).parse()
    # direction of the walk from the row's src: the subject side, or the inverse path from the object
    from_object = kind in ("VC", "VCp", "CVr")
    key = (rpq, from_object)
    if key not in cache:
        cache[key] = min_dfa(O.invert(ast) if from_object else ast)
    dfa = cache[key]
    src = int(r["src"])
    dst = int(r["dst"]) if r["dst"] not in ("", "-1") else None
    got = pathfinder(dfa, src, dst)
    exp = dict(states=int(r["pf_states"]), trans=int(r["pf_transitions"]), edges=int(r["pf_edges"]),
               answers=int(r["pf_answers"]), paths=float(r["pf_paths"]))
    n += 1
    ok = len(dfa[1]) == int(r["pf_dfa_states"]) and all(
        abs(got[k] - exp[k]) <= 1e-5 * max(1.0, abs(got[k])) for k in exp)
    if not ok:
        bad += 1
        if bad <= 5:
            print("MISMATCH", r["qid"], kind, src, dst, "dfa", len(dfa[1]), r["pf_dfa_states"], got, exp)
print(f"{rowsfile}: {n} rows checked, {bad} mismatches")
sys.exit(1 if bad else 0)
