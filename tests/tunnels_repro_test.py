#!/usr/bin/env python3
"""End-to-end test of tunnels/reproduce_paper.sh on tests/data/metro (task tunnels-paper-repro).

  T1  --only log,generated,backends,automata with a paper.env that points to a temporary DATA_DIR:
      the outputs exist, have data rows, and use the configured dictionaries
  T2  --dry-run prints the commands of the eight steps and runs/creates nothing
  T3  without a configuration file: exit code 2 and a pointer to paper.env.example
  T4  missing inputs: exit code 2, every problem is listed, nothing is created
  T5  --list
  T6  tunnels/paper.env is ignored by git
  T7  shellcheck, if installed
  T8  total time under 3 minutes
  T9  (and T9b) the step pathfinder without adj/wd.dat.adj and without the step backends: exit code 2 and the problem
      listed; MDB_RESULTS without MDB_RAW (or a file that does not exist): exit code 2

The metro predicates are renamed to .../prop/direct/... in the dictionary and the query log of
the test, because gen-queries only takes the predicates of that form from the log for its pool.

Usage: tests/tunnels_repro_test.py --build-dir build
"""

import argparse
import glob
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SCRIPT = os.path.join(ROOT, 'tunnels', 'reproduce_paper.sh')
METRO = os.path.join(HERE, 'data', 'metro')
SHAPES = os.path.join(HERE, 'tunnels', 'shapes_small.csv')
STEPS = ['build', 'log', 'generated', 'backends', 'pathfinder', 'automata', 'figures', 'tables']

failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)
        print('FAIL', msg)


def run(args, **kw):
    return subprocess.run(['bash', SCRIPT] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, cwd=ROOT, **kw)


def write_env(path, **kv):
    with open(path, 'w') as f:
        for k, v in kv.items():
            f.write(f'{k}="{v}"\n')


def data_rows(path):
    """Number of lines after the header, or -1 if the file does not exist."""
    if not os.path.isfile(path):
        return -1
    with open(path) as f:
        return max(0, len(f.read().splitlines()) - 1)


def tree(d):
    return sorted(os.path.join(r, n) for r, ds, fs in os.walk(d) for n in ds + fs) if os.path.exists(d) else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--build-dir', required=True)
    args = ap.parse_args()
    build = os.path.abspath(args.build_dir)
    t0 = time.time()

    with tempfile.TemporaryDirectory() as tmp:
        tmp = os.path.realpath(tmp)
        data = os.path.join(tmp, 'data')
        os.makedirs(os.path.join(data, 'dicts'))
        dat = os.path.join(data, 'wd-small.dat')
        shutil.copy(os.path.join(METRO, 'graph.dat'), dat)
        dict_p = os.path.join(data, 'dicts', 'predicates.txt')
        dict_so = os.path.join(data, 'dicts', 'nodes.txt')
        queries = os.path.join(data, 'log.tsv')
        shapes = os.path.join(data, 'shapes.csv')
        for src, dst in ((os.path.join(METRO, 'graph.dat.P'), dict_p),
                         (os.path.join(METRO, 'graph.dat.SO'), dict_so),
                         (os.path.join(METRO, 'queries.tsv'), queries)):
            with open(src) as f, open(dst, 'w') as g:
                g.write(f.read().replace('/pred/', '/prop/direct/'))
        shutil.copy(SHAPES, shapes)
        out = os.path.join(tmp, 'out')
        cfg = os.path.join(tmp, 'paper.env')
        base = dict(DATA_DIR=data, WD_DAT=dat, WD_DICT_P=dict_p, WD_DICT_SO=dict_so, WD_QUERIES=queries,
                    SHAPES_CSV=shapes, OUT_DIR=out, BUILD_DIR=build, NAVGRAPH_DIR='', GEN_N='10',
                    GEN_SEED='1', EXTRA_OPTS='--reps 1')
        write_env(cfg, **base)

        # T1
        p = run(['--config', cfg, '--only', 'log,generated,backends,pathfinder,automata'])
        check(p.returncode == 0, f'T1: exit code {p.returncode}\n{p.stdout[-1500:]}\n{p.stderr[-1500:]}')
        for n in ('cv', 'vv_bridge', 'vv_ring', 'vv_all', 'sp_early', 'sp_early_adj'):
            check(data_rows(os.path.join(out, n + '.csv')) > 0, f'T1: {n}.csv has no data rows')
        check(not os.path.exists(os.path.join(out, 'sp.csv')), 'T1: sp.csv is no longer produced')
        for n in ('summary_cv', 'summary_vv_bridge', 'summary_vv', 'summary_pathfinder', 'pathfinder', 'pathfinder_adj',
                  'pathfinder_paper'):
            check(os.path.getsize(os.path.join(out, n + '.md')) > 0 if os.path.isfile(os.path.join(out, n + '.md')) else False,
                  f'T1: {n}.md missing or empty')
        pp = os.path.join(out, 'pathfinder_paper.md')
        if os.path.isfile(pp):
            with open(pp) as f:
                txt = f.read()
            check('## Comparison with PathFinder' in txt and '| Ring |' in txt and '| Plain |' in txt, 'T1: pathfinder_paper.md lacks the comparison or the Ring/Plain rows')
            plain = [l for l in txt.splitlines() if l.startswith('| Plain |')]
            cells = [c.strip() for c in plain[0].strip('|').split('|')] if plain else []
            check(len(cells) == 5 and all(c != 'n/a' for c in cells[1:3]), f'T1: the Plain row of the time table has no numbers: {plain}')
        g = os.path.join(out, 'gen-10-1')
        check(data_rows(os.path.join(g, 'queries.tsv')) >= 0, 'T1: gen-10-1/queries.tsv missing')
        with open(os.path.join(g, 'queries.tsv')) as f:
            check(len(f.read().splitlines()) > 0, 'T1: gen-10-1/queries.tsv is empty')
        check(data_rows(os.path.join(g, 'cv.csv')) > 0, 'T1: gen-10-1/cv.csv has no data rows')
        check(data_rows(os.path.join(out, 'adj', 'cv.csv')) > 0, 'T1: adj/cv.csv has no data rows')
        check(data_rows(os.path.join(out, 'adj', 'vv_bridge.csv')) > 0, 'T1: adj/vv_bridge.csv has no data rows')
        csvs = glob.glob(os.path.join(out, 'automata', '*.csv'))
        check(len(csvs) >= 2, f'T1: automata/*.csv: {len(csvs)} files')
        for c in csvs:
            check(data_rows(c) > 0, f'T1: {c} has no data rows')
            md = c[:-4] + '.md'
            check(os.path.isfile(md) and os.path.getsize(md) > 0, f'T1: {md} missing or empty')
        check(os.path.getsize(os.path.join(out, 'reproduce.log')) > 0 if os.path.isfile(os.path.join(out, 'reproduce.log')) else False,
              'T1: reproduce.log missing or empty')
        if os.path.isfile(os.path.join(out, 'reproduce.log')):
            with open(os.path.join(out, 'reproduce.log')) as f:
                lg = f.read()
            for s in ('log', 'generated', 'backends', 'pathfinder', 'automata'):
                check(f'== {s}: start' in lg and f'== {s}: ok (' in lg, f'T1: reproduce.log lacks the markers of {s}')
        for sub in ('basic', 'split', 'adj'):
            for ext, src in (('P', dict_p), ('SO', dict_so)):
                d = os.path.join(out, sub, 'wd.dat.' + ext)
                if os.path.isfile(d):
                    with open(d) as f, open(src) as h:
                        check(f.read() == h.read(), f'T1: {sub}/wd.dat.{ext} is not the configured dictionary')
                else:
                    check(False, f'T1: {d} missing')

        # T2: dry run with stubs that fail if run
        stubs = os.path.join(tmp, 'stubs')
        marker = os.path.join(tmp, 'stub-ran')
        os.makedirs(stubs)
        for n in ('build-index-basic', 'build-index-split', 'ring-tunnels', 'ring-tunnels-split', 'adj-tunnels',
                  'gen-queries', 'nav-tunnels', 'nav-build-index', 'cmake'):
            sp = os.path.join(stubs, n)
            with open(sp, 'w') as f:
                f.write(f'#!/bin/bash\ntouch "{marker}"\nexit 99\n')
            os.chmod(sp, 0o755)
        out2 = os.path.join(tmp, 'out-dry')
        cfg2 = os.path.join(tmp, 'dry.env')
        write_env(cfg2, **dict(base, OUT_DIR=out2, BUILD_DIR=stubs))
        env = dict(os.environ, PATH=stubs + os.pathsep + os.environ['PATH'])
        p = run(['--config', cfg2, '--dry-run'], env=env)
        check(p.returncode == 0, f'T2: exit code {p.returncode}\n{p.stderr[-1000:]}')
        lines = p.stdout.splitlines()
        for s in STEPS:
            check(f'== {s}: dry-run' in lines, f'T2: no section for the step {s}')
        plus = [l for l in lines if l.startswith('+ ')]
        for s, needle in (('build', 'cmake'), ('log', 'run_wikidata.sh'), ('generated', 'run_generated.sh'),
                          ('backends', 'run_backends.sh'), ('pathfinder', 'run_pathfinder.sh'), ('automata', 'run_automata.sh'),
                          ('figures', 'paper_figures.py')):
            check(any(needle in l for l in plus), f'T2: no "+ " line for {s} ({needle})')
        check(any(dat in l for l in plus), 'T2: the commands do not use the configured WD_DAT')
        check(any('run_backends.sh' in l and l.endswith(' adj --reps 1') for l in plus),
              'T2: the backends command does not include EXTRA_OPTS')
        if os.path.exists(os.path.join(ROOT, 'tunnels', 'paper_tables.py')):
            check(any('paper_tables.py' in l for l in plus), 'T2: no "+ " line for tables')
        check(not os.path.exists(out2), 'T2: --dry-run created OUT_DIR')
        check(not os.path.exists(marker), 'T2: --dry-run ran a binary')

        # T3: no configuration file
        p = run(['--config', os.path.join(tmp, 'nope.env')])
        check(p.returncode == 2, f'T3: exit code {p.returncode}, expected 2')
        check('paper.env.example' in p.stdout + p.stderr, 'T3: the message does not mention paper.env.example')

        # T4: missing inputs
        out4 = os.path.join(tmp, 'out-missing')
        cfg4 = os.path.join(tmp, 'missing.env')
        write_env(cfg4, **dict(base, OUT_DIR=out4, WD_QUERIES=os.path.join(tmp, 'no-log.tsv'),
                               SHAPES_CSV=os.path.join(tmp, 'no-shapes.csv')))
        before = tree(tmp)
        p = run(['--config', cfg4])
        check(p.returncode == 2, f'T4: exit code {p.returncode}, expected 2')
        msg = p.stdout + p.stderr
        check('WD_QUERIES' in msg and 'SHAPES_CSV' in msg, f'T4: the message does not name both variables:\n{msg}')
        check(not os.path.exists(out4), 'T4: OUT_DIR was created')
        check(tree(tmp) == before, 'T4: something was created')
        cfg4b = os.path.join(tmp, 'inside.env')   # OUT_DIR inside the directory of WD_DAT, and empty OUT_DIR
        write_env(cfg4b, **dict(base, OUT_DIR=os.path.join(data, 'out')))
        p = run(['--config', cfg4b, '--only', 'log'])
        check(p.returncode == 2 and 'inside' in p.stdout + p.stderr, 'T4: OUT_DIR inside the WD_DAT directory is accepted')
        check(not os.path.exists(os.path.join(data, 'out')), 'T4: OUT_DIR inside the data dir was created')

        # T4b: a failing step stops the run; a parallel process killed with kill -9 is detected
        def stub_dir(name, bodies):
            d = os.path.join(tmp, name)
            os.makedirs(d)
            for n in ('build-index-basic', 'build-index-split', 'ring-tunnels', 'ring-tunnels-split', 'adj-tunnels',
                      'gen-queries'):
                with open(os.path.join(d, n), 'w') as fh:
                    fh.write('#!/bin/bash\n' + bodies.get(n, 'exit 99') + '\n')
                os.chmod(os.path.join(d, n), 0o755)
            return d
        writer = ('out=""; a=("$@"); for ((i = 0; i < ${#a[@]}; i++)); do [ "${a[i]}" = --out ] && out=${a[i+1]}; done\n'
                  'printf "h\\nrow\\n" > "$out"')
        def writer_of(body):   # a stub that writes `body` (a shell command) to the --out file
            return ('out=""; a=("$@"); for ((i = 0; i < ${#a[@]}; i++)); do [ "${a[i]}" = --out ] && out=${a[i+1]}; done\n'
                    + body + ' > "$out"')
        failing = stub_dir('stubs-fail', {})
        o5 = os.path.join(tmp, 'out-fail')
        c5 = os.path.join(tmp, 'fail.env')
        write_env(c5, **dict(base, OUT_DIR=o5, BUILD_DIR=failing))
        p = run(['--config', c5, '--only', 'log,generated'])
        check(p.returncode != 0, 'T4b: a failing step exits with 0')
        check('== log: FAILED' in p.stdout, 'T4b: no "== log: FAILED"')
        check('== generated: start' not in p.stdout, 'T4b: the run continued after a failure')
        killed = stub_dir('stubs-kill', {'build-index-basic': 'touch "$1.bwts.L"', 'build-index-split': 'touch "$1.wt_pred_s"',
                                         'ring-tunnels': writer, 'ring-tunnels-split': 'kill -9 $$'})
        o6 = os.path.join(tmp, 'out-kill')
        c6 = os.path.join(tmp, 'kill.env')
        write_env(c6, **dict(base, OUT_DIR=o6, BUILD_DIR=killed))
        p = run(['--config', c6, '--only', 'log'])
        check(p.returncode != 0, 'T4b: a killed parallel process exits with 0')
        check('== log: FAILED' in p.stdout and '== log: ok' not in p.stdout, 'T4b: a killed parallel process is not reported as FAILED')

        # T4c: warning of the tables step on stderr, deduplicated steps, figures without OUT_DIR, bad paper.env
        tdir = os.path.join(tmp, 'copy', 'tunnels')   # a tree without paper_tables.py
        os.makedirs(tdir)
        shutil.copy(SCRIPT, tdir)
        p = subprocess.run(['bash', os.path.join(tdir, 'reproduce_paper.sh'), '--config', cfg, '--only', 'tables'],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=ROOT)
        check(p.returncode == 0, f'T4c: tables without paper_tables.py: exit code {p.returncode}\n{p.stderr[-500:]}')
        check('warning:' in p.stderr and 'warning:' not in p.stdout, 'T4c: the tables warning is not (only) on stderr')
        p = run(['--config', cfg2, '--dry-run', '--only', 'generated,generated'])
        check(p.returncode == 0 and p.stdout.count('== generated:') == 1 and
              len([l for l in p.stdout.splitlines() if l.startswith('+ ') and 'run_generated.sh' in l]) == 1,
              'T4c: --only generated,generated does not run the step once')
        o7 = os.path.join(tmp, 'out-nofigs')
        c7 = os.path.join(tmp, 'figs.env')
        write_env(c7, **dict(base, OUT_DIR=o7))
        p = run(['--config', c7, '--only', 'figures'])
        check(p.returncode == 2, f'T4c: --only figures without OUT_DIR: exit code {p.returncode}, expected 2')
        check(not os.path.exists(o7), 'T4c: --only figures created OUT_DIR')
        c8 = os.path.join(tmp, 'unquoted.env')
        with open(c8, 'w') as fh:
            fh.write(f'DATA_DIR={data}\nEXTRA_OPTS=--reps 1\n')
        bad_lines = {'unquoted-first': f'DATA_DIR={data} x\nOUT_DIR={tmp}/out-bad\nEXTRA_OPTS=\n',
                     'unquoted-mid': f'DATA_DIR={data}\nOUT_DIR={tmp}/out-bad\nWD_DAT=/a b\nEXTRA_OPTS=\n',
                     'false-mid': f'DATA_DIR={data}\nfalse\nOUT_DIR={tmp}/out-bad\nEXTRA_OPTS=\n'}
        for name, text in bad_lines.items():
            cb = os.path.join(tmp, name + '.env')
            with open(cb, 'w') as fh:
                fh.write(text)
            before = tree(tmp)
            p = run(['--config', cb])
            check(p.returncode == 2 and name + '.env' in p.stdout + p.stderr,
                  f'T4c: {name}: exit code {p.returncode}\n{p.stderr[-300:]}')
            check(tree(tmp) == before and not os.path.exists(os.path.join(tmp, 'out-bad')), f'T4c: {name}: something was created')

        # T9: the step pathfinder needs adj/wd.dat.adj (or the step backends); the MDB files go in pairs
        out9 = os.path.join(tmp, 'out-pf')
        cfg9 = os.path.join(tmp, 'pf.env')
        write_env(cfg9, **dict(base, OUT_DIR=out9))
        before = tree(tmp)
        p = run(['--config', cfg9, '--only', 'pathfinder'])
        check(p.returncode == 2 and 'wd.dat.adj' in p.stdout + p.stderr, f'T9: pathfinder without adj: exit code {p.returncode}\n{p.stderr}')
        check(tree(tmp) == before, 'T9: pathfinder without adj created something')
        os.makedirs(out9)
        open(os.path.join(out9, '.ring-tunnels'), 'w').close()
        p = run(['--config', cfg9, '--only', 'pathfinder'])
        check(p.returncode == 2 and 'wd.dat.adj' in p.stderr and 'backends' in p.stderr, f'T9: out dir without adj: {p.returncode}\n{p.stderr}')
        p = run(['--config', cfg9, '--only', 'backends,pathfinder', '--dry-run'], env=env)
        check(p.returncode == 0, f'T9: backends,pathfinder do not need adj beforehand: {p.returncode}\n{p.stderr}')
        mdb = os.path.join(tmp, 'mdb.csv')
        open(mdb, 'w').close()
        for name, kv in (('results-only', dict(MDB_RESULTS=mdb)), ('raw-only', dict(MDB_RAW=mdb)),
                         ('missing-file', dict(MDB_RESULTS=mdb, MDB_RAW=os.path.join(tmp, 'nope.csv')))):
            cm = os.path.join(tmp, name + '.env')
            write_env(cm, **dict(base, OUT_DIR=out2, BUILD_DIR=stubs, **kv))
            p = run(['--config', cm, '--only', 'pathfinder', '--dry-run'], env=env)
            check(p.returncode == 2 and ('MDB_RAW' in p.stderr or 'MDB_RESULTS' in p.stderr), f'T9: {name}: exit code {p.returncode}\n{p.stderr}')
        cm = os.path.join(tmp, 'mdb-ok.env')
        write_env(cm, **dict(base, OUT_DIR=out2, BUILD_DIR=stubs, MDB_RESULTS=mdb, MDB_RAW=mdb))
        p = run(['--config', cm, '--only', 'pathfinder', '--dry-run'], env=env)
        check(p.returncode == 0, f'T9: both MDB files: exit code {p.returncode}\n{p.stderr}')

        # T9b: check_pathfinder. The stubs end with exit code 0 but leave an output missing: the FAILED comes from
        # check_pathfinder (message "missing or empty output: <file>"), not from a failing script. python3 is a stub too.
        hdr = 'qid,kind,src,dst,status,sem,pf_status,model,budget,t_build,t_pf,pf_check,verify'
        good = 'printf "%s\\n1,CV,1,2,ok,shortest,ok,region_rel,4x+10,0.002,0.001,1,1\\n" "' + hdr + '"'
        header_only = 'printf "%s\\n" "' + hdr + '"'

        def pf_case(case, ring_body, adj_body, py=None, **kv):
            """run --only pathfinder with stubs; py: body of a python3 stub (None: the real one). Returns (process, OUT, python3 log)"""
            sd = stub_dir('stubs-pf-' + case, {'ring-tunnels': writer_of(ring_body), 'adj-tunnels': writer_of(adj_body)})
            pylog = os.path.join(sd, 'python3.log')
            if py is not None:
                with open(os.path.join(sd, 'python3'), 'w') as fh:
                    fh.write('#!/bin/bash\necho "$*" >> "%s"\n%s\n' % (pylog, py))
                os.chmod(os.path.join(sd, 'python3'), 0o755)
            op = os.path.join(tmp, 'out-pf-' + case)
            os.makedirs(os.path.join(op, 'basic'))
            os.makedirs(os.path.join(op, 'adj'))
            open(os.path.join(op, '.ring-tunnels'), 'w').close()
            os.symlink(dat, os.path.join(op, 'basic', 'wd.dat'))
            os.symlink(dat, os.path.join(op, 'adj', 'wd.dat'))
            open(os.path.join(op, 'adj', 'wd.dat.adj'), 'w').close()
            cp = os.path.join(tmp, 'pf-' + case + '.env')
            write_env(cp, **dict(base, OUT_DIR=op, BUILD_DIR=sd, **kv))
            p = run(['--config', cp, '--only', 'pathfinder'], env=dict(os.environ, PATH=sd + os.pathsep + os.environ['PATH']))
            return p, op, pylog

        quiet = 'case "$*" in *--paper*) exit 0;; esac; echo "## stub"'   # nothing for --paper
        quiet_all = 'echo "## stub"'                                       # a line for everything
        for case, ring_b, adj_b, py, missing in (('empty-paper', good, good, quiet, 'pathfinder_paper.md'),
                                                  ('ring-no-rows', header_only, good, quiet_all, 'sp_early.csv'),
                                                  ('adj-no-rows', good, header_only, quiet_all, 'sp_early_adj.csv')):
            p, op, _ = pf_case(case, ring_b, adj_b, py)
            msg = p.stdout + p.stderr
            check(p.returncode != 0 and '== pathfinder: FAILED' in p.stdout and '== pathfinder: ok' not in p.stdout,
                  f'T9b {case}: exit code {p.returncode}\n{msg[-600:]}')
            check(f'missing or empty output: {op}/{missing}\n' in msg, f'T9b {case}: the FAILED does not come from check_pathfinder:\n{msg[-600:]}')
        # positive control with the real python3
        p, op, _ = pf_case('ok', good, good)
        check(p.returncode == 0 and '== pathfinder: ok' in p.stdout, f'T9b: positive control: exit code {p.returncode}\n{p.stdout[-600:]}\n{p.stderr[-600:]}')
        with open(os.path.join(op, 'pathfinder_paper.md')) as fh:
            rows = [l for l in fh.read().splitlines() if l.startswith('| Plain |')]
        cells = [c.strip() for c in rows[0].strip('|').split('|')] if rows else []
        check(len(cells) == 5 and cells[1] != 'n/a',
              f'T9b: the Plain row of the time table (run_pathfinder.sh without --adj?): {rows}')
        # MDB_RESULTS and MDB_RAW reach the --paper call of run_pathfinder.sh as --mdb and --mdb-raw (and only then)
        mres, mraw = os.path.join(tmp, 'mdb-results.csv'), os.path.join(tmp, 'mdb-raw.csv')
        open(mres, 'w').close()
        open(mraw, 'w').close()
        p, op, pylog = pf_case('mdb', good, good, quiet_all, MDB_RESULTS=mres, MDB_RAW=mraw)
        check(p.returncode == 0, f'T9b mdb: exit code {p.returncode}\n{p.stdout[-600:]}\n{p.stderr[-600:]}')
        with open(pylog) as fh:
            paper = [l for l in fh.read().splitlines() if '--paper' in l.split()]
        check(len(paper) == 1 and f'--mdb {mres} --mdb-raw {mraw}' in paper[0], f'T9b mdb: the --paper call: {paper}')
        p, op, pylog = pf_case('nomdb', good, good, quiet_all)
        with open(pylog) as fh:
            paper = [l for l in fh.read().splitlines() if '--paper' in l.split()]
        check(len(paper) == 1 and '--mdb' not in paper[0] and '--adj' in paper[0], f'T9b nomdb: the --paper call: {paper}')

        # T5: --list
        p = run(['--list'])
        check(p.returncode == 0, f'T5: exit code {p.returncode}')
        words = [l.split()[0] for l in p.stdout.splitlines() if l.strip()]
        check(words == STEPS, f'T5: steps {words}')

    # T6
    p = subprocess.run(['git', 'check-ignore', 'tunnels/paper.env'], cwd=ROOT, stdout=subprocess.PIPE, text=True)
    check(p.returncode == 0, 'T6: tunnels/paper.env is not ignored by git')

    # T7
    sc = shutil.which('shellcheck')
    if sc:
        p = subprocess.run([sc, '-S', 'error', SCRIPT, os.path.join(ROOT, 'tunnels', 'run_pathfinder.sh'),
                            os.path.join(ROOT, 'tunnels', 'reproduce_small.sh')], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        check(p.returncode == 0, f'T7: shellcheck:\n{p.stdout}')
    else:
        print('T7: shellcheck not installed, skipped')

    # T8
    dt = time.time() - t0
    check(dt < 180, f'T8: {dt:.0f} s (limit 180 s)')

    if failures:
        print(f'{len(failures)} failure(s)')
        return 1
    print(f'tunnels_repro: ok ({dt:.0f} s)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
