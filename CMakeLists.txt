cmake_minimum_required(VERSION 3.16)
project(satellite VERSION 1.0.0 LANGUAGES CXX)

get_filename_component(SATELLITE_COMPILER_BIN "${CMAKE_CXX_COMPILER}" DIRECTORY)
get_filename_component(SATELLITE_TOOLCHAIN_ROOT "${SATELLITE_COMPILER_BIN}/.." ABSOLUTE)
find_path(CURL_INCLUDE_DIR NAMES curl/curl.h PATHS "${SATELLITE_TOOLCHAIN_ROOT}/include")
find_library(CURL_LIBRARY NAMES curl libcurl PATHS "${SATELLITE_TOOLCHAIN_ROOT}/lib")
find_package(CURL REQUIRED)
set(SATELLITE_CURL_INCLUDE_DIR "${CURL_INCLUDE_DIR}" CACHE PATH "libcurl include directory")

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release)
endif()

if(MSVC)
  add_compile_options(/W4)
else()
  add_compile_options(-Wall -Wextra)
endif()

add_subdirectory(core)

enable_testing()
add_subdirectory(tests)

# CLI executable (FASE 20 parte A)
add_executable(satellite cli/main.cpp cli/SatelliteCLI.cpp cli/SatelliteCLI.h)
target_link_libraries(satellite PRIVATE satellite_core)
target_compile_definitions(satellite PRIVATE SATELLITE_ROOT="${PROJECT_SOURCE_DIR}" SATELLITE_VERSION="${PROJECT_VERSION}")

# Distribución (FASE 21): instalación independiente del framework.
install(TARGETS satellite RUNTIME DESTINATION bin)
install(DIRECTORY third_party/json DESTINATION share/satellite/third_party)
install(FILES README.md AGENTS.md DESTINATION share/satellite)
add_executable(satellite_benchmark benchmark/benchmark.cpp)
target_link_libraries(satellite_benchmark PRIVATE satellite_core)
target_compile_definitions(satellite_benchmark PRIVATE SATELLITE_ROOT="${PROJECT_SOURCE_DIR}")
