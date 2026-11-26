#!/usr/bin/env python3
"""Naive reference evaluator for 2RPQs, used as a correctness oracle.

It evaluates queries over the completion of the graph (Section 3.1 of the
paper) under set semantics with a plain product-graph BFS over a Thompson
NFA.  It is deliberately simple and independent from the C++ implementation;
it is only meant for small graphs.

Input formats are the ones used by the C++ tools:
  <graph>.dat     one "s p o" triple of integer ids per line
  <graph>.dat.SO  "id string" per line (nodes)
  <graph>.dat.P   "id string" per line (predicates)
  queries         "<subject> <rpq> <object>" per line, where subject/object
                  are a variable (?x) or a node string, and predicates are
                  written <iri> or <%iri> (inverse).

Output: one "query_number subject_id object_id" line per solution, sorted.
"""

import argparse
import sys
from collections import defaultdict, deque


# ---------------------------------------------------------------- parsing

class Sym:
    def __init__(self, pred, inverse):
        self.pred, self.inverse = pred, inverse


class Concat:
    def __init__(self, left, right):
        self.left, self.right = left, right


class Alt:
    def __init__(self, left, right):
        self.left, self.right = left, right


class Repeat:
    def __init__(self, child, op):
        self.child, self.op = child, op  # op in '*', '+', '?'


class UnknownPredicate(Exception):
    pass


def tokenize(rpq):
    tokens, i = [], 0
    while i < len(rpq):
        c = rpq[i]
        if c == '<':
            j = rpq.index('>', i)
            tokens.append(rpq[i:j + 1])
            i = j + 1
        elif c in '/|*+?()^':
            tokens.append(c)
            i += 1
        elif c.isspace():
            i += 1
        else:
            raise ValueError(f"unexpected character {c!r} in {rpq!r}")
    return tokens


class Parser:
    """alt := concat ('|' concat)* ; concat := post ('/' post)* ;
    post := atom ('*'|'+'|'?')* ; atom := '^'? (iri | '(' alt ')')"""

    def __init__(self, tokens, pred_ids):
        self.tokens, self.pos, self.pred_ids = tokens, 0, pred_ids

    def peek(self):
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def take(self):
        tok = self.tokens[self.pos]
        self.pos += 1
        return tok

    def parse(self):
        node = self.alt()
        if self.peek() is not None:
            raise ValueError(f"trailing tokens: {self.tokens[self.pos:]}")
        return node

    def alt(self):
        node = self.concat()
        while self.peek() == '|':
            self.take()
            node = Alt(node, self.concat())
        return node

    def concat(self):
        node = self.post()
        while self.peek() == '/':
            self.take()
            node = Concat(node, self.post())
        return node

    def post(self):
        node = self.atom()
        while self.peek() in ('*', '+', '?'):
            node = Repeat(node, self.take())
        return node

    def atom(self):
        tok = self.take()
        if tok == '^':
            return invert(self.atom())
        if tok == '(':
            node = self.alt()
            if self.take() != ')':
                raise ValueError("missing ')'")
            return node
        if tok.startswith('<'):
            inverse = tok.startswith('<%')
            name = '<' + tok[2:] if inverse else tok
            if name not in self.pred_ids:
                raise UnknownPredicate(name)
            return Sym(self.pred_ids[name], inverse)
        raise ValueError(f"unexpected token {tok!r}")


def invert(node):
    if isinstance(node, Sym):
        return Sym(node.pred, not node.inverse)
    if isinstance(node, Concat):
        return Concat(invert(node.right), invert(node.left))
    if isinstance(node, Alt):
        return Alt(invert(node.left), invert(node.right))
    return Repeat(invert(node.child), node.op)


# ---------------------------------------------------------------- NFA

class NFA:
    def __init__(self):
        self.eps = defaultdict(list)    # state -> [state]
        self.trans = defaultdict(list)  # state -> [((pred, inverse), state)]
        self.n = 0

    def new_state(self):
        self.n += 1
        return self.n - 1

    def build(self, node):
        """Thompson construction; returns (start, accept)."""
        if isinstance(node, Sym):
            s, t = self.new_state(), self.new_state()
            self.trans[s].append(((node.pred, node.inverse), t))
            return s, t
        if isinstance(node, Concat):
            s1, t1 = self.build(node.left)
            s2, t2 = self.build(node.right)
            self.eps[t1].append(s2)
            return s1, t2
        if isinstance(node, Alt):
            s, t = self.new_state(), self.new_state()
            for child in (node.left, node.right):
                cs, ct = self.build(child)
                self.eps[s].append(cs)
                self.eps[ct].append(t)
            return s, t
        s, t = self.new_state(), self.new_state()
        cs, ct = self.build(node.child)
        self.eps[s].append(cs)
        self.eps[ct].append(t)
        if node.op in ('*', '?'):
            self.eps[s].append(t)
        if node.op in ('*', '+'):
            self.eps[ct].append(cs)
        return s, t

    def closure(self, states):
        stack, seen = list(states), set(states)
        while stack:
            q = stack.pop()
            for r in self.eps[q]:
                if r not in seen:
                    seen.add(r)
                    stack.append(r)
        return seen


# ---------------------------------------------------------------- graph

class Graph:
    def __init__(self, dat_path):
        self.out = defaultdict(lambda: defaultdict(set))  # node -> (pred, inv) -> {node}
        self.nodes = set()
        with open(dat_path) as f:
            for line in f:
                parts = line.split()
                if len(parts) != 3:
                    continue
                s, p, o = map(int, parts)
                self.out[s][(p, False)].add(o)
                self.out[o][(p, True)].add(s)
                self.nodes.update((s, o))

    def reach(self, nfa, start, accept, source):
        """All nodes y such that a path source -> y matches the NFA."""
        init = nfa.closure([start])
        seen = {(source, q) for q in init}
        queue = deque(seen)
        result = set()
        while queue:
            x, q = queue.popleft()
            if q == accept:
                result.add(x)
            for label, r in nfa.trans[q]:
                for y in self.out[x].get(label, ()):
                    for r2 in nfa.closure([r]):
                        if (y, r2) not in seen:
                            seen.add((y, r2))
                            queue.append((y, r2))
        return result


def load_dict(path):
    ids = {}
    with open(path) as f:
        for line in f:
            line = line.rstrip('\r\n')
            if not line:
                continue
            ident, name = line.split(' ', 1)
            ids[name] = int(ident)
    return ids


def split_query(line):
    line = line.rstrip('\r\n')
    first = line.index(' ')
    last = line.rindex(' ')
    return line[:first], line[first + 1:last], line[last + 1:]


def evaluate(graph, nodes, preds, line):
    subj, rpq, obj = split_query(line)
    try:
        ast = Parser(tokenize(rpq), preds).parse()
    except UnknownPredicate:
        return None
    for term in (subj, obj):
        if not term.startswith('?') and term not in nodes:
            return None
    nfa = NFA()
    start, accept = nfa.build(ast)
    if not subj.startswith('?'):
        s = nodes[subj]
        ys = graph.reach(nfa, start, accept, s)
        if not obj.startswith('?'):
            ys &= {nodes[obj]}
        return {(s, y) for y in ys}
    if not obj.startswith('?'):
        o = nodes[obj]
        rnfa = NFA()
        rstart, raccept = rnfa.build(invert(ast))
        return {(x, o) for x in graph.reach(rnfa, rstart, raccept, o)}
    return {(x, y) for x in sorted(graph.nodes)
            for y in graph.reach(nfa, start, accept, x)}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('dat', help='path to the .dat graph file')
    ap.add_argument('queries', help='query file')
    args = ap.parse_args()

    graph = Graph(args.dat)
    nodes = load_dict(args.dat + '.SO')
    preds = load_dict(args.dat + '.P')
    out = []
    with open(args.queries) as f:
        for q, line in enumerate(f, start=1):
            if not line.strip():
                continue
            sols = evaluate(graph, nodes, preds, line)
            for s, o in sorted(sols or ()):
                out.append(f"{q} {s} {o}")
    sys.stdout.write('\n'.join(out) + ('\n' if out else ''))


if __name__ == '__main__':
    main()
