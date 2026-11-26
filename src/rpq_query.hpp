#ifndef RING_RPQ_RPQ_QUERY_HPP
#define RING_RPQ_RPQ_QUERY_HPP

// Parsed RPQs and the expressions their automata are built from.

#include <algorithm>
#include <cstdint>
#include <string>
#include <unordered_map>
#include <vector>
#include "RpqAutomata.hpp"

typedef std::unordered_map<std::string, uint64_t> dictionary_type;

// A predicate of an RPQ: its identifier and whether it is traversed inverted.
struct predicate_ref {
    uint64_t id;
    bool inverse;
};

// An RPQ "<subject> <rpq> <object>" with its constants and predicates mapped
// to identifiers, plus the features used to plan it (src/planner.hpp).
struct rpq_query {
    bool valid = false;           // false if a constant or predicate is unknown
    bool subject_is_const = false;
    bool object_is_const = false;
    uint64_t subject_id = 0;
    uint64_t object_id = 0;
    std::string rpq;              // the RPQ, with inverse predicates written <%...> and no '/'
    dictionary_type pred_map;     // <iri> -> predicate id and <%iri> -> id of its inverse
    std::vector<predicate_ref> predicates;  // in order of appearance
    std::string query_type;       // the operators of the RPQ, without parentheses
    uint64_t n_predicates = 0;
    uint64_t n_operators = 0;
    bool is_negated_pred = false; // some predicate is inverse
    bool is_a_path = true;        // only concatenations
    bool is_or = true;            // only disjunctions
    uint64_t first_pred_id = 0;
    uint64_t last_pred_id = 0;
};

// n_predicates_graph is the number of predicates of the graph: the inverse
// of predicate p is p + n_predicates_graph in the index.
inline rpq_query parse_query_line(const std::string &raw, const dictionary_type &map_SO,
                                  const dictionary_type &map_P, uint64_t n_predicates_graph) {
    rpq_query ql;
    // Accept both LF and CRLF line endings.
    const auto end = raw.find_last_not_of(" \t\r\n");
    if (end == std::string::npos) return ql;
    const std::string line = raw.substr(0, end + 1);
    const auto first_space = line.find(' ');
    const auto last_space = line.rfind(' ');
    if (first_space == std::string::npos || first_space == last_space) return ql;
    const std::string subject = line.substr(0, first_space);
    const std::string object = line.substr(last_space + 1);
    const std::string rpq = line.substr(first_space + 1, last_space - first_space - 1);

    if (subject[0] != '?') {
        ql.subject_is_const = true;
        auto it = map_SO.find(subject);
        if (it == map_SO.end()) return ql;
        ql.subject_id = it->second;
    }
    if (object[0] != '?') {
        ql.object_is_const = true;
        auto it = map_SO.find(object);
        if (it == map_SO.end()) return ql;
        ql.object_id = it->second;
    }

    for (uint64_t i = 0; i < rpq.size(); i++) {
        const char c = rpq[i];
        if (c == '<') {
            const auto close = rpq.find('>', i);
            if (close == std::string::npos) return ql;
            const std::string pred = rpq.substr(i, close - i + 1);  // <iri> or <%iri>
            i = close;
            std::string name = pred;
            if (pred[1] == '%') {
                name = "<" + pred.substr(2);
                ql.is_negated_pred = true;
            }
            auto it = map_P.find(name);
            if (it == map_P.end()) return ql;
            ql.rpq += pred;
            ql.last_pred_id = ql.pred_map[name] = it->second;
            ql.pred_map["<%" + name.substr(1)] = n_predicates_graph + it->second;
            ql.predicates.push_back({it->second, pred[1] == '%'});
            ql.n_predicates++;
            if (ql.n_predicates == 1) ql.first_pred_id = it->second;
        } else if (c == '/') {
            ql.n_operators++;
            ql.query_type += c;
            ql.is_or = false;
        } else if (c != ' ') {
            ql.n_operators++;
            if (c == '^') {
                ql.rpq += '%';
            } else {
                ql.rpq += c;
                if (c != '(' && c != ')') ql.query_type += c;
                ql.is_a_path = false;
                if (c != '|') ql.is_or = false;
            }
        }
    }

    ql.valid = true;
    return ql;
}

// Number of states of the Glushkov automaton of the query. Every automaton
// the index builds for the query (reversed, or for a part of it when it is
// split) has at most this many states.
inline uint64_t automaton_states(const rpq_query &ql) {
    std::unordered_map<std::string, uint64_t> ids;
    for (auto at = ql.rpq.find('<'); at != std::string::npos; at = ql.rpq.find('<', at + 1)) {
        const std::string pred = ql.rpq.substr(at, ql.rpq.find('>', at) - at + 1);
        ids.emplace(pred, ids.size() + 1);
    }
    return RpqAutomata::countStates(ql.rpq, ids);
}

// Expressions for the automata of an RPQ (internal form, see rpq_query::rpq).
// The automaton of the object side drives traversals that start at the
// object end of the RPQ; that of the subject side, at the subject end.
// Every predicate of the result has an entry in rpq_query::pred_map.

// Copies the expression from position i to the matching ')' (or the end),
// inverting every predicate: <iri> becomes <%iri> and <%iri> becomes <iri>.
inline std::string parse_object_side(const std::string &rpq, int64_t &i) {
    std::string out;
    while (i < (int64_t) rpq.size()) {
        if (rpq[i] == ')') {
            return out;
        } else if (rpq[i] == '<') {
            const bool inverse = rpq[i + 1] == '%';
            const auto close = rpq.find('>', i);
            const std::string name = rpq.substr(i + 1 + inverse, close - i - 1 - inverse);
            out += (inverse ? "<" : "<%") + name + ">";
            i = close + 1;
        } else if (rpq[i] == '(') {
            i++;
            out += '(' + parse_object_side(rpq, i) + ')';
            i++;
        } else {
            out += rpq[i++];
        }
    }
    return out;
}

// Copies the expression backwards from position i to the matching '(' (or
// the beginning), reversing the order of concatenations; unary operators
// stay after their operand and predicates are kept as they are.
inline std::string parse_subject_side(const std::string &rpq, int64_t &i) {
    std::string out;
    // The predicate that ends at position i (its '>').
    auto predicate = [&]() {
        const auto open = rpq.rfind('<', i);
        std::string pred = rpq.substr(open, i - open + 1);
        i = (int64_t) open - 1;
        return pred;
    };
    while (i >= 0) {
        const char c = rpq[i];
        if (c == '(') {
            return out;
        } else if (c == '*' || c == '+' || c == '?') {
            if (rpq[i - 1] == ')') {
                i -= 2;
                out += '(' + parse_subject_side(rpq, i) + ')' + c;
                i--;
            } else {
                i--;
                out += predicate() + c;
            }
        } else if (c == '>') {
            out += predicate();
        } else if (c == ')') {
            --i;
            out += '(' + parse_subject_side(rpq, i) + ')';
            i--;
        } else {
            out += c;
            i--;
        }
    }
    return out;
}

inline std::string object_side_expression(const std::string &rpq) {
    int64_t i = 0;
    return parse_object_side(rpq, i);
}

inline std::string subject_side_expression(const std::string &rpq) {
    int64_t i = rpq.size() - 1;
    return parse_subject_side(rpq, i);
}

#endif
