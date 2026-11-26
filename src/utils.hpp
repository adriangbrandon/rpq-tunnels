#ifndef UTILS_RING
#define UTILS_RING

#include <cstddef>
#include <cstdint>
#include <unordered_set>
#include <utility>

// Hash of a pair of node identifiers, for the sets that remove duplicate
// answers. Answers come with clustered identifiers, so nearby pairs must stay
// in nearby buckets: a hash that scatters them is much slower on sets of
// hundreds of millions of pairs (cache misses). A plain XOR keeps them close
// but collapses (a, a), nearby identifiers and square groups of answers;
// a smaller multiplier than 31 also collapses large square groups.
// It is noexcept so that libstdc++ does not store the hash in every node.
struct pair_hash {
    std::size_t operator()(const std::pair<uint64_t, uint64_t> &v) const noexcept {
        return 31 * v.first + v.second;
    }
};

typedef std::unordered_set<std::pair<uint64_t, uint64_t>, pair_hash> pair_set;

#endif
