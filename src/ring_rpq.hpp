/*
 * ring_rpq.hpp
 * Copyright (C) 2021 Diego Arroyuelo
 * 
 * This is free software: you can redistribute it and/or modify it
 * under the terms of the GNU General Public License as published by the
 * Free Software Foundation, either version 3 of the License, or
 * (at your option) any later version.
 * 
 * This software is distributed in the hope that it will be useful, but
 * WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 * See the GNU General Public License for more details.
 * 
 * You should have received a copy of the GNU General Public License along
 * with this program.  If not, see <http://www.gnu.org/licenses/>.
 */

#ifndef RING_RPQ_HPP
#define RING_RPQ_HPP

#include <cstdint>
#include <chrono>
#include <set>
#include <unordered_set>
#include <stdio.h>
#include <stdlib.h>

#include "bwt.hpp"
#include "bwt-C-nose.hpp"
#include "bwt_interval.hpp"
#include "utils.hpp"
#include "RpqAutomata.hpp"
#include "RpqTree.hpp"
#include "query_config.hpp"
#include "rpq_query.hpp"
#include "selectivity.hpp"

using namespace std::chrono;

// A range of objects (in L_P) reached with the set of active NFA states current_D.
template<class word_t>
struct interval_state {
    bwt_interval interval;
    word_t current_D;
};

// Solutions of one half of a split RPQ for one splitting node.
typedef struct {
    uint64_t element;
    std::vector<std::pair<uint64_t, uint64_t>> solutions;
} element_solution_type;

// Container (std::queue or std::stack) chooses a BFS or DFS traversal of the
// product graph. Sets of NFA states are words of 16, 32 or 64 bits, chosen
// per query by the length of the RPQ.
template<template<class...> class Container = std::queue>
class ring_rpq {
    template<class word_t>
    using container_type = Container<interval_state<word_t>>;

    bwt_nose L_S;
    bwt L_P;
    // Predecessor of each position of L_S with the same symbol, used to count
    // distinct values for the RPQ-splitting heuristic (Section 5.2). Optional.
    bwt_type wt_pred_s;
    bool split_support = false;

    uint64_t real_max_P;

    uint64_t max_S;
    uint64_t max_P;
    uint64_t max_O;
    uint64_t nTriples;  // number of triples

    uint64_t paths;

    // Arrays B of Section 4.1 (two, for the halves of a split RPQ) for each word size.
    std::tuple<std::vector<uint16_t>, std::vector<uint32_t>, std::vector<uint64_t>> B_arrays[2];
    // Array S of Section 4.2 for each word size, reused (and reset) by every traversal.
    std::tuple<std::unique_ptr<rpq_wavelet::visited_states<uint16_t>>,
               std::unique_ptr<rpq_wavelet::visited_states<uint32_t>>,
               std::unique_ptr<rpq_wavelet::visited_states<uint64_t>>> S_arrays;

public:

    ring_rpq() { ; }

    // Builds the index from the triples in D (which is modified). With
    // split_support it also builds what splitting RPQs needs (Section 5.2).
    ring_rpq(vector<spo_triple>& D, bool with_split_support, bool verbose = true)
    {
        uint64_t i, pos_c;
        vector<spo_triple>::iterator it, triple_begin, triple_end;
        uint64_t n;

        // for every triple, adds its reverse (using a predicate
        // shifted by max_P, so 1 becomes max_P+1 in the reverse,
        // and so on)

        uint64_t d = D.size();
        spo_triple triple_aux;

        max_S = get<0>(D[0]);
        max_P = get<1>(D[0]);
        max_O = get<2>(D[0]);

        for (i = 1; i < D.size(); i++) {
            if (max_S < get<0>(D[i])) max_S = get<0>(D[i]);
            if (max_P < get<1>(D[i])) max_P = get<1>(D[i]);
            if (max_O < get<2>(D[i])) max_O = get<2>(D[i]);
        }

        real_max_P = max_P;

        if (verbose) cout << "  > Adding inverted edges";
        for (uint64_t i = 0; i < d; i++) {
            triple_aux = D[i];
            D.push_back(spo_triple(get<2>(D[i]), get<1>(D[i]), get<0>(D[i])));
            get<0>(D[i]) = get<0>(triple_aux);
            get<1>(D[i]) = get<1>(D[i]) + real_max_P;
            get<2>(D[i]) = get<2>(triple_aux);
        }
        D.shrink_to_fit();

        if (verbose) cout << "... [done]" << endl;
        max_S = get<0>(D[0]);
        max_P = get<1>(D[0]);
        max_O = get<2>(D[0]);

        for (i = 1; i < D.size(); i++) {
            if (max_S < get<0>(D[i])) max_S = get<0>(D[i]);
            if (max_P < get<1>(D[i])) max_P = get<1>(D[i]);
            if (max_O < get<2>(D[i])) max_O = get<2>(D[i]);
        }

        {
            bit_vector bv_s(max_S + 1, 0);
            for (i = 0; i < D.size(); i++) {
                bv_s[get<0>(D[i])] = 1;
            }

            bit_vector bv_o(max_O + 1, 0);
            for (i = 0; i < D.size(); i++) {
                bv_o[get<2>(D[i])] = 1;
            }

            uint64_t _c = 0;
            for (i = 1; i < max_S + 1; i++) {
                if (!bv_s[i]) {
                    D.push_back(spo_triple(i, max_P + 1, max_O + 1));
                    _c++;
                }
            }

            _c = 0;
            for (i = 1; i < max_O + 1; i++) {
                if (!bv_o[i]) {
                    D.push_back(spo_triple(max_O + 1, max_P + 1, i));
                    _c++;
                }
            }

        }

        max_S++;
        max_O++;
        max_P++;

        triple_begin = D.begin();
        triple_end = D.end();

        if (verbose) cout << "  > Triples set = " << D.size() * sizeof(spo_triple) << " bytes" << endl;
        fflush(stdout);

        n = nTriples = triple_end - triple_begin;

        if (verbose) cout << "  > Determining number of elements per symbol";
        fflush(stdout);
        uint64_t alphabet_SO = (max_S < max_O) ? max_O : max_S;

        std::vector<uint32_t> M_O(alphabet_SO + 1, 0), M_P(max_P + 1, 0);

        for (i = 0; i < D.size(); i++) {
            M_O[std::get<2>(D[i])]++;
            M_P[std::get<1>(D[i])]++;
        }

        M_O.shrink_to_fit();
        M_P.shrink_to_fit();

        if (verbose) cout << "... [done]\n  > Sorting out triples in OSP order" << flush;
        // Sorts the triples in OSP order
        sort(D.begin(), D.end(), [](const spo_triple& a,
                                    const spo_triple& b) { return std::tie(std::get<2>(a), std::get<0>(a), std::get<1>(a))
                                                                  < std::tie(std::get<2>(b), std::get<0>(b), std::get<1>(b));});
        if (verbose) cout << "... [done]" << endl;

        //Building BWT_P

        {
            vector<uint64_t> new_C_P;
            if (verbose) cout << "  > Building C_P";
            uint64_t cur_pos = 1;
            new_C_P.push_back(0);  // Dummy value
            new_C_P.push_back(cur_pos);
            for (uint64_t c = 2; c <= alphabet_SO; c++) {
                cur_pos += M_O[c-1];
                new_C_P.push_back(cur_pos);
            }
            new_C_P.push_back(n+1);
            new_C_P.shrink_to_fit();
            M_O.clear();
            if (verbose) cout << "... [done]" << endl;
            if (verbose) cout << "  > Building bwtp" << flush;
            int_vector<> new_P(n+1);
            new_P[0] = 0;
            for (i=1; i<=n; i++)
                new_P[i] = std::get<1>(D[i-1]);
            util::bit_compress(new_P);

            L_P = bwt(new_P, new_C_P);
        }

        if (verbose) cout << "... [done]\n  > Stable sorting out triples in POS order" << flush;
        stable_sort(D.begin(), D.end(), [](const spo_triple& a,
                                           const spo_triple& b) {return std::get<1>(a) < std::get<1>(b);});
        if (verbose) cout << "... [done]" << endl;

        //Building BWT_S
        {
            vector<uint64_t> new_C_S;
            if (verbose) cout << "  > Building C_S";
            uint64_t cur_pos = 1;
            new_C_S.push_back(0);  // Dummy value
            new_C_S.push_back(cur_pos);
            for (uint64_t c = 2; c <= max_P; c++) {
                cur_pos += M_P[c-1];
                new_C_S.push_back(cur_pos);
            }
            new_C_S.push_back(n+1);
            new_C_S.shrink_to_fit();
            M_P.clear();
            if (verbose) cout << "... [done]" << endl;
            if (verbose) cout << "  > Building bwts" << flush;
            int_vector<> new_S(n+1);
            new_S[0] = 0;
            for (i=1; i<=n; i++)
                new_S[i] = std::get<0>(D[i-1]);
            if (with_split_support) {
                // pred_S[i]: previous position of L_S with the same symbol (0 if none)
                int_vector<> pred_S(n+1);
                std::unordered_map<uint64_t, uint64_t> hash_table;
                pred_S[0] = 0;
                for (i=1; i<=n; i++){
                    auto ht_it = hash_table.find(new_S[i]);
                    if(ht_it == hash_table.end()){
                        pred_S[i] = 0;
                        hash_table.insert({new_S[i], i});
                    }else {
                        pred_S[i] = ht_it->second;
                        ht_it->second = i;
                    }
                }
                util::bit_compress(pred_S);
                construct_im(wt_pred_s, pred_S);
                split_support = true;
            }
            util::bit_compress(new_S);
            L_S = bwt_nose(new_S, new_C_S);
            if (verbose) cout << "... [done]" << endl;
        }

        if (verbose) cout << "-- Index constructed successfully" << endl;
    };

    // Size of the index in bytes
    uint64_t size() {

        return L_S.size() + L_P.size() + (split_support ? sdsl::size_in_bytes(wt_pred_s) : 0);
    }

    uint64_t n_triples() {
        return nTriples;
    }

    uint64_t n_labels() {
        return max_P;
    }

    // Predicates of the graph; the inverse of predicate p is p + n_graph_predicates().
    uint64_t n_graph_predicates() const {
        return real_max_P;
    }

    void save(string filename) {
        L_S.save(filename + ".bwts");
        L_P.save(filename + ".bwtp");
        if (split_support) sdsl::store_to_file(wt_pred_s, filename + ".wt_pred_s");

        std::ofstream ofs(filename + ".nTriples");
        ofs << nTriples << endl;
        ofs << real_max_P << endl;
        ofs << max_S << endl;
        ofs << max_P << endl;
        ofs << max_O << endl;
    };

    void load(string filename) {
        L_S.load(filename + ".bwts");
        L_P.load(filename + ".bwtp");
        split_support = std::ifstream(filename + ".wt_pred_s").good();
        if (split_support) sdsl::load_from_file(wt_pred_s, filename + ".wt_pred_s");
        std::ifstream ifs(filename + ".nTriples");
        ifs >> nTriples;
        ifs >> real_max_P;
        ifs >> max_S;
        ifs >> max_P;
        ifs >> max_O;
    };

    bool has_split_support() const {
        return split_support;
    }

    uint64_t pred_selectivity(uint64_t pred_id) {
        return L_S.get_C(pred_id + 1) - L_S.get_C(pred_id);
    };

    inline uint64_t pred_reverse(uint64_t pred_id) const{
        return (pred_id > real_max_P) ? pred_id - real_max_P : pred_id + real_max_P;
    }

private:

    template<class T>
    static T &last_element(std::stack<T> &stack) { return stack.top(); }

    template<class T>
    static T first_element(std::stack<T> &stack) { return stack.top(); }

    template<class T>
    static T &last_element(std::queue<T> &queue) { return queue.back(); }

    template<class T>
    static T first_element(std::queue<T> &queue) { return queue.front(); }

    template<class word_t>
    std::vector<word_t> &B_array(int k) {
        auto &B = std::get<std::vector<word_t>>(B_arrays[k]);
        if (B.empty()) B.assign(4 * max_P, 0);
        return B;
    }

    // Array S, empty. Traversals do not nest, so one per word size suffices.
    template<class word_t>
    rpq_wavelet::visited_states<word_t> &S_array() {
        auto &S = std::get<std::unique_ptr<rpq_wavelet::visited_states<word_t>>>(S_arrays);
        if (!S) S.reset(new rpq_wavelet::visited_states<word_t>(4 * (max_O + 1)));
        else S->reset();
        return *S;
    }

    // Calls f(word_t()) with the narrowest word that holds the NFA states of
    // the RPQ: at most one per predicate plus the initial state (B7).
    template<class F>
    static void with_word_type(const std::string &rpq, F f) {
        const auto states = std::count(rpq.begin(), rpq.end(), '<') + 1;
        if (states <= 16) f(uint16_t());
        else if (states <= 32) f(uint32_t());
        else f(uint64_t());
    }

    template<class word_t>
    void push_merge_interval(container_type<word_t> &ist_container,
                             const std::tuple<uint64_t, word_t, std::pair<uint64_t, uint64_t>> &element){

        /**
             * get<0>: symbol
             * get<1>: NFA states (D)
             * get<2>: [lb, rb]
             */

        const auto lb = L_P.get_C(get<0>(element));
        const auto rb = L_P.get_C(get<0>(element) + 1) - 1;

        if(ist_container.empty()){
            ist_container.push(interval_state<word_t>{bwt_interval(lb, rb),get<1>(element)});
            return;
        }
        //The previous interval is contiguous to element's interval and
        //both have equal NFA state
        auto& last = last_element(ist_container);
        if(last.interval.right()+1 == lb && last.current_D == get<1>(element)){
            last.interval.set_right(rb);
        }else{
            ist_container.push(interval_state<word_t>{bwt_interval(lb, rb),get<1>(element)});
        }
    }

    // ---------------------------------------------------------------------
    // Traversal of the product graph (Section 4). Every traversal visits
    // ranges of objects (in L_P) together with the set D of active NFA states,
    // simulating the automaton backwards from its final states.

    // Time limit of a query (TIME_OUT seconds since start).
    struct deadline {
        high_resolution_clock::time_point start;
        bool expired() const {
            duration<double> elapsed = duration_cast<microseconds>(high_resolution_clock::now() - start);
            return elapsed.count() > TIME_OUT;
        }
    };

    enum class traversal_end { exhausted, stopped, timed_out };

    // One step from the object range I_p with states current_D:
    //  1. distinct predicates of I_p leading to an active state (Section 4.1),
    //  2. for each one, the subjects not yet visited with those states (4.2),
    //  3. each subject is queued as an object range (4.3).
    // visit(node) is called for every reached node where the automaton accepts;
    // it returns true to stop the traversal.
    // Buffers of next_step, reused along a traversal.
    template<class word_t>
    struct step_buffers {
        std::vector<std::pair<uint64_t, std::pair<uint64_t, uint64_t>>> predicates;
        std::vector<std::tuple<uint64_t, word_t, std::pair<uint64_t, uint64_t>>> subjects;
    };

    template<class word_t, class Visitor>
    bool next_step(RpqAutomata &A, std::vector<word_t> &B_array,
                   rpq_wavelet::visited_states<word_t> &D_array,
                   word_t current_D, bwt_interval &I_p,
                   container_type<word_t> &ist_container, step_buffers<word_t> &buffers, Visitor &visit) {
        auto &pred_vec = buffers.predicates;
        pred_vec.clear();
        L_P.active_predicates<word_t>(I_p.left(), I_p.right(), B_array, current_D, pred_vec);

        for (uint64_t i = 0; i < pred_vec.size(); i++) {
            auto interval = L_P.backward_step_test(I_p.left(), I_p.right(), pred_vec[i].first,
                                                   pred_vec[i].second.first,
                                                   pred_vec[i].second.second);
            uint64_t c = L_S.get_C(pred_vec[i].first);
            auto &subj_vec = buffers.subjects;
            subj_vec.clear();
            L_S.active_subjects<word_t>(c + interval.first, c + interval.second,
                                                          D_array,
                                                          (word_t) A.next(current_D, pred_vec[i].first, BWD),
                                                          subj_vec);
            paths += subj_vec.size();
            for (uint64_t j = 0; j < subj_vec.size(); j++) {
                push_merge_interval(ist_container, subj_vec[j]);
                if (A.atFinal(get<1>(subj_vec[j]), BWD) && visit(get<0>(subj_vec[j])))
                    return true;
            }
        }
        return false;
    }

    // Runs the traversal until no range is left, visit() stops it or the
    // deadline expires (checked after every step; it wins over a stop).
    template<class word_t, class Visitor>
    traversal_end traverse(RpqAutomata &A, std::vector<word_t> &B_array,
                           rpq_wavelet::visited_states<word_t> &D_array,
                           container_type<word_t> &ist_container, const deadline &limit, Visitor visit) {
        step_buffers<word_t> buffers;
        while (!ist_container.empty()) {
            auto ist_top = first_element(ist_container);
            ist_container.pop();
            bool stop = next_step(A, B_array, D_array, ist_top.current_D, ist_top.interval,
                                  ist_container, buffers, visit);
            if (limit.expired()) return traversal_end::timed_out;
            if (stop) return traversal_end::stopped;
        }
        return traversal_end::exhausted;
    }

    // Nodes where the automaton accepts, starting from every object at once
    // (first half of the variable-to-variable strategies). Returns true, and
    // reports nothing, if the automaton accepts the empty word: then every
    // node qualifies.
    template<class word_t>
    bool objects_reached(RpqAutomata &A, std::vector<uint64_t> &objects,
                         std::vector<word_t> &B_array, high_resolution_clock::time_point start) {
        auto &D_array = S_array<word_t>();
        word_t current_D = (word_t) A.getFinalStates();
        if (A.atFinal(current_D, BWD)) return true;

        container_type<word_t> ist_container;
        ist_container.push(interval_state<word_t>{bwt_interval(1, nTriples), current_D});
        traverse(A, B_array, D_array, ist_container, deadline{start},
                 [&](uint64_t node) { objects.push_back(node); return false; });
        return false;
    }

    // Every node reached from `node` where the automaton accepts, reported as
    // (node, x) if node_is_subject and as (x, node) otherwise. Returns true on
    // timeout.
    template<class word_t>
    bool reach_from(RpqAutomata &A, std::vector<word_t> &B_array, uint64_t node,
                    std::vector<std::pair<uint64_t, uint64_t>> &solutions,
                    bool node_is_subject, high_resolution_clock::time_point start) {
        auto &D_array = S_array<word_t>();
        container_type<word_t> ist_container;
        word_t current_D = (word_t) A.getFinalStates();
        ist_container.push(interval_state<word_t>{bwt_interval(L_P.get_C(node), L_P.get_C(node + 1) - 1),
                                               current_D});
        if (A.atFinal(current_D, BWD)) {
            solutions.emplace_back(node, node);
            rpq_wavelet::markD(node, L_S.max_level(), D_array, current_D);
        }
        auto end = traverse(A, B_array, D_array, ist_container, deadline{start}, [&](uint64_t x) {
            if (node_is_subject) solutions.emplace_back(node, x);
            else solutions.emplace_back(x, node);
            return false;
        });
        return end == traversal_end::timed_out;
    }

    // Reports (from, to) -- or (to, from) if !from_is_subject -- if the
    // automaton accepts at `to` starting from `from`. Returns true on timeout.
    template<class word_t>
    bool reaches(RpqAutomata &A, std::vector<word_t> &B_array, uint64_t from, uint64_t to,
                 std::vector<std::pair<uint64_t, uint64_t>> &solutions,
                 bool from_is_subject, high_resolution_clock::time_point start) {
        auto &D_array = S_array<word_t>();
        container_type<word_t> ist_container;
        word_t current_D = (word_t) A.getFinalStates();
        ist_container.push(interval_state<word_t>{bwt_interval(L_P.get_C(from), L_P.get_C(from + 1) - 1),
                                               current_D});
        if (from == to && A.atFinal(current_D, BWD)) {
            solutions.emplace_back(from, from);
            return false;
        }
        auto end = traverse(A, B_array, D_array, ist_container, deadline{start},
                            [&](uint64_t x) { return x == to; });
        if (end == traversal_end::timed_out) return true;
        if (end == traversal_end::stopped) {
            if (from_is_subject) solutions.emplace_back(from, to);
            else solutions.emplace_back(to, from);
        }
        return false;
    }

    inline void get_elements_intersection(const uint64_t pred_1, const uint64_t pred_2,
                                    std::vector<uint64_t> &elements){

        std::vector<std::array<uint64_t, 2ul>> ranges;

        ranges.push_back({L_S.get_C(pred_1), L_S.get_C(pred_1+1)-1});
        ranges.push_back({L_S.get_C(pred_2), L_S.get_C(pred_2+1)-1});
        elements = L_S.intersect_nofreq(ranges);
    }

    inline void get_elements(const uint64_t pred_1, std::vector<uint64_t> &elements){
        elements = L_S.all_values_in_range(L_S.get_C(pred_1), L_S.get_C(pred_1+1)-1);
    }

    std::pair<std::string, std::string> split_rpq(RpqTree &rpqTree,
                                                  MandatoryData &pos_pred_vec,
                                                  bool &first_left,
                                                  std::vector<uint64_t> &elements,
                                                  uint64_t n_predicates,
                                                  selectivity::query_type q_type = selectivity::var_var){

        uint64_t i_split = 0;
        uint64_t sigma = (max_O > max_S) ? max_O : max_S;
        selectivity::h_sum_path_intersection h(pos_pred_vec, L_S, wt_pred_s, real_max_P,
                                                    sigma, n_predicates, q_type);

        selectivity::info sel_min = {std::numeric_limits<double>::max(), selectivity::source, true};
        selectivity::info sel_info;
        for(uint64_t i = 0; i < pos_pred_vec.size();++i){
            if(i+1 < pos_pred_vec.size()
               && pos_pred_vec[i].pos_e == pos_pred_vec[i+1].pos_b-1) {
                sel_info = h.intersection(i);
            }else {
                sel_info = h.simple(i);
            }
            if(sel_info.weight < sel_min.weight){
                sel_min = sel_info;
                i_split = i;
            }

        }

        std::cerr << "weight: " << sel_info.weight << std::endl;

        //Computing the elements and splitting the RPQ
        first_left = sel_min.first_left;
        if(sel_min.split == selectivity::intersect){
            //Target of i_split is the splitting node
            elements = h.get_elements_intersection(i_split);
            return rpqTree.splitRpq(pos_pred_vec[i_split].pos_e);
        }else if (sel_min.split == selectivity::source){
            //Source of i_split is the splitting node
            auto pred = pos_pred_vec[i_split].pred_b;
            auto pred_rev = pred_reverse(pred);
            get_elements(pred_rev, elements);
            return rpqTree.splitRpq(pos_pred_vec[i_split].pos_b-1);
        }else{
            //Target of i_split is the splitting node
            auto pred = pos_pred_vec[i_split].pred_e;
            get_elements(pred, elements);
            return rpqTree.splitRpq(pos_pred_vec[i_split].pos_e);
        }
    }

    std::pair<uint64_t, selectivity::info> pos_split_rpq(const MandatoryData &pos_pred_vec,
                                                     const dictionary_type &predicates_map,
                                                     uint64_t n_predicates,
                                                     selectivity::query_type q_type = selectivity::var_var){

        uint64_t i_split = 0;
        uint64_t sigma = (max_O > max_S) ? max_O : max_S;
        selectivity::h_sum_path_intersection h(pos_pred_vec, L_S, wt_pred_s, real_max_P,
                                                    sigma, n_predicates, q_type);
        selectivity::info
            sel_min = {std::numeric_limits<double>::max(), selectivity::source, true};

        //1. Checking mandatory data
        for(uint64_t i = 0; i < pos_pred_vec.size(); ++i){
            selectivity::info sel_info;
            sel_info = h.simple(i);
            //3. Taking info with the smallest selectivity
            if(sel_info.weight < sel_min.weight){
                sel_min = sel_info;
                i_split = i;
            }
            //2. Intersection
            if(i+1 < pos_pred_vec.size()
               && pos_pred_vec[i].pos_e == pos_pred_vec[i+1].pos_b-1) {
                sel_info = h.intersection(i);
            }
            //3. Taking info with the smallest selectivity
            if(sel_info.weight < sel_min.weight){
                sel_min = sel_info;
                i_split = i;
            }

        }
        return {i_split, sel_min};
    }

public:

    // Disjunction of n_or predicates between two variables (Section 6.1).
    void or_query_var_to_var(const rpq_query &query, uint64_t n_or,
                             std::vector<std::pair<uint64_t, uint64_t>> &output) {
        uint64_t i, pred, k, a, max_pos;
        bool negated_pred;
        std::vector<uint64_t> values_x, values_y;
        std::pair<uint64_t, uint64_t> I_S;
        uint64_t c, object;
        pair_set o_set;
        std::pair<pair_set::iterator, bool> ret;
        std::vector<int64_t> pred_v;
        std::vector<bool> negated_pred_v;
        std::vector<std::pair<uint64_t, uint64_t>> I_S_v;

        auto start_time = high_resolution_clock::now();
        for (k = 0; k < n_or; k++) {
            negated_pred_v.push_back(query.predicates[k].inverse);
            pred_v.push_back(query.predicates[k].id);
            I_S_v.push_back(std::pair<uint64_t, uint64_t>(
                    L_S.get_C(pred_v[k] + real_max_P),
                    L_S.get_C(pred_v[k] + real_max_P + 1) - 1
            ));
        }

        auto stop = high_resolution_clock::now();
        auto total_time = duration_cast<seconds>(stop - start_time).count();
        bool time_out = (total_time > TIME_OUT);
        for (k = 0; !time_out && k < n_or; k++) {
            for (a = 0; a < n_or and pred_v[a] == -1; ++a);
            max_pos = a;
            for (++a; a < n_or; ++a) {
                if (pred_v[a] == -1) continue;
                if (I_S_v[a].second - I_S_v[a].first > I_S_v[max_pos].second - I_S_v[max_pos].first)
                    max_pos = a;
            }
            pred = pred_v[max_pos];
            I_S = I_S_v[max_pos];
            negated_pred = negated_pred_v[max_pos];
            pred_v[max_pos] = -1;
            // Now extract all ?x values
            values_x = L_S.all_values_in_range(I_S.first, I_S.second);
            stop = high_resolution_clock::now();
            total_time = duration_cast<seconds>(stop - start_time).count();
            time_out = (total_time > TIME_OUT);
            // For each ?x obtained, search for ?y p ?x using backward search
            for (i = 0; i < values_x.size() && !time_out; i++) {
                object = values_x[i];
                I_S = L_P.backward_step(L_P.get_C(object), L_P.get_C(object + 1) - 1, pred);
                c = L_S.get_C(pred);
                I_S.first += c;
                I_S.second += c;
                values_y.clear();
                values_y = L_S.all_values_in_range(I_S.first, I_S.second);
                if (negated_pred) {
                    for (uint64_t j = 0; !time_out && j < values_y.size(); ++j) {
                        ret = o_set.insert(std::pair<uint64_t, uint64_t>(values_y[j], object));
                        if (ret.second == true)
                            output.push_back(std::pair<uint64_t, uint64_t>(values_y[j], object));
                        stop = high_resolution_clock::now();
                        total_time = duration_cast<seconds>(stop - start_time).count();
                        time_out = (total_time > TIME_OUT);
                    }
                } else {
                    for (uint64_t j = 0; !time_out && j < values_y.size(); ++j) {
                        ret = o_set.insert(std::pair<uint64_t, uint64_t>(object, values_y[j]));
                        if (ret.second == true)
                            output.push_back(std::pair<uint64_t, uint64_t>(object, values_y[j]));
                        stop = high_resolution_clock::now();
                        total_time = duration_cast<seconds>(stop - start_time).count();
                        time_out = (total_time > TIME_OUT);
                    }
                }
            }

        }
    };

    // Solves single-predicate queries (with no operator, except ^)
    void single_predicate_query(uint64_t predicate, uint64_t object, uint64_t query_type,
                                bool is_negated,
                                std::vector<std::pair<uint64_t, uint64_t>> &output,
                                high_resolution_clock::time_point start) {
        if (query_type == VAR_TO_CONST or query_type == CONST_TO_VAR) {
            std::pair<uint64_t, uint64_t> I_S = L_P.backward_step(L_P.get_C(object), L_P.get_C(object + 1) - 1,
                                                                  predicate);
            uint64_t c = L_S.get_C(predicate);
            I_S.first += c;
            I_S.second += c;

            std::vector<uint64_t> values_in_I_S = L_S.all_values_in_range(I_S.first, I_S.second);

            if (query_type == VAR_TO_CONST) {
                auto stop = high_resolution_clock::now();
                auto total_time = duration_cast<seconds>(stop - start).count();
                bool time_out = (total_time > TIME_OUT);
                for (uint64_t i = 0; !time_out && i < values_in_I_S.size(); ++i) {
                    output.push_back(std::pair<uint64_t, uint64_t>(values_in_I_S[i], object));
                    stop = high_resolution_clock::now();
                    total_time = duration_cast<seconds>(stop - start).count();
                    time_out = (total_time > TIME_OUT);
                }
            }else{
                auto stop = high_resolution_clock::now();
                auto total_time = duration_cast<seconds>(stop - start).count();
                bool time_out = (total_time > TIME_OUT);
                for (uint64_t i = 0; !time_out && i < values_in_I_S.size(); ++i) {
                    output.push_back(std::pair<uint64_t, uint64_t>(object, values_in_I_S[i]));
                    stop = high_resolution_clock::now();
                    total_time = duration_cast<seconds>(stop - start).count();
                    time_out = (total_time > TIME_OUT);
                }

            }

        } else {
            // asumo que siempre envio el predicado original, sin negar, el var-to-var tiene que hacer eso
            std::pair<uint64_t, uint64_t> I_S = std::pair<uint64_t, uint64_t>(
                    L_S.get_C(predicate + real_max_P),
                    L_S.get_C(predicate + real_max_P + 1) - 1
            );

            // Now extract all ?x values
            std::vector<uint64_t> values_x, values_y;
            values_x = L_S.all_values_in_range(I_S.first, I_S.second);
            uint64_t c;
            auto stop = high_resolution_clock::now();
            auto total_time = duration_cast<seconds>(stop - start).count();
            bool time_out = (total_time > TIME_OUT);
            // For each ?x obtained, search for ?y p ?x using backward search
            for (uint64_t i = 0; !time_out && i < values_x.size(); i++) {
                object = values_x[i];
                I_S = L_P.backward_step(L_P.get_C(object), L_P.get_C(object + 1) - 1, predicate);
                c = L_S.get_C(predicate);
                I_S.first += c;
                I_S.second += c;
                values_y.clear();
                values_y = L_S.all_values_in_range(I_S.first, I_S.second);
                for (uint64_t j = 0; !time_out && j < values_y.size() ; ++j){
                    if (is_negated) {
                        output.push_back(std::pair<uint64_t, uint64_t>(values_y[j], object));
                    } else {
                        output.push_back(std::pair<uint64_t, uint64_t>(object, values_y[j]));
                    }
                    stop = high_resolution_clock::now();
                    total_time = duration_cast<seconds>(stop - start).count();
                    time_out = (total_time > TIME_OUT);
                }
            }
        }
    };

    // Concatenation of two predicates p1/p2 (Section 6.1): the middle nodes
    // are the intersection of the targets of p1 and the sources of p2.
    void path_query(const rpq_query &query, uint64_t object, uint64_t query_type,
                    std::vector<std::pair<uint64_t, uint64_t>> &output,
                    high_resolution_clock::time_point start) {
        const predicate_ref p1 = query.predicates[0], p2 = query.predicates[1];
        if (query_type == VAR_TO_CONST || query_type == CONST_TO_VAR) {

            uint64_t i;
            uint64_t pred_fixed = p1.inverse ? real_max_P + p1.id : p1.id;
            uint64_t pred_open = p2.inverse ? p2.id : real_max_P + p2.id;
            if (query_type == VAR_TO_CONST) std::swap(pred_fixed, pred_open);

            std::pair<uint64_t, uint64_t>
                    Is_p1 = std::pair<uint64_t, uint64_t>(L_S.get_C(pred_open),
                                                          L_S.get_C(pred_open + 1) - 1);

            // first, backward search for object ^pred_2, starting from the L_P interval of object
            std::pair<uint64_t, uint64_t> Is_p2 = L_P.backward_step(L_P.get_C(object),
                                                                    L_P.get_C(object + 1) - 1,
                                                                    pred_fixed);
            uint64_t c = L_S.get_C(pred_fixed);
            Is_p2.first += c;
            Is_p2.second += c;

            std::vector<std::pair<uint64_t, uint64_t>> z_values;
            std::vector<std::array<uint64_t, 2ul>> ranges;

            ranges.push_back({Is_p1.first, Is_p1.second});
            ranges.push_back({Is_p2.first, Is_p2.second});
            z_values = L_S.intersect(ranges);

            std::vector<uint64_t> values_s;

            pred_open = (pred_open > real_max_P) ? pred_open - real_max_P : pred_open + real_max_P;

            pair_set o_set;
            std::pair<pair_set::iterator, bool> ret;

            auto stop = high_resolution_clock::now();
            auto total_time = duration_cast<seconds>(stop - start).count();
            auto time_out = (total_time > TIME_OUT);
            for (i = 0; !time_out && i < z_values.size(); ++i) {
                Is_p1 = L_P.backward_step(L_P.get_C(z_values[i].first), L_P.get_C(z_values[i].first + 1) - 1, pred_open);
                c = L_S.get_C(pred_open);
                Is_p1.first += c;
                Is_p1.second += c;
                values_s.clear();
                values_s = L_S.all_values_in_range(Is_p1.first, Is_p1.second);
                for (uint64_t j = 0; !time_out && j < values_s.size(); ++j) {
                    if(query_type == CONST_TO_VAR){
                        ret = o_set.insert(std::pair<uint64_t, uint64_t>(object, values_s[j]));
                        if (ret.second == true)
                            output.push_back(std::pair<uint64_t, uint64_t>(object, values_s[j]));
                    }else{
                        ret = o_set.insert(std::pair<uint64_t, uint64_t>(values_s[j], object));
                        if (ret.second == true)
                            output.push_back(std::pair<uint64_t, uint64_t>(values_s[j], object));
                    }
                    stop = high_resolution_clock::now();
                    total_time = duration_cast<seconds>(stop - start).count();
                    time_out = (total_time > TIME_OUT);
                }
            }
        } else {
            if (query_type == VAR_TO_VAR) {
                uint64_t i;
                const bool negated_p1 = p1.inverse, negated_p2 = p2.inverse;
                uint64_t pred_1 = negated_p1 ? real_max_P + p1.id : p1.id;
                uint64_t pred_2 = negated_p2 ? p2.id : real_max_P + p2.id;  // actually, ^pred_2

                std::pair<uint64_t, uint64_t>
                        Is_p1 = std::pair<uint64_t, uint64_t>(L_S.get_C(pred_1),
                                                              L_S.get_C(pred_1 + 1) - 1),
                        Is_p2 = std::pair<uint64_t, uint64_t>(L_S.get_C(pred_2),
                                                              L_S.get_C(pred_2 + 1) - 1);

                std::vector<std::pair<uint64_t, uint64_t>> z_values;
                std::vector<std::array<uint64_t, 2ul>> ranges;

                ranges.push_back({Is_p1.first, Is_p1.second});
                ranges.push_back({Is_p2.first, Is_p2.second});
                z_values = L_S.intersect(ranges);

                std::vector<uint64_t> values_s1, values_s2;
                uint64_t c, z;

                pred_1 = pred_1 + ((negated_p1) ? -real_max_P : real_max_P);
                pred_2 = pred_2 + ((negated_p2) ? real_max_P : -real_max_P); // now it becomes pred_2

                pair_set o_set;
                std::pair<pair_set::iterator, bool> ret;
                auto stop = high_resolution_clock::now();
                auto total_time = duration_cast<seconds>(stop - start).count();
                auto time_out = (total_time > TIME_OUT);
                for (i = 0; !time_out && i < z_values.size(); ++i) {
                    z = z_values[i].first;
                    Is_p1 = L_P.backward_step(L_P.get_C(z), L_P.get_C(z + 1) - 1, pred_1);
                    c = L_S.get_C(pred_1);
                    Is_p1.first += c;
                    Is_p1.second += c;
                    Is_p2 = L_P.backward_step(L_P.get_C(z), L_P.get_C(z + 1) - 1, pred_2);
                    c = L_S.get_C(pred_2);
                    Is_p2.first += c;
                    Is_p2.second += c;
                    values_s1.clear();
                    values_s2.clear();
                    values_s1 = L_S.all_values_in_range(Is_p1.first, Is_p1.second);
                    values_s2 = L_S.all_values_in_range(Is_p2.first, Is_p2.second);

                    for (uint64_t j = 0; !time_out && j < values_s1.size(); ++j) {
                        for (uint64_t w = 0; !time_out && w < values_s2.size(); ++w) {
                            ret = o_set.insert(std::pair<uint64_t, uint64_t>(values_s1[j], values_s2[w]));
                            if (ret.second == true)
                                output.push_back(std::pair<uint64_t, uint64_t>(values_s1[j], values_s2[w]));
                            stop = high_resolution_clock::now();
                            total_time = duration_cast<seconds>(stop - start).count();
                            time_out = (total_time > TIME_OUT);
                        }
                    }
                }
            }
        }
    };

private:

    // Marks in B_array, while alive, the NFA states reached through each
    // predicate of an automaton, at its leaf and its ancestors in the wavelet
    // tree of L_P (array B of Section 4.1).
    template<class word_t>
    class marked_predicates {
        const bwt &m_L_P;
        std::vector<word_t> &m_B;
        std::unordered_map<uint64_t, uint64_t> m_masks;
    public:
        marked_predicates(const bwt &L_P, RpqAutomata &A, std::vector<word_t> &B)
                : m_L_P(L_P), m_B(B), m_masks(A.getB()) {
            for (const auto &m : m_masks) m_L_P.mark<word_t>(m.first, m_B, (word_t) m.second);
        }
        ~marked_predicates() {
            for (const auto &m : m_masks) m_L_P.unmark<word_t>(m.first, m_B);
        }
        marked_predicates(const marked_predicates &) = delete;
        marked_predicates &operator=(const marked_predicates &) = delete;
    };

    // Automaton for traversals that start at the object end of the RPQ.
    RpqAutomata object_side_automaton(const std::string &rpq, const dictionary_type &predicates_map) {
        return RpqAutomata(object_side_expression(rpq), predicates_map);
    }

    // Automaton for traversals that start at the subject end of the RPQ.
    RpqAutomata subject_side_automaton(const std::string &rpq, const dictionary_type &predicates_map) {
        return RpqAutomata(subject_side_expression(rpq), predicates_map);
    }

    // Variable-to-variable RPQs (Section 4.5): a first traversal from all the
    // nodes at once finds the nodes at one end that have some answer, then a
    // traversal from each of them reports the answers. With objects_first
    // ("os") the first traversal uses the automaton of the object end.
    template<class word_t>
    void var_to_var(const std::string &rpq, const dictionary_type &predicates_map,
                    std::vector<word_t> &B_array, std::vector<std::pair<uint64_t, uint64_t>> &output,
                    bool objects_first) {
        RpqAutomata first = objects_first ? object_side_automaton(rpq, predicates_map)
                                          : subject_side_automaton(rpq, predicates_map);
        auto start = high_resolution_clock::now();
        std::vector<uint64_t> ends;
        bool all;
        {
            marked_predicates<word_t> marks(L_P, first, B_array);
            all = objects_reached(first, ends, B_array, start);
        }

        RpqAutomata second = objects_first ? subject_side_automaton(rpq, predicates_map)
                                           : object_side_automaton(rpq, predicates_map);
        marked_predicates<word_t> marks(L_P, second, B_array);
        bool time_out = false;
        if (all) {  // the RPQ accepts the empty word: every node is an end
            uint64_t sigma = (max_O > max_S) ? max_O : max_S;
            for (uint64_t i = 1; !time_out && i < sigma; i++)
                time_out = reach_from(second, B_array, i, output, objects_first, start);
        } else {
            for (uint64_t i = 0; !time_out && i < ends.size(); i++)
                time_out = reach_from(second, B_array, ends[i], output, objects_first, start);
        }
    }

    // Solves an RPQ split at a node set (Section 5.2): rpq_l is solved towards
    // the subject and rpq_r towards the object from every splitting node in
    // `elements` (starting with rpq_l if first_left), and the answers of both
    // halves are joined by the splitting node. An empty half means the split
    // is at one end of the RPQ.
    template<class word_t>
    void solve_split(const std::string &rpq_l, const std::string &rpq_r,
                     const std::vector<uint64_t> &elements,
                     const dictionary_type &predicates_map,
                     std::vector<word_t> &B_array_l, std::vector<word_t> &B_array_r,
                     bool first_left, std::vector<std::pair<uint64_t, uint64_t>> &solution,
                     high_resolution_clock::time_point start) {
        bool time_out = false;
        if (rpq_l.empty()) {
            RpqAutomata A_r = subject_side_automaton(rpq_r, predicates_map);
            marked_predicates<word_t> marks(L_P, A_r, B_array_r);
            for (uint64_t i = 0; !time_out && i < elements.size(); ++i)
                time_out = reach_from(A_r, B_array_r, elements[i], solution, true, start);
            return;
        }
        if (rpq_r.empty()) {
            RpqAutomata A_l = object_side_automaton(rpq_l, predicates_map);
            marked_predicates<word_t> marks(L_P, A_l, B_array_l);
            for (uint64_t i = 0; !time_out && i < elements.size(); ++i)
                time_out = reach_from(A_l, B_array_l, elements[i], solution, false, start);
            return;
        }

        RpqAutomata A_l = object_side_automaton(rpq_l, predicates_map);
        RpqAutomata A_r = subject_side_automaton(rpq_r, predicates_map);
        marked_predicates<word_t> marks_l(L_P, A_l, B_array_l);
        marked_predicates<word_t> marks_r(L_P, A_r, B_array_r);
        const deadline limit{start};
        // Answers of the left half are (x, e), those of the right half (e, y).
        auto solve_left = [&](uint64_t e, std::vector<std::pair<uint64_t, uint64_t>> &out) {
            return reach_from(A_l, B_array_l, e, out, false, start);
        };
        auto solve_right = [&](uint64_t e, std::vector<std::pair<uint64_t, uint64_t>> &out) {
            return reach_from(A_r, B_array_r, e, out, true, start);
        };
        std::vector<std::pair<uint64_t, uint64_t>> output_1, output_2;  // first and second half solved
        pair_set sol_set;
        for (uint64_t i = 0; !time_out && i < elements.size(); ++i) {
            const auto e = elements[i];
            time_out = first_left ? solve_left(e, output_1) : solve_right(e, output_1);
            if (output_1.empty()) continue;  // no answer through e
            time_out = first_left ? solve_right(e, output_2) : solve_left(e, output_2);
            time_out = limit.expired();
            for (uint64_t i2 = 0; !time_out && i2 < output_2.size(); ++i2) {
                for (uint64_t i1 = 0; !time_out && i1 < output_1.size(); ++i1) {
                    const auto &l = first_left ? output_1[i1] : output_2[i2];
                    const auto &r = first_left ? output_2[i2] : output_1[i1];
                    if (sol_set.insert({l.first, r.second}).second)
                        solution.emplace_back(l.first, r.second);
                    time_out = limit.expired();
                }
            }
            output_2.clear();
            output_1.clear();
        }
    }

    // solve_split with the word size of the whole RPQ.
    void solve_split_any(const std::string &rpq, const std::string &rpq_l, const std::string &rpq_r,
                         const std::vector<uint64_t> &elements,
                         const dictionary_type &predicates_map, bool first_left,
                         std::vector<std::pair<uint64_t, uint64_t>> &solution,
                         high_resolution_clock::time_point start) {
        with_word_type(rpq, [&](auto word) {
            using word_t = decltype(word);
            solve_split(rpq_l, rpq_r, elements, predicates_map, B_array<word_t>(0), B_array<word_t>(1),
                        first_left, solution, start);
        });
    }

public:

    // Query evaluation primitives. The strategy for each query is chosen by
    // the planner (src/planner.hpp).

    // A single predicate, without automaton (Section 6.1). For CONST_TO_VAR
    // and VAR_TO_CONST, `constant` is the subject or object.
    void single_predicate(const rpq_query &query, uint64_t constant, uint64_t query_type,
                          std::vector<std::pair<uint64_t, uint64_t>> &output) {
        const predicate_ref p = query.predicates[0];
        uint64_t predicate = p.id;
        if ((query_type == CONST_TO_VAR && p.inverse) || (query_type == VAR_TO_CONST && !p.inverse))
            predicate += real_max_P;
        single_predicate_query(predicate, constant, query_type, p.inverse, output,
                               high_resolution_clock::now());
    }

    // A concatenation of two predicates, without automaton (Section 6.1).
    void two_predicate_path(const rpq_query &query, uint64_t constant, uint64_t query_type,
                            std::vector<std::pair<uint64_t, uint64_t>> &output) {
        path_query(query, constant, query_type, output, high_resolution_clock::now());
    }

    // <s> E ?y
    void rpq_const_s_to_var_o(const rpq_query &query, std::vector<std::pair<uint64_t, uint64_t>> &output) {
        with_word_type(query.rpq, [&](auto word) {
            using word_t = decltype(word);
            auto &B = B_array<word_t>(0);
            RpqAutomata A = subject_side_automaton(query.rpq, query.pred_map);
            marked_predicates<word_t> marks(L_P, A, B);
            reach_from(A, B, query.subject_id, output, true, high_resolution_clock::now());
        });
    }

    // <s> E <o>: reports (s, o) if some path from s to o matches E.
    void rpq_const_s_to_const_o(const rpq_query &query, std::vector<std::pair<uint64_t, uint64_t>> &output) {
        with_word_type(query.rpq, [&](auto word) {
            using word_t = decltype(word);
            auto &B = B_array<word_t>(0);
            RpqAutomata A = subject_side_automaton(query.rpq, query.pred_map);
            marked_predicates<word_t> marks(L_P, A, B);
            reaches(A, B, query.subject_id, query.object_id, output, true, high_resolution_clock::now());
        });
    }

    // ?x E <o>
    void rpq_var_s_to_const_o(const rpq_query &query, std::vector<std::pair<uint64_t, uint64_t>> &output) {
        with_word_type(query.rpq, [&](auto word) {
            using word_t = decltype(word);
            auto &B = B_array<word_t>(0);
            RpqAutomata A = object_side_automaton(query.rpq, query.pred_map);
            marked_predicates<word_t> marks(L_P, A, B);
            reach_from(A, B, query.object_id, output, false, high_resolution_clock::now());
        });
    }

    // ?x E ?y, finding first the objects that have some answer.
    void rpq_var_to_var_os(const rpq_query &query, std::vector<std::pair<uint64_t, uint64_t>> &output) {
        with_word_type(query.rpq, [&](auto word) {
            var_to_var(query.rpq, query.pred_map, B_array<decltype(word)>(0), output, true);
        });
    }

    // ?x E ?y, finding first the subjects that have some answer.
    void rpq_var_to_var_so(const rpq_query &query, std::vector<std::pair<uint64_t, uint64_t>> &output) {
        with_word_type(query.rpq, [&](auto word) {
            var_to_var(query.rpq, query.pred_map, B_array<decltype(word)>(0), output, false);
        });
    }

    // How to split an RPQ (Section 5.2): its two halves, the splitting nodes
    // and which half is solved first.
    struct split_plan {
        std::string rpq_l, rpq_r;
        std::vector<uint64_t> elements;
        bool first_left;
        high_resolution_clock::time_point start;  // the time limit counts from here
    };

    // Chooses the split of an RPQ with the estimation of Eq. (9). Returns
    // false if the RPQ has no mandatory position, so it cannot be split.
    bool plan_split(const rpq_query &query, split_plan &plan) {
        RpqTree rpqTree(query.rpq, query.pred_map, real_max_P);
        auto mand_data = rpqTree.getMandatoryData();
        if (mand_data.empty()) return false;
        plan.start = high_resolution_clock::now();
        std::tie(plan.rpq_l, plan.rpq_r) = split_rpq(rpqTree, mand_data, plan.first_left, plan.elements,
                                                     query.n_predicates);
        return true;
    }

    // ?x E ?y split as planned.
    void rpq_var_to_var_split(const rpq_query &query, const split_plan &plan,
                              std::vector<std::pair<uint64_t, uint64_t>> &solution) {
        paths = 0;
        solve_split_any(query.rpq, plan.rpq_l, plan.rpq_r, plan.elements, query.pred_map, plan.first_left,
                        solution, plan.start);
        std::cerr << "paths: " << paths << std::endl;  // traversed edges, for the experiments
    }

    // Low-level access for tools built on the primitives of the index
    // (src/ring-tunnels.cpp, src/gen-queries.cpp).
    bwt_nose &subjects_bwt() { return L_S; }
    bwt &predicates_bwt() { return L_P; }
    uint64_t max_subject() const { return max_S; }
    uint64_t max_object() const { return max_O; }

    // <from> E <to> with an automaton A built by the caller, whose predicates
    // are already marked in B (A drives the traversal from `from`). Reports
    // (from, to), or (to, from) if !from_is_subject. Returns true on timeout.
    template<class word_t>
    bool reaches_marked(RpqAutomata &A, std::vector<word_t> &B, uint64_t from, uint64_t to,
                        std::vector<std::pair<uint64_t, uint64_t>> &solutions, bool from_is_subject) {
        return reaches(A, B, from, to, solutions, from_is_subject, high_resolution_clock::now());
    }

    // Experiment of Fig. 13 (middle): solves the RPQ with every possible
    // split and prints the time of each one.
    void rpq_var_to_var_split_all(const rpq_query &query) {
        const std::string &rpq = query.rpq;
        const dictionary_type &predicates_map = query.pred_map;
        const uint64_t n_predicates = query.n_predicates;

        RpqTree rpqTree(rpq, predicates_map, real_max_P);
        auto pos_pred_vec = rpqTree.getMandatoryData();

        auto a = pos_split_rpq(pos_pred_vec, predicates_map, n_predicates);

        if(a.second.split == selectivity::none){
            std::cout << "Selectivity: solving query starting to " << (a.second.first_left ? "left" : "right") << std::endl;
        }else if(a.second.split == selectivity::intersect){
            std::cout << "Selectivity: " << a.first << "-th mandatory pred by intersecting " << std::endl;
            std::cout << "Startitng with subquery: " << (a.second.first_left ? "left" : "right") << std::endl;
        }else if (a.second.split == selectivity::target){
            std::cout << "Selectivity: " << a.first << "-th mandatory pred by target" << std::endl;
            std::cout << "Startitng with subquery: " << (a.second.first_left ? "left" : "right") << std::endl;
        }else{
            std::cout << "Selectivity: " << a.first << "-th mandatory pred by source" << std::endl;
            std::cout << "Startitng with subquery: " << (a.second.first_left ? "left" : "right") << std::endl;
        }

        std::string rpq_l, rpq_r;
        std::vector<uint64_t> elements;
        std::cout << "Source" << std::endl;
        for(uint64_t i = 0; i < pos_pred_vec.size(); ++i) {
            std::vector<std::pair<uint64_t, uint64_t>> solution;

            high_resolution_clock::time_point start, stop;
            double total_time = 0.0;
            duration<double> time_span;
            start = high_resolution_clock::now();
            auto pred = pos_pred_vec[i].pred_b;
            auto pred_rev = pred_reverse(pred);
            get_elements(pred_rev, elements);
            std::tie(rpq_l, rpq_r) = rpqTree.splitRpq(pos_pred_vec[i].pos_b-1);
            solve_split_any(rpq, rpq_l, rpq_r, elements, predicates_map, true, solution, start);
            stop = high_resolution_clock::now();
            time_span = duration_cast<microseconds>(stop - start);
            total_time = time_span.count();
            //Format: split; n_solutions; n_elements; time (ns)
            cout << i << ";" << solution.size() << ";" << elements.size() << ";left;" << (uint64_t) (total_time * 1000000000ULL) << endl;
            if(!rpq_l.empty() && !rpq_r.empty()) {
                solution.clear();
                start = high_resolution_clock::now();
                pred = pos_pred_vec[i].pred_b;
                pred_rev = pred_reverse(pred);
                get_elements(pred_rev, elements);
                std::tie(rpq_l, rpq_r) = rpqTree.splitRpq(pos_pred_vec[i].pos_b - 1);

                solve_split_any(rpq, rpq_l, rpq_r, elements, predicates_map, false, solution, start);

                stop = high_resolution_clock::now();
                time_span = duration_cast<microseconds>(stop - start);
                total_time = time_span.count();
                //Format: split; n_solutions; n_elements; time (ns)
                cout << i << ";" << solution.size() << ";" << elements.size() << ";right;"
                     << (uint64_t) (total_time * 1000000000ULL) << endl;
            }
        }

        std::cout << "Target" << std::endl;
        for(uint64_t i = 0; i < pos_pred_vec.size(); ++i) {
            std::vector<std::pair<uint64_t, uint64_t>> solution;

            high_resolution_clock::time_point start, stop;
            double total_time = 0.0;
            duration<double> time_span;
            start = high_resolution_clock::now();
            auto pred = pos_pred_vec[i].pred_e;
            get_elements(pred, elements);
            std::tie(rpq_l, rpq_r) = rpqTree.splitRpq(pos_pred_vec[i].pos_e);
            solve_split_any(rpq, rpq_l, rpq_r, elements, predicates_map, true, solution, start);
            stop = high_resolution_clock::now();
            time_span = duration_cast<microseconds>(stop - start);
            total_time = time_span.count();
            //Format: split; n_solutions; n_elements; time (ns)
            cout << i << ";" << solution.size() << ";" << elements.size() << ";left;" << (uint64_t) (total_time * 1000000000ULL) << endl;
            if(!rpq_l.empty() && !rpq_r.empty()) {
                solution.clear();
                start = high_resolution_clock::now();
                pred = pos_pred_vec[i].pred_e;
                get_elements(pred, elements);
                std::tie(rpq_l, rpq_r) = rpqTree.splitRpq(pos_pred_vec[i].pos_e);
                solve_split_any(rpq, rpq_l, rpq_r, elements, predicates_map, false, solution, start);

                stop = high_resolution_clock::now();
                time_span = duration_cast<microseconds>(stop - start);
                total_time = time_span.count();
                //Format: split; n_solutions; n_elements; time (ns)
                cout << i << ";" << solution.size() << ";" << elements.size() << ";right;" << (uint64_t) (total_time * 1000000000ULL) << endl;
            }
        }
        std::cout << "Intersection" << std::endl;
        for(uint64_t i = 0; i < pos_pred_vec.size()-1; ++i) {
            if(i+1 < pos_pred_vec.size()
               && pos_pred_vec[i].pos_e == pos_pred_vec[i+1].pos_b-1){
                std::vector<std::pair<uint64_t, uint64_t>> solution;

                high_resolution_clock::time_point start, stop;
                double total_time = 0.0;
                duration<double> time_span;
                start = high_resolution_clock::now();

                auto pred_rev = pred_reverse(pos_pred_vec[i+1].pred_b);
                auto pred = pos_pred_vec[i].pred_e;
                get_elements_intersection(pred_rev, pred, elements);
                std::tie(rpq_l, rpq_r) = rpqTree.splitRpq(pos_pred_vec[i].pos_e);
                solve_split_any(rpq, rpq_l, rpq_r, elements, predicates_map, true, solution, start);
                stop = high_resolution_clock::now();
                time_span = duration_cast<microseconds>(stop - start);
                total_time = time_span.count();
                //Format: split; n_solutions; n_elements; time (ns)
                cout << i << ";" << solution.size() << ";" << elements.size() << ";left;" << (uint64_t) (total_time * 1000000000ULL) << endl;
                if(!rpq_l.empty() && !rpq_r.empty()) {
                    solution.clear();
                    start = high_resolution_clock::now();
                    pred_rev = pred_reverse(pos_pred_vec[i+1].pred_b);
                    pred = pos_pred_vec[i].pred_e;
                    get_elements_intersection(pred_rev, pred, elements);
                    std::tie(rpq_l, rpq_r) = rpqTree.splitRpq(pos_pred_vec[i].pos_e);
                    solve_split_any(rpq, rpq_l, rpq_r, elements, predicates_map, false, solution, start);
                    stop = high_resolution_clock::now();
                    time_span = duration_cast<microseconds>(stop - start);
                    total_time = time_span.count();
                    //Format: split; n_solutions; n_elements; time (ns)
                    cout << i << ";" << solution.size() << ";" << elements.size() << ";right;" << (uint64_t) (total_time * 1000000000ULL) << endl;
                }
            }
        }
        std::cout << "---------------------" << std::endl;
    }

};

typedef ring_rpq<std::stack> ring_rpq_dfs;
typedef ring_rpq<std::queue> ring_rpq_bfs;

#endif
