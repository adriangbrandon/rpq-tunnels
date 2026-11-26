add_library(nfa STATIC
        src/bitmasks.cpp
        src/options.cpp
        src/parser.cpp
        src/regular.cpp
        src/RpqAutomata.cpp
        src/RpqTree.cpp)
target_include_directories(nfa PUBLIC "${CMAKE_CURRENT_SOURCE_DIR}/include")
