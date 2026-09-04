cmake_minimum_required(VERSION 3.16)
project(satellite VERSION 1.0.0 LANGUAGES CXX)

# ---------------------------------------------------------------------------
# Satellite — framework de microagentes generados por LLM (C++17)
#
# Estructura:
#   core/    → librería estática satellite_core (framework)
#   cli/     → binario `satellite` (CLI)
#   tools/   → binario `satellite_agent_host` (host aislado de plugins)
#   factory/ → binario `satellite_factory_cli` (expansión de agentes por LLM)
#   tests/   → suite CTest (solo con BUILD_TESTING=ON)
# ---------------------------------------------------------------------------

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release)
endif()

# --- Dependencias del framework ---
# HttpClient usa libcurl para las llamadas HTTP a los proveedores LLM.
find_package(CURL REQUIRED)

# --- Librería central del framework ---
add_subdirectory(core)

# --- Tests (habilitados por defecto vía CTest) ---
include(CTest)
if(BUILD_TESTING)
  add_subdirectory(tests)
endif()

# ===========================================================================
# Binario: satellite (CLI)
# ===========================================================================
add_executable(satellite
  cli/main.cpp
  cli/SatelliteCLI.cpp
)
target_link_libraries(satellite PRIVATE satellite_core)
# La CLI compila agentes generados en runtime con el compilador del sistema.
target_compile_definitions(satellite PRIVATE
  SATELLITE_ROOT="${PROJECT_SOURCE_DIR}"
  SATELLITE_VERSION="${PROJECT_VERSION}"
)

# ===========================================================================
# Binario: satellite_agent_host (host aislado de plugins de agentes)
# ===========================================================================
add_executable(satellite_agent_host
  tools/agent_host.cpp
)
target_link_libraries(satellite_agent_host PRIVATE satellite_core)
if(NOT MSVC)
  target_link_libraries(satellite_agent_host PRIVATE ${CMAKE_DL_LIBS})
endif()

# ===========================================================================
# Binario: satellite_factory_cli (fábrica de agentes por LLM)
# ===========================================================================
add_executable(satellite_factory_cli
  factory/FactoryCLI.cpp
)
target_link_libraries(satellite_factory_cli PRIVATE satellite_core)
target_compile_definitions(satellite_factory_cli PRIVATE
  SATELLITE_ROOT="${PROJECT_SOURCE_DIR}"
)
if(NOT MSVC)
  target_link_libraries(satellite_factory_cli PRIVATE ${CMAKE_DL_LIBS})
endif()

# ===========================================================================
# Instalación
# ===========================================================================
install(TARGETS satellite satellite_factory_cli satellite_agent_host
  RUNTIME DESTINATION bin)
install(DIRECTORY third_party/json DESTINATION share/satellite/third_party)
install(FILES README.md AGENTS.md DESTINATION share/satellite)
