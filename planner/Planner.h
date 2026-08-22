#pragma once

// Planificador de tareas para Satellite.
// Representa una tarea como una secuencia de microagentes (PlanSteps).
// Provee validación estructural, orden topológico y generación de planes vía LLM.

#include <json.hpp>
#include <string>
#include <vector>

#include "core/agent/AgentID.h"
#include "core/catalog/AgentCatalog.h"
#include "llm/ILLMProvider.h"

namespace satellite::planner
{

using satellite::core::agent::AgentID;
using satellite::core::agent::UNKNOWN_AGENT_ID;
using satellite::core::catalog::AgentCatalog;
using satellite::llm::ILLMProvider;

struct PlanStep
{
    AgentID agent_id = UNKNOWN_AGENT_ID;
    nlohmann::json input;
    std::vector<std::size_t> dependencies;
    std::vector<std::string> conditions;
    std::size_t order = 0;
    std::string description;
};

struct Plan
{
    std::string goal;
    std::vector<PlanStep> steps;
};

class Planner
{
public:
    // Validación ESTRUCTURAL determinista (sin LLM, sin ejecutar nada):
    // - plan vacío (0 steps) → VÁLIDO (no-op).
    // - cada step: agent_id != UNKNOWN_AGENT_ID (0);
    //   dependencies: cada índice < steps.size() y != índice propio;
    //   SIN duplicados en dependencies;
    //   order: los valores deben ser 0..n-1 exactamente una vez (permutación) — si no, error "invalid order".
    // - ciclos: detectados por execution_order (validate también los detecta vía execution_order interno).
    bool validate(const Plan& plan, std::string& error) const;

    // JSON → Plan (formato que produce el LLM): {"goal": "...", "steps": [...]}
    bool from_json(const nlohmann::json& j, Plan& plan, std::string& error) const;

    // Orden de ejecución por algoritmo topológico (Kahn): devuelve índices de steps
    // en orden ejecutable; detecta ciclos → false + error.
    bool execution_order(const Plan& plan, std::vector<std::size_t>& order, std::string& error) const;

    // Goal → Plan usando el LLM (prompt con catálogo, parse + validación):
    bool plan_goal(const std::string& goal, const AgentCatalog& catalog, ILLMProvider& llm, Plan& plan, std::string& error) const;
};

} // namespace satellite::planner