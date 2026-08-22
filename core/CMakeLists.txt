# Biblioteca estática core del framework
add_library(satellite_core STATIC)

target_sources(satellite_core PRIVATE
  agent/AgentID.h
  agent/AgentStatus.h
  agent/AgentError.h
  agent/AgentRequest.h
  agent/AgentResult.h
  agent/AgentMetadata.h
  agent/AgentDescriptor.h
  agent/IAgent.h
  agent/agent.h
  agent/agent.cpp
)

target_include_directories(satellite_core PUBLIC
  ${PROJECT_SOURCE_DIR}
  ${PROJECT_SOURCE_DIR}/third_party
  ${PROJECT_SOURCE_DIR}/third_party/json
)