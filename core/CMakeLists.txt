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
  registry/AgentRegistry.h
  registry/AgentRegistry.cpp
  validation/InputValidator.h
  validation/InputValidator.cpp
  dispatcher/Dispatcher.h
  dispatcher/Dispatcher.cpp
  agents/NativeAgents.h
  agents/NativeAgents.cpp
  protocol/Protocol.h
  protocol/Protocol.cpp
  ../llm/LLMTypes.h
  ../llm/ILLMProvider.h
  ../llm/DeepSeekProvider.h
  ../llm/DeepSeekProvider.cpp
)

target_include_directories(satellite_core PUBLIC
  ${PROJECT_SOURCE_DIR}
  ${PROJECT_SOURCE_DIR}/third_party
  ${PROJECT_SOURCE_DIR}/third_party/json
)