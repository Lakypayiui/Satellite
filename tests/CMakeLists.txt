# Helper para agregar tests del framework
function(add_satellite_test TEST_NAME)
  add_executable(${TEST_NAME} ${ARGN})
  target_link_libraries(${TEST_NAME} PRIVATE satellite_core)
  add_test(NAME ${TEST_NAME} COMMAND ${TEST_NAME})
endfunction()

add_satellite_test(test_agent_core test_agent_core.cpp)

add_satellite_test(test_registry test_registry.cpp)

add_satellite_test(test_validator test_validator.cpp)

add_satellite_test(test_dispatcher test_dispatcher.cpp)

add_satellite_test(test_native_agents test_native_agents.cpp)

add_satellite_test(test_protocol test_protocol.cpp)

add_satellite_test(test_llm_provider test_llm_provider.cpp)

add_satellite_test(test_catalog test_catalog.cpp)

add_satellite_test(test_context_engine test_context_engine.cpp)

add_satellite_test(test_context_optimizer test_context_optimizer.cpp)

add_satellite_test(test_project_adapter test_project_adapter.cpp)

add_satellite_test(test_orchestrator test_orchestrator.cpp)

add_satellite_test(test_planner test_planner.cpp)