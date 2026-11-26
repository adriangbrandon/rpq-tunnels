#!/usr/bin/env python3
"""The tables of the paper (Markdown) from the ring-tunnels CSVs.

Usage: python3 paper_tables.py <results dir> [--generated <gen-N-S dir>] [--min-h-edges N] > tables.md

<results dir> is the output of run_wikidata.sh (cv.csv, vv_bridge.csv, vv_ring.csv, vv_all.csv, nav/, adj/,
<backend>/index.log); --generated is a gen-N-S/ directory of run_generated.sh (the two tables of generated queries).
Only rows with status=ok count; "mean" = sum / number of rows considered. Every missing input is reported
on stderr ("warning: ...") and, in the table that needs it, as a line "no data: <path> missing".
Exit code: 1 if <results dir>/cv.csv is missing, 0 otherwise.

Units: CSV times are seconds; "GB" of the table of graph representations is 2^30 bytes (Maximum resident set size is in KiB).
--min-h-edges (default 1000, the threshold of the c-to-v table of the Ring) is there so that tiny test graphs can exercise it.
"""
import argparse
import csv
import os
import re
import statistics
import sys
from collections import defaultdict

NA = "n/a"
CFG = [("region", "0", "0"), ("region", "1000", "1000"), ("region", "10000", "10000"), ("region", "100000", "100000"),
       ("region", "inf", "inf"), ("region_rel", "1x+10", "1x+10"), ("region_rel", "4x+10", "4x+10"),
       ("region_rel", "16x+10", "16x+10")]
H0 = ("region", "0")
S4 = ("region_rel", "4x+10")

_cache = {}
_warned = set()
missing = []   # paths reported as "no data" in the table being written


def warn(msg):
    if msg not in _warned:
        _warned.add(msg)
        print(f"warning: {msg}", file=sys.stderr)


def load(path):
    """rows with status=ok of a CSV, or None (warning) if it does not exist"""
    if path not in _cache:
        try:
            with open(path, newline="") as fh:
                _cache[path] = [r for r in csv.DictReader(fh) if r.get("status") == "ok"]
        except FileNotFoundError:
            warn(f"{path} not found")
            _cache[path] = None
    return _cache[path]


def need(path):
    """rows of path; records the path in `missing` if absent"""
    rows = load(path)
    if rows is None:
        if path not in missing:
            missing.append(path)
        return []
    return rows


def num(r, k):
    v = r.get(k)
    return float(v) if v not in ("", None) else 0.0


def has(rows, k):
    return any(r.get(k) not in ("", None) for r in rows)


def sel(rows, kinds, cfg=None):
    return [r for r in rows if r["kind"] in kinds and (cfg is None or (r["model"], r["budget"]) == cfg)]


def total(rows, k):
    return sum(num(r, k) for r in rows)


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


def table(head, body):
    out = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    out += ["| " + " | ".join(str(c).replace("|", "\\|") for c in r) + " |" for r in body]
    return "\n".join(out) + "\n"


def section(title, body):
    global missing
    s = f"## {title}\n\n{body}"
    for p in missing:
        s += f"\nno data: {p} missing\n"
    missing = []
    return s + "\n"


def exists_all(R):
    """rows of vv_all.csv; if it does not exist, the concatenation of vv_bridge.csv and vv_ring.csv (warning)"""
    p = os.path.join(R, "vv_all.csv")
    rows = load(p)
    if rows is not None:
        return rows
    parts = [load(os.path.join(R, "vv_bridge.csv")), load(os.path.join(R, "vv_ring.csv"))]
    if all(x is None for x in parts):
        if p not in missing:
            missing.append(p)
        return []
    warn(f"{p} not found, using vv_bridge.csv + vv_ring.csv")
    return [r for x in parts if x for r in x]


# ------------------------------------------------------------------ graph representations
def parse_log(path):
    """(bytes per triple, build hours, peak GB) of an index.log; None for what is missing"""
    try:
        text = open(path, errors="replace").read()
    except FileNotFoundError:
        warn(f"{path} not found")
        return None, None, None
    m = re.findall(r"([0-9]+(?:\.[0-9]+)?)\s+bytes per triple", text)
    bpt = float(m[-1]) if m else None
    hours = None
    m = re.search(r"Elapsed \(wall clock\) time \(h:mm:ss or m:ss\):\s*([0-9:.]+)", text)
    if m:
        secs = 0.0
        for part in m.group(1).split(":"):
            secs = secs * 60 + float(part)
        hours = secs / 3600
    gb = None
    m = re.search(r"Maximum resident set size \(kbytes\):\s*([0-9]+)", text)
    if m:
        gb = int(m.group(1)) / 2 ** 20
    for v, what in ((bpt, "bytes per triple"), (hours, "Elapsed"), (gb, "Maximum resident set size")):
        if v is None:
            warn(f"{path}: no '{what}' line")
    return bpt, hours, gb


def table2(R):
    body = []
    for rep, sub in [("Ring", "basic"), ("RingAB", "split"), ("Compact", "nav"), ("Plain", "adj")]:
        bpt, h, gb = parse_log(os.path.join(R, sub, "index.log"))
        body.append([rep, fmt(bpt), fmt(h), fmt(gb, 0)])
    return section("Graph representations: space, construction time and peak memory",
                   table(["representation", "bytes/edge", "construction (h)", "peak memory (GB)"], body))


# ------------------------------------------------------------------ size of H and S
def table3(R):
    cv = need(os.path.join(R, "cv.csv"))
    vv = exists_all(R)
    classes = [("c-to-v", cv, ("CV", "VC")), ("c-to-c", cv, ("CVp", "VCp")), ("v-to-v", vv, ("VVr",)),
               ("v-to-v, all sources", vv, ("VVu",))]
    body = []
    for name, rows, kinds in classes:
        h = sel(rows, kinds, H0)
        s = sel(rows, kinds, S4)
        body.append([f"{name} ({len(h)} queries)", fmt(mean(h, "H_nodes", 1e-3), 3), fmt(mean(h, "H_edges", 1e-3), 3),
                     fmt(mean(h, "Hplain_bits", 1 / 8000), 2), fmt(mean(h, "H_bits", 1 / 8000), 2),
                     fmt(mean(s, "S_bits", 1 / 8000), 2)])
    return section("Size of H and S per query class (means)",
                   table(["class", "H nodes (thousands)", "H edges (thousands)", "plain H (KB)", "differential H (KB)",
                          "S, 4x+10 (KB)"], body))


# ------------------------------------------------------------------ construction time
def times(rows, kinds, scale):
    h = sel(rows, kinds, H0)
    s = sel(rows, kinds, S4)
    cost = None
    if s and all(has(s, k) for k in ("t_cost", "t_select", "t_encode")):
        cost = (total(s, "t_cost") + total(s, "t_select") + total(s, "t_encode")) / len(s) * scale
    return [fmt(mean(h, "t_product", scale), 2), fmt(mean(h, "t_live", scale), 2), fmt(cost, 2)]


def table4(R):
    P = lambda *a: os.path.join(R, *a)
    cvs = [("Ring", ""), ("Compact", "nav"), ("Plain", "adj")]
    body = []
    for cls, kinds in [("c-to-v", ("CV", "VC")), ("c-to-c", ("CVp", "VCp"))]:
        for rep, sub in cvs:
            body.append([cls, rep] + times(need(P(sub, "cv.csv")), kinds, 1e3))
    allsrc = exists_all(R)
    vvs = [("RingAB, Ring-RPQ split", exists_all(R), ("VVr",)),
           ("Ring, bridge split", need(P("vv_bridge.csv")), ("VVb",)),
           ("Compact, bridge split", need(P("nav", "vv_bridge.csv")), ("VVb",)),
           ("Plain, bridge split", need(P("adj", "vv_bridge.csv")), ("VVb",)),
           ("Ring, all sources", allsrc, ("VVu",))]
    for rep, rows, kinds in vvs:
        body.append(["v-to-v", rep] + times(rows, kinds, 1.0))
    return section("Construction time of R, H and S (ms for c-to-v and c-to-c, s for v-to-v; means)",
                   table(["class", "representation", "R", "H", "S"], body))


# ------------------------------------------------------------------ c-to-v on the Ring
def qkey(r):
    return (r["qid"], r["kind"], r["src"], r["dst"])


def table5(R, min_h):
    cv = need(os.path.join(R, "cv.csv"))
    kinds = ("CV", "VC")
    big = {qkey(r) for r in sel(cv, kinds, H0) if num(r, "H_edges") >= min_h}
    body = []
    for m, b, label in CFG:
        rs = [r for r in sel(cv, kinds, (m, b)) if qkey(r) in big]
        if not rs:
            body.append([label] + [NA] * 4)
            continue
        h = total(rs, "H_bits")
        med = statistics.median([num(r, "S_bits") / max(1, num(r, "H_bits")) for r in rs])
        small = sum(num(r, "S_bits") <= 0.01 * num(r, "H_bits") for r in rs) / len(rs)
        body.append([label, fmt(ratio(total(rs, "S_bits"), h, 100), 2), fmt(100 * med, 2), fmt(100 * small, 1),
                     fmt(ratio(total(rs, "t_expand"), total(rs, "t_H_expand")), 2)])
    return section(f"c-to-v on the Ring: space of S relative to H, queries with at least {min_h} H edges ({len(big)} queries)",
                   table(["configuration", "total S/H (%)", "median S/H per query (%)", "queries with S <= 1% of H (%)",
                          "expansion (S / explicit H)"], body))


# ------------------------------------------------------------------ generated queries
def table6(G):
    if G is None:
        return section("Generated queries relative to the explicit H", "no data: --generated missing\n")
    cv = need(os.path.join(G, "cv.csv"))
    vv = exists_all(G)
    classes = [("c-to-v", cv, ("CV", "VC"), 1e-3, "KB", 1e3, "ms", 10000),
               ("c-to-c", cv, ("CVp", "VCp"), 1e-3, "KB", 1e3, "ms", 10000),
               ("v-to-v", vv, ("VVr",), 1e-6, "MB", 1.0, "s", 1000)]
    head = ["row"]
    for name, _, _, _, su, _, tu, _ in classes:
        head += [f"{name} space", f"{name} time"]
    body = [["explicit H"]]
    for name, rows, kinds, ss, su, ts, tu, largest in classes:
        h = sel(rows, kinds, H0)
        v = mean(h, "H_bits", ss / 8)
        t = mean(h, "t_H_expand", ts)
        body[0] += [f"{fmt(v)} {su}" if v is not None else NA, f"{fmt(t, 2)} {tu}" if t is not None else NA]
    configs = [("W=0", lambda L: ("region", "0")), ("largest W", lambda L: ("region", str(L))),
               ("1x+10", lambda L: ("region_rel", "1x+10")), ("4x+10", lambda L: ("region_rel", "4x+10")),
               ("16x+10", lambda L: ("region_rel", "16x+10")), ("inf", lambda L: ("region", "inf"))]
    for label, cfgf in configs:
        row = [label if label != "largest W" else "largest W (10000; 1000 in v-to-v)"]
        found = False
        for name, rows, kinds, ss, su, ts, tu, largest in classes:
            rs = sel(rows, kinds, cfgf(largest))
            found |= bool(rs)
            row += [fmt(ratio(total(rs, "S_bits"), total(rs, "H_bits"), 100), 2) + " %" if rs else NA,
                    fmt(ratio(total(rs, "t_expand"), total(rs, "t_H_expand")), 2) + "x" if rs else NA]
        if found or label != "1x+10":   # 1x+10 only if it exists
            body.append(row)
    return section("Generated queries relative to the explicit H", table(head, body))


def table7(G):
    if G is None:
        return section("Generated c-to-v queries by shape", "no data: --generated missing\n")
    cv = need(os.path.join(G, "cv.csv"))
    kinds = ("CV", "VC")
    by = defaultdict(lambda: defaultdict(list))
    for r in sel(cv, kinds):
        by[r["shape"]][(r["model"], r["budget"])].append(r)
    shapes = sorted(by, key=lambda s: (-len(by[s].get(H0, [])), s))[:12]
    body = []
    for s in shapes:
        d = by[s]
        h = d.get(H0, [])
        hb = total(h, "H_bits")

        def pct(cfg):
            rs = d.get(cfg, [])
            return fmt(ratio(total(rs, "S_bits"), total(rs, "H_bits"), 100), 2) if rs else NA

        def tr(cfg):
            rs = d.get(cfg, [])
            return fmt(ratio(total(rs, "t_expand"), total(rs, "t_H_expand")), 2) if rs else NA

        body.append([s, len(h), fmt(total(h, "H_edges") / 1e6, 3), fmt(hb / 8e6, 2), pct(H0), pct(("region", "10000")),
                     pct(S4), tr(S4), tr(("region", "inf"))])
    return section("Generated c-to-v queries by shape (the 12 most frequent)",
                   table(["shape", "n", "H edges (millions)", "explicit H (MB)", "S, W=0 (% of H)", "S, W=10^4 (% of H)",
                          "S, 4x+10 (% of H)", "time, 4x+10", "time, inf"], body))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("results")
    ap.add_argument("--generated")
    ap.add_argument("--min-h-edges", type=int, default=1000)
    a = ap.parse_args()
    cv = os.path.join(a.results, "cv.csv")
    if not os.path.isfile(cv):
        print(f"error: {cv} not found", file=sys.stderr)
        return 1
    G = a.generated
    if G is not None and not os.path.isdir(G):
        warn(f"{G} is not a directory")
        G = None
    out = [table2(a.results), table3(a.results), table4(a.results), table5(a.results, a.min_h_edges), table6(G), table7(G)]
    sys.stdout.write("\n".join(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
