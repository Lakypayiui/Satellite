#pragma once

// Agentes nativos de prueba para validar el runtime (FASE 4).
// Estos agentes NO representan el objetivo final del framework.
// IDs fijos: 1=sum, 2=subtract, 3=multiply, 4=divide, 5=average.

#include <json.hpp>

#include "core/agent/IAgent.h"
#include "core/agent/AgentDescriptor.h"
#include "core/agent/AgentRequest.h"
#include "core/agent/AgentResult.h"
#include "core/agent/AgentStatus.h"
#include "core/agent/AgentError.h"
#include "core/registry/AgentRegistry.h"

namespace satellite::core::agents
{

class SumAgent : public satellite::core::agent::IAgent
{
public:
    satellite::core::agent::AgentResult execute(const satellite::core::agent::AgentRequest& request) override;

    static satellite::core::agent::AgentDescriptor descriptor(satellite::core::agent::IAgent* impl);
};

class SubtractAgent : public satellite::core::agent::IAgent
{
public:
    satellite::core::agent::AgentResult execute(const satellite::core::agent::AgentRequest& request) override;

    static satellite::core::agent::AgentDescriptor descriptor(satellite::core::agent::IAgent* impl);
};

class MultiplyAgent : public satellite::core::agent::IAgent
{
public:
    satellite::core::agent::AgentResult execute(const satellite::core::agent::AgentRequest& request) override;

    static satellite::core::agent::AgentDescriptor descriptor(satellite::core::agent::IAgent* impl);
};

class DivideAgent : public satellite::core::agent::IAgent
{
public:
    satellite::core::agent::AgentResult execute(const satellite::core::agent::AgentRequest& request) override;

    static satellite::core::agent::AgentDescriptor descriptor(satellite::core::agent::IAgent* impl);
};

class AverageAgent : public satellite::core::agent::IAgent
{
public:
    satellite::core::agent::AgentResult execute(const satellite::core::agent::AgentRequest& request) override;

    static satellite::core::agent::AgentDescriptor descriptor(satellite::core::agent::IAgent* impl);
};

void register_native_agents(satellite::core::registry::AgentRegistry& registry);

} // namespace satellite::core::agents