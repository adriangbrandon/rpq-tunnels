// Evaluates RPQs on an index without splitting (Ring / RingA in the paper).

#include "query_driver.hpp"

int main(int argc, char **argv) {
    return run_query_driver(argc, argv, planner_mode::basic);
}
