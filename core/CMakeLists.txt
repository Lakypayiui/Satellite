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
  catalog/AgentCatalog.h
  catalog/AgentCatalog.cpp
  ../llm/LLMTypes.h
  ../llm/ILLMProvider.h
  ../llm/DeepSeekProvider.h
  ../llm/DeepSeekProvider.cpp
  ../context/engine/ProjectContext.h
  ../context/engine/ContextEngine.h
  ../context/engine/ContextEngine.cpp
  ../context/optimizer/ContextOptimizer.h
  ../context/optimizer/ContextOptimizer.cpp
  ../context/adapter/ProjectAdapter.h
  ../context/adapter/ProjectAdapter.cpp
  ../orchestrator/Orchestrator.h
  ../orchestrator/Orchestrator.cpp
  ../planner/Planner.h
  ../planner/Planner.cpp
  ../factory/AgentFactory.h
  ../factory/AgentFactory.cpp
  ../factory/AgentPlugin.h
  ../factory/AgentExpander.h
  ../factory/AgentExpander.cpp
  ../persistence/AgentStore.h
  ../persistence/AgentStore.cpp
  ../persistence/ProjectInitializer.h
  ../persistence/ProjectInitializer.cpp
  ../config/Config.h
  ../config/Config.cpp
  ../observability/ExecutionLog.h
  ../observability/ExecutionLog.cpp
  ../security/SecurityPolicy.h
  ../security/SecurityPolicy.cpp
  )

target_include_directories(satellite_core PUBLIC
  ${PROJECT_SOURCE_DIR}
  ${PROJECT_SOURCE_DIR}/third_party
  ${PROJECT_SOURCE_DIR}/third_party/json
)

if(NOT MSVC)
    target_link_libraries(satellite_core PRIVATE ${CMAKE_DL_LIBS})
endif()