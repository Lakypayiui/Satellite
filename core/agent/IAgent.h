#pragma once

// Interfaz base que deben implementar todos los agentes.
// El runtime invoca execute() con un AgentRequest y espera un AgentResult.

#include "AgentRequest.h"
#include "AgentResult.h"

namespace satellite::core::agent
{

class IAgent
{
public:
    virtual ~IAgent() = default;
    virtual AgentResult execute(const AgentRequest& request) = 0;
};

} // namespace satellite::core::agent