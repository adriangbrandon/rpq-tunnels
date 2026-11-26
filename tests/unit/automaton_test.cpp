// Checks RpqAutomata::next when table T is split in several slices (B8):
// the result must not depend on the width of the slices.

#include <cstdint>
#include <iostream>
#include <random>
#include <string>
#include <unordered_map>
#include <vector>
#include "RpqAutomata.hpp"
#include "RpqTree.hpp"

extern int OptDetWidth;

namespace {

const std::unordered_map<std::string, uint64_t> predicates = {
        {"<a>", 1}, {"<b>", 2}, {"<c>", 3}, {"<%a>", 4}};

std::string chain(int n) {
    const char *names[] = {"<a>", "<b>", "<c>", "<%a>"};
    std::string rpq;
    for (int i = 0; i < n; i++) {
        if (i) rpq += '/';
        rpq += std::string("(") + names[i % 4] + (i % 3 ? ")*" : ")");
    }
    return rpq;
}

int count_positions(const std::string &rpq) {
    int n = 0;
    for (char c : rpq) n += c == '<';
    return n;
}

}  // namespace

int main() {
    const std::vector<std::string> queries = {
            "<a>/(<b>)*/<b>",
            "((<a>|<b>))*/<c>",
            "((<a>/<b>)|<c>)+/(<a>)?",
            "<%a>/(<a>|<c>)*/((<b>)?/<a>)+",
            chain(10),
            chain(15),
            chain(20),
    };
    std::mt19937_64 rng(42);
    int failures = 0;

    for (const auto &rpq : queries) {
        // Upper bound of the number of states (classes like (<a>|<b>) take one position).
        OptDetWidth = count_positions(rpq) + 1;  // a single slice: the reference
        RpqAutomata reference(rpq, predicates);
        uint64_t all = 1;  // initial state plus every state targeted by some predicate
        for (const auto &b : reference.getB()) all |= b.second;
        int states = 0;
        while (states < 64 && (all >> states)) states++;

        for (int width : {3, 5, 8}) {
            OptDetWidth = width;
            RpqAutomata sliced(rpq, predicates);
            for (int k = 0; k < 20000; k++) {
                const uint64_t current = (k < states) ? (1ULL << k) : (rng() & all);
                for (const auto &p : predicates) {
                    for (int type : {FWD, BWD}) {
                        const uint64_t expected = reference.next(current, p.second, type);
                        const uint64_t got = sliced.next(current, p.second, type);
                        if (expected != got && failures++ < 10) {
                            std::cerr << "FAIL " << rpq << " width=" << width << " current=" << current
                                      << " pred=" << p.second << (type == FWD ? " FWD" : " BWD")
                                      << ": expected " << expected << ", got " << got << "\n";
                        }
                    }
                }
            }
        }
    }
    // B13: predicate IRIs must be matched literally ('.' is not a wildcard).
    {
        OptDetWidth = 16;
        const std::unordered_map<std::string, uint64_t> dotted = {{"<a.c>", 1}, {"<abc>", 2}};
        RpqAutomata automaton("<a.c>/<abc>", dotted);
        const uint64_t initial = 1;
        if (automaton.next(initial, 1, FWD) != 2 || automaton.next(initial, 2, FWD) != 0 ||
            automaton.next(2, 2, FWD) != 4) {
            std::cerr << "FAIL literal IRIs: " << automaton.next(initial, 1, FWD) << " "
                      << automaton.next(initial, 2, FWD) << " " << automaton.next(2, 2, FWD) << "\n";
            failures++;
        }
    }

    // B13b: the same for the syntax tree used to split RPQs. Both positions
    // of <a.c>/<abc> are mandatory (one group each) with their own predicates.
    {
        const std::unordered_map<std::string, uint64_t> dotted = {{"<a.c>", 1}, {"<abc>", 2}};
        RpqTree tree("<a.c><abc>", dotted, 10);
        auto mandatory = tree.getMandatoryData();
        if (mandatory.size() != 2 || mandatory[0].pred_b != 1 || mandatory[1].pred_b != 2) {
            std::cerr << "FAIL literal IRIs in RpqTree: " << mandatory.size() << " groups";
            for (const auto &m : mandatory) std::cerr << " [" << m.pred_b << "," << m.pred_e << "]";
            std::cerr << "\n";
            failures++;
        }
    }

    // countStates gives the number of states without building the automaton,
    // whose masks are wrong beyond 64 states (heap overflow in the NFA code).
    {
        OptDetWidth = 16;
        for (int n : {1, 15, 16, 40, 63}) {
            std::string rpq;
            for (int i = 0; i < n; i++) rpq += (i ? "/" : "") + std::string(i % 2 ? "<a>" : "<b>");
            RpqAutomata automaton(rpq, predicates);
            if (RpqAutomata::countStates(rpq, predicates) != automaton.states()) {
                std::cerr << "FAIL countStates(" << n << " predicates)\n";
                failures++;
            }
        }
        std::string long_rpq;
        for (int i = 0; i < 64; i++) long_rpq += (i ? "/" : "") + std::string("<a>");
        if (RpqAutomata::countStates(long_rpq, predicates) != 65) {
            std::cerr << "FAIL countStates(64 predicates) = " << RpqAutomata::countStates(long_rpq, predicates) << "\n";
            failures++;
        }
    }

    std::cout << (failures ? "automaton_test: FAILED" : "automaton_test: OK") << std::endl;
    return failures ? 1 : 0;
}
