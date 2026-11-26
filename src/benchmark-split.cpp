// Experiment (Fig. 13, middle): times every split of each variable-to-variable RPQ.

#include "query_driver.hpp"

int main(int argc, char **argv) {
    return run_query_driver(argc, argv, planner_mode::all_splits);
}
