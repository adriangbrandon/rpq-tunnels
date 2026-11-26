// Experiment: splits every variable-to-variable RPQ and skips the rest.

#include "query_driver.hpp"

int main(int argc, char **argv) {
    return run_query_driver(argc, argv, planner_mode::split_always);
}
