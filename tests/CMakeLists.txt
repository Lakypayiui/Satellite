# Helper para agregar tests del framework
function(add_satellite_test TEST_NAME)
  add_executable(${TEST_NAME} ${ARGN})
  target_link_libraries(${TEST_NAME} PRIVATE satellite_core)
  add_test(NAME ${TEST_NAME} COMMAND ${TEST_NAME})
endfunction()

add_satellite_test(test_agent_core test_agent_core.cpp)

add_satellite_test(test_sandbox test_sandbox.cpp)

add_satellite_test(test_registry test_registry.cpp)

add_satellite_test(test_validator test_validator.cpp)

add_satellite_test(test_dispatcher test_dispatcher.cpp)

add_satellite_test(test_native_agents test_native_agents.cpp)

add_satellite_test(test_protocol test_protocol.cpp)

add_satellite_test(test_llm_provider test_llm_provider.cpp)

add_satellite_test(test_catalog test_catalog.cpp)

add_satellite_test(test_context_engine test_context_engine.cpp)

add_satellite_test(test_project_index test_project_index.cpp)

add_satellite_test(test_context_invalidation test_context_invalidation.cpp)

add_satellite_test(test_semantic_context test_semantic_context.cpp)

add_satellite_test(test_context_optimizer test_context_optimizer.cpp)

add_satellite_test(test_project_adapter test_project_adapter.cpp)

add_satellite_test(test_orchestrator test_orchestrator.cpp)

add_satellite_test(test_planner test_planner.cpp)

add_satellite_test(test_agent_factory test_agent_factory.cpp)
target_compile_definitions(test_agent_factory PRIVATE SATELLITE_ROOT="${PROJECT_SOURCE_DIR}")

add_satellite_test(test_agent_expander test_agent_expander.cpp)
target_compile_definitions(test_agent_expander PRIVATE SATELLITE_ROOT="${PROJECT_SOURCE_DIR}")

add_satellite_test(test_persistence test_persistence.cpp)
target_compile_definitions(test_persistence PRIVATE SATELLITE_ROOT="${PROJECT_SOURCE_DIR}")

add_satellite_test(test_project_initializer test_project_initializer.cpp)

add_satellite_test(test_config test_config.cpp)

add_satellite_test(test_security test_security.cpp)

add_satellite_test(test_observability test_observability.cpp)
add_satellite_test(test_cli test_cli.cpp ../cli/SatelliteCLI.cpp)
target_compile_definitions(test_cli PRIVATE SATELLITE_ROOT="${PROJECT_SOURCE_DIR}" SATELLITE_VERSION="${PROJECT_VERSION}")
if(WIN32)
  # test_cli ejecuta los binarios de build/satellite (subprocesos) y necesita
  # las DLLs de runtime de MinGW al arrancar; copiarlas a su dir de salida.
  file(GLOB CLI_RUNTIME_DLLS "C:/msys64/mingw64/bin/libstdc++-6.dll"
       "C:/msys64/mingw64/bin/libgcc_s_seh-1.dll"
       "C:/msys64/mingw64/bin/libgcc_s_*.dll"
       "C:/msys64/mingw64/bin/libwinpthread-1.dll")
  if(CLI_RUNTIME_DLLS)
    add_custom_command(TARGET test_cli POST_BUILD
      COMMAND ${CMAKE_COMMAND} -E copy_if_different
        ${CLI_RUNTIME_DLLS} $<TARGET_FILE_DIR:test_cli>
      COMMENT "Copiando DLLs de runtime junto a test_cli")
  endif()
endif()

add_satellite_test(test_portability test_portability.cpp)
target_compile_definitions(test_portability PRIVATE SATELLITE_ROOT="${PROJECT_SOURCE_DIR}")

add_satellite_test(test_benchmark test_benchmark.cpp)
