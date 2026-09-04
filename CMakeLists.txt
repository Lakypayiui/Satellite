cmake_minimum_required(VERSION 3.16)
project(satellite VERSION 1.0.0 LANGUAGES CXX)

get_filename_component(SATELLITE_COMPILER_BIN "${CMAKE_CXX_COMPILER}" DIRECTORY)
get_filename_component(SATELLITE_TOOLCHAIN_ROOT "${SATELLITE_COMPILER_BIN}/.." ABSOLUTE)
find_package(CURL REQUIRED)

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

add_executable(satellite_agent_host tools/agent_host.cpp)
target_include_directories(satellite_agent_host PRIVATE
  ${PROJECT_SOURCE_DIR}
  ${PROJECT_SOURCE_DIR}/third_party
  ${PROJECT_SOURCE_DIR}/third_party/json
)
if(NOT MSVC)
  target_link_libraries(satellite_agent_host PRIVATE ${CMAKE_DL_LIBS})
endif()

add_executable(satellite_factory_cli
  factory/FactoryCLI.cpp
  factory/AgentFactory.cpp
  factory/ProcessAgentProxy.cpp
  factory/AgentExpander.cpp
  core/registry/AgentRegistry.cpp
  core/catalog/AgentCatalog.cpp
  core/protocol/Protocol.cpp
  llm/HttpClient.cpp
  llm/DeepSeekProvider.cpp
)
target_include_directories(satellite_factory_cli PRIVATE
  ${PROJECT_SOURCE_DIR}
  ${PROJECT_SOURCE_DIR}/third_party
  ${PROJECT_SOURCE_DIR}/third_party/json
)
target_compile_definitions(satellite_factory_cli PRIVATE SATELLITE_ROOT="${PROJECT_SOURCE_DIR}")
target_link_libraries(satellite_factory_cli PRIVATE CURL::libcurl)
if(NOT MSVC)
  target_link_libraries(satellite_factory_cli PRIVATE ${CMAKE_DL_LIBS})
endif()

install(TARGETS satellite_factory_cli satellite_agent_host RUNTIME DESTINATION bin)
install(DIRECTORY third_party/json DESTINATION share/satellite/third_party)
install(FILES README.md AGENTS.md DESTINATION share/satellite)
