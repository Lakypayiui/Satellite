#include "security/SecurityPolicy.h"
#include "core/agent/AgentDescriptor.h"

namespace satellite::security
{

SecurityPolicy::SecurityPolicy()
    : allow_()
{
}

void SecurityPolicy::set_allowed(const std::string& capability, bool allowed)
{
    allow_[capability] = allowed;
}

bool SecurityPolicy::is_allowed(const std::string& capability) const
{
    auto it = allow_.find(capability);
    if (it == allow_.end())
    {
        return false;
    }
    return it->second;
}

void SecurityPolicy::load_defaults()
{
    allow_["filesystem.read"] = true;
    allow_["filesystem.write"] = false;
    allow_["process.execute"] = false;
    allow_["compiler.execute"] = false;
    allow_["network.request"] = false;
}

void SecurityPolicy::from_config(const std::map<std::string, bool>& allow_map)
{
    allow_ = allow_map;
}

bool SecurityPolicy::validate_agent(const satellite::core::agent::AgentDescriptor& descriptor, std::string& denied_capability) const
{
    if (descriptor.capabilities.empty())
    {
        return true;
    }

    for (const auto& cap : descriptor.capabilities)
    {
        if (!is_allowed(cap))
        {
            denied_capability = cap;
            return false;
        }
    }

    return true;
}

std::map<std::string, bool> SecurityPolicy::allow_map() const
{
    return allow_;
}

} // namespace satellite::security