// Implementación del registro canónico de capacidades del runtime Satellite (Fase 2).
// Proporciona gestión de inscripción, consulta y estado habilitado/deshabilitado de agentes.

#include "core/registry/AgentRegistry.h"

#include <algorithm>

using satellite::core::agent::AgentID;
using satellite::core::agent::AgentDescriptor;
using satellite::core::agent::UNKNOWN_AGENT_ID;

namespace satellite::core::registry
{

bool AgentRegistry::register_agent(const AgentDescriptor& descriptor)
{
    if (descriptor.id == UNKNOWN_AGENT_ID)
    {
        return false;
    }

    if (registry_.find(descriptor.id) != registry_.end())
    {
        return false;
    }

    registry_.emplace(descriptor.id, descriptor);
    return true;
}

bool AgentRegistry::unregister_agent(AgentID id)
{
    auto it = registry_.find(id);
    if (it == registry_.end())
    {
        return false;
    }

    registry_.erase(it);
    disabled_.erase(id);
    return true;
}

const AgentDescriptor* AgentRegistry::find_agent(AgentID id) const
{
    auto it = registry_.find(id);
    if (it == registry_.end())
    {
        return nullptr;
    }
    return &it->second;
}

bool AgentRegistry::has_agent(AgentID id) const
{
    return registry_.find(id) != registry_.end();
}

std::vector<AgentDescriptor> AgentRegistry::list_agents() const
{
    std::vector<AgentDescriptor> result;
    result.reserve(registry_.size());

    for (const auto& pair : registry_)
    {
        result.push_back(pair.second);
    }

    std::sort(result.begin(), result.end(),
              [](const AgentDescriptor& a, const AgentDescriptor& b)
              {
                  return a.id < b.id;
              });

    return result;
}

bool AgentRegistry::enable_agent(AgentID id)
{
    if (registry_.find(id) == registry_.end())
    {
        return false;
    }

    disabled_.erase(id);
    return true;
}

bool AgentRegistry::disable_agent(AgentID id)
{
    if (registry_.find(id) == registry_.end())
    {
        return false;
    }

    disabled_.insert(id);
    return true;
}

bool AgentRegistry::is_enabled(AgentID id) const
{
    if (registry_.find(id) == registry_.end())
    {
        return false;
    }

    return disabled_.find(id) == disabled_.end();
}

} // namespace satellite::core::registry