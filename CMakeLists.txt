cmake_minimum_required(VERSION 3.12)
project(Ring-RPQ LANGUAGES C CXX)

# Optimised build unless the user asks for something else
# (e.g. cmake -DCMAKE_BUILD_TYPE=Debug ..).
if(NOT CMAKE_BUILD_TYPE AND NOT CMAKE_CONFIGURATION_TYPES)
    set(CMAKE_BUILD_TYPE Release CACHE STRING "Build type" FORCE)
    set_property(CACHE CMAKE_BUILD_TYPE PROPERTY STRINGS Debug Release RelWithDebInfo)
endif()

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

# Extended SDSL (https://github.com/adriangbrandon/sdsl-lite).
# Its install prefix must contain include/sdsl and lib/libsdsl.a.
set(SDSL_ROOT "$ENV{HOME}" CACHE PATH "Install prefix of the extended SDSL library")
find_path(SDSL_INCLUDE_DIR sdsl/wm_int.hpp HINTS "${SDSL_ROOT}/include" REQUIRED)
find_library(SDSL_LIBRARY sdsl HINTS "${SDSL_ROOT}/lib" REQUIRED)
find_library(DIVSUFSORT_LIBRARY divsufsort HINTS "${SDSL_ROOT}/lib" REQUIRED)
find_library(DIVSUFSORT64_LIBRARY divsufsort64 HINTS "${SDSL_ROOT}/lib" REQUIRED)

add_library(sdsl_ext INTERFACE)
target_include_directories(sdsl_ext SYSTEM INTERFACE "${SDSL_INCLUDE_DIR}")
target_link_libraries(sdsl_ext INTERFACE
        "${SDSL_LIBRARY}" "${DIVSUFSORT_LIBRARY}" "${DIVSUFSORT64_LIBRARY}")

# Flags used in the paper (-O3 -msse4.2); SSE4.2 only exists on x86.
# Release already gets -O3 -DNDEBUG from CMake.
set(CMAKE_CXX_FLAGS_RELWITHDEBINFO "-O3 -g -DNDEBUG")
add_compile_options("$<$<CONFIG:Release,RelWithDebInfo>:-funroll-loops>")
if(CMAKE_SYSTEM_PROCESSOR MATCHES "x86_64|AMD64")
    add_compile_options("$<$<CONFIG:Release,RelWithDebInfo>:-msse4.2>")
endif()

add_subdirectory(nfa)

# Legacy code still relies on -fpermissive; to be removed during the refactor.
add_library(ring_rpq_common INTERFACE)
target_include_directories(ring_rpq_common INTERFACE "${CMAKE_CURRENT_SOURCE_DIR}/src")
target_compile_options(ring_rpq_common INTERFACE -fpermissive)
target_link_libraries(ring_rpq_common INTERFACE nfa sdsl_ext)

function(ring_rpq_executable name source)
    add_executable(${name} ${source})
    target_link_libraries(${name} PRIVATE ring_rpq_common)
endfunction()

ring_rpq_executable(build-index-basic src/build-index-basic.cpp)
ring_rpq_executable(build-index-split src/build-index-split.cpp)
ring_rpq_executable(query-index-basic src/query-index-basic.cpp)
ring_rpq_executable(query-index-split src/query-index-split.cpp)
ring_rpq_executable(benchmark-split src/benchmark-split.cpp)
ring_rpq_executable(benchmark-paths src/benchmark-paths.cpp)
ring_rpq_executable(sort-dict src/utilities/sort-pos.cpp)
ring_rpq_executable(sort-dict-bfs src/utilities/sort-pos-bfs.cpp)
ring_rpq_executable(yago2s-prepare src/utilities/yago2s-prepare.cpp)

# Tunnels for the RPQ solution subgraph (src/ring-tunnels.cpp, tunnels/README.md).
# Timeout (s) of Ring-RPQ's own evaluations, the baselines inside ring-tunnels:
# cmake -DRING_TIMEOUT=...
set(RING_TIMEOUT 60 CACHE STRING "Timeout (s) of the Ring-RPQ baselines of ring-tunnels")
ring_rpq_executable(ring-tunnels src/ring-tunnels.cpp)
target_compile_definitions(ring-tunnels PRIVATE TIME_OUT=${RING_TIMEOUT})
ring_rpq_executable(ring-tunnels-split src/ring-tunnels.cpp)
target_compile_definitions(ring-tunnels-split PRIVATE RING_SPLIT TIME_OUT=${RING_TIMEOUT})
ring_rpq_executable(gen-queries src/gen-queries.cpp)
# the same tunnels on plain adjacency lists (built from the .dat, cached in <dat>.adj)
ring_rpq_executable(adj-tunnels src/ring-tunnels.cpp)
target_compile_definitions(adj-tunnels PRIVATE ADJ_GRAPH)
# ... and on navGraph (github.com/j-rparra/navGraph): cmake -DNAVGRAPH_DIR=/path/to/navGraph
set(NAVGRAPH_DIR "" CACHE PATH "Source tree of navGraph (optional, for nav-tunnels)")
if(NAVGRAPH_DIR)
    ring_rpq_executable(nav-tunnels src/ring-tunnels.cpp)
    target_compile_definitions(nav-tunnels PRIVATE NAV_GRAPH)
    target_include_directories(nav-tunnels PRIVATE ${NAVGRAPH_DIR}/src)
    add_executable(nav-build-index ${NAVGRAPH_DIR}/src/build-index.cpp)
    target_include_directories(nav-build-index PRIVATE ${NAVGRAPH_DIR}/src)
    target_compile_options(nav-build-index PRIVATE -w)
    target_link_libraries(nav-build-index PRIVATE nfa sdsl_ext)
endif()

# Tests: `ctest --test-dir build` (the regression suite needs Python 3).
enable_testing()
add_executable(automaton_test tests/unit/automaton_test.cpp)
target_link_libraries(automaton_test PRIVATE nfa)
add_test(NAME automaton COMMAND automaton_test)
find_package(Python3 COMPONENTS Interpreter)
if(Python3_FOUND)
    add_test(NAME regression
             COMMAND ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/regression.py
                     --build-dir ${CMAKE_BINARY_DIR})
    add_test(NAME tunnels_paper
             COMMAND ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/tunnels_paper_test.py
                     --build-dir ${CMAKE_BINARY_DIR})
    add_test(NAME tunnels
             COMMAND ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/tunnels_regression.py
                     --build-dir ${CMAKE_BINARY_DIR})
    add_test(NAME tunnels_automata
             COMMAND ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/tunnels_automata_test.py
                     --build-dir ${CMAKE_BINARY_DIR})
    add_test(NAME tunnels_scripts
             COMMAND ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/tunnels_scripts_test.py
                     --build-dir ${CMAKE_BINARY_DIR})
    add_test(NAME tunnels_repro
             COMMAND ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/tunnels_repro_test.py
                     --build-dir ${CMAKE_BINARY_DIR})
    add_test(NAME tunnels_pathfinder
             COMMAND ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/tunnels_pathfinder_test.py
                     --build-dir ${CMAKE_BINARY_DIR})
    add_test(NAME tunnels_early
             COMMAND ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/tunnels_early_test.py
                     --build-dir ${CMAKE_BINARY_DIR})
    add_test(NAME tunnels_small
             COMMAND ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/tunnels_small_test.py
                     --build-dir ${CMAKE_BINARY_DIR})
endif()
