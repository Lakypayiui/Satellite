#pragma once

// Dispatcher determinista: AgentRequest → Registry → AgentDescriptor → Validación → IAgent::execute() → AgentResult.
// No interpreta lenguaje natural. Sin logs, sin persistencia, sin LLM.

#include "core/agent/AgentRequest.h"
#include "core/agent/AgentResult.h"
#include "core/registry/AgentRegistry.h"

namespace satellite::core::dispatcher
{

class Dispatcher
{
public:
    explicit Dispatcher(satellite::core::registry::AgentRegistry& registry);
    satellite::core::agent::AgentResult dispatch(const satellite::core::agent::AgentRequest& request);

private:
    satellite::core::registry::AgentRegistry& registry_;
};

} // namespace satellite::core::dispatcher