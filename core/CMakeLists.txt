# ---------------------------------------------------------------------------
# satellite_core — librería estática del framework Satellite
#
# Nota: este archivo se incluye vía add_subdirectory(core) desde la raíz.
# Las rutas de fuentes son relativas a ESTE directorio (core/); las de
# módulos hermanos (llm/, context/, etc.) usan el prefijo ../.
# ---------------------------------------------------------------------------

add_library(satellite_core STATIC)

target_sources(satellite_core PRIVATE
  # agent / registro / catálogo / dispatcher / validación / protocolo
  agent/agent.cpp
  registry/AgentRegistry.cpp
  validation/InputValidator.cpp
  dispatcher/Dispatcher.cpp
  agents/NativeAgents.cpp
  protocol/Protocol.cpp
  catalog/AgentCatalog.cpp

  # llm (proveedores HTTP + local)
  ../llm/HttpClient.cpp
  ../llm/DeepSeekProvider.cpp
  ../llm/LocalLLMProvider.cpp
  ../llm/OpenAICompatibleProvider.cpp
  ../llm/AnthropicProvider.cpp
  ../llm/ProviderFactory.cpp

  # contexto (motor, índice, semántico, preprocesador local, optimizador, adaptadores)
  ../context/engine/ContextEngine.cpp
  ../context/engine/ProjectIndex.cpp
  ../context/engine/SemanticContext.cpp
  ../context/LocalPreprocessor.cpp
  ../context/optimizer/ContextOptimizer.cpp
  ../context/adapter/ProjectAdapter.cpp

  # orquestación y planificación
  ../orchestrator/Orchestrator.cpp
  ../planner/Planner.cpp

  # fábrica de agentes (expansión por LLM + ejecución en proceso aislado)
  ../factory/AgentFactory.cpp
  ../factory/ProcessAgentProxy.cpp
  ../factory/AgentExpander.cpp

  # persistencia, configuración, seguridad, observabilidad
  ../persistence/AgentStore.cpp
  ../persistence/ProjectInitializer.cpp
  ../config/Config.cpp
  ../security/SecurityPolicy.cpp
  ../observability/ExecutionLog.cpp
)

target_include_directories(satellite_core PUBLIC
  ${PROJECT_SOURCE_DIR}
  ${PROJECT_SOURCE_DIR}/third_party
  ${PROJECT_SOURCE_DIR}/third_party/json
)

target_link_libraries(satellite_core PRIVATE CURL::libcurl)
if(NOT MSVC)
  target_link_libraries(satellite_core PRIVATE ${CMAKE_DL_LIBS})
endif()
