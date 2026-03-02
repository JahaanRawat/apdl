cmake_minimum_required(VERSION 3.20)
project(apdl-ingestion LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

find_package(Crow REQUIRED)
find_package(hiredis REQUIRED)
find_package(RapidJSON REQUIRED)
find_package(spdlog REQUIRED)
find_package(GTest REQUIRED)

# ---------- Main executable ----------
set(INGESTION_SOURCES
    src/main.cpp
    src/handlers/events.cpp
    src/validation/schema.cpp
    src/streaming/redis_producer.cpp
    src/middleware/auth.cpp
    src/middleware/rate_limit.cpp
    src/util/json.cpp
)

add_executable(ingestion ${INGESTION_SOURCES})

target_include_directories(ingestion PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/include
)

target_link_libraries(ingestion PRIVATE
    Crow::Crow
    hiredis::hiredis
    rapidjson
    spdlog::spdlog
    pthread
)

target_compile_definitions(ingestion PRIVATE
    RAPIDJSON_HAS_STDSTRING=1
)

# ---------- Test executable ----------
set(TEST_SOURCES
    tests/test_events_handler.cpp
    tests/test_schema_validation.cpp
    src/handlers/events.cpp
    src/validation/schema.cpp
    src/streaming/redis_producer.cpp
    src/middleware/auth.cpp
    src/middleware/rate_limit.cpp
    src/util/json.cpp
)

add_executable(ingestion_tests ${TEST_SOURCES})

target_include_directories(ingestion_tests PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/include
)

target_link_libraries(ingestion_tests PRIVATE
    Crow::Crow
    hiredis::hiredis
    rapidjson
    spdlog::spdlog
    GTest::gtest
    GTest::gtest_main
    pthread
)

target_compile_definitions(ingestion_tests PRIVATE
    RAPIDJSON_HAS_STDSTRING=1
)

enable_testing()
add_test(NAME ingestion_tests COMMAND ingestion_tests)
