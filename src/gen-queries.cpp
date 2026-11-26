// gen-queries.cpp — RPQs instantiated from the expression shapes of Hammerer & Martens
// ("A Compendium of Regular Expression Shapes in SPARQL Queries", GRADES-NDA 2025;
// shapes at https://zenodo.org/records/15363430, e.g. WD_organic.csv).
//
// A shape (e.g. a/(b)*) is drawn with probability proportional to its number of distinct RPQs
// (or of valid RPQs, --weight valid). Its letters are bound to predicates by a random walk on the
// graph (Ring index) that follows a random word of the expression from a random start node:
// an unbound letter takes a predicate available at the current node, drawn with the weight it has
// in a pool (by default, the predicates of a query log, weighted by their occurrences there).
// Distinct letters get distinct predicates. Letters not used by the word are bound from the pool.
// The walk guarantees at least one answer. Half of the queries are c-to-v (the start node is the
// constant), half v-to-c (the walk runs the inverse expression from the constant). Constants are
// written <id:N>, which ring-tunnels accepts. Shapes with negated property sets (!) are skipped.
//
// Usage: gen-queries <ring-index> <shapes.csv> <query-log for the pool> <N> [--seed s]
//        [--weight unique|valid] [--pool log|graph] [--max-steps 40]  > queries.tsv
#include <algorithm>
#include <cctype>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <map>
#include <random>
#include <set>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <vector>
#include "ring_rpq.hpp"
typedef uint64_t u64;
using Ring = ring_rpq_bfs;

struct Node { char t; char letter = 0; bool inv = false; std::vector<Node> k; };   // t: 'l', '/', '|', '*', '+', '?'
struct ShapeParser {
    const std::string &s; size_t i = 0;
    explicit ShapeParser(const std::string &x) : s(x) {}
    void ws() { while (i < s.size() && s[i] == ' ') i++; }
    Node alt() { Node a = cat(); ws(); if (i < s.size() && s[i] == '|') { Node r{'|'}; r.k.push_back(a); while (i < s.size() && s[i] == '|') { i++; r.k.push_back(cat()); ws(); } return r; } return a; }
    Node cat() { Node a = un(); ws(); if (i < s.size() && s[i] == '/') { Node r{'/'}; r.k.push_back(a); while (i < s.size() && s[i] == '/') { i++; r.k.push_back(un()); ws(); } return r; } return a; }
    Node un() {
        ws();
        if (i < s.size() && s[i] == '^') { i++; return inv(un()); }
        Node a;
        if (i < s.size() && s[i] == '(') { i++; a = alt(); ws(); if (i >= s.size() || s[i] != ')') throw std::runtime_error("missing )"); i++; }
        else if (i < s.size() && islower((unsigned char)s[i])) { a = Node{'l'}; a.letter = s[i++]; }
        else throw std::runtime_error("unsupported shape");
        for (;;) { ws(); if (i < s.size() && (s[i] == '*' || s[i] == '+' || s[i] == '?')) { Node r{s[i]}; r.k.push_back(a); a = r; i++; } else break; }
        return a;
    }
    static Node inv(const Node &x) {
        Node r = x;
        if (x.t == 'l') { r.inv = !x.inv; return r; }
        for (auto &c : r.k) c = inv(c);
        if (x.t == '/') std::reverse(r.k.begin(), r.k.end());
        return r;
    }
};

int main(int argc, char **argv) {
    if (argc < 5) { fprintf(stderr, "usage: gen-queries <ring-index> <shapes.csv> <query-log> <N> [--seed s] [--weight unique|valid] [--pool log|graph] [--max-steps 40]\n"); return 1; }
    std::string index = argv[1], shapesFile = argv[2], logFile = argv[3]; long N = atol(argv[4]);
    u64 seed = 1; std::string weight = "unique", pool = "log"; int maxSteps = 40;
    for (int i = 5; i < argc; i++) { std::string a = argv[i];
        if (a == "--seed") seed = std::stoull(argv[++i]); else if (a == "--weight") weight = argv[++i];
        else if (a == "--pool") pool = argv[++i]; else if (a == "--max-steps") maxSteps = atoi(argv[++i]); }
    std::mt19937_64 rng(seed);
    Ring graph; graph.load(index);
    bwt_nose &LS = graph.subjects_bwt(); bwt &LP = graph.predicates_bwt(); const u64 M = graph.n_graph_predicates();
    // predicates
    std::unordered_map<std::string, u64> pid; std::unordered_map<u64, std::string> piri;
    { std::ifstream f(index + ".P"); std::string d; while (std::getline(f, d)) { auto sp = d.find(' '); if (sp == std::string::npos) continue;
        std::string iri = d.substr(sp + 1); while (!iri.empty() && (iri.back() == '\r' || iri.back() == ' ')) iri.pop_back();
        u64 id = std::stoull(d.substr(0, sp)); pid[iri] = id; piri[id] = iri; } }
    // pool weights
    std::unordered_map<u64, double> w;
    if (pool == "log") {
        std::ifstream f(logFile); std::string l; std::regex re("<[^>]*/prop/direct/[^>]*>");
        while (std::getline(f, l)) for (std::sregex_iterator it(l.begin(), l.end(), re), e; it != e; ++it) { auto p = pid.find(it->str()); if (p != pid.end()) w[p->second] += 1; }
    } else {
        for (auto &kv : piri) { u64 p = kv.first; if (p > M) continue; double c = (double)(LS.get_C(p + 1) - LS.get_C(p)); if (c > 0) w[p] = c; }
    }
    fprintf(stderr, "pool: %zu predicates\n", w.size());
    // shapes
    struct Shape { std::string expr; Node ast; double wt; int letters; };
    std::vector<Shape> shapes;
    { std::ifstream f(shapesFile); std::string l; std::getline(f, l);
      while (std::getline(f, l)) {
          std::vector<std::string> c; std::string cur; bool q = false;
          for (char ch : l) { if (ch == '"') q = !q; else if (ch == ',' && !q) { c.push_back(cur); cur.clear(); } else cur += ch; }
          c.push_back(cur);
          if (c.size() < 5) continue;
          std::string expr = c[1]; if (expr.find('!') != std::string::npos) continue;
          double wt = std::stod(weight == "valid" ? c[2] : c[4]);
          try { ShapeParser P(expr); Node a = P.alt(); P.ws(); if (P.i != expr.size()) continue;
                std::set<char> ls; for (char ch : expr) if (islower((unsigned char)ch)) ls.insert(ch);
                if (ls.size() > 14) continue;   // the Ring's automata have at most 16 states
                shapes.push_back({expr, a, wt, (int)ls.size()}); } catch (...) {}
      } }
    fprintf(stderr, "shapes: %zu\n", shapes.size());
    std::vector<double> shapeW; for (auto &sh : shapes) shapeW.push_back(sh.wt);
    std::discrete_distribution<size_t> pickShape(shapeW.begin(), shapeW.end());
    std::vector<u64> poolP; std::vector<double> poolW; for (auto &kv : w) { poolP.push_back(kv.first); poolW.push_back(kv.second); }
    std::discrete_distribution<size_t> pickPool(poolW.begin(), poolW.end());

    // random word: sequence of (letter, inverse)
    std::function<bool(const Node &, std::vector<std::pair<char, bool>> &)> word = [&](const Node &x, std::vector<std::pair<char, bool>> &out) -> bool {
        if ((int)out.size() > maxSteps) return false;
        std::uniform_real_distribution<double> U(0, 1);
        switch (x.t) {
            case 'l': out.push_back({x.letter, x.inv}); return true;
            case '/': for (auto &c : x.k) if (!word(c, out)) return false; return true;
            case '|': return word(x.k[std::uniform_int_distribution<size_t>(0, x.k.size() - 1)(rng)], out);
            case '?': return U(rng) < 0.5 ? word(x.k[0], out) : true;
            case '*': case '+': {
                int n = x.t == '+' ? 1 : (U(rng) < 0.25 ? 0 : 1);
                if (n) while (n < 4 && U(rng) < 0.5) n++;
                for (int r = 0; r < n; r++) if (!word(x.k[0], out)) return false;
                return true; }
        }
        return false;
    };
    // labels available at v (Ring label ids: p, or p + M for the inverse)
    auto labelsAt = [&](u64 v) { std::vector<uint64_t> r; u64 lb = LP.get_C(v), rb = LP.get_C(v + 1) - 1; if (lb <= rb) r = LP.all_values_in_range(lb, rb); return r; };
    auto step = [&](u64 v, u64 label, u64 &to) -> bool {
        u64 lb = LP.get_C(v), rb = LP.get_C(v + 1) - 1; if (lb > rb) return false;
        auto rr = LP.backward_step(lb, rb, label); if (rr.second + 1 <= rr.first) return false;   // e = rank - 1 may wrap   // ranks of label in [lb, rb]
        u64 c = LS.get_C(label); u64 k = std::uniform_int_distribution<u64>(rr.first, rr.second)(rng);
        to = LS[c + k]; return true; };
    auto printNode = [&](const Node &x, const std::map<char, u64> &bind, auto &&self) -> std::string {
        if (x.t == 'l') return std::string(x.inv ? "^" : "") + piri[bind.at(x.letter)];
        if (x.t == '/' || x.t == '|') { std::string o; for (size_t j = 0; j < x.k.size(); j++) { if (j) o += x.t;
            std::string c = self(x.k[j], bind, self); o += (x.t == '/' && x.k[j].t == '|') ? "(" + c + ")" : c; } return o; }
        return "(" + self(x.k[0], bind, self) + ")" + std::string(1, x.t);
    };

    std::set<std::string> seen; long made = 0, tries = 0;
    std::map<std::string, long> perShape;
    while (made < N && tries < N * 2000) {
        tries++;
        const Shape &S = shapes[pickShape(rng)];
        bool vc = std::uniform_int_distribution<int>(0, 1)(rng);
        Node ast = vc ? ShapeParser::inv(S.ast) : S.ast;   // v-to-c: walk the inverse expression from the constant
        std::vector<std::pair<char, bool>> wd; if (!word(ast, wd) || wd.empty()) continue;
        // start node: one that has the first label of the word (if bound later, any pool predicate)
        u64 p0 = poolP[pickPool(rng)]; u64 lab0 = wd[0].second ? p0 + M : p0, rev0 = graph.pred_reverse(lab0);
        u64 lb = LS.get_C(rev0), rb = LS.get_C(rev0 + 1); if (lb >= rb) continue;
        u64 start = LS[std::uniform_int_distribution<u64>(lb, rb - 1)(rng)];
        std::map<char, u64> bind; std::set<u64> used; u64 v = start; bool ok = true;
        for (size_t s = 0; s < wd.size() && ok; s++) {
            char L = wd[s].first; bool inv = wd[s].second;
            if (!bind.count(L)) {
                std::vector<u64> cand; std::vector<double> cw;
                for (u64 lab : labelsAt(v)) { bool li = lab > M; u64 p = li ? lab - M : lab;
                    if (li != inv || !w.count(p) || used.count(p)) continue; cand.push_back(p); cw.push_back(w[p]); }
                if (s == 0 && std::find(cand.begin(), cand.end(), p0) != cand.end()) { cand = {p0}; cw = {1}; }
                if (cand.empty()) { ok = false; break; }
                u64 p = cand[std::discrete_distribution<size_t>(cw.begin(), cw.end())(rng)];
                bind[L] = p; used.insert(p);
            }
            u64 to; if (!step(v, inv ? bind[L] + M : bind[L], to)) { ok = false; break; }
            v = to;
        }
        if (!ok) continue;
        // letters not used by the word
        for (char ch : S.expr) if (islower((unsigned char)ch) && !bind.count(ch)) {
            u64 p; int g = 0; do { p = poolP[pickPool(rng)]; } while (used.count(p) && ++g < 100);
            bind[ch] = p; used.insert(p); }
        std::string path = printNode(S.ast, bind, printNode);
        std::string q = vc ? "?x " + path + " <id:" + std::to_string(start) + ">" : "<id:" + std::to_string(start) + "> " + path + " ?x";
        if (!seen.insert(q).second) continue;
        printf("%s\n", q.c_str()); made++; perShape[S.expr]++;
    }
    fprintf(stderr, "generated %ld queries in %ld tries; %zu shapes used\n", made, tries, perShape.size());
    std::vector<std::pair<long, std::string>> top; for (auto &kv : perShape) top.push_back({kv.second, kv.first});
    std::sort(top.rbegin(), top.rend());
    for (size_t i = 0; i < top.size() && i < 15; i++) fprintf(stderr, "  %6ld  %s\n", top[i].first, top[i].second.c_str());
    return 0;
}
