#ifndef RING_RPQ_QUERY_DRIVER_HPP
#define RING_RPQ_QUERY_DRIVER_HPP

// Command-line driver shared by the query executables: loads an index,
// reads a file of RPQs (one "<subject> <rpq> <object>" per line), solves each
// one with the planner and prints "query;results;nanoseconds".

#include <chrono>
#include <cstdint>
#include <fstream>
#include <iostream>
#include <string>
#include <utility>
#include <vector>
#include "ring_rpq.hpp"
#include "planner.hpp"
#include "rpq_query.hpp"

// Loads a dictionary file with one "id string" entry per line.
inline dictionary_type load_dictionary(const std::string &path) {
    dictionary_type dict;
    std::ifstream in(path);
    std::string data;
    while (std::getline(in, data)) {
        auto space = data.find(' ');
        dict[data.substr(space + 1)] = std::stoull(data.substr(0, space));
    }
    return dict;
}

// Writes the result line of a query and, if requested, all its solutions
// as "query subject object" (used by tests/regression.py).
inline void report_query(uint64_t q, std::vector<std::pair<uint64_t, uint64_t>> &output,
                         std::chrono::high_resolution_clock::duration elapsed,
                         std::ofstream &solutions) {
    using namespace std::chrono;
    duration<double> time_span = duration_cast<microseconds>(elapsed);
    std::cout << q << ";" << output.size() << ";"
              << (uint64_t) (time_span.count() * 1000000000ULL) << std::endl;
    if (solutions.is_open()) {
        std::sort(output.begin(), output.end());
        for (const auto &sol : output)
            solutions << q << " " << sol.first << " " << sol.second << "\n";
        solutions.flush();
    }
}

inline void report_skipped(uint64_t q) {
    std::cout << q << ";0;0" << std::endl;
}

// argv: <ring-index-file> <queries-file> [solutions-file]
inline int run_query_driver(int argc, char **argv, planner_mode mode) {
    using namespace std;
    using namespace std::chrono;

    if (argc < 3) {
        cerr << "  Usage: " << argv[0] << " <ring-index-file> <queries-file> [solutions-file]" << endl;
        return 1;
    }

    ring_rpq_bfs graph;
    graph.load(string(argv[1]));
    if (mode != planner_mode::basic && !graph.has_split_support()) {
        cerr << "  The index has no support for splitting RPQs: build it with build-index-split" << endl;
        return 1;
    }
    cout << "  Index loaded " << (float) graph.size() / (graph.n_triples() / 2) << " bytes per triple" << endl;

    const dictionary_type map_SO = load_dictionary(string(argv[1]) + ".SO");
    const dictionary_type map_P = load_dictionary(string(argv[1]) + ".P");
    ifstream ifs_q(argv[2]);
    ofstream ofs_solutions;
    if (argc > 3) ofs_solutions.open(argv[3]);

    vector<pair<uint64_t, uint64_t>> query_output;
    string line;
    uint64_t q = 0;

    while (getline(ifs_q, line)) {
        q++;
        if (line.find_first_not_of(" \t\r") == string::npos) continue;  // blank line
        rpq_query ql = parse_query_line(line, map_SO, map_P, graph.n_graph_predicates());
        if (!ql.valid) {
            report_skipped(q);
            continue;
        }
        if (automaton_states(ql) > 64) {
            // Sets of NFA states are stored in 64-bit words at most.
            cerr << "query " << q << " skipped: its automaton has " << automaton_states(ql)
                 << " states, at most 64 are supported" << endl;
            report_skipped(q);
            continue;
        }
        query_output.clear();
        auto start = high_resolution_clock::now();
        solve(graph, ql, mode, query_output);
        report_query(q, query_output, high_resolution_clock::now() - start, ofs_solutions);
    }
    return 0;
}

#endif
