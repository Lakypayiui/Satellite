#pragma once

// Fábrica de microagentes: pipeline determinista que toma una AgentSpec (especificación + código + tests)
// y produce un agente registrado en el AgentRegistry.
// Fases: validate → write code → compile test harness → run tests → compile shared lib → load lib → register.

#include <json.hpp>
#include <filesystem>
#include <map>
#include <string>
#include <vector>
#include <utility>

#include "core/agent/AgentDescriptor.h"
#include "core/agent/AgentID.h"
#include "core/registry/AgentRegistry.h"

namespace satellite::factory
{

using satellite::core::agent::AgentID;
using satellite::core::agent::UNKNOWN_AGENT_ID;
using satellite::core::agent::AgentDescriptor;
using satellite::core::registry::AgentRegistry;

struct AgentSpec
{
    AgentID id = UNKNOWN_AGENT_ID;
    std::string name;
    std::string description;
    std::string version = "1.0.0";
    nlohmann::json input_schema;
    nlohmann::json output_schema;
    std::vector<std::string> context_requirements;
    std::vector<std::string> capabilities;
    std::string implementation_code;
    std::vector<std::pair<nlohmann::json, nlohmann::json>> test_cases;
};

struct FactoryResult
{
    bool ok = false;
    std::string stage;
    std::string message;
    AgentID agent_id = UNKNOWN_AGENT_ID;
};

class AgentFactory
{
public:
    AgentFactory(AgentRegistry& registry, std::filesystem::path work_dir, std::filesystem::path framework_root, std::string compiler = "g++");
    ~AgentFactory();

    FactoryResult create_agent(const AgentSpec& spec);
    bool release_agent(AgentID id);
    void cleanup();

private:
    using DestroyAgentFn = void (*)(satellite::core::agent::IAgent*);

    struct LoadedLibrary
    {
        void* handle = nullptr;
        satellite::core::agent::IAgent* agent = nullptr;
        DestroyAgentFn destroy = nullptr;
    };

    AgentRegistry& registry_;
    std::filesystem::path work_dir_;
    std::filesystem::path framework_root_;
    std::string compiler_;
    std::map<AgentID, LoadedLibrary> loaded_libs_;

    bool compile(const std::vector<std::string>& sources, const std::vector<std::string>& extra_flags, std::string& output, std::string& error) const;
    std::string get_library_path(AgentID id) const;
    std::string get_test_executable_path(AgentID id) const;
    void unload_all();
};

// Serialización de AgentSpec para persistencia
void to_json(nlohmann::json& j, const AgentSpec& spec);
void from_json(const nlohmann::json& j, AgentSpec& spec);

} // namespace satellite::factory