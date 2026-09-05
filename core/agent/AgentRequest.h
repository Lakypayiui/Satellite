#pragma once

// Petición de ejecución enviada a un agente.
// Contiene el ID del agente, el payload de entrada, contexto, presupuesto de tokens y metadatos de ejecución.

#include <json.hpp>

#include "AgentID.h"
#include "core/protocol/Protocol.h"

namespace satellite::core::agent
{

// Declaración adelantada (ver AgentSandbox.h). Los agentes de cómputo puro no
// leen este puntero => retrocompatible con la ABI actual.
struct AgentSandbox;

struct AgentRequest
{
    AgentID agent_id = UNKNOWN_AGENT_ID;
    nlohmann::json input;
    nlohmann::json context;
    nlohmann::json metadata;
    satellite::core::protocol::TokenBudget token_budget;
    satellite::core::protocol::ExecutionMetadata execution_metadata;
    // Sandbox de efectos de sistema (opcional). Lo arma el host; el agente solo
    // puede usarlo si la capability correspondiente está permitida.
    const AgentSandbox* sandbox = nullptr;
};

} // namespace satellite::core::agent
