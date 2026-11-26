#!/usr/bin/env python3
"""Helpers of validation/run.sh.

  summarize.py digest <solutions-file>
      One line "query count digest" per query of a solutions file (or FIFO)
      written by the query drivers ("query subject object", sorted per query).
      The digest is a SHA-256 of the solutions in that order (duplicates
      included).

  summarize.py summary <results-dir>
      Reads the results of run.sh, writes <results-dir>/summary.json and
      prints a Markdown summary: index construction, and for every variant
      and query file the times of both versions, the comparison on the
      queries with the same results, the queries whose results differ, and
      the times by query shape.
"""

import glob
import hashlib
import json
import math
import os
import re
import statistics
import sys
from collections import defaultdict

DEFAULT_TIMEOUT_S = 600  # TIME_OUT of the query drivers, unless build.sh --timeout
LONG_NS = 10**6           # queries under 1 ms are dominated by per-query setup
IRI_RE = re.compile(r'<%?[^>]*>')


# ------------------------------------------------------------------ digests

def digest(path):
    """Streams the solutions (a file or a FIFO; '-' is stdin) and prints
    "query count digest" per query. The drivers write the solutions of each
    query sorted, so they are hashed as they come: memory does not grow with
    the number of results (queries can have hundreds of millions)."""
    stream = sys.stdin if path == '-' else (open(path) if os.path.exists(path) else None)
    if stream is None:
        return
    current, count, h = None, 0, None
    for line in stream:
        q, rest = line.split(' ', 1)
        if q != current:
            if current is not None:
                print(f'{current} {count} {h.hexdigest()[:16]}')
            current, count, h = q, 0, hashlib.sha256()
        count += 1
        h.update(rest.encode())
    if current is not None:
        print(f'{current} {count} {h.hexdigest()[:16]}')


def read_digests(path):
    d = {}
    if os.path.exists(path):
        with open(path) as f:
            for line in f:
                q, n, h = line.split()
                d[int(q)] = (int(n), h)
    return d


# ------------------------------------------------------------------ parsing

def read_kv(path):
    kv = {}
    if os.path.exists(path):
        with open(path) as f:
            for line in f:
                if ':' in line:
                    k, v = line.split(':', 1)
                    kv.setdefault(k.strip(), v.strip())
    return kv


def read_results(path):
    """query -> (count, ns) from the stdout of a query driver."""
    res = {}
    with open(path) as f:
        for line in f:
            parts = line.strip().split(';')
            if len(parts) == 3 and all(p.isdigit() for p in parts):
                res[int(parts[0])] = (int(parts[1]), int(parts[2]))
    return res


def query_shapes(path):
    shapes = {}
    with open(path, newline='') as f:
        for q, line in enumerate(f, start=1):
            line = line.rstrip('\r\n')
            if not line.strip():
                continue
            a, b = line.index(' '), line.rindex(' ')
            rpq = IRI_RE.sub(lambda m: '^p' if m.group().startswith('<%') else 'p', line[a + 1:b])
            shapes[q] = ('v' if line[0] == '?' else 'c') + ' ' + rpq + ' ' + ('v' if line[b + 1] == '?' else 'c')
    return shapes


def expected_fix(variant, shape, baseline_count, candidate_count):
    """The bug fix of docs/REFACTORING.md that explains a difference of
    results between the versions, or None (to be reviewed)."""
    if not shape:
        return None
    subj, *rpq, obj = shape.split(' ')
    rpq = ' '.join(rpq)
    n_preds = rpq.count('p')
    if n_preds > 15:
        return 'B7'
    if subj == 'c' and obj == 'c':
        return 'B3'
    if rpq in ('p', '^p') and subj != obj:
        return 'B4'
    if subj == 'v' and obj == 'v' and n_preds >= 4 and set(rpq) <= set('p/^'):
        return 'B14'
    if variant == 'split' and subj == 'v' and obj == 'v':
        return 'B5'
    if variant == 'split' and abs(baseline_count - candidate_count) == 1:
        return 'B6'
    return None


def geomean(values):
    values = [v for v in values if v > 0]
    return math.exp(sum(map(math.log, values)) / len(values)) if values else None


def fmt(x, digits=3):
    return '-' if x is None else f'{x:.{digits}f}'


# ------------------------------------------------------------------ summary

def summarize(res_dir):
    env = read_kv(os.path.join(res_dir, 'environment.txt'))
    query_files = env.get('queries', '').split()
    summary = {'environment': env, 'build': read_kv(os.path.join(res_dir, 'build-info.txt')),
               'indexes': {}, 'benchmarks': []}
    timeout_ns = int(summary['build'].get('timeout_seconds') or DEFAULT_TIMEOUT_S) * 10**9

    for t in sorted(glob.glob(os.path.join(res_dir, 'index-*-*.time'))):
        key = os.path.basename(t)[len('index-'):-len('.time')]  # <variant>-<version>
        info = read_kv(t)
        log = os.path.join(res_dir, f'index-{key}.log')
        if os.path.exists(log):
            m = re.search(r'Index built ([\d.]+) bytes per triple', open(log).read())
            if m:
                info['bytes_per_triple_input'] = m.group(1)
        sizes = os.path.join(res_dir, f'index-{key}.sizes')
        if os.path.exists(sizes):
            info['index_bytes'] = sum(int(l.split()[0]) for l in open(sizes) if l.strip())
        comparison = os.path.join(res_dir, f'index-{key.split("-")[0]}-comparison.txt')
        if os.path.exists(comparison):
            info['files_identical_to_other_version'] = 'DIFFERENT' not in open(comparison).read()
        summary['indexes'][key] = info

    groups = defaultdict(lambda: defaultdict(list))  # (variant, qname) -> version -> [run results]
    times = defaultdict(lambda: defaultdict(list))
    for out in sorted(glob.glob(os.path.join(res_dir, 'queries-*-run*.out'))):
        m = re.match(r'queries-(basic|split)-(.+)-(baseline|candidate)-run(\d+)\.out$', os.path.basename(out))
        if not m:
            continue
        variant, qname, version, run = m.groups()
        groups[(variant, qname)][version].append(read_results(out))
        times[(variant, qname)][version].append(read_kv(out[:-len('.out')] + '.time'))

    for (variant, qname), versions in sorted(groups.items()):
        if 'baseline' not in versions or 'candidate' not in versions:
            continue
        qfile = next((f for f in query_files if os.path.basename(f) == qname + '.tsv'), None)
        shapes = query_shapes(qfile) if qfile and os.path.exists(qfile) else {}
        med = {}
        for version, runs in versions.items():
            queries = set().union(*runs)
            med[version] = {q: (runs[0][q][0], statistics.median(r[q][1] for r in runs if q in r))
                            for q in queries if q in runs[0]}
        dig = {v: read_digests(os.path.join(res_dir, f'digests-{variant}-{qname}-{v}.txt'))
               for v in ('baseline', 'candidate')}
        base, cand = med['baseline'], med['candidate']
        queries = sorted(set(base) & set(cand))

        def same_result(q):
            return dig['baseline'].get(q, (0, '-')) == dig['candidate'].get(q, (0, '-'))

        same = [q for q in queries if same_result(q)]
        diff = [q for q in queries if not same_result(q)]
        timeouts = {v: [q for q in queries if med[v][q][1] >= timeout_ns * 0.999] for v in med}
        long_q = [q for q in same if base[q][1] >= LONG_NS]
        short_q = [q for q in same if 0 < base[q][1] < LONG_NS]

        per_version = {}
        for v in ('baseline', 'candidate'):
            per_version[v] = {
                'runs_total_seconds': [round(sum(t for _, t in r.values()) / 1e9, 3) for r in versions[v]],
                'median_total_seconds': round(sum(med[v][q][1] for q in queries) / 1e9, 3),
                'mean_query_seconds': round(statistics.mean(med[v][q][1] for q in queries) / 1e9, 4) if queries else None,
                'median_query_seconds': round(statistics.median(med[v][q][1] for q in queries) / 1e9, 5) if queries else None,
                'timeouts': len(timeouts[v]),
                'max_rss_kb': [t.get('max_rss_kb') for t in times[(variant, qname)][v]],
                'exit_status': [t.get('exit_status') for t in times[(variant, qname)][v]],
            }

        comparison = {
            'queries': len(queries),
            'same_results': len(same),
            'different_results': len(diff),
            'total_ratio_same': (sum(cand[q][1] for q in same) / sum(base[q][1] for q in same))
                                if same and sum(base[q][1] for q in same) else None,
            'geomean_ratio_long': geomean([cand[q][1] / base[q][1] for q in long_q]),
            'long_queries': len(long_q),
            'geomean_ratio_short': geomean([cand[q][1] / base[q][1] for q in short_q]),
            'short_queries': len(short_q),
            'speedup_quantiles_long': None,
        }
        if long_q:
            r = sorted(base[q][1] / cand[q][1] for q in long_q)
            comparison['speedup_quantiles_long'] = {p: round(r[min(len(r) - 1, int(p / 100 * len(r)))], 3)
                                                    for p in (5, 25, 50, 75, 95)}
        slowest = sorted(((cand[q][1] / base[q][1], q) for q in long_q), reverse=True)[:10]
        comparison['slowest_relative'] = [{'query': q, 'ratio': round(x, 3)} for x, q in slowest]
        comparison['unexplained_differences'] = 0  # filled below

        differences = []
        for q in diff:
            b, c = dig['baseline'].get(q, (0, '-')), dig['candidate'].get(q, (0, '-'))
            timed_out = q in timeouts['baseline'] or q in timeouts['candidate']
            differences.append({'query': q, 'shape': shapes.get(q), 'baseline_count': b[0],
                                'candidate_count': c[0],
                                'kind': 'count' if b[0] != c[0] else 'pairs',
                                # results cut by the time limit depend on the speed: not comparable
                                'explained_by': 'timeout' if timed_out else expected_fix(variant, shapes.get(q), b[0], c[0]),
                                'baseline_seconds': round(base[q][1] / 1e9, 4),
                                'candidate_seconds': round(cand[q][1] / 1e9, 4),
                                'timeout': timed_out})

        comparison['unexplained_differences'] = sum(1 for d in differences if d['explained_by'] is None)

        by_shape = defaultdict(lambda: {'queries': 0, 'baseline_seconds': 0.0, 'candidate_seconds': 0.0,
                                        'different_results': 0})
        for q in queries:
            s = by_shape[shapes.get(q, '?')]
            s['queries'] += 1
            s['baseline_seconds'] += base[q][1] / 1e9
            s['candidate_seconds'] += cand[q][1] / 1e9
            s['different_results'] += q in diff

        summary['benchmarks'].append({
            'variant': variant, 'query_file': qname, 'versions': per_version, 'comparison': comparison,
            'differences': differences,
            'by_shape': {k: {kk: round(vv, 4) if isinstance(vv, float) else vv for kk, vv in v.items()}
                         for k, v in sorted(by_shape.items(), key=lambda kv: -kv[1]['baseline_seconds'])},
        })

    with open(os.path.join(res_dir, 'summary.json'), 'w') as f:
        json.dump(summary, f, indent=1)
    print_markdown(summary)


def print_markdown(s):
    env = s['environment']
    print(f"# Validation: {env.get('dataset')}\n")
    print(f"- triples: {env.get('triples')}, nodes: {env.get('nodes')}, predicates: {env.get('predicates')}")
    print(f"- baseline: {s['build'].get('baseline')}")
    print(f"- candidate: {s['build'].get('candidate')}")
    print(f"- runs per version: {env.get('reps')}; time limit per query: "
          f"{s['build'].get('timeout_seconds', DEFAULT_TIMEOUT_S)} s; host: {env.get('build_host')}\n")

    if s['indexes']:
        print('## Index construction\n')
        print('| index | seconds | peak RSS (MB) | size (MB) | identical files |')
        print('|---|---|---|---|---|')
        for k, v in s['indexes'].items():
            rss = v.get('max_rss_kb')
            size = v.get('index_bytes')
            print(f"| {k} | {v.get('wall_seconds')} | {int(rss) // 1024 if rss else '-'} | "
                  f"{size // 2**20 if size else '-'} | {v.get('files_identical_to_other_version', '-')} |")
        print()

    for b in s['benchmarks']:
        c = b['comparison']
        print(f"## {b['variant']} / {b['query_file']}\n")
        print('| version | total (s), per run | mean (s) | median (s) | timeouts |')
        print('|---|---|---|---|---|')
        for v in ('baseline', 'candidate'):
            pv = b['versions'][v]
            print(f"| {v} | {pv['runs_total_seconds']} | {pv['mean_query_seconds']} | "
                  f"{pv['median_query_seconds']} | {pv['timeouts']} |")
        print(f"\n{c['queries']} queries, {c['same_results']} with the same results, "
              f"{c['different_results']} different.\n")
        print(f"- candidate/baseline on the queries with the same results: total {fmt(c['total_ratio_same'])}, "
              f"geometric mean {fmt(c['geomean_ratio_long'])} ({c['long_queries']} queries >= 1 ms), "
              f"{fmt(c['geomean_ratio_short'])} ({c['short_queries']} queries < 1 ms)")
        if c['speedup_quantiles_long']:
            print(f"- speedup baseline/candidate (> 1: candidate faster), quantiles over the queries >= 1 ms: "
                  f"{c['speedup_quantiles_long']}")
        if b['differences']:
            explained = defaultdict(int)
            for d in b['differences']:
                explained[d['explained_by'] or 'UNEXPLAINED'] += 1
            print(f"- differences by the fix that explains them: {dict(sorted(explained.items()))}")
            print('\n| query | shape | baseline results | candidate results | kind | explained by |')
            print('|---|---|---|---|---|---|')
            for d in sorted(b['differences'], key=lambda d: d['explained_by'] is not None)[:30]:
                print(f"| {d['query']} | `{d['shape']}` | {d['baseline_count']} | {d['candidate_count']} | "
                      f"{d['kind']} | {d['explained_by'] or '**UNEXPLAINED**'} |")
            if len(b['differences']) > 30:
                print(f"\n... and {len(b['differences']) - 30} more (summary.json)")
        print()


def main():
    if len(sys.argv) == 3 and sys.argv[1] == 'digest':
        digest(sys.argv[2])
    elif len(sys.argv) == 3 and sys.argv[1] == 'summary':
        summarize(sys.argv[2])
    else:
        sys.stderr.write(__doc__)
        sys.exit(1)


if __name__ == '__main__':
    main()
