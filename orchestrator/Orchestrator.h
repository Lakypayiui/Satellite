#pragma once

// Orchestrator: responsable de alto nivel (Fase 11).
// Recibe objetivo; consulta Agent Catalog; selecciona agentes; construye plan;
// solicita contexto; utiliza Context Optimizer; ejecuta agentes vía Dispatcher;
// procesa resultados; continúa el plan; detecta capacidades inexistentes.
// El Orchestrator NUNCA ejecuta código directamente — solo construye AgentRequest
// y delega en el Dispatcher (determinismo).
// El Planner formal es FASE 12; aquí se usa una representación de plan MÍNIMA
// interna, documentada como tal.

#include <json.hpp>
#include <string>
#include <vector>

#include "core/agent/AgentID.h"
#include "core/agent/AgentResult.h"
#include "core/catalog/AgentCatalog.h"
#include "core/dispatcher/Dispatcher.h"
#include "context/engine/ProjectContext.h"
#include "context/optimizer/ContextOptimizer.h"
#include "core/protocol/Protocol.h"
#include "llm/ILLMProvider.h"

namespace satellite::observability
{
class ExecutionLogger;
}

namespace satellite::orchestrator
{

using satellite::core::agent::AgentID;
using satellite::core::agent::AgentResult;
using satellite::core::agent::UNKNOWN_AGENT_ID;
using satellite::core::catalog::AgentCatalog;
using satellite::context::ProjectContext;
using satellite::core::protocol::TokenBudget;

// Representación MÍNIMA de paso (se formalizará en FASE 12 con el Planner).
struct OrchestrationStep
{
    AgentID agent_id = UNKNOWN_AGENT_ID;
    nlohmann::json input;
    std::vector<AgentID> dependencies;   // pasos que deben completarse antes (por índice de paso)
    std::string description;             // breve, para el contexto/trazabilidad
};

// Resultado de una orquestación completa.
struct OrchestrationResult
{
    bool ok = false;
    std::vector<AgentResult> results;    // uno por paso, en orden de ejecución
    std::string summary;                 // resumen legible ("paso i: agent X -> SUCCESS/FAILED ...")
};

class Orchestrator
{
public:
    Orchestrator(satellite::core::registry::AgentRegistry& registry,
                 satellite::core::dispatcher::Dispatcher& dispatcher,
                 satellite::context::IContextOptimizer& optimizer,
                 satellite::llm::ILLMProvider* llm);

    // Ejecución DETERMINISTA de un plan (sin LLM).
    OrchestrationResult run_plan(const std::vector<OrchestrationStep>& plan,
                                 const ProjectContext& project,
                                 const TokenBudget& budget);

    // Objetivo → plan (usa el LLM si está disponible; si llm == nullptr → ok=false con mensaje claro).
    OrchestrationResult execute_goal(const std::string& goal,
                                     const ProjectContext& project,
                                     const TokenBudget& budget,
                                     const AgentCatalog& catalog);

    // Detección de capacidades ausentes (heurística SIN LLM):
    // keywords del goal vs capabilities del catálogo.
    std::vector<std::string> detect_missing_capabilities(const std::string& goal,
                                                         const AgentCatalog& catalog) const;

    // Establece el logger de observabilidad (opcional; nullptr = sin registro).
    void set_logger(satellite::observability::ExecutionLogger* logger);

private:
    satellite::core::registry::AgentRegistry& registry_;
    satellite::core::dispatcher::Dispatcher& dispatcher_;
    satellite::context::IContextOptimizer& optimizer_;
    satellite::llm::ILLMProvider* llm_;   // nullable (el runtime no depende del LLM)
    satellite::observability::ExecutionLogger* logger_ = nullptr;

    AgentResult execute_step(const OrchestrationStep& step,
                             const ProjectContext& project,
                             const TokenBudget& budget) const;
};

} // namespace satellite::orchestrator