"""Figures (ctov.pdf, ctoc.pdf, vtov.pdf) and numbers.md for the paper from the ring-tunnels CSVs.
Usage: python3 paper_figures.py <results dir> <out dir>

<results dir> is the output of run_wikidata.sh (or one gen-N-S/ of run_generated.sh): cv.csv (Ring, with
the CVp/VCp/CVr/VCr pair rows), vv_bridge.csv (VVb and VVu rows), vv_ring.csv, vv_all.csv, and nav/, adj/.
Every missing file, series or configuration is reported on stderr as a line starting with "warning:".
Exit code: 1 if <results dir>/cv.csv is missing, 0 otherwise. matplotlib is optional: without it numbers.md
is still written and the figures are skipped.
"""
import csv, sys, os, statistics, math
from collections import defaultdict

R, OUT = sys.argv[1], sys.argv[2]
if not os.path.isfile(os.path.join(R, "cv.csv")):
    print(f"error: {os.path.join(R, 'cv.csv')} not found", file=sys.stderr)
    sys.exit(1)
os.makedirs(OUT, exist_ok=True)


def warn(msg):
    print(f"warning: {msg}", file=sys.stderr)


plt = matplotlib = None


def init_mpl():
    """import matplotlib lazily (only the functions that draw call this); False if it is not available"""
    global plt, matplotlib
    if plt is not None: return True
    try:
        import matplotlib as mpl
        mpl.use("Agg")
        import matplotlib.pyplot as pl
        import matplotlib.ticker  # noqa: F401  (used as matplotlib.ticker.MaxNLocator)
    except ImportError:
        return False
    matplotlib, plt = mpl, pl
    plt.rcParams.update({"font.family": "serif", "font.size": 7.5, "axes.edgecolor": MUTED, "axes.labelcolor": INK,
                         "xtick.color": MUTED, "ytick.color": MUTED, "axes.linewidth": 0.6, "legend.frameon": False})
    return True


BACK = [("Ring", "", "#2a78d6", "o"), ("Compact", "nav/", "#eb6834", "s"), ("Plain", "adj/", "#1baf7a", "^")]
INK, MUTED, GRID = "#0b0b0b", "#52514e", "#e4e3df"
CFG = [("region", "0", "0"), ("region", "1000", "$10^3$"), ("region", "10000", "$10^4$"), ("region", "100000", "$10^5$"),
       ("region", "inf", r"$\infty$"), ("region_rel", "1x+10", "1x+10"), ("region_rel", "4x+10", "4x+10"),
       ("region_rel", "16x+10", "16x+10")]
f = lambda r, k: float(r[k]) if r.get(k) not in ("", None) else 0.0


_CACHE = {}


def load(path):
    """rows of a CSV, or None (with a warning, once) if the file does not exist"""
    if path not in _CACHE:
        try:
            with open(path, newline="") as fh: _CACHE[path] = list(csv.DictReader(fh))
        except FileNotFoundError:
            warn(f"{path} not found")
            _CACHE[path] = None
    return _CACHE[path]


def agg(rows, kinds, who=None):
    ok = [r for r in rows if r["status"] == "ok" and r["kind"] in kinds]
    res = {}
    for m, b, _ in CFG:
        rs = [r for r in ok if r["model"] == m and r["budget"] == b]
        if not rs:
            if who: warn(f"{who}: no ok rows for configuration {m}/{b}")
            continue
        big = [r for r in rs if f(r, "H_edges") >= 1000]
        res[(m, b)] = dict(n=len(rs), S=sum(f(r, "S_bits") for r in rs), H=sum(f(r, "H_bits") for r in rs),
                           Hg=sum(f(r, "H_bits_global") for r in rs), He=sum(f(r, "H_edges") for r in rs),
                           tb=sum(f(r, "t_build") for r in rs), tx=sum(f(r, "t_expand") for r in rs),
                           tHx=sum(f(r, "t_H_expand") for r in rs), tr=sum(max(0, f(r, "t_ring")) for r in rs),
                           tp=sum(f(r, "t_product") for r in rs),
                           med=statistics.median([f(r, "S_bits") / max(1, f(r, "H_bits")) for r in big]) if big else 0,
                           le1=sum(f(r, "S_bits") <= 0.01 * f(r, "H_bits") for r in big) / max(1, len(big)),
                           nbig=len(big),
                           medx=statistics.median([f(r, "t_expand") / max(1e-9, f(r, "t_H_expand")) for r in big]) if big else 0,
                           vis=sum(f(r, "dec_visited") for r in rs) / max(1, sum(f(r, "H_nodes") for r in rs)))
    return res


SHOW = {("region", "0"): "0", ("region", "10000"): "$10^4$", ("region", "inf"): r"$\infty$", ("region_rel", "4x+10"): "4x"}
OFF = {"0": (3, -6.5), "H": (3, 1.5), "$10^4$": (-4, 3.5), "4x": (3, 3.5), "$10^3$": (3, 3.5), r"$\infty$": (3, 1.5)}
NOTAG = {"bridge split (Ring)"}
OFF_SERIES = {"Ring, best orientation": {r"$\infty$": (3, -7)}, "PMR of all sources (Ring)": {"4x": (3, 1.5)}, "Ring-RPQ split (RingAB)": {"4x": (3, -6.5), r"$\infty$": (3, -6.5)}, "per source (Ring)": {"4x": (3, 1.5), "$10^3$": (-4, 3.5)}}


def logfmt(v):
    return f"{v:g}" if 1e-2 <= v < 1e5 else f"$10^{{{int(round(math.log10(v)))}}}$"


def _slash(a, where, horiz):
    """small diagonal break marks on an axis edge"""
    kw = dict(transform=a.transAxes, color=MUTED, clip_on=False, lw=0.6)
    d = 0.018
    if horiz:   # break on the bottom/top edge of a y band, at x=0 of the panel
        y = 0 if where == "bottom" else 1
        a.plot([-d, d], [y - 2 * d, y + 2 * d], **kw)
    else:       # break on the x axis, at the right edge (where="right") or left edge of a panel
        x = 1 if where == "right" else 0
        a.plot([x - d, x + d], [-2 * d, 2 * d], **kw)


DISPLAY = {"Ring": "Ring graph", "Compact": "Compact graph", "Plain": "Plain graph",
           "Ring, best orientation": "Ring graph, best orientation", "plain H": "plain H (no graph)",
           "Ring-RPQ split (RingAB)": "Ring-RPQ split (RingAB graph)", "bridge split (Ring)": "bridge split (Ring graph)",
           "bridge split (Plain)": "bridge split (Plain graph)", "PMR of all sources (Ring)": "PMR of all sources (Ring graph)",
           "plain H, all sources": "plain H, all sources (no graph)", "plain H, factorized": "plain H, factorized (no graph)"}
NQ = None   # when set, x is the average space per query, in KB


def tradeoff(series, xmax, yranges, xref=None, xref_label="plain H, global ids", legend_cols=3, refline=False):
    if NQ:
        k = 1000.0 / NQ; series = [(a, b, c, [(x * k, y * k, t) for x, y, t in pts]) for a, b, c, pts in series]
        xmax *= k; xref = xref * k if xref else xref; yranges = [(l * k, h * k) for l, h in yranges]
    """Linear axes. series: (label, color, marker, [(space MB, time s, tag)]);
    yranges: (lo, hi) bands, top first, which break the y axis; xref: x of a dotted reference line far
    to the right, shown in a narrow panel after a break of the x axis."""
    nr = len(yranges); nc = 2 if xref else 1
    hr = [max(0.7, 1.0) for _ in yranges]
    fig, axs = plt.subplots(nr, nc, figsize=(3.4, 1.9 + 0.45 * (nr - 1)), sharey="row", sharex="col", layout="constrained",
                            squeeze=False, gridspec_kw={"height_ratios": hr, **({"width_ratios": [6, 1.8]} if xref else {})})
    seen = set()
    for lab, col, mk, pts in series:
        for x, y, tag in pts:
            row = next(k for k, (l, h) in enumerate(yranges) if l <= y <= h)
            a = axs[row][1] if (xref and x > xmax * 1.5) else axs[row][0]
            a.scatter([x], [y], color=col, marker=mk, s=13, zorder=3, linewidths=0, label=DISPLAY.get(lab, lab) if lab not in seen else None)
            seen.add(lab)
            if lab in NOTAG: continue
            dx, dy = OFF_SERIES.get(lab, {}).get(tag, OFF.get(tag, (3, 1.5)))
            a.annotate(tag, (x, y), xytext=(dx, dy), textcoords="offset points", fontsize=5.4, color=INK,
                       ha="right" if dx < 0 else "left")
    for row, (lo, hi) in enumerate(yranges):
        pad = 0.12 * (hi - lo) if hi > lo else 0.5 * lo
        for a in axs[row]:
            a.set_ylim(lo - pad, hi + pad)
            a.grid(color=GRID, lw=0.5); a.set_axisbelow(True)
            for sp in ("top", "right"): a.spines[sp].set_visible(False)
            if row < nr - 1:
                a.spines["bottom"].set_visible(False); a.tick_params(axis="x", length=0)
        axs[row][0].yaxis.set_major_locator(matplotlib.ticker.MaxNLocator(3 if nr > 1 else 5))
        if row < nr - 1: _slash(axs[row][0], "bottom", True)
        if row > 0: _slash(axs[row][0], "top", True); axs[row][0].spines["top"].set_visible(False)
        if xref:
            b = axs[row][1]
            b.spines["left"].set_visible(False); b.tick_params(axis="y", length=0)
            if refline: b.axvline(xref, color=MUTED, lw=0.8, ls=(0, (1, 1.5)))
    a = axs[-1][0]
    a.set_xlim(-0.03 * xmax, xmax * 1.12)
    if xref:
        far = [p[0] for sr in series for p in sr[3] if p[0] > xmax * 1.5]; hi_x = max([xref] + far)
        b = axs[-1][1]; b.set_xlim(min([xref] + far) * 0.85, hi_x * 1.15); xt = sorted(set(([xref] if refline else []) + far))
        b.set_xticks(xt); b.set_xticklabels([f"{v/1000:.0f}k" if v >= 10000 else f"{v:,.0f}" for v in xt], fontsize=5.5, rotation=40 if len(xt) > 2 else 0, ha="right" if len(xt) > 2 else "center")
        _slash(a, "right", False); _slash(b, "left", False)
        if refline: axs[0][1].text(0.5, 1.0, xref_label, rotation=90, fontsize=5.4, color=MUTED, transform=axs[0][1].transAxes,
                       va="top", ha="right")
    fig.supylabel("average expansion time per query (ms)" if NQ else "expansion time (s)", fontsize=7.5); fig.supxlabel("average space per query (KB)" if NQ else "space (MB)", fontsize=7.5)
    h, l = [], []
    for a in axs.flat:
        hh, ll = a.get_legend_handles_labels(); h += hh; l += ll
    order = [DISPLAY.get(sr[0], sr[0]) for sr in series]; hl = sorted(zip(h, l), key=lambda z: order.index(z[1])); h = [z[0] for z in hl]; l = [z[1] for z in hl]
    fig.legend(h, l, loc="lower center", bbox_to_anchor=(0.55, 1.0), ncol=legend_cols, fontsize=6.3, handletextpad=0.3, columnspacing=1.2, borderaxespad=0.6)
    return fig, axs


def bands(ts, gap=2.0):
    """group times into y bands, splitting where consecutive values differ by a factor > gap"""
    ts = sorted(ts); g = [[ts[0]]]
    for t in ts[1:]:
        if t > g[-1][-1] * gap: g.append([t])
        else: g[-1].append(t)
    return [(x[0], x[-1]) for x in reversed(g)]


# plain H laid out for traversal (dense ids, array of successors): (MB, expansion s). The runs emit it
# (columns Hplain_bits, t_Hplain_expand); these fixed values, "measured separately", are only a fallback.
PLAINH_FIXED = {"ctov.pdf": (1050.8, 1.5), "ctoc.pdf": (106.2, 0.33)}


def plainh(fname, kinds, rows):
    """(MB, s) of the plain H of the Ring's region/0 rows of the class, from the CSV; the fixed values if empty"""
    rs = [r for r in rows if r["status"] == "ok" and r["kind"] in kinds and r["model"] == "region" and r["budget"] == "0"]
    if any(r.get("Hplain_bits") not in ("", None) for r in rs):
        return sum(f(r, "Hplain_bits") for r in rs) / 8e6, sum(f(r, "t_Hplain_expand") for r in rs)
    warn(f"{fname}: columns Hplain_bits/t_Hplain_expand empty or no region/0 rows in cv.csv, using the fixed plain H values")
    return PLAINH_FIXED[fname]


def oriented(rows):
    """c-to-c pairs from cv.csv: (forward rows, rows of the orientation with the smaller product graph)"""
    fw = [r for r in rows if r["kind"] in ("CVp", "VCp")]; rv = [r for r in rows if r["kind"] in ("CVr", "VCr")]
    key = lambda r, rev: (r["qid"], r["dst"], r["src"]) if rev else (r["qid"], r["src"], r["dst"])
    Rf = {key(r, False): f(r, "R_nodes") for r in fw if r["budget"] == "0" and r["model"] == "region"}
    Rr = {key(r, True): f(r, "R_nodes") for r in rv if r["budget"] == "0" and r["model"] == "region"}
    best = [r for r in fw if Rf.get(key(r, False), 0) <= Rr.get(key(r, False), 1e30)]
    best += [dict(r, kind="CVp") for r in rv if Rr.get(key(r, True), 1e30) < Rf.get(key(r, True), 0)]
    return fw, best


def class_data(name, kinds, fname):
    """aggregates and plot series of one class (c-to-v / c-to-c); None if the Ring has no region/0 rows for it"""
    data = {}
    for lab, sub, col, mk in BACK:
        path = os.path.join(R, sub, "cv.csv")
        rows = load(path)   # warns if missing
        if rows is None:
            warn(f"{name}: series {lab} skipped ({path} missing)")
            continue
        data[lab] = agg(rows, kinds, who=f"{name}, {lab}")
    ring_rows = load(os.path.join(R, "cv.csv"))
    if ("region", "0") not in data.get("Ring", {}):
        warn(f"{name}: no ok region/0 rows of kinds {','.join(kinds)} in {os.path.join(R, 'cv.csv')}, class skipped")
        return None
    rev = False
    if fname == "ctoc.pdf":   # the Ring as built, and from the better endpoint (CVr/VCr rows of the same cv.csv)
        if any(r["kind"] in ("CVr", "VCr") for r in ring_rows):
            fw, best = oriented(ring_rows)
            data["Ring, best orientation"] = agg(best, kinds, who=f"{name}, Ring, best orientation"); rev = True
        else:
            warn(f"{name}: no CVr/VCr rows in cv.csv, series 'Ring, best orientation' skipped")
    series = []
    for lab, sub, col, mk in BACK + ([("Ring, best orientation", "", "#0b3d91", "P")] if rev else []):
        if lab not in data: continue
        d = data[lab]
        if ("region", "0") not in d:
            warn(f"{name}: series {lab} skipped (no region/0 rows)")
            continue
        pts = [(d[k]["S"] / 8e6 if k[1] != "inf" else 0, d[k]["tx"], t) for k, t in SHOW.items() if k in d]
        pts.append((d[("region", "0")]["H"] / 8e6, d[("region", "0")]["tHx"], "H"))
        if lab == "Ring, best orientation": pts = [p for p in pts if p[2] == r"$\infty$"]
        series.append((lab, col, mk, pts))
    ph = None
    if fname in PLAINH_FIXED:
        ph = plainh(fname, kinds, ring_rows)
        series.append(("plain H", INK, "D", [(ph[0], ph[1], "")]))
    return data, series, ph, data["Ring"][("region", "0")]["n"]


def draw_class(series, ph, nq, fname):
    global NQ
    allS = [p[0] for s_ in series for p in s_[3] if not s_[0].startswith("plain H")]; allT = [p[1] for s_ in series for p in s_[3]]
    NQ = nq
    fig, axs = tradeoff(series, max(allS), bands(allT), xref=ph[0], legend_cols=2 if len(series) > 4 else 3)
    fig.savefig(os.path.join(OUT, fname), bbox_inches="tight", pad_inches=0.03); plt.close(fig)


draws = []   # (function, args) of the figures, drawn at the end if matplotlib is available
out = open(os.path.join(OUT, "numbers.md"), "w")
for name, kinds, fname in [("c-to-v", ("CV", "VC"), "ctov.pdf"), ("c-to-c", ("CVp", "VCp"), "ctoc.pdf")]:
    res = class_data(name, kinds, fname)
    out.write(f"\n## {name}\n")
    if res is None:
        out.write("\nno data\n")
        continue
    data, series, ph, nq = res
    draws.append((draw_class, (series, ph, nq, fname)))
    for lab in data:
        d = data[lab]
        if ("region", "0") not in d: continue
        b0 = d[("region", "0")]
        out.write(f"\n### {lab}: {b0['n']} queries ({b0['nbig']} with >=1000 H edges), H {b0['He']:,.0f} edges, explicit {b0['H']/8e6:.1f} MB "
                  f"(global {b0['Hg']/8e6:.1f} MB), own evaluation {b0['tr']:.0f} s, product {b0['tp']:.0f} s, explicit expansion {b0['tHx']:.1f} s\n")
        out.write("| config | S MB | S/H | S/Hglobal | median q | q<=1% | build s | expand s | /explicit | median /explicit | visited/H |\n|---|---|---|---|---|---|---|---|---|---|---|\n")
        for m, b, l in CFG:
            if (m, b) not in d: continue
            x = d[(m, b)]
            out.write(f"| {b} | {x['S']/8e6:.2f} | {100*x['S']/x['H']:.1f}% | {100*x['S']/x['Hg']:.2f}% | {100*x['med']:.1f}% | {100*x['le1']:.0f}% | "
                      f"{x['tb']:.0f} | {x['tx']:.1f} | {x['tx']/x['tHx']:.2f} | {x['medx']:.2f} | {x['vis']:.2f} |\n")

# v-to-v
rows = load(os.path.join(R, "vv_all.csv"))
if rows is None:   # vv_all.csv is the concatenation of vv_bridge.csv and vv_ring.csv
    rows = (load(os.path.join(R, "vv_bridge.csv")) or []) + (load(os.path.join(R, "vv_ring.csv")) or [])
adjrows = load(os.path.join(R, "adj", "vv_bridge.csv")) or []
def persrc(src):
    S0 = {}; per = defaultdict(list)
    for r in src:
        if r["kind"] == "VVs" and r["seeds"]: S0[r["qid"]] = f(r, "seeds")
        if r["kind"] == "VVs" and r["status"] == "ok": per[(r["qid"], r["model"], r["budget"])].append(r)
    return S0, per
VCFG = [("region", "0", "0"), ("region", "1000", "$10^3$"), ("region_rel", "4x+10", "4x+10"), ("region_rel", "16x+10", "16x+10"),
        ("region", "inf", r"$\infty$")]
out.write("\n## v-to-v (queries with a cut and per-source samples)\n")
vv = {}
for kind, src, lab in [("VVr", rows, "Ring-RPQ split (RingAB)"), ("VVb", rows, "bridge split (Ring)"), ("VVb", adjrows, "bridge split (adjacency)")]:
    S0, per = persrc(src)
    ok = [r for r in src if r["kind"] == kind and r["status"] == "ok"]
    qs = sorted(set(r["qid"] for r in ok))
    if not ok:
        warn(f"v-to-v: no ok {kind} rows for {lab}, series skipped")
        continue
    out.write(f"\n### {lab}: {len(qs)} queries, verification failures {sum(r['verify'] != '1' for r in ok)}\n")
    out.write("| config | split S MB | explicit MB | per-source S MB | per-source explicit MB | build s | expand s | explicit expand s | per-source expand s | seeds | live |\n|---|---|---|---|---|---|---|---|---|---|---|\n")
    for m, b, l in VCFG:
        rs = [r for r in ok if (r["model"], r["budget"]) == (m, b)]
        if not rs:
            warn(f"v-to-v, {lab}: no ok rows for configuration {m}/{b}")
            continue
        S = sum(f(r, "S_bits") for r in rs); H = sum(f(r, "H_bits") for r in rs)
        PS = PH = PX = 0   # per-source estimates (no longer used)
        tb = sum(f(r, "t_build") for r in rs); tx = sum(f(r, "t_expand") for r in rs); thx = sum(f(r, "t_H_expand") for r in rs)
        sd = sum(f(r, "seeds") for r in rs); lv = sum(f(r, "live_seeds") for r in rs)
        vv[(lab, b)] = dict(S=S, H=H, PS=PS, PH=PH, PX=PX, tx=tx, thx=thx, tb=tb)
        out.write(f"| {b} | {S/8e6:.2f} | {H/8e6:.1f} | {PS/8e6:,.0f} | {PH/8e6:,.0f} | {tb:.0f} | {tx:.0f} | {thx:.0f} | {PX:,.0f} | {sd:,.0f} | {lv:,.0f} |\n")
out.close()

# v-to-v figure: space vs expansion time
VSHOW = {"0": "0", "1000": "$10^3$", "4x+10": "4x", "inf": r"$\infty$"}
series = []
for lab, key, col, mk in [("Ring-RPQ split (RingAB)", "Ring-RPQ split (RingAB)", "#2a78d6", "o"), ("bridge split (Ring)", "bridge split (Ring)", "#6250d6", "D"),
                          ("bridge split (Plain)", "bridge split (adjacency)", "#1baf7a", "^")]:
    pts = [(vv[(key, b)]["S"] / 8e6 if b != "inf" else 0, vv[(key, b)]["tx"], t) for b, t in VSHOW.items() if (key, b) in vv]
    for b in VSHOW:
        if (key, b) not in vv: warn(f"vtov.pdf: point {b} of series {lab} missing")
    if (key, "0") in vv: pts.append((vv[(key, "0")]["H"] / 8e6, vv[(key, "0")]["thx"], "H"))
    if pts: series.append((lab, col, mk, pts))
    else: warn(f"vtov.pdf: series {lab} skipped (no data)")
cutq = set(r["qid"] for r in rows if r["kind"] == "VVr" and r["status"] == "ok")
U = [r for r in rows if r["kind"] == "VVu" and r["status"] == "ok" and r["qid"] in cutq]   # PMR of all sources
UHG = 0
if U:
    pts = []
    for b, t in VSHOW.items():
        rs = [r for r in U if r["budget"] == b]
        if not rs:
            warn(f"vtov.pdf: point {b} of series PMR of all sources missing")
            continue
        pts.append((sum(f(r, "S_bits") for r in rs) / 8e6 if b != "inf" else 0, sum(f(r, "t_expand") for r in rs), t))
    r0 = [r for r in U if r["budget"] == "0" and r["model"] == "region"]
    if r0: pts.append((sum(f(r, "H_bits") for r in r0) / 8e6, sum(f(r, "t_H_expand") for r in r0), "H"))
    series.append(("PMR of all sources (Ring)", "#eb6834", "s", pts))
    UHG = sum(f(r, "H_bits_global") for r in r0) / 8e6
    series.append(("plain H, all sources", INK, "D", [(sum(f(r, "Hplain_bits") for r in r0) / 8e6, sum(f(r, "t_Hplain_expand") for r in r0), "")]))
    f0 = [r for r in rows if r["kind"] == "VVr" and r["status"] == "ok" and r["budget"] == "0" and r["model"] == "region"]
    if any(r.get("Hplain_bits") for r in f0):
        series.append(("plain H, factorized", "#888888", "D", [(sum(f(r, "Hplain_bits") for r in f0) / 8e6, sum(f(r, "t_Hplain_expand") for r in f0), "")]))
else:
    warn("vtov.pdf: no ok VVu rows (of queries with a cut) in vv_all.csv, series 'PMR of all sources' skipped")
HG = UHG or sum(f(r, "H_bits_global") for r in rows if r["kind"] == "VVr" and r["status"] == "ok" and r["budget"] == "0" and r["model"] == "region") / 8e6


def draw_vtov():
    global NQ
    allS = [p[0] for s_ in series for p in s_[3] if p[0] < 2000]; allT = [p[1] for s_ in series for p in s_[3]]
    far = [p[0] for s_ in series for p in s_[3] if p[0] >= 2000]
    NQ = len(cutq)
    fig, axs = tradeoff(series, max(allS), bands(allT), xref=min(far) if far else HG, legend_cols=2)
    fig.savefig(os.path.join(OUT, "vtov.pdf"), bbox_inches="tight", pad_inches=0.03); plt.close(fig)


if series and any(p[0] < 2000 for s_ in series for p in s_[3]): draws.append((draw_vtov, ()))
else: warn("vtov.pdf: no data, figure skipped")

if init_mpl():
    for fn, args in draws: fn(*args)
else:
    warn("matplotlib not available, figures skipped")
