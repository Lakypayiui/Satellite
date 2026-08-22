#pragma once

// Metadatos de una ejecución individual de agente.
// Usados para observabilidad y trazabilidad (Fase 19).

#include <cstdint>
#include <string>

#include "AgentID.h"

namespace satellite::core::agent
{

struct AgentMetadata
{
    std::string execution_id;
    AgentID agent_id = UNKNOWN_AGENT_ID;
    std::string agent_version;
    std::int64_t timestamp = 0;
    double duration_ms = 0.0;
};

} // namespace satellite::core::agent