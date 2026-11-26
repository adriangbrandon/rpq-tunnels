"""Summarize ring-tunnels CSV output: space and times per query kind and budget.

Usage: python3 ring_summary.py out.csv [--kinds "CV,VC;CVp,VCp"] [--min-edges M]
       python3 ring_summary.py out.csv --vv     (v-to-v: split vs per-source estimate)
       python3 ring_summary.py out.csv --pathfinder   (output of --shortest: PathFinder vs H_sp vs S)

Per kind group and configuration (model, budget), over queries with status ok:
  space : total bits of S, relative to the explicit H (same local-index encoding) and to H
          with global ids; median per query of S_bits / H_bits (queries with H_edges >= M)
  build : total time to build the structure (product graph + liveness + costs + selection +
          encoding), relative to the Ring query time (answers only)
  expand: total and median time to rebuild H from the structure; the explicit H and the
          rerun (W=inf: nothing stored) are the baselines
"""
import csv, sys, statistics
from collections import defaultdict


def arg(name, default, cast=str):
    return cast(sys.argv[sys.argv.index(name) + 1]) if name in sys.argv else default


def main():
    rows = list(csv.DictReader(open(sys.argv[1])))
    minE = arg("--min-edges", 10, int)
    groups = arg("--kinds", "CV,VC;CVp,VCp", str).split(";")
    st = defaultdict(int)
    for r in rows:
        if r["model"] in ("", "region") and r["budget"] in ("", "0"): st[(r["kind"], r["status"])] += 1
    print("status:", ", ".join(f"{k[0]}/{k[1]}={v}" for k, v in sorted(st.items())))
    bad = [r for r in rows if r["status"] == "ok" and r["verify"] != "1"]
    print("decode verification failures:", len(bad))
    f = lambda r, k: float(r[k]) if r[k] not in ("", None) else 0.0
    for g in groups:
        kinds = set(g.split(","))
        ok = [r for r in rows if r["status"] == "ok" and r["kind"] in kinds]
        if not ok: continue
        cfgs = []
        for r in ok:
            c = (r["model"], r["budget"])
            if c not in cfgs: cfgs.append(c)
        base = [r for r in ok if (r["model"], r["budget"]) == cfgs[0]]
        Hb = sum(f(r, "H_bits") for r in base); Hg = sum(f(r, "H_bits_global") for r in base)
        He = sum(f(r, "H_edges") for r in base); tring = sum(max(0, f(r, "t_ring")) for r in base)
        tHx = sum(f(r, "t_H_expand") for r in base)
        print(f"\n## kinds {g}: {len(base)} queries, H = {int(He):,} edges, explicit H = {Hb/8/1e6:.3f} MB "
              f"(global ids: {Hg/8/1e6:.3f} MB), Ring time {tring:.2f} s, explicit-H expansion {tHx:.2f} s")
        print("| model | budget | S bits / H bits | S / H global | median q S/H | build s | build / Ring | expand s | expand / explicit H | median q expand / explicit | visited / H nodes |")
        print("|---|---|---|---|---|---|---|---|---|---|---|")
        for c in cfgs:
            rs = [r for r in ok if (r["model"], r["budget"]) == c]
            Sb = sum(f(r, "S_bits") for r in rs); tb = sum(f(r, "t_build") for r in rs); tx = sum(f(r, "t_expand") for r in rs)
            big = [r for r in rs if f(r, "H_edges") >= minE]
            mq = statistics.median([f(r, "S_bits") / max(1, f(r, "H_bits")) for r in big]) if big else 0
            mx = statistics.median([f(r, "t_expand") / max(1e-9, f(r, "t_H_expand")) for r in big]) if big else 0
            vis = sum(f(r, "dec_visited") for r in rs) / max(1, sum(f(r, "H_nodes") for r in rs))
            print(f"| {c[0]} | {c[1]} | {100*Sb/Hb if Hb else 0:.2f}% | {100*Sb/Hg if Hg else 0:.2f}% | {100*mq:.1f}% | {tb:.2f} | "
                  f"{tb/tring if tring else 0:.2f} | {tx:.2f} | {tx/tHx if tHx else 0:.2f} | {mx:.2f} | {vis:.2f} |")


if __name__ == "__main__" and "--vv" not in sys.argv and "--pathfinder" not in sys.argv:
    main()


def vv_summary(path):
    """v-to-v: split representation (VVr / VVb) against the per-source estimate |S0| x mean(VVs samples)."""
    rows = [r for r in csv.DictReader(open(path))]
    f = lambda r, k: float(r[k]) if r.get(k) not in ("", None) else 0.0
    per = defaultdict(list)   # (qid, src-tag, model, budget) -> VVs rows
    for r in rows:
        if r["kind"] == "VVs" and r["status"] == "ok": per[(r["qid"], r["model"], r["budget"])].append(r)
    S0 = {}
    for r in rows:
        if r["kind"] == "VVs" and r["seeds"]: S0[r["qid"]] = f(r, "seeds")
    for kind in ("VVr", "VVb"):
        ok = [r for r in rows if r["kind"] == kind and r["status"] == "ok"]
        if not ok: continue
        cfgs = []
        for r in ok:
            c = (r["model"], r["budget"])
            if c not in cfgs: cfgs.append(c)
        print(f"\n## {kind}: {len(set(r['qid'] for r in ok))} queries ok; verification failures: {sum(r['verify'] != '1' for r in ok)}")
        print("| model | budget | queries | split S MB | split explicit MB | per-source S MB (est.) | split/per-source | build s | expand s | explicit expand s | per-source expand s (est.) | Ring v-to-v s |")
        print("|---|---|---|---|---|---|---|---|---|---|---|---|")
        for c in cfgs:
            Ss = He = Ps = tb = tx = px = tr = thx = 0.0; n = 0
            for r in ok:
                if (r["model"], r["budget"]) != c: continue
                smp = per.get((r["qid"], c[0], c[1]), [])
                if not smp or r["qid"] not in S0: continue
                n += 1; Ss += f(r, "S_bits"); He += f(r, "H_bits"); tb += f(r, "t_build"); tx += f(r, "t_expand"); thx += f(r, "t_H_expand"); tr += max(0, f(r, "t_ring"))
                Ps += S0[r["qid"]] * statistics.mean(f(x, "S_bits") for x in smp)
                px += S0[r["qid"]] * statistics.mean(f(x, "t_expand") for x in smp)
            print(f"| {c[0]} | {c[1]} | {n} | {Ss/8e6:.3f} | {He/8e6:.3f} | {Ps/8e6:.3f} | {100*Ss/Ps if Ps else 0:.2f}% | {tb:.2f} | {tx:.2f} | {thx:.2f} | {px:.2f} | {tr:.2f} |")


if __name__ == "__main__" and "--vv" in sys.argv:
    vv_summary(sys.argv[1])


def pathfinder_summary(path):
    """--shortest: what PathFinder (MillenniumDB, ALL SHORTEST WALKS) keeps against H_sp and S."""
    rows = [r for r in csv.DictReader(open(path)) if r.get("sem") == "shortest"]
    f = lambda r, k: float(r[k]) if r.get(k) not in ("", None) else 0.0
    groups = arg("--kinds", "CV,VC;CVp,VCp;CVr,VCr;VVs", str).split(";")
    minE = arg("--min-edges", 10, int)
    ok = [r for r in rows if r["status"] == "ok"]
    print("PathFinder simulation failures (pf_check != 1):", sum(r["pf_check"] != "1" for r in ok),
          "| decode verification failures:", sum(r["verify"] != "1" for r in ok),
          "| pf_status:", dict(defaultdict(int, {k: sum(r["pf_status"] == k for r in ok) for k in set(r["pf_status"] for r in ok)})))
    for g in groups:
        kinds = set(g.split(","))
        gr = [r for r in ok if r["kind"] in kinds and r["pf_status"] == "ok"]
        if not gr: continue
        cfgs = []
        for r in gr:
            c = (r["model"], r["budget"])
            if c not in cfgs: cfgs.append(c)
        base = [r for r in gr if (r["model"], r["budget"]) == cfgs[0]]
        pf = sum(f(r, "pf_bytes") for r in base); pfs = sum(f(r, "pf_bytes_structs") for r in base)
        hp = sum(f(r, "Hplain_bits") for r in base) / 8; hx = sum(f(r, "H_bits") for r in base) / 8
        st = sum(f(r, "pf_states") for r in base); hn = sum(f(r, "H_nodes") for r in base)
        over = sum(f(r, "pf_paths") > 100000 for r in base)
        print(f"\n## kinds {g}: {len(base)} queries, PathFinder {pf/1e6:.3f} MB (structs only {pfs/1e6:.3f} MB), "
              f"plain H_sp {hp/1e6:.3f} MB, explicit H_sp {hx/1e6:.3f} MB; PathFinder states / H_sp nodes = {st/max(1, hn):.2f}; "
              f"{over} queries with more than 100,000 shortest walks")
        print("| model | budget | S MB | PathFinder / S | PathFinder structs / S | median q PathFinder / S | plain H_sp / S | explicit H_sp / S |")
        print("|---|---|---|---|---|---|---|---|")
        for c in cfgs:
            rs = [r for r in gr if (r["model"], r["budget"]) == c]
            Sb = sum(f(r, "S_bits") for r in rs) / 8
            big = [r for r in rs if f(r, "H_edges") >= minE]
            mq = statistics.median([f(r, "pf_bytes") / max(1.0, f(r, "S_bits") / 8) for r in big]) if big else 0
            print(f"| {c[0]} | {c[1]} | {Sb/1e6:.4f} | {pf/Sb if Sb else 0:.1f}x | {pfs/Sb if Sb else 0:.1f}x | {mq:.1f}x | {hp/Sb if Sb else 0:.1f}x | {hx/Sb if Sb else 0:.1f}x |")


if __name__ == "__main__" and "--pathfinder" in sys.argv:
    pathfinder_summary(sys.argv[1])
