#ifndef RING_RPQ_BUILD_DRIVER_HPP
#define RING_RPQ_BUILD_DRIVER_HPP

// Command-line driver shared by the index construction executables: reads
// the triples of <graph.dat> ("s p o" integer ids per line) and stores the
// index next to it (<graph.dat>.*).

#include <chrono>
#include <cstdint>
#include <fstream>
#include <iostream>
#include <string>
#include <vector>
#include <sdsl/construct.hpp>
#include "ring_rpq.hpp"

inline int run_build_driver(int argc, char **argv, bool with_split_support) {
    using namespace std;
    using timer = chrono::high_resolution_clock;

    if (argc < 2) {
        cerr << "  Usage: " << argv[0] << " <graph.dat>" << endl;
        return 1;
    }
    vector<spo_triple> D;
    ifstream ifs(argv[1]);
    uint64_t s, p, o;
    while (ifs >> s >> p >> o)
        D.push_back(spo_triple(s, p, o));
    D.shrink_to_fit();
    const uint64_t input_size = D.size();

    cout << "--Indexing " << D.size() << " triples" << endl;
    memory_monitor::start();
    auto start = timer::now();
    ring_rpq_bfs index(D, with_split_support);
    auto stop = timer::now();
    memory_monitor::stop();
    cout << "  Index built " << (float) index.size() / input_size << " bytes per triple" << endl;

    index.save(string(argv[1]));
    cout << "Index saved" << endl;
    cout << chrono::duration_cast<chrono::seconds>(stop - start).count() << " seconds." << endl;
    return 0;
}

#endif
