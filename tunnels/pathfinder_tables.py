#!/usr/bin/env python3
"""Sections P0-P6 (Markdown): Tunnels on H_sp against the simulation of PathFinder, ALL SHORTEST WALKS.

Usage: python3 pathfinder_tables.py <dir> [--sp FILE] [--min-h-edges N] [--top-shapes K] > pathfinder.md
       python3 pathfinder_tables.py <dir> [--sp FILE] --paper [--adj FILE] [--mdb FILE --mdb-raw FILE] > paper.md

<dir> holds sp.csv (ring-tunnels --shortest; --sp FILE: another name, e.g. sp_early.csv of --early-stop) and, optionally, cv.csv (the same run in ALL WALKS): it is the
$OUT of run_wikidata.sh or a $OUT/gen-N-S of run_generated.sh. Read-only: nothing is written but stdout.
Valid rows: sem=shortest and status=ok (P1-P6 also pf_status=ok); the others are only counted in P0.
Configurations (model, budget) are taken in the order of their first appearance in sp.csv.
"per query" = once per (qid, kind, src, dst), first row; "median" = over the queries with H_edges >= --min-h-edges
(P6: over all). Sizes: KB = 10^3 and MB = 10^6 bytes; times: the CSV has seconds, the tables ms.
Every problem goes to stderr as "warning: ...". Exit code: 1 if sp.csv does not exist or has no sem=shortest row.
t_pf is the simulation of PathFinder's BFS on the same index, not MillenniumDB.

--paper prints only "Comparison with PathFinder": the space held after evaluation and the construction time
relative to PathFinder, with the numbers of the text. Rows used: sem=shortest, status=ok, pf_status=ok, model=region_rel, budget=4x+10, once per
(qid, kind, src, dst); the same key with another t_build or t_pf is an error (exit code 1). Ring is --sp,
Plain is --adj (same rules as --sp). --mdb results.csv and --mdb-raw raw.csv (join_pf.py and run_pf.py of
MillDB-PathFinder) add the MillenniumDB columns: the cold time of each n is the first row of raw.csv with
warmup=1, pf_status=ok and no error; the rows of results.csv with mdb_status=ok, dir=subj|obj (and a
non-empty sim_t_pf, if the column exists) are matched to our row by tb_key (CVp with dir=obj is our CVr
with src and dst swapped, and so on). Everything in the time table is a ratio of sums over the same rows.
"""
import argparse
import csv
import os
import statistics
import sys
from collections import defaultdict

NA = "n/a"
H0 = ("region", "0")
S4 = ("region_rel", "4x+10")
CLASSES = [("c-to-v", ("CV", "VC")), ("c-to-c", ("CVp", "VCp")), ("c-to-c (reverse)", ("CVr", "VCr")),
           ("v-to-v (sampled sources)", ("VVs",))]
KIND_CLASS = {k: name for name, kinds in CLASSES for k in kinds}
KEY = ("qid", "kind", "src", "dst")

_warned = set()


def warn(msg):
    if msg not in _warned:
        _warned.add(msg)
        print(f"warning: {msg}", file=sys.stderr)


def read(path):
    try:
        with open(path, newline="") as fh:
            return list(csv.DictReader(fh))
    except FileNotFoundError:
        return None


def num(r, k):
    v = r.get(k)
    return float(v) if v not in ("", None) else 0.0


def total(rows, k):
    return sum(num(r, k) for r in rows)


def has(rows, k):
    return any(r.get(k) not in ("", None) for r in rows)


def mean(rows, k, scale=1.0):
    """mean of a column (sum / rows) times scale; None if there are no rows or the column is empty in all of them"""
    if not rows or not has(rows, k):
        return None
    return total(rows, k) / len(rows) * scale


def ratio(a, b, scale=1.0):
    return a / b * scale if b else None


def fmt(x, d=1):
    if x is None:
        return NA
    return f"{x:,.{d}f}" if abs(x) >= 1000 else f"{x:.{d}f}"


def fx(x, d=2):
    return NA if x is None else fmt(x, d) + "x"


def med(xs):
    return statistics.median(xs) if xs else None


def table(head, body):
    out = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    out += ["| " + " | ".join(str(c).replace("|", "\\|") for c in r) + " |" for r in body]
    return "\n".join(out) + "\n"


def section(title, body, notes=()):
    s = f"## {title}\n\n{body if body else 'no data'}\n"
    for n in notes:
        s += f"\n{n}\n"
    return s + "\n"


def qkey(r):
    return tuple(r.get(k, "") for k in KEY)


def cfg_of(r):
    return (r.get("model", ""), r.get("budget", ""))


def cfg_name(c):
    return f"{c[0]} {c[1]}"


def per_query(rows):
    """first row of each query key, in order of appearance"""
    seen = {}
    for r in rows:
        seen.setdefault(qkey(r), r)
    return list(seen.values())


class Data:
    def __init__(self, rows, min_h, top):
        self.min_h = min_h
        self.top = top
        self.every = rows   # any sem: the rows with status != ok have it empty (P0 counts them)
        self.all = [r for r in rows if r.get("sem") == "shortest"]
        self.valid = [r for r in self.all if r.get("status") == "ok" and r.get("pf_status") == "ok"]
        self.configs = []
        for r in self.valid:
            if cfg_of(r) not in self.configs:
                self.configs.append(cfg_of(r))

    def classes(self, rows):
        """[(class name, rows of the class)] in the fixed order, without the empty classes"""
        out = []
        for name, kinds in CLASSES:
            rs = [r for r in rows if r.get("kind") in kinds]
            if rs:
                out.append((name, rs))
        return out

    def big(self, r):
        return num(r, "H_edges") >= self.min_h

    def s_config(self):
        """configuration of the S columns of P4 and P6: region_rel 4x+10 or, if it does not exist, the last one"""
        if S4 in self.configs:
            return S4
        return self.configs[-1] if self.configs else None


def ms(x, d=3):
    return fmt(x * 1e3, d) if x is not None else NA


# ------------------------------------------------------------------ P0
def p0(d):
    body = []
    pf_vals = defaultdict(int)
    for name, rows in d.classes(d.every):
        qs = per_query(rows)
        ok = [q for q in qs if q.get("status") == "ok"]
        valid = [q for q in ok if q.get("pf_status") == "ok"]
        vrows = [r for r in rows if r.get("status") == "ok" and r.get("pf_status") == "ok"]
        c_pf = sum(1 for q in ok if q.get("pf_status") != "ok")
        c_chk = sum(1 for q in valid if q.get("pf_check") != "1")
        c_ver = sum(1 for r in vrows if r.get("verify") != "1")
        for q in ok:
            pf_vals[q.get("pf_status", "")] += 1
        body.append([name, len(qs), len(qs) - len(ok), c_pf, c_chk, c_ver])
        if c_chk:
            warn(f"{name}: {c_chk} queries with pf_check != 1")
        if c_ver:
            warn(f"{name}: {c_ver} rows with verify != 1")
    vals = dict(sorted(pf_vals.items()))
    return section("P0. Validity", table(["class", "queries", "status != ok", "pf_status != ok", "pf_check != 1",
                                          "verify != 1"], body), [f"pf_status values: {vals}"])


# ------------------------------------------------------------------ P1
def p1(d):
    head = ["class", "queries", "PathFinder", "PathFinder structs", "plain H_sp", "differential H_sp"]
    head += [f"S ({cfg_name(c)})" for c in d.configs]
    body, rbody = [], []
    for name, rows in d.classes(d.valid):
        qs = per_query(rows)
        row = [name, len(qs)] + [fmt(total(qs, k) / 1e6, 3) for k in ("pf_bytes", "pf_bytes_structs")]
        row += [fmt(total(qs, "Hplain_bits") / 8e6, 3), fmt(total(qs, "H_bits") / 8e6, 3)]
        for c in d.configs:
            row.append(fmt(total([r for r in rows if cfg_of(r) == c], "S_bits") / 8e6, 3))
        body.append(row)
        for c in d.configs:
            rs = [r for r in rows if cfg_of(r) == c]
            if not rs:
                continue
            s = total(rs, "S_bits") / 8
            m = med([num(r, "pf_bytes") / (num(r, "S_bits") / 8) for r in rs if d.big(r) and num(r, "S_bits") > 0])
            rbody.append([name, cfg_name(c), fx(ratio(total(rs, "pf_bytes"), s), 1),
                          fx(ratio(total(rs, "pf_bytes_structs"), s), 1), fx(m, 1)])
    t = table(head, body) if body else ""
    t2 = table(["class", "config", "total PathFinder / S", "total structs / S", "median per query PathFinder / S"], rbody) if rbody else ""
    return section("P1. Space kept after evaluation (totals, MB)", t + ("\nPathFinder / S:\n\n" + t2 if t2 else ""))


# ------------------------------------------------------------------ P1b
def p1b(d):
    body = []
    for name, rows in d.classes(d.valid):
        qs = per_query(rows)
        st, hn, rn = total(qs, "pf_states"), total(qs, "H_nodes"), total(qs, "R_nodes")
        tr, he = total(qs, "pf_transitions"), total(qs, "H_edges")

        def plain(c):
            rs = [r for r in rows if cfg_of(r) == c]
            return ratio(total(rs, "Hplain_bits"), total(rs, "S_bits")) if rs else None

        body.append([name, len(qs), fmt(st, 0), fmt(rn, 0), fmt(hn, 0), fmt(ratio(st, rn), 2),
                     fmt(ratio(rn, hn), 2), fmt(ratio(st, hn), 2), fmt(tr, 0), fmt(he, 0), fmt(ratio(tr, he), 2), fmt(plain(H0), 2), fmt(plain(S4), 2)])
    head = ["class", "queries", "PathFinder states", "R nodes", "H_sp nodes", "automaton: states / R nodes",
            "pruning: R / H_sp nodes", "states / H_sp nodes", "PathFinder transitions", "H_sp edges",
            "transitions / H_sp edges", "plain H_sp / S (W=0)", "plain H_sp / S (4x+10)"]
    return section("P1b. Where the difference comes from: pruning vs compression", table(head, body) if body else "",
                   ["H_sp and R use the reverse Glushkov NFA; PathFinder uses the minimal DFA of the same language."])


# ------------------------------------------------------------------ P2, P2b, P3
def by_class_cfg(d):
    for name, rows in d.classes(d.valid):
        for c in d.configs:
            rs = [r for r in rows if cfg_of(r) == c]
            if rs:
                yield name, c, rs


def p2(d):
    body = []
    for name, c, rs in by_class_cfg(d):
        body.append([name, cfg_name(c), len(rs), ms(mean(rs, "t_pf")), ms(mean(rs, "t_build")),
                     fx(ratio(total(rs, "t_build"), total(rs, "t_pf"))), ms(mean(rs, "t_product")),
                     ms(mean(rs, "t_live")), ms(mean(rs, "t_cost")), ms(mean(rs, "t_select")), ms(mean(rs, "t_encode"))])
    head = ["class", "config", "queries", "PathFinder BFS (t_pf)", "build S (t_build)", "build / BFS",
            "of which R (t_product)", "H_sp (t_live)", "cost (t_cost)", "select (t_select)", "encode (t_encode)"]
    return section("P2. First evaluation: building S vs PathFinder's BFS (ms, means per query)",
                   table(head, body) if body else "",
                   ["t_pf is the simulation of PathFinder's BFS on the same index (not MillenniumDB)."])


def p2b(d):
    body = []
    for name, c, rs in by_class_cfg(d):
        faster = sum(1 for r in rs if num(r, "t_expand") < num(r, "t_pf"))
        body.append([name, cfg_name(c), len(rs), ms(mean(rs, "t_pf")), ms(mean(rs, "t_expand")),
                     fx(ratio(total(rs, "t_expand"), total(rs, "t_pf"))), ms(mean(rs, "t_H_expand")),
                     fmt(100 * faster / len(rs), 1)])
    head = ["class", "config", "queries", "re-run BFS (t_pf)", "decode S (t_expand)", "decode / BFS",
            "decode explicit H_sp (t_H_expand)", "queries with t_expand < t_pf (%)"]
    return section("P2b. Recovering the result: decoding S vs re-running the BFS (ms, means per query)",
                   table(head, body) if body else "")


def us(r, k):
    """a time of the CSV (seconds) in whole microseconds"""
    return round(num(r, k) * 1e6)


def break_even(extra, saving):
    """extra, saving: integers (microseconds)"""
    if extra <= 0:
        return "0"
    if saving <= 0:
        return "never"
    return str(-(-extra // saving))


def p3(d):
    body = []
    for name, c, rs in by_class_cfg(d):
        pf = sum(us(r, "t_pf") for r in rs)
        extra = sum(us(r, "t_build") for r in rs) - pf
        saving = pf - sum(us(r, "t_expand") for r in rs)
        never = sum(1 for r in rs if us(r, "t_expand") >= us(r, "t_pf"))
        body.append([name, cfg_name(c), ms(extra / 1e6), ms(saving / 1e6), break_even(extra, saving), fmt(100 * never / len(rs), 1)])
    head = ["class", "config", "extra build (ms)", "saving per reuse (ms)", "break-even reuses",
            "queries where S never pays off (%)"]
    return section("P3. Break-even: reuses after which S pays off", table(head, body) if body else "",
                   ["Reusing S k times costs t_build + k·t_expand; re-running costs (k+1)·t_pf."])


# ------------------------------------------------------------------ P4
def p4(d):
    c = d.s_config()
    title = "P4. By query shape (c-to-v only: kinds CV, VC)"
    if c is None:
        return section(title, "")
    title += f" (config: {cfg_name(c)})"
    rows = [r for r in d.valid if r.get("kind") in ("CV", "VC") and r.get("shape", "") != ""]
    rows = [r for r in rows if cfg_of(r) == c]
    cnt = defaultdict(int)
    for q in per_query(rows):
        cnt[q["shape"]] += 1
    top = sorted(cnt, key=lambda s: (-cnt[s], s))[:d.top]
    body = []
    for s in top:
        rs = [r for r in rows if r["shape"] == s]
        pf, sb = total(rs, "pf_bytes"), total(rs, "S_bits") / 8
        body.append([s, cnt[s], fmt(pf / 1e3, 2), fmt(sb / 1e3, 2), fx(ratio(pf, sb)),
                     fx(ratio(total(rs, "pf_states"), total(rs, "H_nodes"))),
                     fx(ratio(total(rs, "t_build"), total(rs, "t_pf"))),
                     fx(ratio(total(rs, "t_expand"), total(rs, "t_pf")))])
    head = ["shape", "queries", "PathFinder (KB)", "S (KB)", "PathFinder / S", "states / H_sp nodes", "build / BFS",
            "decode / BFS"]
    return section(title, table(head, body) if body else "")


# ------------------------------------------------------------------ P5
def p5(d, path):
    title = "P5. ALL WALKS vs ALL SHORTEST WALKS (same queries)"
    cv = read(path)
    if cv is None:
        warn(f"{path} not found")
        return section(title, f"no data: {path} missing")
    names = [n for n, _ in CLASSES[:3]]

    def rows_of(rows):
        return [r for r in rows if r.get("status") == "ok" and KIND_CLASS.get(r.get("kind")) in names]

    def keyed(rows):
        out = {}
        for r in rows:
            out.setdefault(qkey(r) + cfg_of(r), r)
        return out

    spr, cvr = rows_of(d.valid), rows_of(cv)
    sk, ck = keyed(spr), keyed(cvr)
    body = []
    for name in names:
        for c in d.configs:
            ks = [k for k in sk if KIND_CLASS[k[1]] == name and k[4:] == c and k in ck]
            if not ks:
                continue
            a = [ck[k] for k in ks]
            s = [sk[k] for k in ks]
            ha, hs = total(a, "H_edges"), total(s, "H_edges")
            body.append([name, cfg_name(c), len(ks), fmt(ha, 0), fmt(hs, 0), fmt(ratio(hs, ha), 3),
                         fmt(total(a, "S_bits") / 8e3, 2), fmt(total(s, "S_bits") / 8e3, 2),
                         fmt(ratio(total(a, "S_bits"), total(a, "H_bits"), 100), 2),
                         fmt(ratio(total(s, "S_bits"), total(s, "H_bits"), 100), 2)])
    head = ["class", "config", "matched queries", "H edges (all)", "H_sp edges", "H_sp / H edges", "S (all, KB)",
            "S (shortest, KB)", "S/H (all, %)", "S/H_sp (shortest, %)"]
    ua = sum(1 for k in sk if k not in ck)
    ub = sum(1 for k in ck if k not in sk)
    return section(title, table(head, body) if body else "",
                   [f"unmatched: {ua} rows of sp.csv and {ub} rows of cv.csv"])


# ------------------------------------------------------------------ P6
def p6(d):
    c = d.s_config()
    title = "P6. Number of shortest walks vs size of H_sp and S"
    if c is None:
        return section(title, "")
    title += f" (S config: {cfg_name(c)})"
    body = []
    for name, rows in d.classes(d.valid):
        qs = per_query(rows)
        walks = [num(q, "pf_paths") for q in qs]
        per_edge = [num(q, "pf_paths") / num(q, "H_edges") for q in qs if num(q, "H_edges") > 0]
        s = total([r for r in rows if cfg_of(r) == c], "S_bits") / 8e3
        body.append([name, len(qs), "%.3g" % med(walks), "%.3g" % max(walks), sum(1 for w in walks if w > 100000),
                     fmt(total(qs, "H_edges"), 0), fmt(s, 2), "%.3g" % med(per_edge) if per_edge else NA])
    head = ["class", "queries", "median shortest walks", "max shortest walks", "queries with > 100,000 walks",
            "total H_sp edges", "total S (KB)", "median walks / H_sp edges"]
    return section(title, table(head, body) if body else "")


# ------------------------------------------------------------------ --paper (comparison with PathFinder)
PAPER_CFG = S4
T9_CLASSES = [("c-to-v", ("CV", "VC")), ("c-to-c", ("CVp", "VCp"))]


class PaperError(Exception):
    pass


def paper_rows(rows, label):
    """{(qid, kind, src, dst): row} of the valid rows of the paper (first one of each key)"""
    out = {}
    for r in rows:
        if not (r.get("sem") == "shortest" and r.get("status") == "ok" and r.get("pf_status") == "ok"
                and cfg_of(r) == PAPER_CFG and r.get("t_build", "") != "" and r.get("t_pf", "") != ""):
            continue
        try:
            key = (r.get("qid", ""), r.get("kind", ""), int(r.get("src", "")), int(r.get("dst", "")))
        except ValueError:
            warn(f"{label}: row with a non-integer src or dst skipped (qid {r.get('qid', '')})")
            continue
        old = out.setdefault(key, r)
        if old is not r and any(old.get(k, "") != r.get(k, "") for k in ("t_build", "t_pf")):
            raise PaperError(f"{label}: key {key} repeated with another t_build or t_pf")
    return out


def tb_key(r):
    """key of our row that corresponds to a row of MDB's results.csv (None if the row has no direction)"""
    q, k, s, d = r["qid"], r["kind"], int(r["src"]), int(r["dst"])
    if k in ("CV", "VC"):
        return (q, k, s, d)
    own = "subj" if k == "CVp" else "obj"
    return (q, k, s, d) if r["dir"] == own else (q, {"CVp": "CVr", "VCp": "VCr"}[k], d, s)


def mdb_rows(results, raw):
    """[(class, tb_key, cold time of MDB in s)] of the rows of results.csv that have a cold time"""
    cold = {}
    for r in raw:
        if r.get("warmup") == "1" and r.get("pf_status") == "ok" and r.get("error") == "":
            try:
                cold.setdefault(r["n"], float(r["ns"]) / 1e9)
            except (KeyError, ValueError):
                warn(f"raw.csv: row of n={r.get('n')} with a bad ns skipped")
    kind_class = {k: name for name, ks in T9_CLASSES for k in ks}
    out = []
    for r in results:
        if r.get("kind") not in kind_class or r.get("mdb_status") != "ok" or r.get("dir") not in ("subj", "obj"):
            continue
        if r.get("n") not in cold or r.get("sim_t_pf", "x") == "":
            continue
        try:
            out.append((kind_class[r["kind"]], tb_key(r), cold[r["n"]]))
        except (KeyError, ValueError):
            warn(f"results.csv: row of n={r.get('n')} with a bad src or dst skipped")
    return out


def t9_sums(ours, mdb):
    """{class: (n, missing, sum t_build, sum t_pf, sum t_mdb or None)} of one of our CSVs"""
    res = {}
    for name, kinds in T9_CLASSES:
        if mdb is None:
            rs = [r for (q, k, s, d), r in ours.items() if k in kinds]
            res[name] = (len(rs), 0, total(rs, "t_build"), total(rs, "t_pf"), None)
        else:
            sel = [(ours.get(key), t) for c, key, t in mdb if c == name]
            ok = [(r, t) for r, t in sel if r is not None]
            res[name] = (len(ok), len(sel) - len(ok), total([r for r, _ in ok], "t_build"),
                         total([r for r, _ in ok], "t_pf"), sum(t for _, t in ok))
    return res


def kb(x):
    """a size in KB the way the paper prints it: 11,567 / 675 / 33.2 / 5.1 (3 decimals below 1)"""
    return NA if x is None else (f"{x:,.0f}" if abs(x) >= 100 else f"{x:.1f}" if abs(x) >= 1 else f"{x:.3f}")


def times(x):
    return "1×" if x == 1 else NA if x is None else kb(x) + "×"


def paper_table8(ring):
    rows = [r for (q, k, s, d), r in ring.items() if k in ("CV", "VC")]
    if not rows:
        return "no data: no valid c-to-v rows (CV, VC) in the Ring CSV\n"
    cols = [("PathFinder (total)", "pf_bytes", 1e3), ("PathFinder (structs)", "pf_bytes_structs", 1e3),
            ("plain H_sp", "Hplain_bits", 8e3), ("differential H_sp", "H_bits", 8e3), ("S", "S_bits", 8e3)]
    tot = [total(rows, k) / div for _, k, div in cols]   # KB, summed over the queries
    n = len(rows)
    s_tot = tot[4]
    out = f"c-to-v (CV, VC), {n} queries, config {cfg_name(PAPER_CFG)}, mean per query (KB = 10^3 B):\n\n"
    out += table([""] + [c for c, _, _ in cols],
                 [["KB per query"] + [kb(t / n) for t in tot], ["vs S"] + [times(ratio(t, s_tot)) for t in tot]])
    pf, st, pl, df = tot[:4]
    out += "\nFigures of the text (ratios of totals):\n\n"
    out += "\n".join(f"- {name}: {times(v) if v is not None else NA}" for name, v in (
        ("PathFinder / plain H_sp", ratio(pf, pl)), ("PathFinder structs / plain H_sp", ratio(st, pl)),
        ("plain H_sp / differential H_sp", ratio(pl, df)), ("PathFinder / differential H_sp", ratio(pf, df)),
        ("PathFinder structs / differential H_sp", ratio(st, df)), ("differential H_sp / S", ratio(df, s_tot))))
    out += f"\n- PathFinder states / H_sp nodes, c-to-v: {fmt(ratio(total(rows, 'pf_states'), total(rows, 'H_nodes')), 2)}\n"
    cc = [r for (q, k, s, d), r in ring.items() if k in ("CVp", "VCp")]
    if cc:
        out += (f"- c-to-c (CVp, VCp), {len(cc)} pairs: PathFinder states / H_sp nodes "
                f"{fmt(ratio(total(cc, 'pf_states'), total(cc, 'H_nodes')), 2)}; mean H_sp edges per pair "
                f"{fmt(mean(cc, 'H_edges'), 1)}; mean plain H_sp per pair {fmt(mean(cc, 'Hplain_bits', 1 / 8), 1)} B\n")
    else:
        out += "- c-to-c: no valid rows (CVp, VCp)\n"
    return out


def paper_table9(ring, plain, mdb):
    sums = {"Ring": t9_sums(ring, mdb)}
    if plain is not None:
        sums["Plain"] = t9_sums(plain, mdb)
    names = [n for n, _ in T9_CLASSES]
    head = ["", "t_b/t_pf c-to-v", "t_b/t_pf c-to-c", "t_b/t_MDB c-to-v", "t_b/t_MDB c-to-c"]
    body, tex = [], []
    for sys_ in ("Ring", "Plain"):
        sm = sums.get(sys_)
        cells = []
        if sm is not None:
            cells = [ratio(sm[c][2], sm[c][3]) for c in names] + [ratio(sm[c][2], sm[c][4]) if sm[c][4] else None for c in names]
        cells = cells or [None] * 4
        body.append([sys_] + [fmt(x, 2) for x in cells])
        tex.append(" & ".join([sys_] + [fmt(x, 2).replace("n/a", "--") for x in cells]) + r" \\")
    out = table(head, body)
    out += "\nRatios of sums over the same rows; t_MDB is n/a without --mdb and --mdb-raw.\n\nRows used:\n\n"
    rows = []
    for sys_, sm in sums.items():
        for c in names:
            n, miss, tb, tp, tm = sm[c]
            rows.append([sys_, c, n, miss, f"{tb:.6g}", f"{tp:.6g}", f"{tm:.6g}" if tm is not None else NA])
    out += table(["system", "class", "n", "missing", "sum t_build (s)", "sum t_pf (s)", "sum t_MDB (s)"], rows)
    lines = []
    for c in names:
        r_ = sums["Ring"][c]
        p_ = sums["Plain"][c] if "Plain" in sums else None
        mdb_ok = r_[4] is not None and r_[4] > 0
        both = p_ is not None and r_[0] > 0 and p_[0] > 0
        lines.append(f"- {c}: t_pf Ring / t_pf Plain {fmt(ratio(r_[3], p_[3]) if both else None, 2)}; "
                     f"t_MDB / t_pf Plain {fmt(ratio(p_[4], p_[3]) if p_ and p_[0] > 0 and p_[4] else None, 2)}; "
                     f"t_pf Ring / t_MDB {fmt(ratio(r_[3], r_[4]) if mdb_ok else None, 2)}")
    out += "\n" + "\n".join(lines) + "\n"
    out += ("\nLaTeX:\n\n```latex\n\\begin{tabular}{@{}l rr rr@{}}\n\\toprule\n"
            " & \\multicolumn{2}{c}{$t_b/t_{pf}$} & \\multicolumn{2}{c}{$t_b/t_{MDB}$} \\\\\n"
            " & c-to-v & c-to-c & c-to-v & c-to-c \\\\\n\\midrule\n" + "\n".join(tex) +
            "\n\\bottomrule\n\\end{tabular}\n```\n")
    return out


def paper(ring_rows, plain_rows, mdb):
    """text of the section "Comparison with PathFinder" (raises PaperError)"""
    ring = paper_rows(ring_rows, "Ring CSV")
    plain = paper_rows(plain_rows, "Plain CSV") if plain_rows is not None else None
    results = mdb_rows(*mdb) if mdb else None
    if plain is None:
        warn("no --adj: the Plain row of the time table is n/a")
    s = "## Comparison with PathFinder\n\n### Space held after evaluation (c-to-v)\n\n" + paper_table8(ring)
    s += "\n### Construction time relative to PathFinder (ratios of sums)\n\n" + paper_table9(ring, plain, results)
    return s + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dir")
    ap.add_argument("--sp", default="sp.csv", help="the --shortest CSV, relative to dir or absolute (default sp.csv)")
    ap.add_argument("--min-h-edges", type=int, default=1000)
    ap.add_argument("--top-shapes", type=int, default=12)
    ap.add_argument("--paper", action="store_true", help="print only the comparison with PathFinder (space and time tables)")
    ap.add_argument("--adj", help="with --paper: the --shortest CSV of adj-tunnels (Plain), as --sp")
    ap.add_argument("--mdb", help="with --paper: results.csv of join_pf.py (MillDB-PathFinder)")
    ap.add_argument("--mdb-raw", help="with --paper: raw.csv of run_pf.py (MillDB-PathFinder)")
    a = ap.parse_args()
    if (a.adj or a.mdb or a.mdb_raw) and not a.paper:
        ap.error("--adj, --mdb and --mdb-raw need --paper")
    if bool(a.mdb) != bool(a.mdb_raw):
        ap.error("--mdb and --mdb-raw go together")
    sp = os.path.join(a.dir, a.sp)   # absolute a.sp wins
    rows = read(sp)
    if rows is None:
        print(f"error: {sp} not found", file=sys.stderr)
        return 1
    if a.paper:
        plain, mdb = None, None
        if a.adj:
            plain = read(os.path.join(a.dir, a.adj))
            if plain is None:
                print(f"error: {os.path.join(a.dir, a.adj)} not found", file=sys.stderr)
                return 1
        if a.mdb:
            mdb = (read(a.mdb), read(a.mdb_raw))
            for path, x in zip((a.mdb, a.mdb_raw), mdb):
                if x is None:
                    print(f"error: {path} not found", file=sys.stderr)
                    return 1
        try:
            sys.stdout.write(paper(rows, plain, mdb))
        except PaperError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        return 0
    d = Data(rows, a.min_h_edges, a.top_shapes)
    if not d.all:
        print(f"error: {sp} has no row with sem=shortest", file=sys.stderr)
        return 1
    out = [p0(d), p1(d), p1b(d), p2(d), p2b(d), p3(d), p4(d), p5(d, os.path.join(a.dir, "cv.csv")), p6(d)]
    sys.stdout.write("\n".join(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
