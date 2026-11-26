
#include <iostream>
#include <string>
#include <vector>
#include <regex>
#include "../include/parser.hpp"
#include "../include/regular.hpp"
#include "../include/RpqAutomata.hpp"

using std::pair;
using std::string;
using std::unordered_map;

inline uint64_t fwdTrans(Mask *trans, int current, Mask predB)
{
    return *trans[current] & *predB;
}

inline uint64_t bwdTrans(Mask *trans, int current, Mask predB)
{
    return *trans[current & *predB];
}

inline uint64_t next_1SliceInT(const regularData *nfaData, uint64_t current, uint64_t predicate, int type, Mask b)
{
    if (type == FWD)
        return fwdTrans(nfaData->fwdTrans[0], current, b);
    else
        return bwdTrans(nfaData->bwdTrans[0], current, b);
}

uint64_t RpqAutomata::next(uint64_t current, uint64_t predicate, int type)
{
    const auto mapItem = _B.find(predicate);
    if (mapItem == _B.end())
        return 0;

    const auto bPos = mapItem->second;

    if (_nfaData->slices == 1)
        return next_1SliceInT(_nfaData, current, predicate, type, bPos);

    // Table T is split in slices of `width` states: slice i is indexed by
    // bits [i*width, (i+1)*width) of the set of states (Section 3.5).
    const int width = _nfaData->width;
    const uint64_t slice_mask = (1ULL << width) - 1;
    Mask *const *trans = type == FWD ? _nfaData->fwdTrans : _nfaData->bwdTrans;
    const uint64_t from = type == FWD ? current : current & *bPos;
    uint64_t next = 0;
    for (auto i = 0; i < _nfaData->slices; i++)
        next |= *trans[i][(from >> (i * width)) & slice_mask];
    return type == FWD ? next & *bPos : next;
}

regularData *buildNFA(const char *pattern, int length)
{
    Tree *tree;
    Tree **pos;
    int m;

    m = length;
    pos = new Tree *[m];
    tree = parse(pattern, m, pos);

    regularData *data = tree == NULL ? NULL : regularPreproc(pattern, tree, pos);
    if (tree != NULL)
        freeTree(tree);
    delete[] pos;
    return data;
}

// Writes the RPQ as a pattern for the NFA parser: every distinct predicate
// <...> becomes one character (1, 2, ... in order of first appearance) and
// the concatenation operator '/' is dropped. A single scan: building
// std::regex objects for this used to dominate the cost of short queries.
pair<string, unordered_map<uint64_t, char>> rpqToStringPattern(const string &rpq, const unordered_map<string, uint64_t> &predicates)
{
    unordered_map<uint64_t, char> map;
    char firstUnassigned = 1;
    string pattern;
    pattern.reserve(rpq.size());

    for (size_t i = 0; i < rpq.size(); ++i)
    {
        if (rpq[i] == '<')
        {
            const auto close = rpq.find('>', i);
            const auto predicateId = predicates.at(rpq.substr(i, close - i + 1));
            auto it = map.find(predicateId);
            if (it == map.end())
                it = map.emplace(predicateId, firstUnassigned++).first;
            pattern += it->second;
            i = close;
        }
        else if (rpq[i] != '/')
        {
            pattern += rpq[i];
        }
    }
    return {pattern, map};
}

pair<regularData *, unordered_map<uint64_t, char>> build(const string &rpq, const unordered_map<string, uint64_t> &predicates)
{

    // 1. Transform RPQ to a regular expression
    auto patternAndMap = rpqToStringPattern(rpq, predicates);
    auto pattern = patternAndMap.first;

    // 2. Use nrgrep to parse the pattern and build the NFA
    auto regularData = buildNFA(pattern.c_str(), pattern.length());

    return {regularData, patternAndMap.second};
}

pair<regularData *, unordered_map<uint64_t, char>> build(const string &pattern, const unordered_map<uint64_t , char> &map)
{


    // 2. Use nrgrep to parse the pattern and build the NFA
    auto regularData = buildNFA(pattern.c_str(), pattern.length());

    return {regularData, map};
}

uint64_t RpqAutomata::countStates(const string &rpq, const unordered_map<string, uint64_t> &predicates)
{
    // Only the syntax tree: the bit masks of automata with more than 64
    // states are not built correctly by the NFA code (heap overflow).
    const auto pattern = rpqToStringPattern(rpq, predicates).first;
    const int length = pattern.length();
    Tree **pos = new Tree *[length];
    Tree *tree = parse(pattern.c_str(), length, pos);
    int states = 0;
    if (tree != NULL)
    {
        regularLength(tree, &states);
        freeTree(tree);
    }
    delete[] pos;
    return states;
}

RpqAutomata::RpqAutomata() {
    _nfaData = NULL;
    _isValid = false;
};

RpqAutomata::RpqAutomata(const string &rpq, const unordered_map<string, uint64_t> &predicates)
{
    auto nfaAndMap = build(rpq, predicates);

    _nfaData = nfaAndMap.first;
    _isValid = _nfaData != NULL;

    for (auto pair : nfaAndMap.second)
    {
        _B[pair.first] = _nfaData->B[pair.second];
    }
}

RpqAutomata::RpqAutomata(const string &pattern, const unordered_map<uint64_t , char> &map)
{
    auto nfaAndMap = build(pattern, map);

    _nfaData = nfaAndMap.first;
    _isValid = _nfaData != NULL;

    for (auto pair : nfaAndMap.second)
    {
        _B[pair.first] = _nfaData->B[pair.second];
    }
}

unordered_map<uint64_t, uint64_t> RpqAutomata::getB()
{
    unordered_map<uint64_t, uint64_t> result;

    for (auto pair : _B)
    {
        result[pair.first] = *pair.second;
    }

    return result;
}

RpqAutomata::~RpqAutomata()
{
    if (_nfaData != NULL)
    {
        regularFree(_nfaData);
        _nfaData = NULL;
    }
}