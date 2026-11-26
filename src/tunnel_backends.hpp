// tunnel_backends.hpp — the graph representations on which ring-tunnels runs.
//
//   (default)    Ring of Ring-RPQ (build-index-basic)
//   RING_SPLIT   RingAB of Ring-RPQ (build-index-split), which also offers its split for v-to-v
//   NAV_GRAPH    navGraph of Navarro and Robert (SPIRE 2024), github.com/j-rparra/navGraph
//   ADJ_GRAPH    plain adjacency lists (the "classic" representation), built from the .dat
//
// Every backend offers the same operations on labels as Ring-RPQ numbers them: a label p in
// [1, maxP] is an edge label traversed forward, p + maxP the same label traversed backward.
//   labels(prep, v, j, segs): the labels of the transitions leaving state j (backward-run
//       automaton, as in Ring-RPQ) for which v has neighbors; one segment per label, with the
//       number of neighbors. Returns the number of label checks done (the analysis cost a(v, j)).
//   values(seg, out): the neighbors of the segment, sorted and distinct;  kth(seg, k): the k-th one.
//   nodesWithLabel(p): the nodes that have neighbors by label p (sorted);
//   nodesReachedBy(p): the nodes that are neighbors of some node by p (sorted).
// and the backend's own evaluation of the queries, timed as a baseline (when it has one).
#ifndef TUNNEL_BACKENDS
#define TUNNEL_BACKENDS

#include <algorithm>
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <iostream>
#include <map>
#include <random>
#include <set>
#include <sstream>
#include <stdexcept>
#include <string>
#include <tuple>
#include <unordered_map>
#include <unordered_set>
#include <utility>
#include <vector>
// sdsl first: the NFA library defines a macro W that breaks sdsl/io.hpp if it comes before
#include <sdsl/int_vector.hpp>
#include <sdsl/sd_vector.hpp>
#include <sdsl/bit_vectors.hpp>
#include <sdsl/io.hpp>
#if defined(NAV_GRAPH)
#include "nav_graph.hpp"
#elif !defined(ADJ_GRAPH)
#include "ring_rpq.hpp"
#include "planner.hpp"
#endif
#include "RpqAutomata.hpp"
#include "RpqTree.hpp"
#include "rpq_query.hpp"   // rpq_query: the parsed query, also what the baselines take
typedef uint64_t u64;
typedef uint32_t u32;
// A set of states of the product's automaton: one bit per state, at most 16 states.
typedef uint16_t word_t;

static inline double tb_now() { return std::chrono::duration<double>(std::chrono::steady_clock::now().time_since_epoch()).count(); }

#if defined(NAV_GRAPH)
// ====================================================================== navGraph
#define BACKEND_NAME "navGraph"
static const bool kSplitIndex = false;
struct Backend {
    nav_graph G; u64 M = 0, maxS = 0, maxNodeId = 0;
    struct Prep { std::vector<std::vector<u64>> statePreds; };
    struct Seg { u64 p, v, cnt; bool fwd; };
    void load(const std::string &f) { G.load(f); M = G.max_P; maxS = G.max_S; maxNodeId = G.max_SO; }
    double bytesPerTriple() { return (double)G.size() / (double)G.n; }
    u64 nTriples() { return G.n; }
    u64 maxP() const { return M; }
    u64 maxNode() const { return maxNodeId; }
    u64 predReverse(u64 p) const { return p > M ? p - M : p + M; }
    void prepare(Prep &pr, const std::unordered_map<uint64_t, uint64_t> &Bm, u64 Q) {
        pr.statePreds.assign(Q + 1, {});
        for (auto &it : Bm) for (u64 j = 0; j <= Q && j < 64; j++) if (it.second >> j & 1) pr.statePreds[j].push_back(it.first);
        for (auto &v : pr.statePreds) std::sort(v.begin(), v.end());
    }
    void release(Prep &) {}
    size_t labels(const Prep &pr, u64 v, int j, std::vector<Seg> &segs) {
        segs.clear(); if ((size_t)j >= pr.statePreds.size()) return 0;
        const auto &L = pr.statePreds[j];
        for (u64 p : L) {
            bool fwd = p <= M; u64 l = fwd ? p : p - M; u64 c = 0;
            if (fwd) { if (v <= maxS) c = G.outdegree_l(v, l); }
            else if (v <= maxNodeId) c = G.indegree_l(v, l);
            if (c) segs.push_back({p, v, c, fwd});
        }
        return L.size();
    }
    // the labels of the transitions leaving some state of the set D (used by the PathFinder simulation)
    void labelsMask(const Prep &pr, u64 v, u64 D, std::vector<Seg> &segs) {
        segs.clear(); std::vector<u64> L;
        for (u64 j = 0; j < pr.statePreds.size(); j++) if (D >> j & 1) L.insert(L.end(), pr.statePreds[j].begin(), pr.statePreds[j].end());
        std::sort(L.begin(), L.end()); L.erase(std::unique(L.begin(), L.end()), L.end());
        for (u64 p : L) {
            bool fwd = p <= M; u64 l = fwd ? p : p - M; u64 c = 0;
            if (fwd) { if (v <= maxS) c = G.outdegree_l(v, l); }
            else if (v <= maxNodeId) c = G.indegree_l(v, l);
            if (c) segs.push_back({p, v, c, fwd});
        }
    }
    void values(const Seg &s, std::vector<uint64_t> &out) { out = s.fwd ? G.neigh_l_all(s.v, s.p) : G.rneigh_l_all(s.v, s.p - M); }
    u64 kth(const Seg &s, u64 k) { return s.fwd ? G.neigh_l(s.v, k + 1, s.p) : G.rneigh_l(s.v, k + 1, s.p - M); }
    std::vector<u64> sources(u64 l) { std::vector<u64> r; if (l == 0 || l > M || G.count_l(l) == 0) return r; auto s = G.sources_l(l); r.assign(s.begin(), s.end()); return r; }
    std::vector<u64> targets(u64 l) {
        std::vector<u64> r; if (l == 0 || l > M || G.count_l(l) == 0) return r;
        sdsl::bit_vector b(maxNodeId + 2, 0); G.targets_l_bv(l, b);
        for (u64 i = 0; i < b.size(); i++) if (b[i]) r.push_back(i);
        return r;
    }
    std::vector<u64> nodesWithLabel(u64 p) { return p <= M ? sources(p) : targets(p - M); }
    std::vector<u64> nodesReachedBy(u64 p) { return p <= M ? targets(p) : sources(p - M); }
    u64 countNodesWithLabel(u64 p) { return nodesWithLabel(p).size(); }
    u64 countNodesReachedBy(u64 p) { return nodesReachedBy(p).size(); }
    // navGraph's own evaluation (forward navigation, its default); timeout in TIME_OUT (seconds)
    double constQuery(rpq_query &P, bool cv, u64 node, u64 &nAns) {
        auto pm = P.pred_map; std::vector<std::pair<uint64_t, uint64_t>> out;
        query_start = high_resolution_clock::now(); double t0 = tb_now();
        G.rpq_one_const(P.rpq, pm, node, out, cv, true);
        double t = tb_now() - t0; std::set<u64> an; for (auto &pr : out) an.insert(cv ? pr.second : pr.first); nAns = an.size(); return t;
    }
    double vvQuery(rpq_query &P, u64 &nAns) {
        auto pm = P.pred_map; std::vector<std::pair<uint64_t, uint64_t>> out;
        query_start = high_resolution_clock::now(); double t0 = tb_now();
        G.rpq_no_const(P.rpq, pm, out, true);
        double t = tb_now() - t0; std::unordered_set<u64> s; for (auto &pr : out) s.insert(pr.first * 0x9E3779B97F4A7C15ULL ^ pr.second); nAns = s.size(); return t;
    }
    bool hasCC() const { return false; }
    double ccQuery(RpqAutomata &, rpq_query &, Prep &, u64, u64, bool, u64 &) { return -1; }
    bool hasBaseline() const { return true; }
};
static void setBaselineTimeout(double s) { TIME_OUT = (uint64_t)s; }
static const double kBaselineTimeoutDefault = 60;

#elif defined(ADJ_GRAPH)
// ====================================================================== plain adjacency lists
#define BACKEND_NAME "adjacency lists"
static const bool kSplitIndex = false;
struct Backend {
    // per direction: offsets by node, then (label, neighbor) sorted
    struct Dir { std::vector<u64> off; std::vector<u32> lab, nb; };
    Dir fw, bw; u64 M = 0, maxNodeId = 0, n = 0;
    struct Prep { std::vector<std::vector<u64>> statePreds; };
    struct Seg { const u32 *nb; u64 cnt; u64 p; };
    void build(const std::string &f) {
        FILE *in = fopen(f.c_str(), "r"); if (!in) { fprintf(stderr, "cannot open %s\\n", f.c_str()); exit(1); }
        std::vector<u32> S, P, O; static char buf[1 << 22]; size_t got; u64 cur = 0; int field = 0; bool innum = false; u64 tri[3];
        while ((got = fread(buf, 1, sizeof buf, in)) > 0) for (size_t i = 0; i < got; i++) {
            char c = buf[i];
            if (c >= '0' && c <= '9') { cur = cur * 10 + (c - '0'); innum = true; }
            else if (innum) { tri[field++] = cur; cur = 0; innum = false;
                if (field == 3) { S.push_back((u32)tri[0]); P.push_back((u32)tri[1]); O.push_back((u32)tri[2]); field = 0;
                    maxNodeId = std::max<u64>(maxNodeId, std::max(tri[0], tri[2])); M = std::max<u64>(M, tri[1]); } }
        }
        if (innum && field == 2) { S.push_back((u32)tri[0]); P.push_back((u32)tri[1]); O.push_back((u32)cur); }
        fclose(in); n = S.size();
        auto mk = [&](Dir &d, const std::vector<u32> &from, const std::vector<u32> &to) {
            d.off.assign(maxNodeId + 2, 0); for (u64 i = 0; i < n; i++) d.off[from[i] + 1]++;
            for (u64 v = 0; v <= maxNodeId; v++) d.off[v + 1] += d.off[v];
            d.lab.resize(n); d.nb.resize(n); std::vector<u64> pos(d.off.begin(), d.off.end() - 1);
            for (u64 i = 0; i < n; i++) { u64 q = pos[from[i]]++; d.lab[q] = P[i]; d.nb[q] = to[i]; }
            std::vector<std::pair<u32, u32>> tmp;
            for (u64 v = 0; v <= maxNodeId; v++) { u64 a = d.off[v], b = d.off[v + 1]; if (b - a < 2) continue;
                tmp.clear(); for (u64 q = a; q < b; q++) tmp.push_back({d.lab[q], d.nb[q]});
                std::sort(tmp.begin(), tmp.end()); tmp.erase(std::unique(tmp.begin(), tmp.end()), tmp.end());
                for (u64 q = 0; q < tmp.size(); q++) { d.lab[a + q] = tmp[q].first; d.nb[a + q] = tmp[q].second; }
                for (u64 q = a + tmp.size(); q < b; q++) { d.lab[q] = UINT32_MAX; d.nb[q] = UINT32_MAX; } }   // duplicates: sorted last
        };
        mk(fw, S, O); mk(bw, O, S);
    }
    template<class T> static void wr(FILE *f, const std::vector<T> &v) { u64 s = v.size(); fwrite(&s, 8, 1, f); fwrite(v.data(), sizeof(T), s, f); }
    template<class T> static void rd(FILE *f, std::vector<T> &v) { u64 s; if (fread(&s, 8, 1, f) != 1) exit(1); v.resize(s); if (fread(v.data(), sizeof(T), s, f) != s) exit(1); }
    void load(const std::string &f) {
        std::string bin = f + ".adj";
        FILE *b = fopen(bin.c_str(), "rb");
        if (b) { u64 h[3]; if (fread(h, 8, 3, b) != 3) exit(1); M = h[0]; maxNodeId = h[1]; n = h[2];
            rd(b, fw.off); rd(b, fw.lab); rd(b, fw.nb); rd(b, bw.off); rd(b, bw.lab); rd(b, bw.nb); fclose(b); return; }
        fprintf(stderr, "building adjacency lists from %s (saved to %s)\n", f.c_str(), bin.c_str());
        build(f);
        b = fopen(bin.c_str(), "wb"); u64 h[3] = {M, maxNodeId, n}; fwrite(h, 8, 3, b);
        wr(b, fw.off); wr(b, fw.lab); wr(b, fw.nb); wr(b, bw.off); wr(b, bw.lab); wr(b, bw.nb); fclose(b);
    }
    double bytesPerTriple() { return (double)(8 * (fw.off.size() + bw.off.size()) + 8 * (fw.lab.size() + bw.lab.size())) / (double)n; }
    u64 nTriples() { return n; }
    u64 maxP() const { return M; }
    u64 maxNode() const { return maxNodeId; }
    u64 predReverse(u64 p) const { return p > M ? p - M : p + M; }
    void prepare(Prep &pr, const std::unordered_map<uint64_t, uint64_t> &Bm, u64 Q) {
        pr.statePreds.assign(Q + 1, {});
        for (auto &it : Bm) for (u64 j = 0; j <= Q && j < 64; j++) if (it.second >> j & 1) pr.statePreds[j].push_back(it.first);
        for (auto &v : pr.statePreds) std::sort(v.begin(), v.end());
    }
    void release(Prep &) {}
    inline bool range(const Dir &d, u64 v, u32 l, u64 &a, u64 &cnt) const {
        if (v > maxNodeId) return false;
        const u32 *b = d.lab.data() + d.off[v], *e = d.lab.data() + d.off[v + 1];
        auto r = std::equal_range(b, e, l); a = r.first - d.lab.data(); cnt = r.second - r.first; return cnt > 0;
    }
    size_t labels(const Prep &pr, u64 v, int j, std::vector<Seg> &segs) {
        segs.clear(); if ((size_t)j >= pr.statePreds.size()) return 0;
        const auto &L = pr.statePreds[j];
        for (u64 p : L) { bool f = p <= M; const Dir &d = f ? fw : bw; u64 a, c; if (range(d, v, (u32)(f ? p : p - M), a, c)) segs.push_back({d.nb.data() + a, c, p}); }
        return L.size();
    }
    void labelsMask(const Prep &pr, u64 v, u64 D, std::vector<Seg> &segs) {
        segs.clear(); std::vector<u64> L;
        for (u64 j = 0; j < pr.statePreds.size(); j++) if (D >> j & 1) L.insert(L.end(), pr.statePreds[j].begin(), pr.statePreds[j].end());
        std::sort(L.begin(), L.end()); L.erase(std::unique(L.begin(), L.end()), L.end());
        for (u64 p : L) { bool f = p <= M; const Dir &d = f ? fw : bw; u64 a, c; if (range(d, v, (u32)(f ? p : p - M), a, c)) segs.push_back({d.nb.data() + a, c, p}); }
    }
    void values(const Seg &s, std::vector<uint64_t> &out) { out.assign(s.nb, s.nb + s.cnt); }
    u64 kth(const Seg &s, u64 k) { return s.nb[k]; }
    std::vector<u64> nodesWithLabel(u64 p) {
        std::vector<u64> r; bool f = p <= M; const Dir &d = f ? fw : bw; u32 l = (u32)(f ? p : p - M); u64 a, c;
        for (u64 v = 0; v <= maxNodeId; v++) if (range(d, v, l, a, c)) r.push_back(v);
        return r;
    }
    std::vector<u64> nodesReachedBy(u64 p) { return nodesWithLabel(predReverse(p)); }
    u64 countNodesWithLabel(u64 p) { return nodesWithLabel(p).size(); }
    u64 countNodesReachedBy(u64 p) { return nodesReachedBy(p).size(); }
    double constQuery(rpq_query &, bool, u64, u64 &nAns) { nAns = 0; return -1; }
    double vvQuery(rpq_query &, u64 &nAns) { nAns = 0; return -1; }
    bool hasCC() const { return false; }
    double ccQuery(RpqAutomata &, rpq_query &, Prep &, u64, u64, bool, u64 &) { return -1; }
    bool hasBaseline() const { return false; }
};
static void setBaselineTimeout(double) {}
static const double kBaselineTimeoutDefault = 1e30;

#else
// ====================================================================== Ring / RingAB
// One index class (ring_rpq); RingAB is the index built with split support (build-index-split).
// The baselines are Ring-RPQ's own evaluation, with the strategy its planner chooses
// (src/planner.hpp), as query-index-basic / query-index-split run it.
typedef ring_rpq_bfs RingT;
#ifdef RING_SPLIT
#define BACKEND_NAME "RingAB"
static const bool kSplitIndex = true;
static const planner_mode kPlannerMode = planner_mode::split;
#else
#define BACKEND_NAME "Ring"
static const bool kSplitIndex = false;
static const planner_mode kPlannerMode = planner_mode::basic;
#endif
struct Backend {
    RingT G; u64 M = 0, maxNodeId = 0;
    struct Prep { std::vector<word_t> B; std::unordered_map<uint64_t, uint64_t> Bm; };
    struct Seg { u64 p, start, cnt; };
    std::vector<std::pair<uint64_t, std::pair<uint64_t, uint64_t>>> pv;
    bwt_nose &LS() { return G.subjects_bwt(); }
    bwt &LP() { return G.predicates_bwt(); }
    void load(const std::string &f) {
        G.load(f);
        if (kSplitIndex && !G.has_split_support()) {
            fprintf(stderr, "%s has no support for splitting RPQs: build it with build-index-split\n", f.c_str()); exit(1); }
        M = G.n_graph_predicates(); maxNodeId = std::max(G.max_subject(), G.max_object());
    }
    double bytesPerTriple() { return (double)G.size() / (G.n_triples() / 2); }
    u64 nTriples() { return G.n_triples() / 2; }
    u64 maxP() const { return M; }
    u64 maxNode() const { return maxNodeId; }
    u64 predReverse(u64 p) const { return G.pred_reverse(p); }
    void prepare(Prep &pr, const std::unordered_map<uint64_t, uint64_t> &Bm, u64) {
        if (pr.B.empty()) pr.B.assign(4 * G.n_labels(), 0);
        pr.Bm = Bm; for (auto &it : Bm) LP().mark<word_t>(it.first, pr.B, (word_t)it.second);
    }
    void release(Prep &pr) { for (auto &it : pr.Bm) LP().unmark<word_t>(it.first, pr.B); pr.Bm.clear(); }
    size_t labels(Prep &pr, u64 v, int j, std::vector<Seg> &segs) { return labelsMask(pr, v, 1u << j, segs); }
    // the labels of v for the transitions leaving some state of the set D
    size_t labelsMask(Prep &pr, u64 v, u64 D, std::vector<Seg> &segs) {
        segs.clear(); pv.clear();
        u64 lb = LP().get_C(v), rb = LP().get_C(v + 1) - 1;
        if (lb > rb) return 0;
        LP().active_predicates<word_t>(lb, rb, pr.B, (word_t)D, pv);
        for (auto &e : pv) { auto in = LP().backward_step_test(lb, rb, e.first, e.second.first, e.second.second);
            u64 c = LS().get_C(e.first); segs.push_back({e.first, c + in.first, in.second - in.first + 1}); }
        return pv.size();
    }
    void values(const Seg &s, std::vector<uint64_t> &out) { out = LS().all_values_in_range(s.start, s.start + s.cnt - 1); }
    u64 kth(const Seg &s, u64 k) { return LS()[s.start + k]; }
    std::vector<u64> lsValues(u64 p) { std::vector<u64> r; u64 lb = LS().get_C(p), rb = LS().get_C(p + 1) - 1; if (lb <= rb) { auto v = LS().all_values_in_range(lb, rb); r.assign(v.begin(), v.end()); } return r; }
    u64 lsCount(u64 p) { u64 lb = LS().get_C(p), rb = LS().get_C(p + 1) - 1; return lb <= rb ? LS().count_distinct_values(lb, rb) : 0; }
    std::vector<u64> nodesWithLabel(u64 p) { return lsValues(predReverse(p)); }
    std::vector<u64> nodesReachedBy(u64 p) { return lsValues(p); }
    u64 countNodesWithLabel(u64 p) { return lsCount(predReverse(p)); }
    u64 countNodesReachedBy(u64 p) { return lsCount(p); }
    // c-to-v (cv) or v-to-c from node, as Ring-RPQ evaluates it
    double constQuery(rpq_query &P, bool cv, u64 node, u64 &nAns) {
        rpq_query q = P; std::vector<std::pair<u64, u64>> outp;
        q.subject_is_const = cv; q.object_is_const = !cv;
        if (cv) q.subject_id = node; else q.object_id = node;
        double t0 = tb_now();
        solve(G, q, kPlannerMode, outp);
        double t = tb_now() - t0;
        std::set<u64> an; for (auto &pr : outp) an.insert(cv ? pr.second : pr.first); nAns = an.size();
        return t;
    }
    // v-to-v, as Ring-RPQ evaluates it
    double vvQuery(rpq_query &P, u64 &nAns) {
        rpq_query q = P; std::vector<std::pair<u64, u64>> out;
        q.subject_is_const = q.object_is_const = false;
        double t0 = tb_now();
        solve(G, q, kPlannerMode, out);
        double t = tb_now() - t0;
        std::unordered_set<u64> s; for (auto &pr : out) s.insert(pr.first * 0x9E3779B97F4A7C15ULL ^ pr.second); nAns = s.size();
        return t;
    }
    bool hasCC() const { return true; }
    // c-to-c with the automaton A of the product (already marked in pr.B by prepare)
    double ccQuery(RpqAutomata &A, rpq_query &, Prep &pr, u64 src, u64 dst, bool cv, u64 &nAns) {
        std::vector<std::pair<u64, u64>> sol; double t0 = tb_now();
        G.reaches_marked<word_t>(A, pr.B, src, dst, sol, cv); nAns = sol.size(); return tb_now() - t0;
    }
    bool hasBaseline() const { return true; }
#ifdef RING_SPLIT
    // the split Ring-RPQ chooses for a v-to-v query (false: the RPQ has no mandatory predicate)
    bool split(const rpq_query &q, std::string &l, std::string &r, std::vector<uint64_t> &el, bool &fl) {
        RingT::split_plan plan;
        if (!G.plan_split(q, plan)) return false;
        l = plan.rpq_l; r = plan.rpq_r; el = plan.elements; fl = plan.first_left; return true;
    }
#endif
};
static void setBaselineTimeout(double) {}   // Ring-RPQ's timeout is compile-time (TIME_OUT)
static const double kBaselineTimeoutDefault = TIME_OUT;
#endif

#endif
