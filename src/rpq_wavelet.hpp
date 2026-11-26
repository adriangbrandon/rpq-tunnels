/***
BSD 2-Clause License

Copyright (c) 2018, Adrián
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

* Redistributions of source code must retain the above copyright notice, this
  list of conditions and the following disclaimer.

* Redistributions in binary form must reproduce the above copyright notice,
  this list of conditions and the following disclaimer in the documentation
  and/or other materials provided with the distribution.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
**/

//
// Created by Adrián on 17/5/22.
//

#ifndef RING_RPQ_RPQ_WAVELET_HPP
#define RING_RPQ_RPQ_WAVELET_HPP

// Wavelet-tree algorithms of the RPQ evaluation (Sections 4.1 and 4.2 of the
// paper), written over the public interface of SDSL's wavelet matrix
// (root, expand, max_level). They were part of the extended SDSL library
// (wm_int::mark, unmark, all_active_p_values_in_range_test and
// all_active_s_values_in_range_test), which keeps its own copies.
//
// Arrays B (states reached through the predicates below each node of the
// wavelet tree of L_P) and S (states each node of the wavelet tree of L_S
// was visited with) are stored in heap order: the root is 1 and the
// children of node p are 2p and 2p+1.

#include <algorithm>
#include <cstdint>
#include <cstdlib>
#include <memory>
#include <new>
#include <stack>
#include <tuple>
#include <utility>
#include <vector>
#include <sdsl/wt_helper.hpp>

namespace rpq_wavelet {

    using sdsl::range_type;
    using sdsl::range_vec_type;

    // Array of n words that starts with all entries equal to 0 without
    // initializing its memory, and can be reset to all-zero in O(1). It is
    // array S of Section 4.2: one entry per node of the wavelet tree of L_S.
    // As sdsl::initializable_array, it keeps a bit per entry telling whether
    // it was written, in an array of words that is itself lazily initialized
    // (a block is valid if it is in the stack of written blocks). Unlike it,
    // it has no bounds checks and can be reused.
    template<class word_t>
    class visited_states {
        struct free_deleter { void operator()(void *p) const { std::free(p); } };
        template<class T>
        static std::unique_ptr<T[], free_deleter> zeroed(uint64_t n) {
            void *p = std::calloc(n, sizeof(T));  // pages are zeroed lazily by the OS
            if (!p) throw std::bad_alloc();
            return std::unique_ptr<T[], free_deleter>(static_cast<T *>(p));
        }

        std::unique_ptr<word_t[], free_deleter> values;  // values[i] is meaningful if bit i is set
        std::unique_ptr<uint64_t[], free_deleter> bits;  // written entries of each block of 64
        std::unique_ptr<uint64_t[], free_deleter> where; // position of each block in `blocks`
        std::unique_ptr<uint64_t[], free_deleter> blocks;// blocks written since the last reset
        uint64_t top = 0;

        bool valid(uint64_t b) const { return where[b] < top && blocks[where[b]] == b; }

    public:
        explicit visited_states(uint64_t n)
                : values(zeroed<word_t>(n)), bits(zeroed<uint64_t>(n / 64 + 1)),
                  where(zeroed<uint64_t>(n / 64 + 1)), blocks(zeroed<uint64_t>(n / 64 + 1)) {}

        word_t atPos(uint64_t i) const {
            const uint64_t b = i >> 6;
            return (valid(b) && ((bits[b] >> (i & 63)) & 1)) ? values[i] : 0;
        }

        word_t &operator[](uint64_t i) {
            const uint64_t b = i >> 6, mask = 1ULL << (i & 63);
            if (!valid(b)) {
                where[b] = top;
                blocks[top++] = b;
                bits[b] = 0;
            }
            if (!(bits[b] & mask)) {
                bits[b] |= mask;
                values[i] = 0;
            }
            return values[i];
        }

        void reset() { top = 0; }
    };

    // Adds B_c to B at leaf c and at all its ancestors (Section 4.1). Each
    // entry is the union of the states of the predicates below the node.
    template<class t_wt, class word_t>
    void mark(const t_wt &wt, uint64_t c, std::vector<word_t> &B, word_t B_c) {
        uint64_t mask = 1ULL << (wt.max_level - 1);
        uint64_t pos = 1;
        for (uint32_t k = 0; k < wt.max_level; ++k) {
            B[pos] |= B_c;
            pos = (c & mask) ? 2 * pos + 1 : 2 * pos;
            mask >>= 1;
        }
        B[pos] |= B_c;
    }

    // Clears leaf c and its ancestors in B.
    template<class t_wt, class word_t>
    void unmark(const t_wt &wt, uint64_t c, std::vector<word_t> &B) {
        uint64_t mask = 1ULL << (wt.max_level - 1);
        uint64_t pos = 1;
        for (uint32_t k = 0; k < wt.max_level; ++k) {
            B[pos] = 0;
            pos = (c & mask) ? 2 * pos + 1 : 2 * pos;
            mask >>= 1;
        }
        B[pos] = 0;  // the leaf too (B10): stale bits made later queries explore useless predicates
    }

    // Part 1 (Section 4.1): the distinct predicates p in wt[lb..rb] that lead
    // to some state of D (B[p] & D != 0), each with its range [b, e) in the
    // subsequence of p. Subtrees whose B entry has no state of D are pruned.
    template<class t_wt, class word_t>
    void active_predicates_rec(const t_wt &wt, const typename t_wt::node_type &v, range_type r,
                               uint64_t ilb, const std::vector<word_t> &B, word_t D, uint64_t pos,
                               std::vector<std::pair<uint64_t, std::pair<uint64_t, uint64_t>>> &res) {
        if (!(B[pos] & D)) return;
        if (r[0] > r[1]) return;
        if (v.level == wt.max_level) {
            res.emplace_back(v.sym, std::pair<uint64_t, uint64_t>(r[0], r[0] + sdsl::size(r)));
            return;
        }
        const uint64_t irb = ilb + (1ULL << (wt.max_level - v.level));
        const uint64_t mid = ilb + ((irb - ilb) >> 1);
        auto c_v = wt.expand(v);
        auto c_r = wt.expand(v, r);
        if (!sdsl::empty(c_r[0]) && mid)
            active_predicates_rec(wt, c_v[0], c_r[0], ilb, B, D, 2 * pos, res);
        if (!sdsl::empty(c_r[1]))
            active_predicates_rec(wt, c_v[1], c_r[1], mid, B, D, 2 * pos + 1, res);
    }

    template<class t_wt, class word_t>
    void active_predicates(const t_wt &wt, uint64_t lb, uint64_t rb, const std::vector<word_t> &B, word_t D,
                           std::vector<std::pair<uint64_t, std::pair<uint64_t, uint64_t>>> &res) {
        if (lb <= rb) active_predicates_rec(wt, wt.root(), {lb, rb}, 0, B, D, 1, res);
    }

    // Part 2 (Section 4.2): the distinct subjects s in wt[lb..rb] not yet
    // visited with all the states of D. Each one is reported with the states
    // that are new to it, and S is updated (leaves) and kept as the
    // intersection of the children at the internal nodes, so that subtrees
    // already visited with all the states of D are pruned. Returns S[v].
    template<class t_wt, class word_t>
    word_t active_subjects_rec(const t_wt &wt, const typename t_wt::node_type &v, range_type r,
                               uint64_t ilb, visited_states<word_t> &S, word_t D, uint64_t pos,
                               std::vector<std::tuple<uint64_t, word_t, std::pair<uint64_t, uint64_t>>> &res) {
        word_t Dtemp = S.atPos(pos);
        if ((Dtemp | D) == Dtemp) return Dtemp;
        if (r[0] > r[1]) return Dtemp;
        if (v.level == wt.max_level) {
            D = D & ~Dtemp;
            word_t answ = S[pos] = Dtemp | D;
            res.emplace_back(v.sym, D, std::pair<uint64_t, uint64_t>(r[0], r[0] + sdsl::size(r)));
            return answ;
        }
        const uint64_t irb = ilb + (1ULL << (wt.max_level - v.level));
        const uint64_t mid = ilb + ((irb - ilb) >> 1);
        auto c_v = wt.expand(v);
        auto c_r = wt.expand(v, r);
        word_t answ1, answ2;
        if (!sdsl::empty(c_r[0]) && mid)
            answ1 = active_subjects_rec(wt, c_v[0], c_r[0], ilb, S, D, 2 * pos, res);
        else
            answ1 = Dtemp;
        if (!sdsl::empty(c_r[1]))
            answ2 = active_subjects_rec(wt, c_v[1], c_r[1], mid, S, D, 2 * pos + 1, res);
        else
            answ2 = Dtemp;
        S[pos] = answ1 & answ2;
        return answ1 & answ2;
    }

    template<class t_wt, class word_t>
    void active_subjects(const t_wt &wt, uint64_t lb, uint64_t rb, visited_states<word_t> &S, word_t D,
                         std::vector<std::tuple<uint64_t, word_t, std::pair<uint64_t, uint64_t>>> &res) {
        if (lb <= rb) active_subjects_rec(wt, wt.root(), {lb, rb}, 0, S, D, 1, res);
    }

    // Marks node c of the wavelet tree of L_S as visited with the states D
    // (array S of Section 4.2): adds to S[c] the states of D it did not have
    // and updates the intersections stored at its ancestors.
    template<class word_t>
    void markD(uint64_t c, uint64_t max_level, visited_states<word_t>& D_wt, word_t D){

        uint64_t pos = 1, mid;
        range_type sigma = range_type{0, (1ULL << max_level) -1};
        while (sigma[0] != sigma[1]) { //Find the position to update
            mid = (sigma[0] + sigma[1])>>1;
            if(c > mid){//Right child
                pos = 2*pos+1;
                sigma = range_type{mid+1, sigma[1]};
            }else{      //Left child
                pos = 2*pos;
                sigma = range_type{sigma[0], mid};
            }
        }
        word_t tmp = D_wt.atPos(pos);
        D = D & ~tmp;
        D_wt[pos] = tmp | D;
        while (pos != 1){
            tmp = (pos & 0x1) ? (D_wt.atPos(pos) & D_wt.atPos(pos-1)) : D_wt.atPos(pos) & D_wt.atPos(pos+1);
            if(!tmp) return; //Empty -> nothing to do
            D_wt[pos >> 1] = tmp; //set parent
            pos = pos >> 1; //move to parent
        }
    }


    // The distinct values present in all the ranges (each counted once).
    template<class t_wt>
    std::vector<typename t_wt::value_type>
    intersect_nofreq(const t_wt& wt, const std::vector<range_type>& ranges, typename t_wt::size_type t=0)
    {
        using std::get;
        using size_type      = typename t_wt::size_type;
        using value_type     = typename t_wt::value_type;
        using node_type      = typename t_wt::node_type;
        using pnvr_type      = std::pair<node_type, range_vec_type>;
        typedef std::stack<pnvr_type> stack_type;

        static_assert(has_expand<t_wt, std::array<node_type,2>(const node_type&)>::value,
                "intersect requires t_wt to have expand(const node_type&)");

        std::vector<size_type> res;

        auto push_node = [&wt,&t](stack_type& s, node_type& child,
                                  range_vec_type& child_range) {
            auto end = std::remove_if(child_range.begin(), child_range.end(),
                                      [&](const range_type& x) { return empty(x);});
            if (end > child_range.begin() + t - 1) {
                s.emplace(pnvr_type(child, range_vec_type(child_range.begin(),
                                                          end)));
            }
        };

        if (ranges.empty())
            return res;

        t = (t==0) ? ranges.size() : t;

        std::stack<pnvr_type> stack;
        stack.emplace(pnvr_type(wt.root(), ranges));

        while (!stack.empty()) {
            pnvr_type x = stack.top(); stack.pop();

            if (wt.is_leaf(x.first)) {
                const auto& iv = x.second;
                if (t <= iv.size()) {
                    res.emplace_back(wt.sym(x.first));
                }
            } else {
                auto child        = wt.expand(x.first);
                auto child_ranges = wt.expand(x.first, x.second);

                push_node(stack, get<1>(child), get<1>(child_ranges));
                push_node(stack, get<0>(child), get<0>(child_ranges));
            }
        }
        return res;
    }

}

#endif
