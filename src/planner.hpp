#ifndef RING_RPQ_PLANNER_HPP
#define RING_RPQ_PLANNER_HPP

// Chooses how to evaluate each RPQ on the index (Sections 4.5, 5.2 and 6.1
// of the paper) and runs it.

#include <cstdint>
#include <utility>
#include <vector>
#include "query_config.hpp"
#include "rpq_query.hpp"

enum class planner_mode {
    basic,         // Ring / RingA: no splitting
    split,         // RingAB: splits variable-to-variable RPQs (Section 5.2)
    split_always,  // experiments: split every variable-to-variable RPQ, skip the rest
    all_splits     // experiments (Fig. 13, middle): time every split of variable-to-variable RPQs
};

namespace planner {

typedef std::vector<std::pair<uint64_t, uint64_t>> solutions_type;

// A single predicate, or a concatenation of two, is solved without automaton.
inline bool is_single_predicate(const rpq_query &q) {
    return q.n_predicates == 1 && q.n_operators == 0;
}

inline bool is_two_predicate_path(const rpq_query &q) {
    return q.is_a_path && q.n_operators == 1;
}

template<class Index>
void const_to_var(Index &index, const rpq_query &q, solutions_type &output) {
    if (is_single_predicate(q)) index.single_predicate(q, q.subject_id, CONST_TO_VAR, output);
    else if (is_two_predicate_path(q)) index.two_predicate_path(q, q.subject_id, CONST_TO_VAR, output);
    else index.rpq_const_s_to_var_o(q, output);
}

template<class Index>
void var_to_const(Index &index, const rpq_query &q, solutions_type &output) {
    if (is_single_predicate(q)) index.single_predicate(q, q.object_id, VAR_TO_CONST, output);
    else if (is_two_predicate_path(q)) index.two_predicate_path(q, q.object_id, VAR_TO_CONST, output);
    else index.rpq_var_s_to_const_o(q, output);
}

// Variable-to-variable traversal starting with the objects ("os") or with
// the subjects ("so") that have some answer (Section 4.5). Some strategies
// of the paper do not use the two-predicate shortcut (allow_path).
template<class Index>
void var_to_var(Index &index, const rpq_query &q, bool objects_first, solutions_type &output,
                bool allow_path = true) {
    if (is_single_predicate(q)) index.single_predicate(q, 0, VAR_TO_VAR, output);
    else if (allow_path && is_two_predicate_path(q)) index.two_predicate_path(q, 0, VAR_TO_VAR, output);
    else if (objects_first) index.rpq_var_to_var_os(q, output);
    else index.rpq_var_to_var_so(q, output);
}

// Chooses the end to start a variable-to-variable RPQ from by its operators
// or, failing that, by the selectivity of its first and last predicates.
template<class Index>
void var_to_var_by_shape(Index &index, const rpq_query &q, solutions_type &output) {
    const std::string &type = q.query_type;
    if (type[type.size() - 1] == '*' && type[0] != '*')
        var_to_var(index, q, false, output, false);
    else if (type[0] == '*' || type[0] == '+')
        var_to_var(index, q, true, output, false);
    else
        var_to_var(index, q, index.pred_selectivity(q.first_pred_id) > index.pred_selectivity(q.last_pred_id),
                   output);
}

// Disjunctions of predicates are solved without automaton (Section 6.1).
// Returns the number of predicates of the disjunction, or 0 if q is not one.
inline uint64_t disjunction_size(const rpq_query &q) {
    if (q.query_type == "|") return 2;
    if (q.query_type == "||") return 3;
    if (q.is_or && q.n_predicates > 3) return q.n_predicates;
    return 0;
}

template<class Index>
void split_var_to_var(Index &index, const rpq_query &q, solutions_type &output) {
    typename Index::split_plan plan;
    if (index.plan_split(q, plan)) index.rpq_var_to_var_split(q, plan, output);
    else var_to_var_by_shape(index, q, output);  // nothing to split at
}

template<class Index>
void var_to_var_basic(Index &index, const rpq_query &q, solutions_type &output) {
    const std::string &type = q.query_type;
    if (type.empty() || type == "/*" || type == "+" || type == "*")
        var_to_var(index, q, false, output, false);
    else if (uint64_t n = disjunction_size(q))
        index.or_query_var_to_var(q, n, output);
    else
        var_to_var_by_shape(index, q, output);
}

template<class Index>
void var_to_var_split(Index &index, const rpq_query &q, solutions_type &output) {
    const std::string &type = q.query_type;
    if (type.empty() || type == "+" || type == "*")
        var_to_var(index, q, false, output, false);
    else if (uint64_t n = disjunction_size(q))
        index.or_query_var_to_var(q, n, output);
    else if (is_single_predicate(q) || is_two_predicate_path(q))
        var_to_var(index, q, false, output);
    else
        split_var_to_var(index, q, output);
}

}  // namespace planner

// Evaluates q on the index with the strategy of the given mode.
template<class Index>
void solve(Index &index, const rpq_query &q, planner_mode mode, planner::solutions_type &output) {
    using namespace planner;
    const bool var_to_var_query = !q.subject_is_const && !q.object_is_const;
    switch (mode) {
        case planner_mode::split_always:
            if (var_to_var_query) split_var_to_var(index, q, output);
            return;
        case planner_mode::all_splits:
            if (var_to_var_query) index.rpq_var_to_var_split_all(q);
            return;
        case planner_mode::basic:
        case planner_mode::split:
            break;
    }
    if (var_to_var_query) {
        if (mode == planner_mode::split) var_to_var_split(index, q, output);
        else var_to_var_basic(index, q, output);
    } else if (q.subject_is_const && q.object_is_const) {
        index.rpq_const_s_to_const_o(q, output);
    } else if (q.subject_is_const) {
        const_to_var(index, q, output);
    } else {
        var_to_const(index, q, output);
    }
}

#endif
