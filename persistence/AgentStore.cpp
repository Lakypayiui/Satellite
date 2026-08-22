#include "persistence/AgentStore.h"

#include "factory/AgentFactory.h"
#include "core/registry/AgentRegistry.h"
#include "core/agent/AgentDescriptor.h"

#include <json.hpp>
#include <fstream>
#include <filesystem>

namespace satellite::persistence
{

AgentStore::AgentStore(std::filesystem::path project_root)
    : project_root_(std::move(project_root))
{
}

std::filesystem::path AgentStore::root() const
{
    return project_root_ / ".satellite";
}

bool AgentStore::ensure_dirs() const
{
    try
    {
        std::filesystem::create_directories(root() / "config");
        std::filesystem::create_directories(root() / "registry");
        std::filesystem::create_directories(root() / "agents");
        std::filesystem::create_directories(root() / "context");
        std::filesystem::create_directories(root() / "executions");
        return true;
    }
    catch (...)
    {
        return false;
    }
}

bool AgentStore::has_state() const
{
    return std::filesystem::exists(root()) && std::filesystem::is_directory(root());
}

bool AgentStore::save_spec(const AgentSpec& spec) const
{
    try
    {
        nlohmann::json j;
        to_json(j, spec);
        std::filesystem::path file = root() / "agents" / ("agent_" + std::to_string(spec.id) + ".json");
        std::ofstream ofs(file, std::ios::trunc);
        if (!ofs)
            return false;
        ofs << j.dump(2);
        return true;
    }
    catch (...)
    {
        return false;
    }
}

std::vector<AgentSpec> AgentStore::load_specs() const
{
    std::vector<AgentSpec> specs;
    std::filesystem::path agents_dir = root() / "agents";
    if (!std::filesystem::exists(agents_dir) || !std::filesystem::is_directory(agents_dir))
        return specs;

    for (const auto& entry : std::filesystem::directory_iterator(agents_dir))
    {
        if (!entry.is_regular_file() || entry.path().extension() != ".json")
            continue;

        std::ifstream ifs(entry.path());
        if (!ifs)
            continue;

        std::string content((std::istreambuf_iterator<char>(ifs)), std::istreambuf_iterator<char>());
        nlohmann::json j = nlohmann::json::parse(content, nullptr, false);
        if (j.is_discarded())
            continue;

        AgentSpec spec = j.get<AgentSpec>();
        if (spec.name.empty())
            continue;

        specs.push_back(std::move(spec));
    }
    return specs;
}

bool AgentStore::save_registry(const AgentRegistry& registry) const
{
    try
    {
        nlohmann::json arr = nlohmann::json::array();
        for (const auto& desc : registry.list_agents())
        {
            nlohmann::json obj;
            obj["id"] = desc.id;
            obj["name"] = desc.name;
            obj["description"] = desc.description;
            obj["version"] = desc.version;
            obj["input_schema"] = desc.input_schema;
            obj["output_schema"] = desc.output_schema;
            obj["context_requirements"] = desc.context_requirements;
            obj["capabilities"] = desc.capabilities;
            obj["enabled"] = registry.is_enabled(desc.id);
            arr.push_back(std::move(obj));
        }

        std::filesystem::path file = root() / "registry" / "agents.json";
        std::ofstream ofs(file, std::ios::trunc);
        if (!ofs)
            return false;
        ofs << arr.dump(2);
        return true;
    }
    catch (...)
    {
        return false;
    }
}

bool AgentStore::load_registry(AgentRegistry& registry) const
{
    std::filesystem::path file = root() / "registry" / "agents.json";
    if (!std::filesystem::exists(file))
        return false;

    std::ifstream ifs(file);
    if (!ifs)
        return false;

    std::string content((std::istreambuf_iterator<char>(ifs)), std::istreambuf_iterator<char>());
    nlohmann::json j = nlohmann::json::parse(content, nullptr, false);
    if (j.is_discarded() || !j.is_array())
        return false;

    for (const auto& entry : j)
    {
        using satellite::core::agent::AgentDescriptor;
        using satellite::core::agent::UNKNOWN_AGENT_ID;

        AgentDescriptor desc;
        desc.agent = nullptr;
        desc.id = entry.value("id", UNKNOWN_AGENT_ID);
        desc.name = entry.value("name", "");
        desc.description = entry.value("description", "");
        desc.version = entry.value("version", "1.0.0");
        desc.input_schema = entry.value("input_schema", nlohmann::json::object());
        desc.output_schema = entry.value("output_schema", nlohmann::json::object());
        desc.context_requirements = entry.value("context_requirements", std::vector<std::string>{});
        desc.capabilities = entry.value("capabilities", std::vector<std::string>{});

        if (!registry.register_agent(desc))
            continue;

        bool enabled = entry.value("enabled", true);
        if (!enabled)
            registry.disable_agent(desc.id);
    }
    return true;
}

std::size_t AgentStore::rebuild_agents(AgentRegistry& registry, AgentFactory& factory) const
{
    std::vector<AgentSpec> specs = load_specs();
    std::size_t count = 0;

    for (const auto& spec : specs)
    {
        bool exists = false;
        for (const auto& desc : registry.list_agents())
        {
            if (!spec.capabilities.empty() && !desc.capabilities.empty() && desc.capabilities[0] == spec.capabilities[0])
            {
                exists = true;
                break;
            }
        }
        if (exists)
            continue;

        auto result = factory.create_agent(spec);
        if (result.ok)
            ++count;
    }
    return count;
}

} // namespace satellite::persistence


namespace satellite::factory
{

void to_json(nlohmann::json& j, const AgentSpec& spec)
{
    j = nlohmann::json{
        {"id", spec.id},
        {"name", spec.name},
        {"description", spec.description},
        {"version", spec.version},
        {"input_schema", spec.input_schema},
        {"output_schema", spec.output_schema},
        {"context_requirements", spec.context_requirements},
        {"capabilities", spec.capabilities},
        {"implementation_code", spec.implementation_code},
        {"test_cases", nlohmann::json::array()}
    };
    for (const auto& tc : spec.test_cases)
    {
        j["test_cases"].push_back({{"input", tc.first}, {"expected", tc.second}});
    }
}

void from_json(const nlohmann::json& j, AgentSpec& spec)
{
    spec.id = j.value("id", UNKNOWN_AGENT_ID);
    spec.name = j.value("name", "");
    spec.description = j.value("description", "");
    spec.version = j.value("version", "1.0.0");
    spec.input_schema = j.value("input_schema", nlohmann::json::object());
    spec.output_schema = j.value("output_schema", nlohmann::json::object());
    spec.context_requirements = j.value("context_requirements", std::vector<std::string>{});
    spec.capabilities = j.value("capabilities", std::vector<std::string>{});
    spec.implementation_code = j.value("implementation_code", "");

    spec.test_cases.clear();
    if (j.contains("test_cases") && j["test_cases"].is_array())
    {
        for (const auto& tc : j["test_cases"])
        {
            nlohmann::json input = tc.value("input", nlohmann::json());
            nlohmann::json expected = tc.value("expected", nlohmann::json());
            spec.test_cases.emplace_back(input, expected);
        }
    }
}

} // namespace satellite::factory