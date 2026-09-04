#pragma once

#include <filesystem>
#include <string>

#include "core/agent/IAgent.h"

namespace satellite::factory
{

class ProcessAgentProxy final : public satellite::core::agent::IAgent
{
public:
    ProcessAgentProxy(satellite::core::agent::AgentID agent_id,
                      std::filesystem::path library_path,
                      std::filesystem::path framework_root);

    satellite::core::agent::AgentResult execute(
        const satellite::core::agent::AgentRequest& request) override;

private:
    satellite::core::agent::AgentID agent_id_;
    std::filesystem::path library_path_;
    std::filesystem::path framework_root_;
};

} // namespace satellite::factory
