#!/usr/bin/env python3
"""Characterization of tunnels/run_*.sh with stub binaries (T0 of the tunnels-paper-repro task).

The scripts are copied to a temporary tree T/tunnels, whose T/build holds stubs that only log
their argv and create the minimal outputs the scripts check. The sorted, path-normalized log of
the commands run by run_wikidata.sh, run_generated.sh, run_backends.sh (adj), run_pathfinder.sh and run_automata.sh
is compared with tests/golden/tunnels/scripts.golden.

Usage: tests/tunnels_scripts_test.py --build-dir build [--update]
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
GOLDEN = os.path.join(HERE, 'golden', 'tunnels', 'scripts.golden')
AUTOMATA_HEADER = ('qid,line,kind,shape,m,src,automaton,states,trans,eps_trans,status,R_nodes,R_edges,'
                   'R_eps_edges,lookups,scanned,H_nodes,H_edges,answers,t_product,t_live')

failures = []
PF_RUN = {}   # of the last run_scripts: tree, out dir, environment (with the python3 stub) and script of run_pathfinder.sh
RAW = []   # the commands of the last run_scripts, in the order they ran


def check(cond, msg):
    if not cond:
        failures.append(msg)
        print('FAIL', msg)


def cols_header():
    """The header of the CSV: the strings of COLS in src/ring-tunnels.cpp."""
    with open(os.path.join(ROOT, 'src', 'ring-tunnels.cpp')) as f:
        src = f.read()
    body = re.search(r'static const char \*COLS\[\] = \{(.*?)\};', src, re.S).group(1)
    return ','.join(re.findall(r'"([^"]*)"', body))


STUB = r'''#!/bin/bash
# stub of {name}: logs its argv and creates the minimal outputs
echo "{name} $*" >> "{log}"
# the runs of run_pathfinder.sh (--early-stop) log when they start and end: they must not overlap
if printf '%s\n' "$@" | grep -qx -- --early-stop; then
  echo "START {name}" >> "{log}.seq"; sleep 0.3; echo "END {name}" >> "{log}.seq"
fi
out=""; args=("$@")
for ((i = 0; i < ${{#args[@]}}; i++)); do [ "${{args[i]}}" = --out ] && out=${{args[i+1]}}; done
case "{name}" in
  build-index-basic) touch "$1.bwts.L" ;;
  build-index-split) touch "$1.wt_pred_s" ;;
  gen-queries) echo "<http://example.org/n1> <http://example.org/prop/direct/p1>+ ?y" ;;
  adj-tunnels) touch "$1.adj"
    if printf '%s\n' "$@" | grep -qx -- --automata; then echo "{aut}" > "$out"; else echo "{cols}" > "$out"; fi ;;
  *) echo "{cols}" > "$out" ;;
esac
# --shortest runs: one valid row, so that the tables of run_pathfinder.sh have something to read
if printf '%s\n' "$@" | grep -qx -- --shortest; then echo "{row}" >> "$out"; fi
exit 0
'''


def shortest_row(cols):
    """a row of ring-tunnels --shortest that pathfinder_tables.py accepts"""
    v = dict(qid='1', kind='CV', src='1', dst='2', status='ok', sem='shortest', pf_status='ok', model='region_rel',
             budget='4x+10', t_build='0.002', t_pf='0.001')
    return ','.join(v.get(c, '') for c in cols.split(','))


def make_stubs(bdir, log, cols):
    os.makedirs(bdir, exist_ok=True)
    for name in ['build-index-basic', 'build-index-split', 'ring-tunnels', 'ring-tunnels-split',
                 'adj-tunnels', 'gen-queries']:
        p = os.path.join(bdir, name)
        with open(p, 'w') as f:
            f.write(STUB.format(name=name, log=log, cols=cols, aut=AUTOMATA_HEADER, row=shortest_row(cols)))
        os.chmod(p, 0o755)


def write_data(d, tag):
    """Fake dictionaries and query log in d; returns (P, SO, queries)."""
    os.makedirs(d, exist_ok=True)
    paths = []
    for name, text in (('P', f'1 <http://example.org/{tag}/P>\n'), ('SO', f'1 <http://example.org/{tag}/SO>\n'),
                       ('queries', '<http://example.org/n1> <http://example.org/prop/direct/p1>+ ?y\n')):
        p = os.path.join(d, name)
        with open(p, 'w') as f:
            f.write(text)
        paths.append(p)
    return tuple(paths)


def sh(script, args, env, cwd):
    proc = subprocess.run(['bash', script] + args, env=env, cwd=cwd, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, text=True)
    check(proc.returncode == 0, f'{os.path.basename(script)} exit code {proc.returncode}\n{proc.stdout[-500:]}')


def run_scripts(T, extra_env, cols, stub_build='build'):
    """Run the four scripts in the tree T; returns (normalized sorted command lines, OUT)."""
    shutil.copytree(os.path.join(ROOT, 'tunnels'), os.path.join(T, 'tunnels'),
                    ignore=shutil.ignore_patterns('__pycache__', 'paper.env'))
    log = os.path.join(T, 'argv.log')
    open(log, 'w').close()
    make_stubs(os.path.join(T, stub_build), log, cols)
    data = os.path.join(T, 'tunnels', 'data')
    p, so, q = write_data(data, 'default')
    os.rename(p, os.path.join(data, 'wikidata-unsorted-enumerated.dat.P'))
    os.rename(so, os.path.join(data, 'wikidata-unsorted-enumerated.dat.SO'))
    os.rename(q, os.path.join(data, 'paths-final.tsv'))
    os.makedirs(os.path.join(T, 'wd'))
    dat = os.path.join(T, 'wd', 'wikidata-enumerated.dat')
    open(dat, 'w').close()
    shapes = os.path.join(T, 'shapes.csv')
    with open(shapes, 'w') as f:
        f.write('h\n')
    out = os.path.join(T, 'out')
    env = dict(os.environ)
    env.update(extra_env)
    t = os.path.join(T, 'tunnels')
    sh(os.path.join(t, 'run_wikidata.sh'), [dat, out, '--reps', '1'], env, T)
    sh(os.path.join(t, 'run_generated.sh'), [out, shapes, '7', '3', '--reps', '1'], env, T)
    sh(os.path.join(t, 'run_backends.sh'), [out, 'adj', '--reps', '1'], env, T)
    # python3 that logs its argv (then runs the real one) for run_pathfinder.sh
    pybin = os.path.join(T, 'pybin')
    os.makedirs(pybin)
    with open(os.path.join(pybin, 'python3'), 'w') as f:
        f.write('#!/bin/bash\necho "$*" >> "%s"\nexec "%s" "$@"\n' % (os.path.join(T, 'python3.log'), sys.executable))
    os.chmod(os.path.join(pybin, 'python3'), 0o755)
    open(os.path.join(T, 'python3.log'), 'w').close()
    open(log + '.seq', 'w').close()
    penv = dict(env, PATH=pybin + os.pathsep + env['PATH'])
    PF_RUN.update(T=T, out=out, env=penv, script=os.path.join(t, 'run_pathfinder.sh'))
    sh(os.path.join(t, 'run_pathfinder.sh'), [out, '--reps', '1'], penv, T)
    sh(os.path.join(t, 'run_automata.sh'), [out, '--reps', '1'], env, T)
    with open(log) as f:
        lines = f.read().splitlines()
    RAW[:] = [l.replace(out, '<OUT>').replace(T, '<T>') for l in lines]
    return sorted(l.replace(out, '<OUT>').replace(T, '<T>') for l in lines), out


def env_case(cols):
    """BUILD_DIR, WD_DICT_P, WD_DICT_SO and WD_QUERIES redirect the commands (no golden)."""
    with tempfile.TemporaryDirectory() as tmp:
        T = os.path.realpath(os.path.join(tmp, 'b'))
        os.makedirs(T)
        P, SO, Q = write_data(os.path.join(T, 'elsewhere'), 'custom')
        env = {'BUILD_DIR': os.path.join(T, 'otherbuild'), 'WD_DICT_P': P, 'WD_DICT_SO': SO, 'WD_QUERIES': Q}
        lines, out = run_scripts(T, env, cols, stub_build='otherbuild')
        check(len(lines) == 19, f"env case: {len(lines)} commands, expected 19")
        default_q = '<T>/tunnels/data/paths-final.tsv'
        check(all(default_q not in l for l in lines), 'env case: a command still uses tunnels/data/paths-final.tsv')
        qs = [l for l in lines if l.split()[0] in ('ring-tunnels', 'adj-tunnels', 'gen-queries')
              and '/gen-' not in l.split()[2]]
        check(qs and all(Q.replace(T, '<T>') in l for l in qs if not l.startswith('gen-queries')),
              'env case: the query log is not WD_QUERIES')
        check(any(l.startswith('gen-queries') and Q.replace(T, '<T>') in l for l in lines),
              'env case: gen-queries does not get WD_QUERIES')
        for sub in ('basic', 'split', 'adj'):
            for ext, src in (('P', P), ('SO', SO)):
                with open(os.path.join(out, sub, 'wd.dat.' + ext)) as f, open(src) as g:
                    check(f.read() == g.read(), f'env case: {sub}/wd.dat.{ext} is not the configured dictionary')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--build-dir', required=True)   # unused: the stubs replace the binaries
    ap.add_argument('--update', action='store_true')
    args = ap.parse_args()
    cols = cols_header()

    with tempfile.TemporaryDirectory() as tmp:
        T = os.path.realpath(os.path.join(tmp, 'a'))
        os.makedirs(T)
        lines, out = run_scripts(T, {}, cols)
        text = '\n'.join(lines) + '\n'
        if args.update:
            os.makedirs(os.path.dirname(GOLDEN), exist_ok=True)
            with open(GOLDEN, 'w') as f:
                f.write(text)
            print('golden updated:', GOLDEN)
        else:
            with open(GOLDEN) as f:
                gold = f.read()
            check(text == gold, 'commands differ from tests/golden/tunnels/scripts.golden:\n'
                  + ''.join(l + '\n' for l in sorted(set(text.splitlines()) ^ set(gold.splitlines()))))
        # run_pathfinder.sh: ring-tunnels first and then adj-tunnels (in sequence), after backends, with exactly
        # the options of the paper (the golden has them too: another --vv-samples or no --early-stop fails)
        opts = ('--uniform-cost --budgets 0,1000,10000,100000,inf --rel 1,4,16 --rel-slack 10 --reps 1 '
                '--shortest --early-stop --pairs 5 --pair-reverse --vv-derive --vv-samples 5')
        want = [f'ring-tunnels <OUT>/basic/wd.dat <T>/tunnels/data/paths-final.tsv --out <OUT>/sp_early.csv {opts}',
                f'adj-tunnels <OUT>/adj/wd.dat <T>/tunnels/data/paths-final.tsv --out <OUT>/sp_early_adj.csv {opts}']
        idx = [RAW.index(w) if w in RAW else -1 for w in want]
        check(all(i >= 0 for i in idx) and idx[0] + 1 == idx[1], f'run_pathfinder.sh commands: {idx}')
        check(any('adj-tunnels' in l and '/adj/cv.csv' in l for l in RAW[:idx[0]]), 'run_pathfinder.sh does not run after backends')
        # the calls of pathfinder_tables.py: Plain with <out>/adj as dir (its P5 is not the Ring one), --adj in --paper,
        # no --mdb without MDB_*; with MDB_RESULTS and MDB_RAW the --paper call carries --mdb and --mdb-raw
        def pycalls():
            with open(os.path.join(T, 'python3.log')) as fh:
                return [l.replace(out, '<OUT>').replace(T, '<T>') for l in fh.read().splitlines()]
        pt = '<T>/tunnels/pathfinder_tables.py'
        check(pycalls() == [f'{pt} <OUT> --sp sp_early.csv', f'{pt} <OUT>/adj --sp <OUT>/sp_early_adj.csv',
                            '<T>/tunnels/ring_summary.py <OUT>/sp_early.csv --pathfinder',
                            f'{pt} <OUT> --sp sp_early.csv --paper --adj sp_early_adj.csv'], f'run_pathfinder.sh python3 calls: {pycalls()}')
        with open(os.path.join(T, 'argv.log.seq')) as fh:
            check(fh.read().split('\n')[:-1] == ['START ring-tunnels', 'END ring-tunnels', 'START adj-tunnels', 'END adj-tunnels'],
                  'run_pathfinder.sh: the two runs are not in sequence (ring-tunnels, then adj-tunnels)')
        mres, mraw = os.path.join(T, 'mdb-results.csv'), os.path.join(T, 'mdb-raw.csv')
        open(mres, 'w').close()
        open(mraw, 'w').close()
        open(os.path.join(T, 'python3.log'), 'w').close()
        sh(PF_RUN['script'], [out, '--reps', '1'], dict(PF_RUN['env'], MDB_RESULTS=mres, MDB_RAW=mraw), T)
        check(pycalls()[-1] == f'{pt} <OUT> --sp sp_early.csv --paper --adj sp_early_adj.csv --mdb {mres.replace(T, "<T>")} --mdb-raw {mraw.replace(T, "<T>")}',
              f'run_pathfinder.sh with MDB_*: the --paper call: {pycalls()[-1:]}')
        for n in ('sp_early', 'sp_early_adj'):
            check(os.path.exists(os.path.join(out, n + '.csv')), f'missing {n}.csv')
        for n in ('pathfinder', 'pathfinder_adj', 'summary_pathfinder', 'pathfinder_paper'):
            check(os.path.exists(os.path.join(out, n + '.md')) and os.path.getsize(os.path.join(out, n + '.md')) > 0, f'missing or empty {n}.md')
        for n in ('cv', 'vv_bridge', 'vv_ring', 'vv_all'):
            check(os.path.exists(os.path.join(out, n + '.csv')), f'missing {n}.csv')
        check(os.path.exists(os.path.join(out, 'automata', 'log.csv')), 'missing automata/log.csv')
        with open(os.path.join(out, 'basic', 'wd.dat.P')) as f:
            check('default/P' in f.read(), 'the default dictionary P was not copied')

    env_case(cols)
    if failures:
        print(f'{len(failures)} failure(s)')
        return 1
    print('tunnels_scripts: ok')
    return 0


if __name__ == '__main__':
    sys.exit(main())
