#pragma once

// Resultado de la ejecución de un agente.
// Contiene el estado, output (si éxito), error (si fallo) y duración.

#include <json.hpp>
#include <optional>

#include "AgentID.h"
#include "AgentStatus.h"
#include "AgentError.h"

namespace satellite::core::agent
{

struct AgentResult
{
    AgentID agent_id = UNKNOWN_AGENT_ID;
    AgentStatus status = AgentStatus::FAILED;
    nlohmann::json output;
    std::optional<AgentError> error;
    double duration_ms = 0.0;
};

} // namespace satellite::core::agent