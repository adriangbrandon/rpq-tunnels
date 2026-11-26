// Builds the index with support for splitting RPQs (RingAB in the paper).

#include "build_driver.hpp"

int main(int argc, char **argv) {
    return run_build_driver(argc, argv, /*with_split_support=*/true);
}
