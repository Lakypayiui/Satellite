# Helper para agregar tests del framework
function(add_satellite_test TEST_NAME)
  add_executable(${TEST_NAME} ${ARGN})
  target_link_libraries(${TEST_NAME} PRIVATE satellite_core)
  add_test(NAME ${TEST_NAME} COMMAND ${TEST_NAME})
endfunction()

add_satellite_test(test_agent_core test_agent_core.cpp)