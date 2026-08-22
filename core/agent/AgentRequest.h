#pragma once

// Petición de ejecución enviada a un agente.
// Contiene el ID del agente, el payload de entrada, contexto, presupuesto de tokens y metadatos de ejecución.

#include <json.hpp>

#include "AgentID.h"
#include "core/protocol/Protocol.h"

namespace satellite::core::agent
{

struct AgentRequest
{
    AgentID agent_id = UNKNOWN_AGENT_ID;
    nlohmann::json input;
    nlohmann::json context;
    nlohmann::json metadata;
    satellite::core::protocol::TokenBudget token_budget;
    satellite::core::protocol::ExecutionMetadata execution_metadata;
};

} // namespace satellite::core::agent