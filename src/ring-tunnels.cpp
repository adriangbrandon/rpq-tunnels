// ring-tunnels.cpp — tunnels for the RPQ solution subgraph, on top of Ring-RPQ.
//
// For every RPQ with a constant endpoint (c-to-v, v-to-c) and, with --pairs K, for K
// constant-to-constant pairs per query (target = a random answer), this program
//
//   1. runs the original Ring-RPQ evaluation (time t_ring, the baseline that only
//      reports answers);
//   2. builds the product graph R explored by the same Ring traversal, but one product
//      node per (graph node v, NFA state j) instead of the bit-parallel state sets. A node
//      is expanded with the Ring primitives the bit-parallel search uses: the active labels
//      of v for state j (wavelet tree on L_P, marked with B_array), then the L_S range of
//      each label, whose distinct values are the neighbours. Children are (s, j') for every
//      j' in A.next({j}, p);
//   3. keeps the solution subgraph H (nodes of R that reach an accepting node, (v, 0); for
//      pairs, only (t, 0));
//   4. for each work budget, chooses the stored set S with the region model (as in the
//      prototype tunnels.cpp) and encodes it in bits: keys of S (Elias-Fano), and per node
//      its live list or its dead list, whichever is shorter, as gap-encoded local indices;
//   5. expands it back into H with the Ring (the decoder re-expands every non-stored node
//      and explores its dead successors) and checks that the result is exactly H.
//
// A child is identified by its local index in the parent expansion: the i-th distinct
// value of the L_S range of label p (they are reported in increasing order), times |next|,
// plus the rank of j' in A.next({j}, p). The explicit-H baseline uses the same encoding
// (every internal node of H stored with its live list); H_bits_global is the size with
// global node ids instead.
//
// Build: targets ring-tunnels, ring-tunnels-split and adj-tunnels of CMakeLists.txt (see tunnels/README.md).
#include "tunnel_backends.hpp"     // Ring, RingAB, navGraph or adjacency lists (see there)
#include <functional>
#include <sdsl/sd_vector.hpp>
#include <sdsl/bit_vectors.hpp>

static double now_s() {
    return std::chrono::duration<double>(std::chrono::steady_clock::now().time_since_epoch()).count();
}

// ------------------------------------------------------------------ hash map u64 -> u32
struct HMap {
    std::vector<u64> k; std::vector<u32> v; size_t n = 0, mask = 0;
    static constexpr u64 EMPTY = ~0ULL;
    void reset(size_t cap = 1 << 12) { size_t c = 1; while (c < cap * 2) c <<= 1; k.assign(c, EMPTY); v.assign(c, 0); mask = c - 1; n = 0; }
    static inline u64 h(u64 x) { x ^= x >> 33; x *= 0xff51afd7ed558ccdULL; x ^= x >> 33; x *= 0xc4ceb9fe1a85ec53ULL; x ^= x >> 33; return x; }
    void grow() {
        std::vector<u64> ok = std::move(k); std::vector<u32> ov = std::move(v); size_t c = (mask + 1) * 2; k.assign(c, EMPTY); v.assign(c, 0); mask = c - 1;
        for (size_t i = 0; i < ok.size(); i++) if (ok[i] != EMPTY) { size_t j = h(ok[i]) & mask; while (k[j] != EMPTY) j = (j + 1) & mask; k[j] = ok[i]; v[j] = ov[i]; }
    }
    inline std::pair<u32, bool> get(u64 key, u32 newIdx) {
        if ((n + 1) * 10 > (mask + 1) * 7) grow();
        size_t j = h(key) & mask;
        while (k[j] != EMPTY) { if (k[j] == key) return {v[j], false}; j = (j + 1) & mask; }
        k[j] = key; v[j] = newIdx; n++; return {newIdx, true};
    }
};
constexpr u64 HMap::EMPTY;

// ------------------------------------------------------------------ bit buffer with gamma/delta codes
struct BitWriter {
    sdsl::bit_vector bv; u64 n = 0;
    void ensure(u64 m) { if (m + 64 > bv.size()) bv.resize(std::max<u64>(m + 64, 2 * bv.size() + 128)); }
    void put(u64 x, unsigned len) { if (!len) return; ensure(n + len); bv.set_int(n, x, len); n += len; }
    void gamma(u64 x) { unsigned L = 63 - __builtin_clzll(x); put(0, L); put(1, 1); put(x, L); }  // x >= 1
    void delta(u64 x) { unsigned L = 63 - __builtin_clzll(x); gamma(L + 1); put(x, L); }          // x >= 1
    void finish() { bv.resize(n + 64); }   // padding for 64-bit reads
};
struct BitReader {
    const sdsl::bit_vector *bv; u64 p;
    inline u64 get(unsigned len) { if (!len) return 0; u64 x = bv->get_int(p, len); p += len; return x; }
    inline u64 gamma() {
        unsigned L = 0;
        for (;;) { u64 w = bv->get_int(p, 64); if (w) { unsigned z = __builtin_ctzll(w); L += z; p += z + 1; break; } L += 64; p += 64; }
        return (1ULL << L) | get(L);
    }
    inline u64 delta() { unsigned L = (unsigned)gamma() - 1; return (1ULL << L) | get(L); }
};

// ------------------------------------------------------------------ query parsing
struct ParsedQuery : rpq_query {   // rpq: Ring-RPQ form (predicates <...>, inverses <%...>)
    std::string raw, shape;
    bool skip = false;
};

static std::string shapeOf(const std::string &path) {   // abstract shape: predicates -> a, b, c...
    std::string out; std::map<std::string, char> m;
    for (size_t i = 0; i < path.size(); i++) {
        if (path[i] == '<') { size_t j = path.find('>', i); std::string p = path.substr(i, j - i + 1);
            if (!m.count(p)) m[p] = 'a' + (char)m.size(); out += m[p]; i = j; }
        else if (path[i] != ' ') out += path[i];
    }
    return out;
}

// ---- SPARQL-style inverses (^<p>, ^(...)) into the Ring's form, where only predicates are
// inverted: <%p>. Ring-RPQ's own input uses <%p>; a '^' left in the query becomes a literal
// symbol of the automaton that never matches. ^ is pushed down: ^(a/b) = ^b/^a, ^(a|b) = ^a|^b,
// ^(a*) = (^a)*.
struct RxNode { char t; std::string pred; std::vector<RxNode> k; };   // t: 'p' pred, '/', '|', '*', '+', '?'
struct RxParser {
    const std::string &s; size_t i = 0;
    explicit RxParser(const std::string &x) : s(x) {}
    void ws() { while (i < s.size() && s[i] == ' ') i++; }
    RxNode alt() { RxNode a = cat(); ws(); if (i < s.size() && s[i] == '|') { RxNode r{'|', "", {a}}; while (i < s.size() && s[i] == '|') { i++; r.k.push_back(cat()); ws(); } return r; } return a; }
    RxNode cat() { RxNode a = un(); ws(); if (i < s.size() && s[i] == '/') { RxNode r{'/', "", {a}}; while (i < s.size() && s[i] == '/') { i++; r.k.push_back(un()); ws(); } return r; } return a; }
    RxNode un() {
        ws();
        if (i < s.size() && s[i] == '^') { i++; RxNode x = un(); return inv(x); }
        RxNode a;
        if (i < s.size() && s[i] == '(') { i++; a = alt(); ws(); if (i >= s.size() || s[i] != ')') throw std::runtime_error("missing )"); i++; }
        else if (i < s.size() && s[i] == '<') { size_t j = s.find('>', i); if (j == std::string::npos) throw std::runtime_error("missing >"); a = RxNode{'p', s.substr(i, j - i + 1), {}}; i = j + 1; }
        else throw std::runtime_error("bad path");
        for (;;) { ws(); if (i < s.size() && (s[i] == '*' || s[i] == '+' || s[i] == '?')) { a = RxNode{s[i], "", {a}}; i++; } else break; }
        return a;
    }
    static RxNode inv(const RxNode &x) {
        if (x.t == 'p') { std::string p = x.pred; if (p.size() > 1 && p[1] == '%') p = "<" + p.substr(2); else p = "<%" + p.substr(1); return RxNode{'p', p, {}}; }
        RxNode r{x.t, "", {}};
        for (auto &c : x.k) r.k.push_back(inv(c));
        if (x.t == '/') std::reverse(r.k.begin(), r.k.end());
        return r;
    }
    static std::string print(const RxNode &x) {
        if (x.t == 'p') return x.pred;
        if (x.t == '/' || x.t == '|') { std::string o; for (size_t j = 0; j < x.k.size(); j++) { if (j) o += x.t;
            std::string c = print(x.k[j]); o += (x.t == '/' && x.k[j].t == '|') ? "(" + c + ")" : c; } return o; }
        return "(" + print(x.k[0]) + ")" + std::string(1, x.t);
    }
};
static std::string normalizeInverses(const std::string &path) {
    if (path.find('^') == std::string::npos) return path;
    RxParser P(path); RxNode r = P.alt(); P.ws();
    if (P.i != path.size()) throw std::runtime_error("trailing text in path");
    return RxParser::print(r);
}
// a node: an IRI of the dictionary, or <id:N> (generated queries)
static bool lookupNode(const std::string &t, std::unordered_map<std::string, u64> &map_SO, u64 &id) {
    if (t.compare(0, 4, "<id:") == 0) { id = std::stoull(t.substr(4, t.size() - 5)); return true; }
    auto it = map_SO.find(t); if (it == map_SO.end()) return false; id = it->second; return true;
}

// "<subject> <path> <object>": the path is parsed by Ring-RPQ's own parser (parse_query_line,
// src/rpq_query.hpp) once its SPARQL-style inverses are rewritten; the terms are looked up here,
// since they can also be <id:N>.
static ParsedQuery parseLine(std::string line, std::unordered_map<std::string, u64> &map_SO,
                             std::unordered_map<std::string, u64> &map_P, u64 realMaxP) {
    ParsedQuery Q;
    while (!line.empty() && (line.back() == '\r' || line.back() == '\n' || line.back() == ' ' || line.back() == '\t')) line.pop_back();
    Q.raw = line;
    if (line.empty()) { Q.skip = true; return Q; }
    size_t a = line.find(' '), b = line.rfind(' ');
    if (a == std::string::npos || a == b) { Q.skip = true; return Q; }
    std::string subject = line.substr(0, a), object = line.substr(b + 1), path = line.substr(a + 1, b - a - 1);
    while (!path.empty() && path[0] == ' ') path.erase(0, 1);
    std::string ringPath = normalizeInverses(path);   // throws on a malformed path
    Q.subject_is_const = subject[0] != '?';
    Q.object_is_const = object[0] != '?';
    if (Q.subject_is_const && !lookupNode(subject, map_SO, Q.subject_id)) Q.skip = true;
    if (Q.object_is_const && !lookupNode(object, map_SO, Q.object_id)) Q.skip = true;
    if (Q.skip) return Q;
    Q.shape = shapeOf(path);
    rpq_query parsed = parse_query_line("?s " + ringPath + " ?o", map_SO, map_P, realMaxP);
    if (!parsed.valid) { Q.skip = true; return Q; }   // unknown predicate
    parsed.subject_is_const = Q.subject_is_const; parsed.subject_id = Q.subject_id;
    parsed.object_is_const = Q.object_is_const; parsed.object_id = Q.object_id;
    static_cast<rpq_query &>(Q) = parsed;
    return Q;
}

// ------------------------------------------------------------------ product expansion
// A child of (v, j) is identified by its local index: for the i-th label segment of v (in the
// backend's order) with cnt neighbors and the set N = A.next({j}, label) of target states,
// base_i + k * |N| + r, where k is the rank of the neighbor in the segment (they are sorted and
// distinct) and r the rank of the state in N.
static bool gUniformCost = false;   // --uniform-cost: analysis cost = labels leaving the state, on every backend
struct Expander {
    Backend &G; RpqAutomata *A = nullptr; Backend::Prep prep; std::vector<Backend::Seg> segs; std::vector<size_t> nLab;
    std::vector<uint64_t> vals;
    u64 Q = 0, nExp = 0, nOut = 0;   // statistics: expansions, children enumerated
    explicit Expander(Backend &g) : G(g) {}
    // full expansion of (v, j): f(localIndex, s, j') for every child, in increasing local index;
    // returns the analysis cost (label checks)
    template<class F> size_t expand(u64 v, int j, F &&f) {
        nExp++;
        size_t np = G.labels(prep, v, j, segs);
        if (gUniformCost) np = (size_t)j < nLab.size() ? nLab[j] : 0;
        if (segs.empty()) return np;
        auto P = segs;   // f may not reenter, but keep a copy for safety
        u64 base = 0;
        for (auto &e : P) {
            word_t N = (word_t)A->next((word_t)(1u << j), e.p, BWD);
            int nN = __builtin_popcount(N);
            if (!nN) continue;
            G.values(e, vals);
            nOut += vals.size() * nN;
            for (u64 k = 0; k < vals.size(); k++) {
                int r = 0;
                for (word_t M = N; M; M &= M - 1, r++) f(base + k * nN + r, (u64)vals[k], __builtin_ctz(M));
            }
            base += e.cnt * nN;
        }
        return np;
    }
    // decode the children of (v, j) with the given sorted local indices: f(s, j')
    template<class F> void decodeList(u64 v, int j, const std::vector<u64> &idx, F &&f) {
        nExp++;
        G.labels(prep, v, j, segs); if (segs.empty()) return;
        auto P = segs;
        u64 base = 0; size_t t = 0;
        for (auto &e : P) {
            if (t >= idx.size()) break;
            word_t N = (word_t)A->next((word_t)(1u << j), e.p, BWD);
            int nN = __builtin_popcount(N);
            if (!nN) continue;
            u64 end = base + e.cnt * nN;
            for (; t < idx.size() && idx[t] < end; t++) {
                u64 li = idx[t] - base, k = li / nN, r = li % nN;
                u64 s = G.kth(e, k);
                word_t M = N; for (u64 z = 0; z < r; z++) M &= M - 1;
                nOut++;
                f(s, __builtin_ctz(M));
            }
            base = end;
        }
    }
};

// ------------------------------------------------------------------ encoded stored set
struct Encoded {
    sdsl::sd_vector<> keys; sdsl::rank_support_sd<1> krank; sdsl::sd_vector<> offs; sdsl::select_support_sd<1> osel;
    BitWriter rec; bool rootStored = false; u64 n = 0, entries = 0;
    u64 bits() const {
        return (n ? 8 * (sdsl::size_in_bytes(keys) + sdsl::size_in_bytes(offs)) : 0) + rec.n + 1;
    }
    // stored: nodes sorted by key (root first if stored, key SENT). lists: (dead?, sorted local indices)
    void build(const std::vector<u64> &skeys, std::vector<std::pair<bool, std::vector<u64>>> &lists, bool root) {
        rootStored = root; rec = BitWriter(); entries = 0;
        std::vector<u64> starts; starts.reserve(lists.size());
        for (size_t i = 0; i < lists.size(); i++) {
            starts.push_back(rec.n);
            auto &L = lists[i]; rec.put(L.first ? 1 : 0, 1); rec.gamma(L.second.size() + 1);
            u64 prev = 0; bool first = true;
            for (u64 x : L.second) { rec.delta(first ? x + 1 : x - prev); prev = x; first = false; }
            entries += L.second.size();
        }
        rec.finish();
        size_t off = root ? 1 : 0; n = skeys.size();
        if (n) {
            sdsl::sd_vector_builder kb(skeys.back() + 1, n);
            for (u64 k : skeys) kb.set(k);
            keys = sdsl::sd_vector<>(kb); sdsl::util::init_support(krank, &keys);
            sdsl::sd_vector_builder ob(rec.n + 1, n);
            for (size_t i = 0; i < n; i++) ob.set(starts[i + off]);
            offs = sdsl::sd_vector<>(ob); sdsl::util::init_support(osel, &offs);
        }
    }
    // record of a stored node, or false
    inline bool find(u64 key, BitReader &br) const {
        if (!n || key >= keys.size() || !keys[key]) return false;
        br.bv = &rec.bv; br.p = osel(krank(key) + 1); return true;
    }
    inline void readList(BitReader &br, bool &dead, std::vector<u64> &out) const {
        dead = br.get(1); u64 c = br.gamma() - 1; out.clear(); u64 prev = 0;
        for (u64 i = 0; i < c; i++) { u64 d = br.delta(); prev = i ? prev + d : d - 1; out.push_back(prev); }
    }
};

// ------------------------------------------------------------------ args
struct Args {
    std::string index, queries, out = "ring_tunnels.csv", vv = "auto";
    std::vector<long long> budgets{0, 10, 100, 1000, -1};
    std::vector<double> rel{4}; std::vector<long long> relSlack{10};
    int pairs = 0, reps = 3, vvReps = -1, maxQueries = -1, vvSamples = 5; u64 seed = 1;
    u64 maxNodes = 200000000, maxEdges = 1000000000, pairLimit = 20000000; double timeout = 600, baselineTimeout = kBaselineTimeoutDefault; u64 cap = 10000;
    std::set<int> only; bool pairRev = false, vvUnion = false, shortest = false, earlyStop = false; bool noRing = false, noRingVV = false, ringVVDerived = false, vvDerive = false, vvOnly = false, noPerSource = false;
    bool automata = false;   // --automata: compare the Glushkov, reverse Glushkov and Thompson product graphs (adj-tunnels)
};
static Args A;
static u64 CAP = 10000;
static const u64 INFC = 1ULL << 60, SENT = ~0ULL - 1;

static void usage() {
    fprintf(stderr,
        "usage: ring-tunnels <ring-index> <queries> [options]\n"
        "  --out f.csv            output (default ring_tunnels.csv)\n"
        "  --budgets 0,10,...,inf absolute work budgets of the region model\n"
        "  --rel 4 --rel-slack 10 relative budgets c*x+W0 (--rel none to skip)\n"
        "  --pairs K              K constant-to-constant pairs per c-to-v / v-to-c query\n"
        "  --pair-reverse         also solve each pair from its target (rows CVr / VCr)\n"
        "  --vv-union             v-to-v baseline: one multi-source PMR from all valid sources (rows VVu)\n"
        "  --uniform-cost         analysis cost of (w,q) = labels leaving q, also on the Ring (default there: labels w has)\n"
        "  --shortest             ALL SHORTEST WALKS: H is H_sp (edges of shortest walks) and each row also has the\n"
        "                         simulation of PathFinder (MillenniumDB), columns pf_*. Only c-to-v / v-to-c, pairs\n"
        "                         and per-source v-to-v samples (--vv none, no --vv-union)\n"
        "  --early-stop           with --shortest: in the rows with a fixed target (CVp, CVr, VCp, VCr) every BFS (product graph,\n"
        "                         PathFinder simulation, decoder) stops expanding at the layer where the target is first found\n"
        "  --automata             only compare the product graphs of the Glushkov, reverse Glushkov and Thompson automata\n"
        "                         of each c-to-v / v-to-c query (adj-tunnels only; own CSV columns)\n"
        "  --vv ring|bridge|both|none   v-to-v split (default: ring with the RingAB binary, bridge otherwise)\n"
        "  --vv-derive            also run the v-to-v query obtained by dropping the constant of each c-to-v / v-to-c query\n"
        "  --vv-only              only v-to-v queries (with --vv-derive: only the derived ones)\n"
        "  --vv-samples K         sampled sources for the per-source baseline of v-to-v (0: none)\n"
        "  --reps 3               repetitions of each expansion (minimum time is reported)\n"
        "  --vv-reps R            repetitions for v-to-v splits (default: --reps)\n"
        "  --timeout s --max-nodes N --max-edges N --cost-cap 10000 --pair-limit N\n"
        "  --only q1,q2 --max-queries N --seed s --no-ring --no-ring-vv\n"
        "  --ring-vv-derived      also time Ring-RPQ on the derived v-to-v queries (can be very slow)\n");
}
template<class T> static std::vector<T> splitNums(const std::string &s) {
    std::vector<T> r; std::stringstream ss(s); std::string t;
    while (std::getline(ss, t, ',')) { if (t == "inf") r.push_back((T)-1); else r.push_back((T)std::stod(t)); }
    return r;
}
static inline u64 mixh(u64 a, u64 b) { return HMap::h(a * 0x9E3779B97F4A7C15ULL ^ HMap::h(b + 0x632BE59BD9B4E019ULL)); }

// ------------------------------------------------------------------ CSV rows
static const char *COLS[] = {"qid", "line", "kind", "src", "dst", "shape", "Q", "status", "R_nodes", "R_edges", "H_nodes", "H_edges",
    "leaves", "answers", "ring_answers", "t_ring", "t_product", "t_live", "t_cost", "H_bits", "H_bits_global", "t_H_expand", "model",
    "budget", "S_nodes", "S_edges", "S_list", "S_bits", "replay_model", "max_region_work", "t_select", "t_encode", "t_build",
    "t_expand", "dec_visited", "dec_expanded", "dec_children", "verify", "seeds", "live_seeds", "explored_seeds", "seed_bits",
    "split", "first", "t_Hplain_expand", "Hplain_bits",
    "sem", "pf_status", "pf_dfa_states", "pf_states", "pf_transitions", "pf_edges", "pf_idx_searches", "pf_answers",
    "pf_paths", "pf_bytes_structs", "pf_bytes", "pf_check", "t_pf"};
struct Row {
    std::unordered_map<std::string, std::string> f;
    template<class T> void set(const std::string &k, T v) { std::ostringstream o; o << v; f[k] = o.str(); }
    void setd(const std::string &k, double v) { char b[64]; snprintf(b, sizeof b, "%.6f", v); f[k] = b; }
    void print(FILE *out) const {
        bool first = true;
        for (const char *c : COLS) { if (!first) fputc(',', out); first = false; auto it = f.find(c); if (it != f.end()) fputs(it->second.c_str(), out); }
        fputc('\n', out);
    }
};

// ------------------------------------------------------------------ liveness and H statistics (shared by encoder and decoder)
// Graph: node 0 is the root (key SENT); its edges e < soff[1] carry the index rseed[e] of their seed.
// alive0: reaches an accepting node ((v, 0), with v == dst if dst >= 0).
// alive : alive0 and reachable from the root through root edges of the seeds with useSeed (all if null).
// mask (--shortest): the edges of H's graph, those of shortest walks (null: all)
struct GView {
    const std::vector<u64> *keys, *soff; const std::vector<u32> *succ, *rseed; u64 Q; const std::vector<char> *mask = nullptr;
    size_t n() const { return keys->size(); }
};
static inline bool edgeOn(const GView &g, u32 x, u64 e, const std::vector<char> *useSeed) {
    if (g.mask && !(*g.mask)[e]) return false;
    return !(x == 0 && useSeed && !(*useSeed)[(*g.rseed)[e]]);
}
static void livenessOf(const GView &g, long long dst, const std::vector<char> *accept0, std::vector<char> &alive0) {
    size_t R = g.n(); const auto &soff = *g.soff; const auto &succ = *g.succ; const std::vector<char> *mk = g.mask;
    std::vector<u64> roff(R + 1, 0); for (u64 e = 0; e < succ.size(); e++) if (!mk || (*mk)[e]) roff[succ[e] + 1]++;
    for (size_t i = 0; i < R; i++) roff[i + 1] += roff[i];
    std::vector<u32> pred(roff[R]); { std::vector<u64> f(roff.begin(), roff.end() - 1);
        for (size_t i = 0; i < R; i++) for (u64 e = soff[i]; e < soff[i + 1]; e++) if (!mk || (*mk)[e]) pred[f[succ[e]]++] = i; }
    alive0.assign(R, 0); std::vector<u32> q;
    for (size_t i = 1; i < R; i++) {
        u64 k = (*g.keys)[i];
        bool acc = accept0 ? (*accept0)[i] : (k % g.Q == 0 && (dst < 0 || (long long)(k / g.Q) == dst));
        if (acc) { alive0[i] = 1; q.push_back(i); }
    }
    for (size_t h = 0; h < q.size(); h++) { u32 x = q[h]; for (u64 e = roff[x]; e < roff[x + 1]; e++) { u32 y = pred[e]; if (!alive0[y]) { alive0[y] = 1; q.push_back(y); } } }
}
static void restrictOf(const GView &g, const std::vector<char> &alive0, const std::vector<char> *useSeed, std::vector<char> &alive) {
    if (!useSeed) { alive = alive0; return; }
    size_t R = g.n(); const auto &soff = *g.soff; const auto &succ = *g.succ;
    alive.assign(R, 0); std::vector<u32> q;
    for (u64 e = soff[0]; e < soff[1]; e++) if (edgeOn(g, 0, e, useSeed) && alive0[succ[e]] && !alive[succ[e]]) { alive[succ[e]] = 1; q.push_back(succ[e]); }
    if (!q.empty()) alive[0] = 1;
    for (size_t h = 0; h < q.size(); h++) { u32 x = q[h];
        for (u64 e = soff[x]; e < soff[x + 1]; e++) { if (g.mask && !(*g.mask)[e]) continue;
            u32 y = succ[e]; if (alive0[y] && !alive[y]) { alive[y] = 1; q.push_back(y); } } }
}
struct HStats { u64 nodes = 0, edges = 0, hash = 0, leaves = 0; };
static HStats statsOf(const GView &g, const std::vector<char> &alive, const std::vector<char> *useSeed) {
    HStats s; size_t R = g.n(); const auto &soff = *g.soff; const auto &succ = *g.succ; const auto &keys = *g.keys;
    for (size_t i = 0; i < R; i++) if (alive[i]) { s.nodes++; u64 c = 0;
        for (u64 e = soff[i]; e < soff[i + 1]; e++) if (alive[succ[e]] && edgeOn(g, i, e, useSeed)) { c++; s.hash += mixh(keys[i], keys[succ[e]]); }
        s.edges += c; if (c == 0 && i != 0) s.leaves++; }
    return s;
}

// ------------------------------------------------------------------ minimal DFA (PathFinder's automaton)
// PathFinder (MillenniumDB) runs a DFA built from the RPQ and minimized with Moore's algorithm on the
// partial DFA (RPQ_DFA::optimize: a missing transition only matches a missing one). The minimal DFA of
// a language is unique, so the one built here from our automaton has the states of PathFinder's.
// Subset construction from F with A.next(., p, BWD) (the automaton runs backward from its final states
// and accepts when it holds state 0, as in the product graph).
struct MinDFA {
    u64 start = 0; std::vector<char> fin;
    std::vector<u64> rep;                              // a set of NFA states each state stands for
    std::vector<std::vector<std::pair<u64, u32>>> tr;  // per state: (label, next state), sorted by label
    size_t n() const { return fin.size(); }
    int nFinal() const { int c = 0; for (char f : fin) c += f; return c; }
};
static MinDFA minimalDFA(RpqAutomata &A, const std::unordered_map<uint64_t, uint64_t> &Bm, u64 F) {
    std::vector<u64> lab; for (auto &it : Bm) lab.push_back(it.first);
    std::sort(lab.begin(), lab.end());
    std::vector<u64> sub{F}; std::unordered_map<u64, u32> id{{F, 0}}; std::vector<std::vector<long long>> tab;
    for (size_t i = 0; i < sub.size(); i++) {
        std::vector<long long> row(lab.size(), -1);
        for (size_t k = 0; k < lab.size(); k++) {
            u64 nx = A.next(sub[i], lab[k], BWD); if (!nx) continue;
            auto r = id.emplace(nx, (u32)sub.size()); if (r.second) sub.push_back(nx);
            row[k] = r.first->second;
        }
        tab.push_back(row);
    }
    size_t n = sub.size();
    std::vector<u32> part(n); for (size_t i = 0; i < n; i++) part[i] = (sub[i] & 1) ? 1 : 0;
    size_t cnt = 0;
    for (;;) {   // refine by (class, classes reached by each label); -1 only matches -1
        std::map<std::vector<long long>, u32> sig; std::vector<u32> np(n);
        for (size_t i = 0; i < n; i++) {
            std::vector<long long> k{(long long)part[i]};
            for (long long t : tab[i]) k.push_back(t < 0 ? -1 : (long long)part[t]);
            np[i] = sig.emplace(k, (u32)sig.size()).first->second;
        }
        part = np;
        if (sig.size() == cnt) break;
        cnt = sig.size();
    }
    MinDFA D; D.fin.assign(cnt, 0); D.rep.assign(cnt, 0); D.tr.assign(cnt, {}); std::vector<char> done(cnt, 0);
    D.start = part[0];
    for (size_t i = 0; i < n; i++) {
        u32 c = part[i]; if (done[c]) continue; done[c] = 1;
        D.fin[c] = (sub[i] & 1) ? 1 : 0; D.rep[c] = sub[i];
        for (size_t k = 0; k < lab.size(); k++) if (tab[i][k] >= 0) D.tr[c].push_back({lab[k], part[tab[i][k]]});
    }
    return D;
}

// ------------------------------------------------------------------ one product graph
// A c-to-v query (one seed, the constant) or one side of a split v-to-v query (seeds = the
// candidate middle nodes). The root (key SENT) has the children (m, f), for each seed m and
// final state f of the (backward-run) automaton, with local index seedIndex * |F| + rank(f).
struct Side {
    Expander X; RpqAutomata *Au = nullptr; std::unordered_map<uint64_t, uint64_t> Bm;
    u64 Q = 1; word_t F = 0; int nF = 0; bool rootFixed = false;
    std::vector<u64> seeds;
    HMap M; std::vector<u64> keys, soff, lidx; std::vector<u32> succ, par, rseed; std::vector<uint8_t> npred;
    size_t R = 0, E = 0; std::string status = "ok";
    std::vector<char> alive0, alive, seedLive0; const std::vector<char> *useSeed = nullptr;
    std::vector<u64> hcnt, deadcnt, cost; std::vector<u32> clos, stamp, hindeg; u32 curStamp = 0;
    u64 Hn = 0, He = 0, leaves = 0, Hhash = 0, deadWork = 0;
    std::vector<u32> compOf, compStart, compNodes, compMin; u32 nc = 0;
    std::vector<char> stored;
    double tBuild = 0, tLive = 0, tCost = 0;
    // --shortest: H is H_sp, the edges (x, y) of R with dist(y) = dist(x) + 1 that lead to an accepting
    // node (dist: BFS layer from the root, whose children are at 1)
    bool shortest = false; std::vector<u32> dist; std::vector<char> spMask; MinDFA dfa;
    explicit Side(Backend &g) : X(g) {}
    ~Side() { clear(); }
    GView view() const { return GView{&keys, &soff, &succ, &rseed, Q, shortest ? &spMask : nullptr}; }
    void setup(const std::string &aq, std::unordered_map<std::string, uint64_t> &pm) {
        clear();
        Au = new RpqAutomata(aq, pm); Bm = Au->getB();
        F = (word_t)Au->getFinalStates(); nF = __builtin_popcount(F);
        if (shortest) dfa = minimalDFA(*Au, Bm, F);
        u64 all = F; for (auto &it : Bm) all |= it.second;
        Q = std::max<u64>(1, all ? 64 - __builtin_clzll(all) : 1);
        X.G.prepare(X.prep, Bm, Q);
        X.nLab.assign(Q + 1, 0); for (auto &it : Bm) for (u64 j = 0; j <= Q && j < 64; j++) if (it.second >> j & 1) X.nLab[j]++;
        X.A = Au; X.Q = Q;
    }
    void clear() { if (Au) { X.G.release(X.prep); delete Au; Au = nullptr; } }
    bool edgeOk(u32 x, u64 e) const { return !(shortest && !spMask[e]) && !(x == 0 && useSeed && !(*useSeed)[rseed[e]]); }
    // BFS layers (keys are in BFS order and par is the discovering parent) and the edges of shortest walks
    void shortestEdges() {
        dist.assign(R, 0); for (size_t i = 1; i < R; i++) dist[i] = dist[par[i]] + 1;
        spMask.assign(E, 0);
        for (size_t x = 0; x < R; x++) for (u64 e = soff[x]; e < soff[x + 1]; e++) spMask[e] = dist[succ[e]] == dist[x] + 1;
    }

    // product graph explored from the seeds
    // stopDst >= 0 (--early-stop): no node of the layer where the state (stopDst, 0) is first inserted is
    // expanded (the root is layer 0, the seeds are layer 1); those nodes stay in R as leaves
    void build(const std::vector<u64> &sd, long long stopDst = -1) {
        double t0 = now_s(); seeds = sd; status = "ok";
        const u64 stopKey = stopDst >= 0 ? (u64)stopDst * Q : 0; u32 dLim = UINT32_MAX; std::vector<u32> lay{0};   // lay is only filled with stopDst >= 0 (memory)
        M.reset(1 << 12); keys.clear(); soff.clear(); succ.clear(); lidx.clear(); par.clear(); npred.clear(); rseed.clear();
        keys.push_back(SENT); par.push_back(UINT32_MAX); M.get(SENT, 0);
        for (size_t i = 0; i < keys.size(); i++) {
            soff.push_back(succ.size());
            u64 k = keys[i];
            auto add = [&](u64 li, u64 nk) { auto g = M.get(nk, (u32)keys.size());
                if (g.second) { keys.push_back(nk); par.push_back(i); if (stopDst >= 0) { lay.push_back(lay[i] + 1); if (nk == stopKey && dLim == UINT32_MAX) dLim = lay[i] + 1; } }
                succ.push_back(g.first); lidx.push_back(li); };
            if (k == SENT) {
                for (size_t si = 0; si < seeds.size(); si++) { int r = 0;
                    for (word_t m = F; m; m &= m - 1, r++) { add(si * nF + r, seeds[si] * Q + __builtin_ctz(m)); rseed.push_back((u32)si); } }
                npred.push_back(0); continue;
            }
            u64 v = k / Q; int j = (int)(k % Q);
            if (j == 0 || (dLim != UINT32_MAX && lay[i] >= dLim)) { npred.push_back(0); continue; }
            size_t np = X.expand(v, j, [&](u64 li, u64 s, int q) { add(li, s * Q + q); });
            npred.push_back((uint8_t)std::min<size_t>(np, 255));
            if (keys.size() > A.maxNodes || succ.size() > A.maxEdges) { status = "too_large"; break; }
            if ((i & 0xfff) == 0 && now_s() - t0 > A.timeout) { status = "timeout"; break; }
        }
        R = keys.size(); E = succ.size(); soff.push_back(E);
        while (soff.size() < R + 1) soff.push_back(E);
        tBuild = now_s() - t0;
    }
    // liveness for target dst (-1: any accepting node) restricted to the seeds in use (null: all)
    void live(long long dst, const std::vector<char> *use) {
        double t0 = now_s(); useSeed = use;
        if (shortest) shortestEdges();
        GView g = view();
        livenessOf(g, dst, nullptr, alive0);
        seedLive0.assign(seeds.size(), 0);
        for (u64 e = soff[0]; e < soff[1]; e++) if (alive0[succ[e]]) seedLive0[rseed[e]] = 1;
        restrictOf(g, alive0, use, alive);
        HStats s = statsOf(g, alive, use); Hn = s.nodes; He = s.edges; Hhash = s.hash; leaves = s.leaves;
        hcnt.assign(R, 0); deadcnt.assign(R, 0); hindeg.assign(R, 0);
        for (size_t i = 0; i < R; i++) if (alive[i]) for (u64 e = soff[i]; e < soff[i + 1]; e++) if (edgeOk(i, e)) {
            if (alive[succ[e]]) { hcnt[i]++; hindeg[succ[e]]++; } else deadcnt[i]++; }
        tLive = now_s() - t0;
    }
    // work to explore the dead closure of the given start nodes (capped at CAP); skip: nodes not to enter
    u64 closure(const std::vector<u32> &start, const std::vector<char> *skip) {
        if (stamp.size() < R) stamp.resize(R, 0);
        if (++curStamp == 0) { std::fill(stamp.begin(), stamp.end(), 0); curStamp = 1; }
        std::vector<u32> q; u64 cnt = 0;
        for (u32 y : start) if (stamp[y] != curStamp && !(skip && (*skip)[y])) { stamp[y] = curStamp; q.push_back(y); }
        for (size_t h = 0; h < q.size() && cnt < CAP; h++) { u32 z = q[h]; cnt += 1 + npred[z];
            for (u64 f = soff[z]; f < soff[z + 1]; f++) { u32 w = succ[f]; if (stamp[w] != curStamp && !(skip && (*skip)[w])) { stamp[w] = curStamp; q.push_back(w); } } }
        return std::min<u64>(cnt, CAP);
    }
    // cost(x): sum over the dead successors y of x of the work of exploring y's dead closure
    // (work(z) = 1 + number of labels of z's graph node active for its state); INFC if a branch hits CAP
    void costs() {
        double t0 = now_s();
        cost.assign(R, 0); clos.assign(R, UINT32_MAX); deadWork = 0;
        for (size_t i = 0; i < R; i++) if (!alive0[i]) deadWork += 1 + npred[i];
        std::vector<u32> one(1);
        for (size_t i = 0; i < R; i++) {
            if (!alive[i]) continue;
            u64 c = 0; bool inf = false;
            for (u64 e = soff[i]; e < soff[i + 1]; e++) {
                if (!edgeOk(i, e)) continue;
                u32 y = succ[e]; if (alive[y]) continue;
                if (clos[y] == UINT32_MAX) { one[0] = y; clos[y] = (u32)closure(one, nullptr); }
                if (clos[y] >= CAP) { inf = true; break; }
                c += clos[y];
            }
            cost[i] = inf ? INFC : c;
        }
        scc();
        tCost = now_s() - t0;
    }
    void scc() {   // SCCs of H (Tarjan, iterative), sinks first
        std::vector<u32> tidx(R, UINT32_MAX), low(R, 0), stk; std::vector<char> onst(R, 0); std::vector<std::pair<u32, u64>> cs;
        compOf.assign(R, UINT32_MAX); compStart.clear(); compNodes.clear(); compMin.clear(); nc = 0; u32 counter = 0;
        for (u32 s0 = 0; s0 < R; s0++) {
            if (!alive[s0] || tidx[s0] != UINT32_MAX) continue;
            cs.clear(); cs.push_back({s0, soff[s0]}); tidx[s0] = low[s0] = counter++; stk.push_back(s0); onst[s0] = 1;
            while (!cs.empty()) {
                u32 v = cs.back().first; u64 e = cs.back().second;
                if (e < soff[v + 1]) {
                    cs.back().second = e + 1; u32 w = succ[e];
                    if (!alive[w] || !edgeOk(v, e)) continue;
                    if (tidx[w] == UINT32_MAX) { tidx[w] = low[w] = counter++; stk.push_back(w); onst[w] = 1; cs.push_back({w, soff[w]}); }
                    else if (onst[w]) low[v] = std::min(low[v], tidx[w]);
                } else {
                    if (low[v] == tidx[v]) {
                        compStart.push_back(compNodes.size()); u32 mn = UINT32_MAX;
                        for (;;) { u32 w = stk.back(); stk.pop_back(); onst[w] = 0; compOf[w] = nc; compNodes.push_back(w); mn = std::min(mn, w); if (w == v) break; }
                        compMin.push_back(mn); nc++;
                    }
                    cs.pop_back();
                    if (!cs.empty()) { u32 p = cs.back().first; low[p] = std::min(low[p], low[v]); }
                }
            }
        }
        compStart.push_back(compNodes.size());
    }
    // region pass (as tunnels.cpp): every SCC subtree satisfies extra work <= cc * output + w0
    // pre: nodes stored beforehand (their cost does not count)
    void regionPass(double cc, long long w0, bool infinite, u64 &cut, u64 &maxRW, const std::vector<char> *pre = nullptr) {
        stored.assign(R, 0); cut = 0; maxRW = 0;
        if (infinite || Hn == 0) return;
        if (pre) stored = *pre;
        std::vector<u64> subC(nc, 0), subOut(nc, 0); std::vector<std::pair<u32, u32>> items, ch; std::vector<u32> tmp;
        auto viol = [&](u64 nInf, u64 csum, u64 co) { return nInf > 0 || (double)csum > cc * (double)co + (double)w0; };
        for (u32 c = 0; c < nc; c++) {
            u64 oc = 0, oo = 0, nInf = 0;
            for (u64 k = compStart[c]; k < compStart[c + 1]; k++) { u32 x = compNodes[k]; oo += hcnt[x]; if (stored[x]) continue; if (cost[x] >= INFC) nInf++; else oc += cost[x]; }
            if (viol(nInf, oc, oo)) {
                tmp.assign(compNodes.begin() + compStart[c], compNodes.begin() + compStart[c + 1]);
                std::sort(tmp.begin(), tmp.end(), [&](u32 a, u32 b) { return cost[a] != cost[b] ? cost[a] > cost[b] : a < b; });
                for (u32 x : tmp) { if (!viol(nInf, oc, oo)) break; if (cost[x] == 0) break; if (rootFixed && x == 0) continue; if (stored[x]) continue;
                    stored[x] = 1; if (cost[x] >= INFC) nInf--; else oc -= cost[x]; }
            }
            items.clear();
            for (u64 k = compStart[c]; k < compStart[c + 1]; k++) { u32 x = compNodes[k];
                for (u64 e = soff[x]; e < soff[x + 1]; e++) { u32 y = succ[e]; if (alive[y] && edgeOk(x, e) && compOf[y] != c) items.push_back({compOf[y], y}); } }
            std::sort(items.begin(), items.end()); items.erase(std::unique(items.begin(), items.end()), items.end());
            ch.clear(); u64 tc = oc, to = oo;
            for (size_t k = 0; k < items.size(); k++) if (k == 0 || items[k].first != items[k - 1].first) {
                u32 d = items[k].first; ch.push_back({d, (u32)k}); tc += subC[d];
                if (compOf[par[compMin[d]]] == c) to += subOut[d]; }
            auto credit = [&](u32 d) -> u64 { u32 p = par[compMin[d]]; return (p != UINT32_MAX && compOf[p] == c) ? subOut[d] : 0; };
            auto value = [&](u32 d) -> double { return (double)subC[d] - cc * (double)credit(d); };
            std::sort(ch.begin(), ch.end(), [&](const std::pair<u32, u32> &a, const std::pair<u32, u32> &b) {
                double va = value(a.first), vb = value(b.first); return va != vb ? va > vb : compMin[a.first] < compMin[b.first]; });
            for (auto &d : ch) {
                if (!viol(0, tc, to)) break;
                if (value(d.first) <= 0) break;
                for (size_t k = d.second; k < items.size() && items[k].first == d.first; k++) if (!stored[items[k].second]) { stored[items[k].second] = 1; cut++; }
                tc -= subC[d.first]; to -= credit(d.first);
            }
            subC[c] = tc; subOut[c] = to; maxRW = std::max(maxRW, tc);
        }
    }
    // The region pass bounds the work of a region by summing its child subregions, so a costly
    // node shared by many parents is counted once per parent and can force many cuts. Two other
    // stored sets also satisfy the bound: the one for W = 0 (every node with dead successors is
    // stored) and the region pass after storing beforehand the costly nodes with several parents
    // in H. Keep the candidate with the smallest encoding.
    void selectBest(double cc, long long w0, bool infinite, u64 &cut, u64 &maxRW) {
        if (infinite || Hn == 0) { regionPass(cc, w0, infinite, cut, maxRW); return; }
        std::vector<char> best; u64 bestBits = UINT64_MAX, bc = 0, bm = 0;
        auto consider = [&](u64 c, u64 m) { Encoded e; u64 a, b, d; encode(e, false, a, b, d);
            if (e.bits() < bestBits) { bestBits = e.bits(); best = stored; bc = c; bm = m; } };
        u64 c, m;
        regionPass(cc, w0, false, c, m); consider(c, m);
        regionPass(0.0, 0, false, c, m); consider(c, m);
        std::vector<char> pre(R, 0); bool any = false;
        for (u32 i = 0; i < R; i++) if (alive[i] && hindeg[i] > 1 && cost[i] > 0 && !(rootFixed && i == 0)) { pre[i] = 1; any = true; }
        if (any) { regionPass(cc, w0, false, c, m, &pre); consider(c, m); }
        stored = best; cut = bc; maxRW = bm;
    }
    // encode the stored nodes (explicitAll: every internal node of H, with its live list)
    void encode(Encoded &En, bool explicitAll, u64 &S, u64 &SE, u64 &replay) {
        std::vector<u32> ids; S = SE = replay = 0;
        for (u32 i = 0; i < R; i++) if (alive[i]) {
            bool st = explicitAll ? hcnt[i] > 0 : stored[i];
            if (rootFixed && i == 0) st = false;
            if (st) { S++; SE += hcnt[i]; if (i) ids.push_back(i); }
            else replay += std::min<u64>(cost[i], CAP);
        }
        std::sort(ids.begin(), ids.end(), [&](u32 a, u32 b) { return keys[a] < keys[b]; });
        std::vector<u64> sk; std::vector<std::pair<bool, std::vector<u64>>> lists;
        auto listOf = [&](u32 x) {
            std::vector<u64> L, D;
            for (u64 e = soff[x]; e < soff[x + 1]; e++) if (edgeOk(x, e)) (alive[succ[e]] ? L : D).push_back(lidx[e]);
            bool dead = !explicitAll && D.size() < L.size();
            lists.push_back({dead, dead ? D : L}); };
        bool root = alive[0] && !rootFixed && (explicitAll ? hcnt[0] > 0 : stored[0]);
        if (root) listOf(0);
        for (u32 x : ids) { sk.push_back(keys[x]); listOf(x); }
        En.build(sk, lists, root);
    }
    // size with global node ids (explicit H): keys + offsets + one id per edge
    u64 globalBits(const Encoded &EH) {
        if (!EH.n) return 0;
        u64 maxKey = 1; for (size_t i = 1; i < R; i++) maxKey = std::max(maxKey, keys[i]);
        unsigned w = 64 - __builtin_clzll(maxKey);
        std::vector<u32> ids; for (u32 i = 1; i < R; i++) if (alive[i] && hcnt[i] > 0) ids.push_back(i);
        std::sort(ids.begin(), ids.end(), [&](u32 a, u32 b) { return keys[a] < keys[b]; });
        sdsl::sd_vector_builder ob(He + 1, ids.size()); u64 acc = 0;
        for (u32 x : ids) { ob.set(acc); acc += hcnt[x]; }
        sdsl::sd_vector<> o2(ob);
        return 8 * (sdsl::size_in_bytes(EH.keys) + sdsl::size_in_bytes(o2)) + He * w;
    }
};

// ------------------------------------------------------------------ PathFinder simulation
// ALL SHORTEST WALKS of PathFinder, the path engine of MillenniumDB (commit 1d928c3,
// src/query/executor/binding_iter/paths/all_shortest_walks): from the constant, a BFS over
// (node, state of the minimal DFA) that explores everything reachable (BFSEnum for c-to-v / v-to-c,
// BFSCheck for c-to-c, which also empties its queue; v-to-v runs it from each start node). It keeps
//   - a SearchState (40 B) per visited state, in a boost::unordered_node_set (one allocation each);
//   - a TransitionLinkedList (32 B) per arrival at a state with its optimal distance, in an arena of
//     blocks of 4096;
//   - with several final DFA states, the optimal distance of each node reached in a final state
//     (boost::unordered_flat_map<uint64_t, size_t>; BFSEnum<true> only).
// pf_bytes_structs counts only the structs; pf_bytes adds the containers with the constants below.
static const u64 PF_STATE = 40, PF_TRANS = 32, PF_ARENA = 4096;
static const u64 PF_MALLOC_STATE = 48;    // malloc(40) on glibc: 40 + 8 B header, rounded to 16
// boost::unordered (foa) tables: groups of 15 slots plus 16 B of metadata, a power of two of groups,
// rehashed beyond a load of 0.875. A node set stores a pointer per slot, the flat map the pair (16 B).
static u64 pfTableBytes(u64 n, u64 slotBytes) {
    u64 groups = 1; while ((double)groups * 15 * 0.875 < (double)n) groups <<= 1;
    return groups * (15 * slotBytes + 16);
}
struct PFStats {
    std::string status = "ok"; u64 states = 0, trans = 0, edges = 0, idx = 0, answers = 0, bytesStructs = 0, bytes = 0;
    double paths = 0, t = 0; std::unordered_map<u64, u32> ansDist;   // answer -> optimal distance
    std::vector<u64> walkNodes;   // graph nodes on the shortest walks to the answers (sorted), for the check
};
static PFStats simulatePF(Side &sd, u64 src, long long dst, bool stop = false) {
    PFStats o; const MinDFA &D = sd.dfa; double t0 = now_s(); const u64 nD = D.n();
    std::vector<u64> node{src}; std::vector<u32> st{(u32)D.start}, dist{0}; std::vector<double> cnt{1};
    std::vector<std::pair<u32, u32>> tl;   // the transitions (from, to), only for the check
    HMap M; M.reset(1 << 12); M.get(src * nD + D.start, 0);
    if (dst >= 0 && (u64)dst == src && D.fin[D.start]) {   // BFSCheck: the empty walk is the only shortest one
        o.states = 1; o.paths = 1; o.answers = 1; o.ansDist[src] = 0;
        o.t = now_s() - t0;   // as below: walkNodes only feeds pf_check
        o.walkNodes.push_back(src);
    } else {
        std::vector<Backend::Seg> segs; std::vector<uint64_t> vals; u32 dLim = UINT32_MAX;   // --early-stop: layer of the target
        for (size_t i = 0; i < node.size(); i++) {
            if (dist[i] >= dLim) break;
            u64 v = node[i]; u32 c = st[i]; const auto &T = D.tr[c];
            o.idx += T.size(); if (T.empty()) continue;
            sd.X.G.labelsMask(sd.X.prep, v, D.rep[c], segs);
            for (auto &e : segs) {
                auto it = std::lower_bound(T.begin(), T.end(), std::make_pair((u64)e.p, (u32)0));
                if (it == T.end() || it->first != e.p) continue;
                u32 to = it->second; sd.X.G.values(e, vals);
                for (u64 w : vals) {
                    o.edges++;
                    auto g = M.get(w * nD + to, (u32)node.size());
                    if (g.second) { if (stop && (long long)w == dst && D.fin[to] && dLim == UINT32_MAX) dLim = dist[i] + 1;
                        node.push_back(w); st.push_back(to); dist.push_back(dist[i] + 1); cnt.push_back(cnt[i]); o.trans++; tl.push_back({(u32)i, g.first}); }
                    else if (dist[g.first] == dist[i] + 1) { cnt[g.first] += cnt[i]; o.trans++; tl.push_back({(u32)i, g.first}); }
                }
            }
            if (node.size() > A.maxNodes || o.trans > A.maxEdges) { o.status = "too_large"; break; }
            if ((i & 0xfff) == 0 && now_s() - t0 > A.timeout) { o.status = "timeout"; break; }
        }
        o.states = node.size();
        // answers: nodes reached in a final state, at their optimal distance (over the final states)
        for (size_t i = 0; i < node.size(); i++) if (D.fin[st[i]]) {
            auto r = o.ansDist.emplace(node[i], dist[i]); if (!r.second && dist[i] < r.first->second) r.first->second = dist[i]; }
        for (size_t i = 0; i < node.size(); i++) if (D.fin[st[i]] && o.ansDist[node[i]] == dist[i] && (dst < 0 || node[i] == (u64)dst)) o.paths += cnt[i];
        o.answers = dst < 0 ? o.ansDist.size() : o.ansDist.count((u64)dst);
        // t_pf stops here: BFS, answers, distances and paths. What follows only feeds pf_check
        // (the only check cost kept inside t_pf is storing the transitions in tl).
        // The byte model below is plain arithmetic and also stays outside t_pf.
        o.t = now_s() - t0;
        // states that reach an answer at its optimal distance through the transitions kept
        std::vector<char> live(node.size(), 0);
        for (size_t i = 0; i < node.size(); i++) live[i] = D.fin[st[i]] && o.ansDist[node[i]] == dist[i] && (dst < 0 || node[i] == (u64)dst);
        std::sort(tl.begin(), tl.end(), [&](const std::pair<u32, u32> &a, const std::pair<u32, u32> &b) { return dist[a.first] > dist[b.first]; });
        for (auto &e : tl) if (live[e.second]) live[e.first] = 1;
        for (size_t i = 0; i < node.size(); i++) if (live[i]) o.walkNodes.push_back(node[i]);
        std::sort(o.walkNodes.begin(), o.walkNodes.end()); o.walkNodes.erase(std::unique(o.walkNodes.begin(), o.walkNodes.end()), o.walkNodes.end());
    }
    o.bytesStructs = o.states * PF_STATE + o.trans * PF_TRANS;
    u64 blocks = std::max<u64>(1, (o.trans + PF_ARENA - 1) / PF_ARENA);
    o.bytes = o.states * PF_MALLOC_STATE + pfTableBytes(o.states, 8) + blocks * PF_ARENA * PF_TRANS
            + (dst < 0 && D.nFinal() > 1 ? pfTableBytes(o.ansDist.size(), 16) : 0);
    return o;
}
// The answers of H and their walk lengths (layer of (t, 0) minus 1), and the graph nodes of H_sp,
// must be those of PathFinder: two different automata for the same language.
static bool pfAgrees(const Side &sd, const PFStats &pf, long long dst) {
    std::vector<u64> hn;
    for (size_t i = 1; i < sd.R; i++) if (sd.alive[i]) hn.push_back(sd.keys[i] / sd.Q);
    std::sort(hn.begin(), hn.end()); hn.erase(std::unique(hn.begin(), hn.end()), hn.end());
    if (hn != pf.walkNodes) return false;
    std::unordered_map<u64, u32> h;
    for (size_t i = 1; i < sd.R; i++) if (sd.keys[i] % sd.Q == 0) {
        u64 t = sd.keys[i] / sd.Q; if (dst >= 0 && t != (u64)dst) continue;
        h[t] = sd.dist[i] - 1; }
    if (h.size() != pf.ansDist.size() && dst < 0) return false;
    for (auto &kv : h) { auto it = pf.ansDist.find(kv.first); if (it == pf.ansDist.end() || it->second != kv.second) return false; }
    return dst >= 0 ? h.size() == pf.answers : true;
}

// ------------------------------------------------------------------ decoder
// BFS from the root over the seeds seedsD; stored nodes use their list (live list: only those
// children; dead list: all children but those), the others are fully expanded with the Ring
// (explicitMode: they are leaves). Then liveness (explicitMode: alive = stored or accepting).
struct DGraph {
    std::vector<u64> keys, soff; std::vector<u32> succ, rseed, dist; std::vector<char> alive0, alive, seedLive; u64 Q = 1;
    HMap M;
    GView view() const { return GView{&keys, &soff, &succ, &rseed, Q}; }
};
struct DecOut { u64 visited = 0, expanded = 0, children = 0; };
static DecOut decodeSide(Side &sd, const Encoded &E, bool explicitMode, const std::vector<u64> &seedsD, long long dst, DGraph &g, bool stop = false) {
    DecOut o; u64 e0 = sd.X.nExp, c0 = sd.X.nOut; u64 Q = sd.Q;
    g.Q = Q; g.M.reset(1 << 12); g.keys.clear(); g.soff.clear(); g.succ.clear(); g.rseed.clear(); g.dist.clear();
    g.keys.push_back(SENT); g.M.get(SENT, 0); g.dist.push_back(0);
    // --shortest: the BFS gives the layer of each node and only edges to the next layer are kept. A node
    // of H_sp gets its true layer (the shortest walks to it are in H_sp), and a node with a wrong (larger)
    // layer cannot reach an accepting node through kept edges, so liveness yields exactly H_sp.
    const bool sp = sd.shortest; u32 dcur = 0;
    // --early-stop (dst fixed): no node of the layer where the target (dst, 0) is first inserted is expanded
    const bool es = stop && sp && dst >= 0; const u64 stopKey = es ? (u64)dst * Q : 0; u32 dLim = UINT32_MAX;
    auto add = [&](u64 nk) {
        auto r = g.M.get(nk, (u32)g.keys.size());
        if (r.second) { g.keys.push_back(nk); g.dist.push_back(dcur + 1); if (es && nk == stopKey && dLim == UINT32_MAX) dLim = dcur + 1; }
        else if (sp && g.dist[r.first] != dcur + 1) return false;
        g.succ.push_back(r.first); return true; };
    BitReader br; bool dead; std::vector<u64> L;
    for (size_t i = 0; i < g.keys.size(); i++) {
        g.soff.push_back(g.succ.size());
        u64 k = g.keys[i];
        if (k == SENT) {
            if (E.rootStored) { br.bv = &E.rec.bv; br.p = 0; E.readList(br, dead, L); } else { L.clear(); dead = true; }
            size_t t = 0;
            for (size_t si = 0; si < seedsD.size(); si++) { int r = 0;
                for (word_t m = sd.F; m; m &= m - 1, r++) {
                    u64 li = si * sd.nF + r; bool inList = t < L.size() && L[t] == li; if (inList) t++;
                    if (inList != dead && add(seedsD[si] * Q + __builtin_ctz(m))) g.rseed.push_back((u32)si); } }
            continue;
        }
        dcur = g.dist[i];
        u64 v = k / Q; int j = (int)(k % Q);
        if (j == 0 || dcur >= dLim) continue;
        if (E.find(k, br)) {
            E.readList(br, dead, L);
            if (!dead) sd.X.decodeList(v, j, L, [&](u64 s, int q) { add(s * Q + q); });
            else { size_t t = 0; sd.X.expand(v, j, [&](u64 li, u64 s, int q) {
                    while (t < L.size() && L[t] < li) t++;
                    if (t < L.size() && L[t] == li) return;
                    add(s * Q + q); }); }
        } else if (!explicitMode) sd.X.expand(v, j, [&](u64, u64 s, int q) { add(s * Q + q); });
    }
    g.soff.push_back(g.succ.size());
    size_t R = g.keys.size(); o.visited = R; o.expanded = sd.X.nExp - e0; o.children = sd.X.nOut - c0;
    GView gv = g.view();
    if (explicitMode) {
        std::vector<char> acc(R, 0); BitReader b2;
        for (size_t i = 1; i < R; i++) { u64 kk = g.keys[i];
            acc[i] = E.find(kk, b2) || (kk % Q == 0 && (dst < 0 || (long long)(kk / Q) == dst)); }
        livenessOf(gv, dst, &acc, g.alive0);
    } else livenessOf(gv, dst, nullptr, g.alive0);
    g.seedLive.assign(seedsD.size(), 0);
    for (u64 e = g.soff[0]; e < g.soff[1]; e++) if (g.alive0[g.succ[e]]) g.seedLive[g.rseed[e]] = 1;
    return o;
}

// ------------------------------------------------------------------ seed lists (v-to-v)
struct SeedInfo { BitWriter rec; u64 explored = 0, work = 0, listLen = 0; bool liveList = false;
    u64 bits() const { return rec.n; } };
static void encodeSeeds(SeedInfo &si, bool liveList, const std::vector<u64> &idx, bool firstLeft) {
    si.rec = BitWriter(); si.liveList = liveList; si.listLen = idx.size();
    si.rec.put(firstLeft ? 1 : 0, 1); si.rec.put(liveList ? 1 : 0, 1); si.rec.gamma(idx.size() + 1);
    u64 prev = 0; bool first = true;
    for (u64 x : idx) { si.rec.delta(first ? x + 1 : x - prev); prev = x; first = false; }
    si.rec.finish();
}
static std::vector<u64> decodeSeeds(const SeedInfo &si, const std::vector<u64> &C) {
    BitReader br{&si.rec.bv, 0}; br.get(1); bool liveList = br.get(1); u64 c = br.gamma() - 1;
    std::vector<u64> idx; u64 prev = 0;
    for (u64 i = 0; i < c; i++) { u64 d = br.delta(); prev = i ? prev + d : d - 1; idx.push_back(prev); }
    std::vector<u64> D;
    if (liveList) { for (u64 i : idx) D.push_back(C[i]); }
    else { size_t t = 0; for (u64 i = 0; i < C.size(); i++) { if (t < idx.size() && idx[t] == i) { t++; continue; } D.push_back(C[i]); } }
    return D;
}

#ifdef ADJ_GRAPH
// ------------------------------------------------------------------ automata comparison (--automata)
// Product graphs of the same query with different automata, on adjacency lists:
//   glushkov   standard (forward) Glushkov automaton: initial state 0 plus one state per position;
//              all the transitions ENTERING a state have the same label
//   rglushkov  the one we use (reverse of the Glushkov automaton of the reversed expression): one
//              state per position plus a final state; all the transitions LEAVING a state have the
//              same label. It is also what remains of Thompson's automaton after removing the
//              epsilon-transitions and keeping only the states with a labeled outgoing transition.
//   thompson   Thompson's construction (as in the Dragon book), whose epsilon-transitions become
//              product edges that do not access the graph
// The query is normalized to "start at constant c and read e along the edges": c-to-v (c, r, ?y)
// reads r from c; v-to-c (?x, r, c) reads the inverse of r from c. A label is a predicate id p
// (forward edge) or p + M (backward edge).
struct ANfa {
    std::string name; u32 nS = 0; std::vector<u32> init; std::vector<char> fin;
    std::vector<std::vector<std::pair<long long, u32>>> tr;   // (label, target); label -1 = epsilon
    u64 nLab = 0, nEps = 0;
    u32 add() { tr.emplace_back(); fin.push_back(0); return nS++; }
    void edge(u32 a, long long l, u32 b) { tr[a].push_back({l, b}); if (l < 0) nEps++; else nLab++; }
};
// A union of single predicates, such as (a|b), is one position with a class of labels, as in
// Ring-RPQ's automaton (and a single class transition in Thompson's).
static bool aIsClass(const RxNode &x) { if (x.t != '|') return false; for (auto &c : x.k) if (c.t != 'p') return false; return true; }
typedef std::vector<long long> ALabs;
struct AInfo { bool nullable; std::vector<u32> first, last; };
static AInfo aGlushInfo(const RxNode &x, std::vector<ALabs> &letter, std::vector<std::set<u32>> &follow,
                        const std::function<long long(const std::string &)> &lab) {
    AInfo r;
    auto uni = [](std::vector<u32> &a, const std::vector<u32> &b) { a.insert(a.end(), b.begin(), b.end()); std::sort(a.begin(), a.end()); a.erase(std::unique(a.begin(), a.end()), a.end()); };
    if (x.t == 'p' || aIsClass(x)) { u32 p = letter.size(); ALabs L;
        if (x.t == 'p') L.push_back(lab(x.pred)); else for (auto &c : x.k) L.push_back(lab(c.pred));
        std::sort(L.begin(), L.end()); L.erase(std::unique(L.begin(), L.end()), L.end());
        letter.push_back(L); follow.emplace_back(); r.nullable = false; r.first = r.last = {p}; return r; }
    if (x.t == '|') { r.nullable = false; for (auto &c : x.k) { AInfo s = aGlushInfo(c, letter, follow, lab); r.nullable |= s.nullable; uni(r.first, s.first); uni(r.last, s.last); } return r; }
    if (x.t == '/') {
        r.nullable = true; bool firstDone = false;
        for (auto &c : x.k) { AInfo s = aGlushInfo(c, letter, follow, lab);
            for (u32 p : r.last) for (u32 q : s.first) follow[p].insert(q);
            if (!firstDone) uni(r.first, s.first);
            if (!s.nullable) { firstDone = true; r.last = s.last; } else uni(r.last, s.last);
            r.nullable &= s.nullable; }
        return r;
    }
    AInfo s = aGlushInfo(x.k[0], letter, follow, lab);   // '*', '+', '?'
    r = s; if (x.t != '+') r.nullable = true;
    if (x.t != '?') for (u32 p : s.last) for (u32 q : s.first) follow[p].insert(q);
    return r;
}
static void aBuild(const RxNode &e, const std::function<long long(const std::string &)> &lab, ANfa &G, ANfa &D, ANfa &T) {
    std::vector<ALabs> letter; std::vector<std::set<u32>> follow;
    AInfo I = aGlushInfo(e, letter, follow, lab); u32 m = letter.size();
    // forward Glushkov: state 0 initial, position p -> state p + 1
    G = ANfa(); G.name = "glushkov"; for (u32 i = 0; i <= m; i++) G.add();
    G.init = {0}; G.fin[0] = I.nullable;
    for (u32 q : I.first) for (long long l : letter[q]) G.edge(0, l, q + 1);
    for (u32 p = 0; p < m; p++) { for (u32 q : follow[p]) for (long long l : letter[q]) G.edge(p + 1, l, q + 1); }
    for (u32 p : I.last) G.fin[p + 1] = 1;
    // reverse Glushkov of the reversed expression: position p -> state p (next letter: letter[p]); final state m
    D = ANfa(); D.name = "rglushkov"; for (u32 i = 0; i <= m; i++) D.add();
    D.fin[m] = 1; D.init.assign(I.first.begin(), I.first.end()); if (I.nullable) D.init.push_back(m);
    std::vector<char> isLast(m, 0); for (u32 p : I.last) isLast[p] = 1;
    for (u32 p = 0; p < m; p++) for (long long l : letter[p]) { for (u32 q : follow[p]) D.edge(p, l, q); if (isLast[p]) D.edge(p, l, m); }
    // Thompson (Dragon book: concatenation merges the end of one part with the start of the next)
    T = ANfa(); T.name = "thompson";
    std::function<u32(const RxNode &, u32)> th = [&](const RxNode &x, u32 s) -> u32 {
        if (x.t == 'p') { u32 t = T.add(); T.edge(s, lab(x.pred), t); return t; }
        if (aIsClass(x)) { u32 t = T.add(); std::set<long long> L; for (auto &c : x.k) L.insert(lab(c.pred)); for (long long l : L) T.edge(s, l, t); return t; }
        if (x.t == '/') { u32 c = s; for (auto &k : x.k) c = th(k, c); return c; }
        if (x.t == '|') { u32 f = T.add(); for (auto &k : x.k) { u32 si = T.add(); T.edge(s, -1, si); u32 ei = th(k, si); T.edge(ei, -1, f); } return f; }
        u32 s1 = T.add(); u32 e1 = th(x.k[0], s1); u32 f = T.add();
        T.edge(s, -1, s1); T.edge(e1, -1, f);
        if (x.t != '+') T.edge(s, -1, f);
        if (x.t != '?') T.edge(e1, -1, s1);
        return f;
    };
    u32 s0 = T.add(); T.init = {s0}; u32 f = th(e, s0); T.fin[f] = 1;
}
struct AStats { std::string status = "ok"; u64 R = 0, E = 0, Eeps = 0, lookups = 0, scanned = 0, Hn = 0, He = 0, answers = 0; double tProd = 0, tLive = 0; };
static AStats aProduct(const Backend &g, const ANfa &N, u64 c) {
    AStats s; double t0 = now_s();
    HMap hm; hm.reset(1 << 12); std::vector<u64> keys; std::vector<u32> from, to;
    const u64 Q = N.nS;
    auto node = [&](u64 v, u32 q) -> u32 { auto r = hm.get(v * Q + q, keys.size()); if (r.second) keys.push_back(v * Q + q); return r.first; };
    for (u32 q : N.init) node(c, q);
    // per state: its labeled transitions grouped by label
    std::vector<std::vector<std::pair<long long, std::vector<u32>>>> byLab(Q);
    for (u32 q = 0; q < Q; q++) { std::map<long long, std::vector<u32>> m; for (auto &t : N.tr[q]) if (t.first >= 0) m[t.first].push_back(t.second);
        for (auto &it : m) byLab[q].push_back(it); }
    const u64 M = g.maxP();
    for (size_t i = 0; i < keys.size(); i++) {
        if (keys.size() > A.maxNodes || from.size() > A.maxEdges) { s.status = "too_large"; break; }
        if ((i & 0xfff) == 0 && now_s() - t0 > A.timeout) { s.status = "timeout"; break; }
        u64 v = keys[i] / Q; u32 q = keys[i] % Q;
        for (auto &t : N.tr[q]) if (t.first < 0) { u32 y = node(v, t.second); from.push_back(i); to.push_back(y); s.Eeps++; }
        for (auto &bl : byLab[q]) {
            long long l = bl.first; bool f = (u64)l <= M; const auto &d = f ? g.fw : g.bw; u64 a, cnt;
            s.lookups++;
            if (!g.range(d, v, (u32)(f ? l : l - M), a, cnt)) continue;
            s.scanned += cnt;
            for (u64 k = 0; k < cnt; k++) { u64 w = d.nb[a + k]; for (u32 q2 : bl.second) { u32 y = node(w, q2); from.push_back(i); to.push_back(y); } }
        }
    }
    s.R = keys.size(); s.E = from.size(); s.tProd = now_s() - t0;
    if (s.status != "ok") return s;
    t0 = now_s();
    // live nodes: those that reach an accepting node (backward BFS over the reversed edges)
    std::vector<u64> off(s.R + 1, 0); for (u32 y : to) off[y + 1]++;
    for (u64 i = 0; i < s.R; i++) off[i + 1] += off[i];
    std::vector<u32> rin(s.E); { std::vector<u64> pos(off.begin(), off.end() - 1); for (u64 e = 0; e < s.E; e++) rin[pos[to[e]]++] = from[e]; }
    std::vector<char> live(s.R, 0); std::vector<u32> st; std::set<u64> ans;
    for (u64 i = 0; i < s.R; i++) if (N.fin[keys[i] % Q]) { live[i] = 1; st.push_back(i); ans.insert(keys[i] / Q); }
    while (!st.empty()) { u32 y = st.back(); st.pop_back(); for (u64 e = off[y]; e < off[y + 1]; e++) if (!live[rin[e]]) { live[rin[e]] = 1; st.push_back(rin[e]); } }
    for (u64 i = 0; i < s.R; i++) s.Hn += live[i];
    for (u64 e = 0; e < s.E; e++) s.He += live[to[e]];
    s.answers = ans.size(); s.tLive = now_s() - t0;
    return s;
}
// One query of the file in --automata mode. Writes three rows (glushkov, rglushkov, thompson) for a valid
// c-to-v / v-to-c query and returns true; v-to-v, c-to-c, blank lines, unknown IRIs or predicates and
// constants outside the graph write nothing.
static bool automataQuery(const Backend &graph, const std::string &line0, int qid, int lineNo,
                          std::unordered_map<std::string, u64> &map_SO, std::unordered_map<std::string, u64> &map_P,
                          u64 realMaxP, u64 maxNode, FILE *out) {
    ParsedQuery P;
    try { P = parseLine(line0, map_SO, map_P, realMaxP); } catch (std::exception &) { return false; }
    if (P.skip) return false;
    if (!P.subject_is_const == !P.object_is_const) return false;   // v-to-v or c-to-c
    bool cv = P.subject_is_const;
    u64 c = cv ? P.subject_id : P.object_id;
    if (c == 0 || c > maxNode) return false;
    const std::string &L = P.raw;
    size_t a = L.find(' '), b = L.rfind(' ');
    std::string path = L.substr(a + 1, b - a - 1);
    while (!path.empty() && path[0] == ' ') path.erase(0, 1);
    RxNode e;
    try { RxParser RP(path); e = RP.alt(); RP.ws(); if (RP.i != path.size()) return false; } catch (std::exception &) { return false; }
    if (!cv) e = RxParser::inv(e);
    const u64 M = graph.maxP(); bool bad = false;
    std::function<long long(const std::string &)> lab = [&](const std::string &x) -> long long {
        bool inv = x.size() > 1 && x[1] == '%'; std::string k = inv ? "<" + x.substr(2) : x;
        auto it = map_P.find(k); if (it == map_P.end()) { bad = true; return 0; } return (long long)(inv ? it->second + M : it->second); };
    ANfa G, D, T; aBuild(e, lab, G, D, T);
    if (bad) return false;
    const char *kind = cv ? "CV" : "VC";
    for (ANfa *N : {&G, &D, &T}) {
        AStats s = aProduct(graph, *N, c);
        fprintf(out, "%d,%d,%s,%s,%llu,%llu,%s,%u,%llu,%llu,%s,%llu,%llu,%llu,%llu,%llu,%llu,%llu,%llu,%.6f,%.6f\n", qid, lineNo, kind, P.shape.c_str(),
                (unsigned long long)(D.nS - 1), (unsigned long long)c, N->name.c_str(), N->nS, (unsigned long long)N->nLab, (unsigned long long)N->nEps, s.status.c_str(),
                (unsigned long long)s.R, (unsigned long long)s.E, (unsigned long long)s.Eeps, (unsigned long long)s.lookups, (unsigned long long)s.scanned,
                (unsigned long long)s.Hn, (unsigned long long)s.He, (unsigned long long)s.answers, s.tProd, s.tLive);
        fflush(out);
    }
    return true;
}
#endif

// ------------------------------------------------------------------ main
int main(int argc, char **argv) {
    if (argc < 3) { usage(); return 1; }
    A.index = argv[1]; A.queries = argv[2];
    for (int i = 3; i < argc; i++) {
        std::string a = argv[i]; auto nx = [&]() { if (i + 1 >= argc) { usage(); exit(1); } return std::string(argv[++i]); };
        if (a == "--out") A.out = nx();
        else if (a == "--budgets") { std::string b = nx(); A.budgets = b == "none" ? std::vector<long long>{} : splitNums<long long>(b); }
        else if (a == "--rel") { std::string s = nx(); A.rel = s == "none" ? std::vector<double>{} : splitNums<double>(s); }
        else if (a == "--rel-slack") A.relSlack = splitNums<long long>(nx());
        else if (a == "--pairs") A.pairs = std::stoi(nx());
        else if (a == "--pair-reverse") A.pairRev = true;
        else if (a == "--vv-union") A.vvUnion = true;
        else if (a == "--uniform-cost") gUniformCost = true;
        else if (a == "--shortest") A.shortest = true;
        else if (a == "--early-stop") A.earlyStop = true;
        else if (a == "--automata") A.automata = true;
        else if (a == "--reps") A.reps = std::max(1, std::stoi(nx()));
        else if (a == "--vv-reps") A.vvReps = std::max(1, std::stoi(nx()));
        else if (a == "--timeout") A.timeout = std::stod(nx());
        else if (a == "--max-nodes") A.maxNodes = std::stoull(nx());
        else if (a == "--max-edges") A.maxEdges = std::stoull(nx());
        else if (a == "--cost-cap") A.cap = std::stoull(nx());
        else if (a == "--pair-limit") A.pairLimit = std::stoull(nx());
        else if (a == "--max-queries") A.maxQueries = std::stoi(nx());
        else if (a == "--seed") A.seed = std::stoull(nx());
        else if (a == "--no-ring") A.noRing = true;
        else if (a == "--no-ring-vv") A.noRingVV = true;
        else if (a == "--ring-vv-derived") A.ringVVDerived = true;
        else if (a == "--baseline-timeout") A.baselineTimeout = std::stod(nx());
        else if (a == "--vv") A.vv = nx();
        else if (a == "--vv-derive") A.vvDerive = true;
        else if (a == "--vv-only") A.vvOnly = true;
        else if (a == "--vv-samples") A.vvSamples = std::stoi(nx());
        else if (a == "--no-per-source") A.vvSamples = 0;
        else if (a == "--only") { for (auto x : splitNums<long long>(nx())) A.only.insert((int)x); }
        else { usage(); return 1; }
    }
    CAP = A.cap;
    if (A.earlyStop && !A.shortest) { fprintf(stderr, "--early-stop needs --shortest\n"); return 1; }
    if (A.automata) {
#ifndef ADJ_GRAPH
        fprintf(stderr, "--automata needs the adjacency-list binary (adj-tunnels)\n"); return 1;
#endif
        if (A.shortest) { fprintf(stderr, "--automata cannot be combined with --shortest\n"); return 1; }
    }
    if (A.shortest) {
        if (A.vv == "auto") A.vv = "none";
        if (A.vv != "none" || A.vvUnion) { fprintf(stderr, "--shortest: v-to-v only as per-source samples (--vv none, no --vv-union)\n"); return 1; }
    }
    if (A.vv == "auto") A.vv = kSplitIndex ? "ring" : "bridge";
    if (!kSplitIndex && (A.vv == "ring" || A.vv == "both")) { fprintf(stderr, "--vv ring needs the RingAB index (ring-tunnels-split)\n"); return 1; }

    Backend graph; graph.load(A.index);
    setBaselineTimeout(A.baselineTimeout);
    fprintf(stderr, "index loaded (%s): %.3f bytes per triple, %llu triples\n", BACKEND_NAME,
            graph.bytesPerTriple(), (unsigned long long)graph.nTriples());
    std::unordered_map<std::string, u64> map_SO, map_P;
    auto readDict = [&](const std::string &f, std::unordered_map<std::string, u64> &m) {
        std::ifstream in(f); std::string d;
        while (std::getline(in, d)) { auto sp = d.find(' '); if (sp == std::string::npos) continue; std::string iri = d.substr(sp + 1);
            while (!iri.empty() && (iri.back() == '\r' || iri.back() == ' ')) iri.pop_back(); m[iri] = std::stoull(d.substr(0, sp)); } };
    readDict(A.index + ".SO", map_SO); readDict(A.index + ".P", map_P);
    fprintf(stderr, "dictionaries: %zu nodes, %zu predicates\n", map_SO.size(), map_P.size());
    const u64 realMaxP = graph.maxP();
    const u64 maxNode = graph.maxNode();
    std::mt19937_64 rng(A.seed);

    FILE *out = fopen(A.out.c_str(), "w");
    if (A.automata) fputs("qid,line,kind,shape,m,src,automaton,states,trans,eps_trans,status,R_nodes,R_edges,R_eps_edges,lookups,scanned,H_nodes,H_edges,answers,t_product,t_live\n", out);
    else
    { bool first = true; for (const char *c : COLS) { if (!first) fputc(',', out); first = false; fputs(c, out); } fputc('\n', out); }

    Side main1(graph), sideA(graph), sideB(graph), sideP(graph);
    main1.shortest = sideA.shortest = sideB.shortest = sideP.shortest = A.shortest;
    int curReps = A.reps;   // repetitions of the single-source expansions (per-source samples use --vv-reps)
    DGraph g1, g2;

    // ---------------- single-source pipeline (c-to-v, v-to-c, pairs, per-source samples)
    // sd is built from {src}; emits one row per budget configuration
    // plain H laid out for traversal (a PMR with dense node numbers): nodes numbered 0..|H|-1, CSR of
    auto plainOf = [&](Side &sd, u64 &bitsOut, double &tOut) {   // nodes numbered 0..|H|-1, CSR of successors, and the
            // product node (graph node, state) of each. Expansion = BFS from the root reporting every edge.
            std::vector<u32> id(sd.R, UINT32_MAX), order; for (u32 i = 0; i < sd.R; i++) if (sd.alive[i]) { id[i] = order.size(); order.push_back(i); }
            size_t n = order.size(); std::vector<u64> off(n + 1, 0), gam(n); std::vector<u32> adj;
            for (size_t k = 0; k < n; k++) { u32 x = order[k]; gam[k] = sd.keys[x];
                for (u64 e = sd.soff[x]; e < sd.soff[x + 1]; e++) if (sd.edgeOk(x, e) && sd.alive[sd.succ[e]]) adj.push_back(id[sd.succ[e]]);
                off[k + 1] = adj.size(); }
            u64 maxKey = 1; for (size_t k = 1; k < n; k++) maxKey = std::max(maxKey, gam[k]);
            unsigned wk = 64 - __builtin_clzll(maxKey), wi = n > 1 ? 64 - __builtin_clzll((u64)n - 1) : 1, wo = 64 - __builtin_clzll((u64)adj.size() + 1);
            bitsOut = ((u64)n * (wk + wo) + (u64)adj.size() * wi);   // gamma + offsets + edges, fixed width
            double tp = 1e30; u64 hh = 0, ne = 0;
            for (int r = 0; r < curReps && n > 0; r++) {
                double t0 = now_s(); std::vector<char> vis(n, 0); std::vector<u32> q{0}; vis[0] = 1; hh = 0; ne = 0;
                for (size_t h = 0; h < q.size(); h++) { u32 x = q[h];
                    for (u64 e = off[x]; e < off[x + 1]; e++) { u32 y = adj[e]; hh += mixh(gam[x], gam[y]); ne++; if (!vis[y]) { vis[y] = 1; q.push_back(y); } } }
                tp = std::min(tp, now_s() - t0);
            }
            if (n > 0 && (ne != sd.He || hh != sd.Hhash)) fprintf(stderr, "plain H mismatch\n");
            tOut = n > 0 ? tp : 0;
        };

    auto runSingle = [&](Side &sd, Row base, u64 src, long long dst, const std::vector<u64> *seedsP) {
        sd.live(dst, nullptr); sd.costs();
        base.set("sem", sd.shortest ? "shortest" : "all");
        if (sd.shortest) {
            PFStats pf = simulatePF(sd, src, dst, A.earlyStop);
            base.set("pf_status", pf.status); base.set("pf_dfa_states", sd.dfa.n()); base.set("pf_states", pf.states);
            base.set("pf_transitions", pf.trans); base.set("pf_edges", pf.edges); base.set("pf_idx_searches", pf.idx);
            base.set("pf_answers", pf.answers); { char b[64]; snprintf(b, sizeof b, "%.6g", pf.paths); base.f["pf_paths"] = b; }
            base.set("pf_bytes_structs", pf.bytesStructs); base.set("pf_bytes", pf.bytes); base.setd("t_pf", pf.t);
            bool ok = pf.status == "ok" && pfAgrees(sd, pf, dst);
            base.set("pf_check", ok ? 1 : 0);
            if (!ok && pf.status == "ok") fprintf(stderr, "q%s %s: PathFinder answers or distances differ from H\n", base.f["qid"].c_str(), base.f["kind"].c_str());
        }
        base.set("R_nodes", sd.R); base.set("R_edges", sd.E); base.set("H_nodes", sd.Hn); base.set("H_edges", sd.He);
        base.set("leaves", sd.leaves); base.set("Q", sd.Q); base.setd("t_product", sd.tBuild); base.setd("t_live", sd.tLive); base.setd("t_cost", sd.tCost);
        std::vector<u64> seedsD; if (seedsP) seedsD = *seedsP; else seedsD.push_back(src);
        // explicit H
        Encoded EH; u64 s0, s1, s2; sd.encode(EH, true, s0, s1, s2);
        double tHx = 1e30; DecOut dh;
        for (int r = 0; r < curReps; r++) { double t = now_s(); dh = decodeSide(sd, EH, true, seedsD, dst, g1, A.earlyStop);
            restrictOf(g1.view(), g1.alive0, nullptr, g1.alive); tHx = std::min(tHx, now_s() - t); }
        HStats hs = statsOf(g1.view(), g1.alive, nullptr);
        bool okH = sd.Hn == 0 || (hs.nodes == sd.Hn && hs.edges == sd.He && hs.hash == sd.Hhash);
        base.set("H_bits", EH.bits()); base.set("H_bits_global", sd.globalBits(EH)); base.setd("t_H_expand", tHx);
        { u64 pb; double pt; plainOf(sd, pb, pt); base.set("Hplain_bits", pb); base.setd("t_Hplain_expand", pt); }
        auto config = [&](const char *model, const std::string &bl, double cc, long long w0, bool inf) {
            Row r = base; double t0 = now_s(); u64 cut = 0, maxRW = 0;
            sd.selectBest(cc, w0, inf, cut, maxRW);
            double tSel = now_s() - t0; t0 = now_s();
            Encoded ES; u64 S, SE, replay; sd.encode(ES, false, S, SE, replay);
            if (inf) { replay = sd.deadWork; maxRW = sd.deadWork; }
            double tEnc = now_s() - t0;
            double tx = 1e30; DecOut d;
            for (int k = 0; k < curReps; k++) { double t = now_s(); d = decodeSide(sd, ES, false, seedsD, dst, g1, A.earlyStop);
                restrictOf(g1.view(), g1.alive0, nullptr, g1.alive); tx = std::min(tx, now_s() - t); }
            HStats ds = statsOf(g1.view(), g1.alive, nullptr);
            bool ok = ds.nodes == sd.Hn && ds.edges == sd.He && ds.hash == sd.Hhash;
            if (!ok || !okH) fprintf(stderr, "q%s %s %s %s: decode mismatch (%llu/%llu nodes, %llu/%llu edges)%s\n", base.f["qid"].c_str(), base.f["kind"].c_str(),
                                     model, bl.c_str(), (unsigned long long)ds.nodes, (unsigned long long)sd.Hn, (unsigned long long)ds.edges, (unsigned long long)sd.He, okH ? "" : " [explicit H]");
            r.set("model", model); r.set("budget", bl); r.set("S_nodes", S); r.set("S_edges", SE); r.set("S_list", ES.entries); r.set("S_bits", ES.bits());
            r.set("replay_model", replay); r.set("max_region_work", maxRW); r.setd("t_select", tSel); r.setd("t_encode", tEnc);
            r.setd("t_build", sd.tBuild + sd.tLive + sd.tCost + tSel + tEnc); r.setd("t_expand", tx);
            r.set("dec_visited", d.visited); r.set("dec_expanded", d.expanded); r.set("dec_children", d.children); r.set("verify", (ok && okH) ? 1 : 0);
            r.print(out);
        };
        for (long long wb : A.budgets) config("region", wb < 0 ? "inf" : std::to_string(wb), 0.0, wb < 0 ? 0 : wb, wb < 0);
        for (double cc : A.rel) for (long long ws : A.relSlack) { char b[64]; snprintf(b, sizeof b, "%gx+%lld", cc, ws); config("region_rel", b, cc, ws, false); }
        if (A.budgets.empty() && A.rel.empty()) { Row r = base; r.set("model", "explicit"); r.set("verify", okH ? 1 : 0); r.print(out); }
        fflush(out);
    };

    // ---------------- v-to-v with a split: seeds C, sides (first, second)
    struct Split { bool ok = false; std::string why, l, r, label; std::vector<u64> C; bool firstLeft = true; u64 pred = 0; bool srcCut = false; };
    auto bridgeSplit = [&](ParsedQuery &P) {
        Split s; auto pm = P.pred_map;
        RpqTree tree(P.rpq, pm, realMaxP); auto md = tree.getMandatoryData();
        if (md.empty()) { s.why = "no_mandatory"; return s; }
        u64 best = UINT64_MAX; int bi = -1; bool bsrc = false; u64 bp = 0;
        for (size_t i = 0; i < md.size(); i++) {
            for (int side = 0; side < 2; side++) {
                u64 p = side == 0 ? (u64)md[i].pred_b : (u64)md[i].pred_e;
                u64 cnt = side == 0 ? graph.countNodesWithLabel(p) : graph.countNodesReachedBy(p);
                if (cnt < best) { best = cnt; bi = i; bsrc = side == 0; bp = p; }
            }
        }
        int pos = bsrc ? md[bi].pos_b - 1 : md[bi].pos_e;
        std::tie(s.l, s.r) = tree.splitRpq(pos);
        s.C = bsrc ? graph.nodesWithLabel(bp) : graph.nodesReachedBy(bp);
        s.pred = bp; s.srcCut = bsrc; s.label = std::string(bsrc ? "src:" : "tgt:") + std::to_string(bsrc ? md[bi].pred_b : md[bi].pred_e);
        // first side: the one that starts with the cut label (its first step always succeeds) is run second
        s.firstLeft = bsrc; s.ok = true; return s;
    };
    auto ringSplit = [&](ParsedQuery &P) {
        Split s;
#ifdef RING_SPLIT
        std::vector<uint64_t> el;
        std::streambuf *old = std::cout.rdbuf(); std::ostringstream sink; std::cout.rdbuf(sink.rdbuf());   // split_rpq prints its weight
        bool ok = graph.split(P, s.l, s.r, el, s.firstLeft);
        std::cout.rdbuf(old);
        if (!ok) { s.why = "no_mandatory"; return s; }
        std::sort(el.begin(), el.end()); el.erase(std::unique(el.begin(), el.end()), el.end());
        s.C.assign(el.begin(), el.end()); s.label = "ring"; s.ok = true;
#endif
        return s;
    };
    auto regenerate = [&](ParsedQuery &P, const Split &s) {   // the decoder recomputes the candidate seeds
        std::vector<u64> C;
        if (s.label == "ring") { Split t = ringSplit(P); C = t.C; }
        else C = s.srcCut ? graph.nodesWithLabel(s.pred) : graph.nodesReachedBy(s.pred);
        return C;
    };

    auto runVV = [&](ParsedQuery &P, Row base, const std::string &mode, double tRing, long long ringAns) {
        const int vvR = A.vvReps > 0 ? A.vvReps : A.reps;
        double tS0 = now_s();
        Split sp = mode == "ring" ? ringSplit(P) : bridgeSplit(P);
        double tSplit = now_s() - tS0;
        base.set("kind", mode == "ring" ? "VVr" : "VVb");
        if (!sp.ok) { base.set("status", sp.why); base.print(out); fflush(out); return; }
        base.set("split", sp.label); base.set("first", sp.firstLeft ? "left" : "right");
        base.setd("t_ring", tRing); if (ringAns >= 0) base.set("ring_answers", ringAns);
        if (tRing >= A.baselineTimeout) base.set("ring_answers", "timeout");
        auto pm = P.pred_map;
        // sides: left = ?x rpq_l e (run backward from e, as v-to-c), right = e rpq_r ?y (as c-to-v)
        std::vector<Side *> sides; std::vector<bool> isLeft;
        auto addSide = [&](bool left) {
            Side *s = sides.empty() ? &sideA : &sideB; std::string aq;
            aq = left ? object_side_expression(sp.l) : subject_side_expression(sp.r);
            s->setup(aq, pm); s->rootFixed = true; sides.push_back(s); isLeft.push_back(left); };
        bool hasL = !sp.l.empty(), hasR = !sp.r.empty();
        if (sp.firstLeft) { if (hasL) addSide(true); if (hasR) addSide(false); }
        else { if (hasR) addSide(false); if (hasL) addSide(true); }
        for (Side *s : sides) if (s->Q > 8 * sizeof(word_t)) { base.set("status", "too_many_states"); base.print(out); for (Side *t : sides) t->clear(); return; }
        const std::vector<u64> &C = sp.C;
        base.set("seeds", C.size());
        // side 1 from all candidates, side 2 from those alive on side 1
        Side &s1 = *sides[0];
        s1.build(C);
        std::string status = s1.status;
        std::vector<u64> A1; std::vector<char> use1(C.size(), 0), use2;
        if (status == "ok") { s1.live(-1, nullptr); for (size_t i = 0; i < C.size(); i++) if (s1.seedLive0[i]) A1.push_back(C[i]); }
        Side *s2p = sides.size() > 1 ? sides[1] : nullptr;
        if (status == "ok" && s2p) { s2p->build(A1); status = s2p->status; }
        if (status != "ok") {
            base.set("status", status); u64 Rn = s1.R + (s2p ? s2p->R : 0), Re = s1.E + (s2p ? s2p->E : 0);
            base.set("R_nodes", Rn); base.set("R_edges", Re); base.print(out); fflush(out); for (Side *t : sides) t->clear(); return;
        }
        std::vector<u64> L; std::vector<char> live2;
        if (s2p) { s2p->live(-1, nullptr); use2.assign(A1.size(), 0);
            for (size_t i = 0; i < A1.size(); i++) if (s2p->seedLive0[i]) { use2[i] = 1; L.push_back(A1[i]); } }
        else L = A1;
        { size_t t = 0; for (size_t i = 0; i < C.size(); i++) { if (t < L.size() && L[t] == C[i]) { use1[i] = 1; t++; } } }
        s1.live(-1, &use1); s1.costs();
        if (s2p) { s2p->live(-1, &use2); s2p->costs(); }
        // cost of exploring a dead seed (decoder: side 1 from it, then side 2 if it is alive there)
        double tSeed0 = now_s();
        std::vector<std::pair<u64, u64>> deadSeeds;   // (cost, index in C)
        {
            std::vector<std::vector<u32>> roots1(C.size()); for (u64 e = s1.soff[0]; e < s1.soff[1]; e++) roots1[s1.rseed[e]].push_back(s1.succ[e]);
            std::vector<std::vector<u32>> roots2; if (s2p) { roots2.resize(A1.size()); for (u64 e = s2p->soff[0]; e < s2p->soff[1]; e++) roots2[s2p->rseed[e]].push_back(s2p->succ[e]); }
            std::vector<char> inH1(s1.alive.begin(), s1.alive.end());
            size_t a = 0;
            for (size_t i = 0; i < C.size(); i++) {
                bool inA1 = a < A1.size() && A1[a] == C[i]; size_t ai = a; if (inA1) a++;
                if (use1[i]) continue;
                u64 c;
                if (!inA1) c = s1.closure(roots1[i], nullptr);
                else { u64 c1 = s1.closure(roots1[i], &inH1), c2 = s2p ? s2p->closure(roots2[ai], nullptr) : 0;
                       c = (c1 >= CAP || c2 >= CAP) ? CAP : std::min<u64>(c1 + c2, CAP); }
                deadSeeds.push_back({c >= CAP ? INFC : c, i});
            }
            std::sort(deadSeeds.begin(), deadSeeds.end());
        }
        double tSeed = now_s() - tSeed0;
        u64 Rn = 0, Re = 0, Hn = 0, He = 0, lv = 0; double tProd = tSplit, tLive = 0, tCost = tSeed;
        for (Side *s : sides) { Rn += s->R; Re += s->E; Hn += s->Hn; He += s->He; lv += s->leaves; tProd += s->tBuild; tLive += s->tLive; tCost += s->tCost; }
        base.set("R_nodes", Rn); base.set("R_edges", Re); base.set("H_nodes", Hn); base.set("H_edges", He); base.set("leaves", lv);
        base.set("Q", s1.Q); base.set("live_seeds", L.size()); base.set("status", "ok");
        base.setd("t_product", tProd); base.setd("t_live", tLive); base.setd("t_cost", tCost);
        // number of answer pairs, to check against Ring-RPQ (only when it ran and the pairs are few)
        if (ringAns >= 0 && tRing >= 0 && tRing < A.baselineTimeout) {
            u64 work = 0; bool okp = true; std::unordered_set<u64> pairs;
            auto answersOf = [&](Side &s, size_t si, const std::vector<std::vector<u32>> &roots, std::vector<u64> &res) {
                res.clear(); std::vector<u32> q; std::unordered_set<u32> seen;
                for (u32 y : roots[si]) if (s.alive[y] && seen.insert(y).second) q.push_back(y);
                for (size_t h = 0; h < q.size(); h++) { u32 z = q[h]; if (s.keys[z] % s.Q == 0) res.push_back(s.keys[z] / s.Q);
                    for (u64 e = s.soff[z]; e < s.soff[z + 1]; e++) { u32 w = s.succ[e]; if (s.alive[w] && seen.insert(w).second) q.push_back(w); } }
                work += q.size(); std::sort(res.begin(), res.end()); res.erase(std::unique(res.begin(), res.end()), res.end()); };
            std::vector<std::vector<u32>> r1(C.size()), r2(A1.size());
            for (u64 e = s1.soff[0]; e < s1.soff[1]; e++) r1[s1.rseed[e]].push_back(s1.succ[e]);
            if (s2p) for (u64 e = s2p->soff[0]; e < s2p->soff[1]; e++) r2[s2p->rseed[e]].push_back(s2p->succ[e]);
            std::vector<u64> x1, x2; size_t a = 0;
            for (size_t i = 0; i < C.size() && okp; i++) {
                bool inA1 = a < A1.size() && A1[a] == C[i]; size_t ai = a; if (inA1) a++;
                if (!use1[i]) continue;
                if (work > A.pairLimit) { okp = false; break; }
                answersOf(s1, i, r1, x1);
                if (s2p) answersOf(*s2p, ai, r2, x2); else x2 = {C[i]};
                const std::vector<u64> &xs = isLeft[0] ? x1 : x2, &ys = isLeft[0] ? x2 : x1;
                if (work + xs.size() * ys.size() > A.pairLimit) { okp = false; break; }
                for (u64 x : xs) for (u64 y : ys) pairs.insert(x * 0x9E3779B97F4A7C15ULL ^ y);
                work += xs.size() * ys.size();
            }
            if (okp) base.set("answers", pairs.size());
        }
        // explicit representation: both sides explicit + seed list (shorter of live and dead)
        u64 tmpS, tmpE, tmpR;
        std::vector<Encoded> EH(sides.size()); u64 Hbits = 0, Hglobal = 0;
        for (size_t k = 0; k < sides.size(); k++) { sides[k]->encode(EH[k], true, tmpS, tmpE, tmpR); Hbits += EH[k].bits(); Hglobal += sides[k]->globalBits(EH[k]); }
        std::vector<u64> liveIdx, deadIdx; for (size_t i = 0; i < C.size(); i++) (use1[i] ? liveIdx : deadIdx).push_back(i);
        SeedInfo seedH; if (deadIdx.size() <= liveIdx.size()) encodeSeeds(seedH, false, deadIdx, sp.firstLeft); else encodeSeeds(seedH, true, liveIdx, sp.firstLeft);
        Hbits += seedH.bits(); Hglobal += seedH.bits();
        // decoder: regenerate C, decode seeds, side 1 from D, side 2 from the seeds alive on side 1, restrict to the seeds alive on both
        auto decodeVV = [&](const std::vector<Encoded> &En, bool explicitMode, const SeedInfo &si, DecOut &dsum, bool &ok) {
            std::vector<u64> Cd = regenerate(P, sp);
            std::vector<u64> D = decodeSeeds(si, Cd);
            DecOut d1 = decodeSide(s1, En[0], explicitMode, D, -1, g1);
            std::vector<u64> D2; for (size_t i = 0; i < D.size(); i++) if (g1.seedLive[i]) D2.push_back(D[i]);
            std::vector<char> m1(D.size(), 0), m2;
            DecOut d2;
            if (s2p) { d2 = decodeSide(*s2p, En[1], explicitMode, D2, -1, g2); m2.assign(D2.size(), 0);
                size_t t = 0; for (size_t i = 0; i < D2.size(); i++) if (g2.seedLive[i]) { m2[i] = 1;
                    while (D[t] != D2[i]) t++; m1[t] = 1; } }
            else for (size_t i = 0; i < D.size(); i++) m1[i] = g1.seedLive[i];
            restrictOf(g1.view(), g1.alive0, &m1, g1.alive);
            HStats h1 = statsOf(g1.view(), g1.alive, &m1);
            ok = h1.nodes == s1.Hn && h1.edges == s1.He && h1.hash == s1.Hhash;
            if (s2p) { restrictOf(g2.view(), g2.alive0, &m2, g2.alive); HStats h2 = statsOf(g2.view(), g2.alive, &m2);
                ok = ok && h2.nodes == s2p->Hn && h2.edges == s2p->He && h2.hash == s2p->Hhash; }
            dsum.visited = d1.visited + d2.visited; dsum.expanded = d1.expanded + d2.expanded; dsum.children = d1.children + d2.children;
        };
        double tHx = 1e30; bool okH = true; DecOut dh;
        for (int r = 0; r < vvR; r++) { double t = now_s(); decodeVV(EH, true, seedH, dh, okH); tHx = std::min(tHx, now_s() - t); }
        base.set("H_bits", Hbits); base.set("H_bits_global", Hglobal); base.setd("t_H_expand", tHx);
        { u64 pbits = 0; double ptime = 0; for (Side *sd2 : sides) { u64 b2; double t2; plainOf(*sd2, b2, t2); pbits += b2; ptime += t2; }
          u64 wsd = maxNode > 1 ? 64 - __builtin_clzll(maxNode) : 1; pbits += (u64)L.size() * wsd;   // the live seeds, plainly
          base.set("Hplain_bits", pbits); base.setd("t_Hplain_expand", ptime); }
        auto config = [&](const char *model, const std::string &bl, double cc, long long w0, bool inf) {
            Row r = base; double t0 = now_s();
            u64 S = 0, SE = 0, replay = 0, maxRW = 0, Sbits = 0, Slist = 0;
            std::vector<Encoded> ES(sides.size());
            for (size_t k = 0; k < sides.size(); k++) { u64 cut, mrw; sides[k]->selectBest(cc, w0, inf, cut, mrw); maxRW = std::max(maxRW, mrw); }
            // seeds: explore the cheapest dead seeds within the budget, list the rest (or the live seeds)
            u64 Wseed = inf ? UINT64_MAX : (u64)std::max(0.0, cc * (double)L.size() + (double)w0), acc = 0; size_t nU = 0;
            while (nU < deadSeeds.size() && (inf || (deadSeeds[nU].first < INFC && acc + deadSeeds[nU].first <= Wseed))) { acc += std::min(deadSeeds[nU].first, CAP); nU++; }
            SeedInfo si; std::vector<u64> excl;
            for (size_t k = nU; k < deadSeeds.size(); k++) excl.push_back(deadSeeds[k].second);
            std::sort(excl.begin(), excl.end());
            if (excl.size() <= liveIdx.size()) encodeSeeds(si, false, excl, sp.firstLeft);
            else { encodeSeeds(si, true, liveIdx, sp.firstLeft); nU = 0; acc = 0; }
            double tSel = now_s() - t0; t0 = now_s();
            for (size_t k = 0; k < sides.size(); k++) { u64 a1, a2, a3; sides[k]->encode(ES[k], false, a1, a2, a3);
                if (inf) a3 = sides[k]->deadWork;
                S += a1; SE += a2; replay += a3; Sbits += ES[k].bits(); Slist += ES[k].entries; }
            replay += acc;
            double tEnc = now_s() - t0;
            double tx = 1e30; DecOut d; bool ok = true;
            for (int k = 0; k < vvR; k++) { double t = now_s(); decodeVV(ES, false, si, d, ok); tx = std::min(tx, now_s() - t); }
            if (!ok || !okH) fprintf(stderr, "q%s %s %s %s: v-to-v decode mismatch%s\n", base.f["qid"].c_str(), base.f["kind"].c_str(), model, bl.c_str(), okH ? "" : " [explicit]");
            r.set("model", model); r.set("budget", bl); r.set("S_nodes", S); r.set("S_edges", SE); r.set("S_list", Slist + si.listLen);
            r.set("S_bits", Sbits + si.bits()); r.set("seed_bits", si.bits()); r.set("explored_seeds", nU);
            r.set("replay_model", replay); r.set("max_region_work", maxRW); r.setd("t_select", tSel); r.setd("t_encode", tEnc);
            r.setd("t_build", tProd + tLive + tCost + tSel + tEnc); r.setd("t_expand", tx);
            r.set("dec_visited", d.visited); r.set("dec_expanded", d.expanded); r.set("dec_children", d.children); r.set("verify", (ok && okH) ? 1 : 0);
            r.print(out);
        };
        for (long long wb : A.budgets) config("region", wb < 0 ? "inf" : std::to_string(wb), 0.0, wb < 0 ? 0 : wb, wb < 0);
        for (double cc : A.rel) for (long long ws : A.relSlack) { char b[64]; snprintf(b, sizeof b, "%gx+%lld", cc, ws); config("region_rel", b, cc, ws, false); }
        fflush(out);
        for (Side *t : sides) t->clear();
    };

    // per-source baseline: K sampled sources of ?x path ?y, each run as a c-to-v query
    auto runPerSource = [&](ParsedQuery &P, Row base) {
        auto pm = P.pred_map; std::string aq = subject_side_expression(P.rpq);
        main1.setup(aq, pm); main1.rootFixed = false;
        base.set("kind", "VVs");
        if (main1.Q > 8 * sizeof(word_t)) { base.set("status", "too_many_states"); base.print(out); main1.clear(); return; }
        if (main1.F & 1) { base.set("status", "eps_source"); base.print(out); main1.clear(); return; }
        // sources: nodes with an edge for a label leaving a final state of the backward-run automaton
        std::vector<u64> S0;
        for (auto &it : main1.Bm) if (it.second & main1.F) {
            auto v = graph.nodesWithLabel(it.first); S0.insert(S0.end(), v.begin(), v.end()); }
        std::sort(S0.begin(), S0.end()); S0.erase(std::unique(S0.begin(), S0.end()), S0.end());
        base.set("seeds", S0.size());
        curReps = A.vvReps > 0 ? A.vvReps : A.reps;
        std::vector<u64> pool = S0; std::shuffle(pool.begin(), pool.end(), rng);
        for (int k = 0; k < A.vvSamples && k < (int)pool.size(); k++) {
            u64 src = pool[k]; Row r = base; r.set("src", src); r.set("dst", -1);
            if (!A.noRing && graph.hasBaseline()) { u64 na; double t = graph.constQuery(P, true, src, na); r.setd("t_ring", t); r.set("ring_answers", na); }
            main1.build({src});
            if (main1.status != "ok") { r.set("status", main1.status); r.set("R_nodes", main1.R); r.set("R_edges", main1.E); r.print(out); continue; }
            r.set("status", "ok");
            u64 na = 0; { std::set<u64> an; for (size_t i = 1; i < main1.R; i++) if (main1.keys[i] % main1.Q == 0) an.insert(main1.keys[i] / main1.Q); na = an.size(); }
            r.set("answers", na);
            runSingle(main1, r, src, -1, nullptr);
        }
        curReps = A.reps;
        main1.clear();
    };

    // union baseline: a single multi-source PMR, the product graph from all the valid sources at once
    // (its root keeps the list of live sources, or of dead ones); rows VVu
    auto runUnion = [&](ParsedQuery &P, Row base) {
        auto pm = P.pred_map; std::string aq = subject_side_expression(P.rpq);
        main1.setup(aq, pm); main1.rootFixed = false;
        base.set("kind", "VVu"); base.set("dst", -1);
        if (main1.Q > 8 * sizeof(word_t)) { base.set("status", "too_many_states"); base.print(out); main1.clear(); return; }
        if (main1.F & 1) { base.set("status", "eps_source"); base.print(out); main1.clear(); return; }
        double t0 = now_s();
        std::vector<u64> S0;
        for (auto &it : main1.Bm) if (it.second & main1.F) {
            auto v = graph.nodesWithLabel(it.first); S0.insert(S0.end(), v.begin(), v.end()); }
        std::sort(S0.begin(), S0.end()); S0.erase(std::unique(S0.begin(), S0.end()), S0.end());
        double tS0 = now_s() - t0;
        base.set("seeds", S0.size());
        curReps = A.vvReps > 0 ? A.vvReps : A.reps;
        main1.build(S0);
        if (main1.status != "ok") { base.set("status", main1.status); base.set("R_nodes", main1.R); base.set("R_edges", main1.E);
            base.setd("t_product", main1.tBuild + tS0); base.print(out); fflush(out); main1.clear(); curReps = A.reps; return; }
        base.set("status", "ok");
        runSingle(main1, base, 0, -1, &S0);
        fflush(out);
        curReps = A.reps;
        main1.clear();
    };

    std::ifstream fq(A.queries); std::string line; int lineNo = 0, qid = 0, done = 0;
    std::unordered_map<std::string, int> seenVV;   // v-to-v path -> first qid
    while (std::getline(fq, line)) {
        lineNo++;
        if (line.find_first_not_of(" \t\r") == std::string::npos) continue;
        qid++;
        if (!A.only.empty() && !A.only.count(qid)) continue;
        if (A.maxQueries >= 0 && done >= A.maxQueries) break;
        if (A.automata) {   // own rows, none of the normal mode
#ifdef ADJ_GRAPH
            if (automataQuery(graph, line, qid, lineNo, map_SO, map_P, realMaxP, maxNode, out)) done++;
#endif
            continue;
        }
        ParsedQuery P;
        Row base; base.set("qid", qid); base.set("line", lineNo);
        try { P = parseLine(line, map_SO, map_P, realMaxP); }
        catch (std::exception &e) { base.set("status", "parse_error"); base.print(out); continue; }
        std::string kind = P.subject_is_const ? (P.object_is_const ? "CC" : "CV") : (P.object_is_const ? "VC" : "VV");
        base.set("kind", kind); base.set("shape", P.shape);
        if (P.skip) { base.set("status", "unknown_iri"); base.print(out); continue; }
        if (kind == "CC") { base.set("status", "cc_in_file"); base.print(out); continue; }
        if (P.n_predicates + 1 > 8 * sizeof(word_t)) { base.set("status", "too_many_states"); base.print(out); continue; }
        done++;
        // v-to-v queries: from the file, and derived from c-to-v / v-to-c by dropping the constant
        std::vector<ParsedQuery> vvq;
        if (kind == "VV") vvq.push_back(P);
        else if (A.vvDerive) {
            std::string l2 = P.raw; std::stringstream X(l2); std::string first; X >> first;
            if (kind == "CV") l2 = "?x" + l2.substr(first.size());
            else { size_t sp = l2.find_last_of(' '); l2 = l2.substr(0, sp) + " ?y"; }
            ParsedQuery P2 = parseLine(l2, map_SO, map_P, realMaxP); if (!P2.skip) vvq.push_back(P2);
        }
        if (kind != "VV" && !A.vvOnly) {
            u64 src = kind == "CV" ? P.subject_id : P.object_id;
            if (src == 0 || src > maxNode) {   // dictionary from another enumeration of the graph
                Row b = base; b.set("src", src); b.set("status", "id_out_of_range"); b.print(out); continue; }
            double tRing = -1; u64 ringAns = 0;
            if (!A.noRing && graph.hasBaseline()) tRing = graph.constQuery(P, kind == "CV", src, ringAns);
            auto pm = P.pred_map; std::string aq;
            aq = kind == "CV" ? subject_side_expression(P.rpq) : object_side_expression(P.rpq);
            main1.setup(aq, pm); main1.rootFixed = false;
            Row b = base; b.set("src", src);
            if (main1.Q > 8 * sizeof(word_t)) { b.set("status", "too_many_states"); b.print(out); main1.clear(); }
            else {
                main1.build({src});
                if (main1.status != "ok") {
                    b.set("status", main1.status); b.set("R_nodes", main1.R); b.set("R_edges", main1.E); b.setd("t_ring", tRing); b.set("ring_answers", ringAns);
                    b.setd("t_product", main1.tBuild); b.print(out); fflush(out);
                } else {
                    std::vector<u64> answers;
                    for (size_t i = 1; i < main1.R; i++) if (main1.keys[i] % main1.Q == 0) answers.push_back(main1.keys[i] / main1.Q);
                    std::sort(answers.begin(), answers.end()); answers.erase(std::unique(answers.begin(), answers.end()), answers.end());
                    std::vector<long long> targets{-1};
                    if (A.pairs > 0 && !answers.empty()) { std::vector<u64> pool = answers; std::shuffle(pool.begin(), pool.end(), rng);
                        for (int k = 0; k < A.pairs && k < (int)pool.size(); k++) targets.push_back((long long)pool[k]); }
                    // the reverse orientation of a pair (c, t): the same path read from t, i.e., the
                    // query of the other kind rooted at t, with target c
                    bool rev = A.pairRev && targets.size() > 1;
                    if (rev) { auto pm2 = P.pred_map; std::string aq2;
                        aq2 = kind == "CV" ? object_side_expression(P.rpq) : subject_side_expression(P.rpq);
                        sideB.setup(aq2, pm2); sideB.rootFixed = false;
                        if (sideB.Q > 8 * sizeof(word_t)) { sideB.clear(); rev = false; } }
                    // --early-stop: each pair has its own product graph from src that stops at the target
                    bool own = A.earlyStop && targets.size() > 1;
                    if (own) { auto pm3 = P.pred_map; sideP.setup(aq, pm3); sideP.rootFixed = false; }
                    for (long long dst : targets) {
                        Row r = b; r.set("dst", dst); r.set("status", "ok");
                        if (dst < 0) { r.setd("t_ring", tRing); r.set("ring_answers", ringAns); r.set("answers", answers.size()); }
                        else {
                            r.set("kind", kind + "p"); r.set("answers", 1);
                            if (!A.noRing && graph.hasCC()) { u64 na; double t = graph.ccQuery(*main1.Au, P, main1.X.prep, src, (u64)dst, kind == "CV", na);
                                r.setd("t_ring", t); r.set("ring_answers", na); }
                        }
                        if (own && dst >= 0) {
                            sideP.build({src}, dst);
                            // in practice only a timeout / too_large of the run alone: sideP's BFS is a prefix of main1's
                            if (sideP.status != "ok") { r.set("status", sideP.status); r.set("R_nodes", sideP.R); r.set("R_edges", sideP.E);
                                r.setd("t_product", sideP.tBuild); r.print(out); }
                            else runSingle(sideP, r, src, dst, nullptr);
                        } else runSingle(main1, r, src, dst, nullptr);
                        if (rev && dst >= 0) {
                            Row r2 = b; r2.set("kind", kind + "r"); r2.set("src", (u64)dst); r2.set("dst", (long long)src); r2.set("answers", 1);
                            sideB.build({(u64)dst}, A.earlyStop ? (long long)src : -1);
                            if (sideB.status != "ok") { r2.set("status", sideB.status); r2.set("R_nodes", sideB.R); r2.set("R_edges", sideB.E);
                                r2.setd("t_product", sideB.tBuild); r2.print(out); }
                            else { r2.set("status", "ok"); runSingle(sideB, r2, (u64)dst, (long long)src, nullptr); }
                            fflush(out);
                        }
                    }
                    if (rev) sideB.clear();
                    if (own) sideP.clear();
                }
                main1.clear();
            }
        }
        for (auto &Pv : vvq) {
            if (A.vv == "none" && !A.vvUnion && !(A.shortest && A.vvSamples > 0)) break;
            Row b = base; b.set("shape", Pv.shape);
            if (&Pv != &vvq[0] || kind != "VV") b.set("src", "derived");
            // many c-to-v queries differ only in the constant: run each v-to-v path once
            auto seen = seenVV.find(Pv.rpq);
            if (seen != seenVV.end()) { b.set("kind", "VV"); b.set("status", "duplicate"); b.set("dst", seen->second); b.print(out); continue; }
            seenVV[Pv.rpq] = qid;
            double tRing = -1; long long ringAns = -1;
            bool derived = !(kind == "VV" && &Pv == &vvq[0]);
            if (A.vv != "none" && !A.noRing && !A.noRingVV && graph.hasBaseline() && (!derived || A.ringVVDerived)) { u64 na; tRing = graph.vvQuery(Pv, na); ringAns = (long long)na; }
            if (A.vv == "ring" || A.vv == "both") runVV(Pv, b, "ring", tRing, ringAns);
            if (A.vv == "bridge" || A.vv == "both") runVV(Pv, b, "bridge", tRing, ringAns);
            if (A.vvSamples > 0) runPerSource(Pv, b);
            if (A.vvUnion) runUnion(Pv, b);
            fflush(out);
        }
        if (done % 10 == 0) fprintf(stderr, "%d queries (line %d)\n", done, lineNo);
    }
    fclose(out);
    fprintf(stderr, "done: %d queries\n", done);
    return 0;
}
