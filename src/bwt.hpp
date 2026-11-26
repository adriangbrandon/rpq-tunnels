/*
 * bwt.hpp
 * Copyright (C) 2020 Author removed for double-blind evaluation
 * 
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

#ifndef BWT_T
#define BWT_T

#include "Config.hpp"
#include "rpq_wavelet.hpp"

using namespace std;

class bwt
 {
    bwt_type L;

    C_type *C_bv;
    C_rank_type C_rank;
    C_select_type C_select;
    C_select0_type C_select0;

    public:

        bwt() {;}

	bwt(int_vector<> & _L, vector<uint64_t> & _C)  
        {
            construct_im(L, _L);

            bit_vector C_aux = bit_vector(_C[_C.size()-1]+1+_C.size(), 0);

            for (uint64_t i=0; i < _C.size(); i++) {
                C_aux[_C[i]+i] = 1;
            }

            C_bv = new C_type(C_aux);
            util::init_support(C_rank,C_bv);
            util::init_support(C_select,C_bv);
            util::init_support(C_select0,C_bv);
        }

        ~bwt() {;}

        uint64_t size()
        {
            return sdsl::size_in_bytes(L) + sdsl::size_in_bytes(*C_bv) 
                 + sdsl::size_in_bytes(C_rank) + sdsl::size_in_bytes(C_select)
                 + sdsl::size_in_bytes(C_select0);
        }

	void save(string filename)
        {
            sdsl::store_to_file(L, filename+".L");
            sdsl::store_to_file(*C_bv, filename+".C");
        }

        void load(string filename)
        {
            sdsl::load_from_file(L, filename+".L");
            C_bv = new C_type; //bit_vector;	
            sdsl::load_from_file(*C_bv, filename+".C");
            util::init_support(C_rank,C_bv);
            util::init_support(C_select,C_bv);
            util::init_support(C_select0,C_bv);
        }

        pair<uint64_t, uint64_t> 
        backward_step(uint64_t left_end, uint64_t right_end, uint64_t value)
        {
            uint64_t s = L.rank(left_end, value);
            uint64_t e = L.rank(right_end+1, value)-1;
            return pair<uint64_t, uint64_t>(s, e);
        }

        pair<uint64_t, uint64_t>
        backward_step_test(uint64_t left_end, uint64_t right_end, uint64_t value, 
                           uint64_t s, uint64_t e)
        {
            return pair<uint64_t, uint64_t>(s, e-1);
        }

        inline uint64_t get_C(uint64_t v) const
        {
            return C_select(v+1)-v;
        }

        std::vector<uint64_t>
        all_values_in_range(uint64_t lb, uint64_t rb) const
        {
            return L.all_values_in_range(lb, rb);
        }

        // Section 4.1: marks / clears the states B_c of predicate c in array B.
        template<typename word_t>
        void mark(uint64_t c, std::vector<word_t>& B_wt, word_t B_c) const
        {
            rpq_wavelet::mark(L, c, B_wt, B_c);
        }

        template<typename word_t>
        void unmark(uint64_t c, std::vector<word_t>& B_wt) const
        {
            rpq_wavelet::unmark(L, c, B_wt);
        }

        // Section 4.1: distinct symbols of [lb, rb] leading to some state of D.
        template<typename word_t>
        void active_predicates(uint64_t lb, uint64_t rb, const std::vector<word_t>& B_wt, word_t D,
                               std::vector<std::pair<uint64_t, std::pair<uint64_t, uint64_t>>>& res_vec) const
        {
            rpq_wavelet::active_predicates(L, lb, rb, B_wt, D, res_vec);
        }

        // Section 4.2: distinct symbols of [lb, rb] not yet visited with all the states of D.
        template <typename word_t>
        void active_subjects(uint64_t lb, uint64_t rb, rpq_wavelet::visited_states<word_t>& D_wt, word_t D,
                             std::vector<std::tuple<uint64_t, word_t, std::pair<uint64_t,uint64_t>>>& res_vec) const
        {
            rpq_wavelet::active_subjects(L, lb, rb, D_wt, D, res_vec);
        }

	std::vector<std::pair<uint64_t,uint64_t>>
	intersect(const std::vector<std::array<uint64_t, 2ul>>& ranges)
	{
            return sdsl::intersect<bwt_type>(L, ranges);
        };

 };
#endif
