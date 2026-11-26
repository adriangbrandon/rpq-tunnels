#!/usr/bin/env python3
"""Characterization test of the tunnels binaries (src/ring-tunnels.cpp).

On tests/data/metro it runs adj-tunnels, ring-tunnels and ring-tunnels-split, each in its normal
mode, with --shortest and with --shortest --early-stop (with pairs), drops the columns whose name
starts with "t_" (times) and compares the number of rows and the SHA-256 of the remaining CSV with
tests/golden/tunnels/<binary>.<mode>.golden. The exit code must be 0.  The indexes are built in a
temporary directory; nothing is written in tests/data.

Usage:
  tests/tunnels_regression.py --build-dir build            # check
  tests/tunnels_regression.py --build-dir build --update   # re-record the golden files
"""

import argparse
import csv
import hashlib
import io
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
# binary -> index builder that produces the index it reads (None: reads the .dat directly)
BINARIES = {'adj-tunnels': None,
            'ring-tunnels': 'build-index-basic',
            'ring-tunnels-split': 'build-index-split'}
MODES = {'normal': [], 'shortest': ['--shortest'],
         'early': ['--shortest', '--early-stop', '--pairs', '2', '--pair-reverse']}


def stable_csv(path):
    """The CSV without the t_* columns, re-serialised with LF line ends: (data rows, sha256)."""
    with open(path, newline='') as f:
        rows = list(csv.reader(f))
    keep = [i for i, c in enumerate(rows[0]) if not c.startswith('t_')]
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator='\n')
    for r in rows:
        w.writerow([r[i] if i < len(r) else '' for i in keep])
    return len(rows) - 1, hashlib.sha256(buf.getvalue().encode()).hexdigest()


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--build-dir', required=True)
    ap.add_argument('--data', default=os.path.join(HERE, 'data', 'metro'))
    ap.add_argument('--golden', default=os.path.join(HERE, 'golden', 'tunnels'))
    ap.add_argument('--update', action='store_true', help='re-record the golden files')
    args = ap.parse_args()

    build = os.path.abspath(args.build_dir)
    work = tempfile.mkdtemp(prefix='ring-tunnels-regression-')
    failures = 0
    try:
        for binary, builder in BINARIES.items():
            wd = os.path.join(work, binary)
            os.makedirs(wd)
            for name in ('graph.dat', 'graph.dat.SO', 'graph.dat.P'):
                shutil.copy(os.path.join(args.data, name), wd)
            dat = os.path.join(wd, 'graph.dat')
            if builder:
                subprocess.run([os.path.join(build, builder), dat], check=True,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            for mode, extra in MODES.items():
                out = os.path.join(wd, f'{mode}.csv')
                proc = subprocess.run(
                    [os.path.join(build, binary), dat, os.path.join(args.data, 'queries.tsv'),
                     '--out', out, '--reps', '1'] + extra,
                    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
                name = f'{binary}.{mode}'
                if proc.returncode != 0:
                    print(f'FAIL {name}: exit code {proc.returncode}\n{proc.stderr[-500:]}')
                    failures += 1
                    continue
                rows, digest = stable_csv(out)
                text = f'rows {rows}\nsha256 {digest}\n'
                gpath = os.path.join(args.golden, f'{name}.golden')
                if args.update:
                    os.makedirs(args.golden, exist_ok=True)
                    with open(gpath, 'w') as f:
                        f.write(text)
                    print(f'recorded {name}: {rows} rows {digest[:16]}')
                    continue
                if not os.path.exists(gpath):
                    print(f'FAIL {name}: missing golden {gpath}')
                    failures += 1
                    continue
                with open(gpath) as f:
                    expected = f.read()
                if expected != text:
                    print(f'FAIL {name}: expected {expected.split()[1]} rows {expected.split()[3][:16]}, '
                          f'got {rows} rows {digest[:16]}')
                    failures += 1
                else:
                    print(f'ok   {name}: {rows} rows')
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
