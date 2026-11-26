#ifndef QUERY_CONFIG
#define QUERY_CONFIG


// timeout for queries, in seconds (ring-tunnels sets its own, see CMakeLists.txt)
#ifndef TIME_OUT
#define TIME_OUT 600
#endif

#define VAR_TO_VAR 0
#define VAR_TO_CONST 1
#define CONST_TO_VAR 2

#endif

