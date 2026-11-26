#!/usr/bin/env python3
"""Generates the deterministic test datasets and query sets used by the
regression suite (tests/regression.py).

Each dataset is written to <out>/<name>/graph.dat (+ .SO and .P dictionaries)
together with <out>/<name>/queries.tsv.  The query shapes are the distinct
patterns found in the benchmark logs under data/ (predicates and nodes are
abstracted away and re-instantiated with the dataset's own symbols), so the
suite exercises the same planner branches as the experiments of the paper.

Query files use CRLF line endings, as the C++ query drivers expect.
"""

import argparse
import itertools
import os
import random
import re

NODE_IRI = '<http://example.org/node/{}>'
PRED_IRI = '<http://example.org/pred/{}>'

IRI_RE = re.compile(r'<%?[^>]*>')


# ---------------------------------------------------------------- graphs

def metro_graph():
    """The running example of the paper (Fig. 1)."""
    names = ['SantaAna', 'UniDeChile', 'LosHeroes', 'BellasArtes', 'Baquedano']
    preds = ['l1', 'l2', 'l5', 'bus']
    sa, uch, lh, ba, baq = range(1, 6)
    l1, l2, l5, bus = range(1, 5)
    both = [(uch, l1, lh), (uch, l1, baq), (sa, l2, lh), (sa, l5, ba), (ba, l5, baq)]
    triples = set()
    for s, p, o in both:
        triples.update({(s, p, o), (o, p, s)})
    triples.update({(sa, bus, uch), (uch, bus, ba), (ba, bus, sa)})
    return names, preds, sorted(triples)


def random_graph(rng, n_nodes, n_preds, n_edges, skew):
    """Random graph with Zipf-like node and predicate popularity."""
    node_cw = list(itertools.accumulate(1.0 / (i + 1) ** skew for i in range(n_nodes)))
    pred_cw = list(itertools.accumulate(1.0 / (i + 1) ** skew for i in range(n_preds)))
    node_ids, pred_ids = range(1, n_nodes + 1), range(1, n_preds + 1)
    # Popular ids are scattered over the id space instead of being 1, 2, ...
    perm = list(node_ids)
    rng.shuffle(perm)
    triples = set()
    # A spanning chain guarantees that every id appears in some triple.
    order = list(range(1, n_nodes + 1))
    rng.shuffle(order)
    for a, b in zip(order, order[1:]):
        triples.add((a, rng.randint(1, n_preds), b))
    while len(triples) < n_edges:
        k = n_edges - len(triples)
        ss = rng.choices(node_ids, cum_weights=node_cw, k=k)
        oo = rng.choices(node_ids, cum_weights=node_cw, k=k)
        pp = rng.choices(pred_ids, cum_weights=pred_cw, k=k)
        triples.update((perm[s - 1], p, perm[o - 1]) for s, p, o in zip(ss, pp, oo))
    names = [f'n{i}' for i in range(1, n_nodes + 1)]
    preds = [f'p{i}' for i in range(1, n_preds + 1)]
    return names, preds, sorted(triples)


# ---------------------------------------------------------------- queries

def log_patterns(log_files):
    """Distinct query shapes: 'P' marks a predicate, '%P' an inverse one."""
    shapes = set()
    for path in log_files:
        with open(path, newline='') as f:
            for line in f:
                line = line.rstrip('\r\n')
                if not line:
                    continue
                first, last = line.index(' '), line.rindex(' ')
                subj, rpq, obj = line[:first], line[first + 1:last], line[last + 1:]
                rpq = IRI_RE.sub(lambda m: '%P' if m.group().startswith('<%') else 'P', rpq)
                shapes.add(('v' if subj.startswith('?') else 'c', rpq,
                            'v' if obj.startswith('?') else 'c'))
    return sorted(shapes)


def instantiate(rng, shape, preds, adjacency, n_nodes):
    subj, rpq, obj = shape
    used = []

    def pred(match):
        p = rng.randint(1, len(preds))
        inverse = match.group() == '%P'
        used.append((p, inverse))
        return ('<%' + PRED_IRI.format(preds[p - 1])[1:]) if inverse else PRED_IRI.format(preds[p - 1])

    rpq = re.sub(r'%?P', pred, rpq)

    def node(p_inv, outgoing):
        # Prefer nodes incident to the relevant predicate so that most
        # queries have answers; fall back to any node.
        p, inverse = p_inv
        candidates = adjacency[(p, outgoing != inverse)]
        if candidates and rng.random() < 0.8:
            return rng.choice(candidates)
        return rng.randint(1, n_nodes)

    s = '?x' if subj == 'v' else NODE_IRI.format(f'id{node(used[0], True)}')
    o = '?y' if obj == 'v' else NODE_IRI.format(f'id{node(used[-1], False)}')
    return f'{s} {rpq} {o}'


def write_dataset(out_dir, name, names, preds, triples, queries):
    path = os.path.join(out_dir, name)
    os.makedirs(path, exist_ok=True)
    with open(os.path.join(path, 'graph.dat'), 'w') as f:
        f.writelines(f'{s} {p} {o}\n' for s, p, o in triples)
    with open(os.path.join(path, 'graph.dat.SO'), 'w') as f:
        f.writelines(f'{i} {NODE_IRI.format(f"id{i}")}\n' for i in range(1, len(names) + 1))
    with open(os.path.join(path, 'graph.dat.P'), 'w') as f:
        f.writelines(f'{i} {PRED_IRI.format(p)}\n' for i, p in enumerate(preds, start=1))
    with open(os.path.join(path, 'queries.tsv'), 'w', newline='') as f:
        f.writelines(q + '\r\n' for q in queries)


def make_queries(rng, shapes, preds, triples, n_nodes, per_shape, extra):
    sets = {(p, d): set() for p in range(1, len(preds) + 1) for d in (True, False)}
    for s, p, o in triples:
        sets[(p, True)].add(s)
        sets[(p, False)].add(o)
    adjacency = {key: sorted(v) for key, v in sets.items()}
    queries = []
    for shape in shapes + extra:
        for _ in range(per_shape):
            queries.append(instantiate(rng, shape, preds, adjacency, n_nodes))
    return queries


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--out', required=True, help='output directory')
    ap.add_argument('--logs', nargs='+', required=True, help='benchmark query logs')
    ap.add_argument('--bench', action='store_true',
                    help='also generate the (larger) benchmark dataset')
    args = ap.parse_args()

    shapes = log_patterns(args.logs)
    # Shapes absent from the logs but supported by the drivers.
    extra = [('c', 'P/(P)*', 'c'), ('c', '(P)+', 'c'), ('v', '(%P)*', 'v'),
             ('c', '(P|%P)*', 'v'), ('v', '(P/P)+', 'c')]
    # Short expressions with every combination of constant/variable ends: they
    # go through the special cases of the drivers (single predicate, paths, ...).
    short = ['P', '%P', 'P/P', 'P/%P', '%P/P', 'P|P', 'P|%P', '(P)*', '(%P)+', '(P)?', 'P/(P)*']
    extra += [(s, rpq, o) for rpq in short for s in 'cv' for o in 'cv']
    # Longer concatenations (the drivers have special cases by number of predicates).
    longer = ['P/P/P', 'P/P/P/P', 'P/%P/P/P/P', '(P)*/P/P/P']
    extra += [(s, rpq, o) for rpq in longer for s in 'cv' for o in 'cv']
    # Expressions whose NFA states need 32 and 64-bit words.
    def chain(n, star_every=0):
        return '/'.join('(P)*' if star_every and i % star_every == 1 else 'P' for i in range(n))
    very_long = [chain(16), chain(20, 3), chain(40, 4)]
    extra += [(s, rpq, o) for rpq in very_long for s in 'cv' for o in 'cv']

    names, preds, triples = metro_graph()
    metro_queries = [
        '?x (<http://example.org/pred/l1>|<http://example.org/pred/l2>|<http://example.org/pred/l5>)+ ?y',
        '?x (<http://example.org/pred/l1>)+|(<http://example.org/pred/l2>)+|(<http://example.org/pred/l5>)+ ?y',
        '<http://example.org/node/id3> <http://example.org/pred/l2>/(<http://example.org/pred/bus>)* ?y',
        '<http://example.org/node/id5> (<http://example.org/pred/l5>)+/<http://example.org/pred/bus> ?y',
    ]
    rng = random.Random(1)
    metro_queries += make_queries(rng, shapes, preds, triples, len(names), 1, extra)
    write_dataset(args.out, 'metro', names, preds, triples, metro_queries)

    configs = [
        # name, seed, nodes, preds, edges, skew, queries per shape
        ('rand-small', 2, 60, 4, 220, 0.6, 3),
        ('rand-medium', 3, 400, 8, 2500, 0.9, 3),
        ('rand-dense', 4, 120, 3, 1800, 0.3, 2),
    ]
    if args.bench:
        configs.append(('bench', 5, 50000, 40, 120000, 0.7, 2))
    for name, seed, n_nodes, n_preds, n_edges, skew, per_shape in configs:
        rng = random.Random(seed)
        names, preds, triples = random_graph(rng, n_nodes, n_preds, n_edges, skew)
        queries = make_queries(rng, shapes, preds, triples, n_nodes, per_shape, extra)
        write_dataset(args.out, name, names, preds, triples, queries)


if __name__ == '__main__':
    main()
