cmake_minimum_required(VERSION 3.20)
project(apdl-config LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

find_package(Crow REQUIRED)
find_package(hiredis REQUIRED)
find_package(RapidJSON REQUIRED)
find_package(spdlog REQUIRED)
find_package(PostgreSQL REQUIRED)
find_package(GTest REQUIRED)

# ---------- Main executable ----------
set(CONFIG_SOURCES
    src/main.cpp
    src/handlers/flags.cpp
    src/handlers/stream.cpp
    src/handlers/admin.cpp
    src/sse/broadcaster.cpp
    src/store/postgres.cpp
    src/store/redis_cache.cpp
    src/flags/evaluator.cpp
)

add_executable(config_service ${CONFIG_SOURCES})

target_include_directories(config_service PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/include
)

target_link_libraries(config_service PRIVATE
    Crow::Crow
    hiredis::hiredis
    rapidjson
    spdlog::spdlog
    PostgreSQL::PostgreSQL
    pthread
)

target_compile_definitions(config_service PRIVATE
    RAPIDJSON_HAS_STDSTRING=1
)

# ---------- Test executable ----------
set(TEST_SOURCES
    tests/test_flags.cpp
    tests/test_evaluator.cpp
    tests/test_broadcaster.cpp
    src/handlers/flags.cpp
    src/handlers/stream.cpp
    src/handlers/admin.cpp
    src/sse/broadcaster.cpp
    src/store/postgres.cpp
    src/store/redis_cache.cpp
    src/flags/evaluator.cpp
)

add_executable(config_tests ${TEST_SOURCES})

target_include_directories(config_tests PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/include
)

target_link_libraries(config_tests PRIVATE
    Crow::Crow
    hiredis::hiredis
    rapidjson
    spdlog::spdlog
    PostgreSQL::PostgreSQL
    GTest::gtest
    GTest::gtest_main
    pthread
)

target_compile_definitions(config_tests PRIVATE
    RAPIDJSON_HAS_STDSTRING=1
)

enable_testing()
add_test(NAME config_tests COMMAND config_tests)
