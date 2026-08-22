#pragma once

// Petición de ejecución enviada a un agente.
// Contiene el ID del agente, el payload de entrada, contexto y metadatos.

#include <json.hpp>

#include "AgentID.h"

namespace satellite::core::agent
{

struct AgentRequest
{
    AgentID agent_id = UNKNOWN_AGENT_ID;
    nlohmann::json input;
    nlohmann::json context;
    nlohmann::json metadata;
};

} // namespace satellite::core::agent