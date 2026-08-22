#pragma once

// Dispatcher determinista: AgentRequest → Registry → AgentDescriptor → Validación → IAgent::execute() → AgentResult.
// No interpreta lenguaje natural. Sin logs, sin persistencia, sin LLM.

#include "core/agent/AgentRequest.h"
#include "core/agent/AgentResult.h"
#include "core/registry/AgentRegistry.h"

namespace satellite::security
{
class SecurityPolicy;
}

namespace satellite::core::dispatcher
{

class Dispatcher
{
public:
    explicit Dispatcher(satellite::core::registry::AgentRegistry& registry, const satellite::security::SecurityPolicy* security = nullptr);
    satellite::core::agent::AgentResult dispatch(const satellite::core::agent::AgentRequest& request);

private:
    satellite::core::registry::AgentRegistry& registry_;
    const satellite::security::SecurityPolicy* security_ = nullptr;
};

} // namespace satellite::core::dispatcher