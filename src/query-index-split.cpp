// Evaluates RPQs splitting the variable-to-variable ones (RingAB in the paper).

#include "query_driver.hpp"

int main(int argc, char **argv) {
    return run_query_driver(argc, argv, planner_mode::split);
}
