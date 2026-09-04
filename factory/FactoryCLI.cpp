#include "factory/AgentExpander.h"
#include "core/catalog/AgentCatalog.h"
#include "core/registry/AgentRegistry.h"
#include "llm/DeepSeekProvider.h"

#include <json.hpp>

#include <cstdlib>
#include <filesystem>
#include <iostream>
#include <string>

namespace
{

#ifndef SATELLITE_ROOT
#define SATELLITE_ROOT "."
#endif

std::string library_filename(satellite::core::agent::AgentID id)
{
#ifdef _WIN32
    return "agent_" + std::to_string(id) + ".dll";
#else
    return "libagent_" + std::to_string(id) + ".so";
#endif
}

nlohmann::json descriptor_json(const satellite::core::agent::AgentDescriptor& descriptor,
                               const std::filesystem::path& library_path)
{
    return {
        {"id", descriptor.id},
        {"name", descriptor.name},
        {"description", descriptor.description},
        {"version", descriptor.version},
        {"input_schema", descriptor.input_schema},
        {"output_schema", descriptor.output_schema},
        {"context_requirements", descriptor.context_requirements},
        {"capabilities", descriptor.capabilities},
        {"library_path", std::filesystem::absolute(library_path).string()}
    };
}

} // namespace

int main()
{
    nlohmann::json request;
    try
    {
        std::string input;
        if (!std::getline(std::cin, input))
        {
            std::cout << nlohmann::json{{"ok", false}, {"error", "missing request"}}.dump() << '\n';
            return 1;
        }
        request = nlohmann::json::parse(input);
    }
    catch (const std::exception& error)
    {
        std::cout << nlohmann::json{{"ok", false}, {"error", error.what()}}.dump() << '\n';
        return 1;
    }

    const std::string goal = request.value("goal", std::string{});
    const std::string capability = request.value("capability", std::string{});
    if (goal.empty() || capability.empty())
    {
        std::cout << nlohmann::json{
            {"ok", false},
            {"error", "request requires non-empty goal and capability"}
        }.dump() << '\n';
        return 1;
    }

    const std::filesystem::path project_root = std::filesystem::current_path();
    const std::filesystem::path work_dir = project_root / ".satellite" / "agents" / "work";
    std::error_code filesystem_error;
    std::filesystem::create_directories(work_dir, filesystem_error);
    if (filesystem_error)
    {
        std::cout << nlohmann::json{
            {"ok", false},
            {"error", "failed to create factory work directory: " + filesystem_error.message()}
        }.dump() << '\n';
        return 1;
    }

    const char* api_key = std::getenv("DEEPSEEK_API_KEY");
    if (api_key == nullptr || std::string(api_key).empty())
    {
        std::cout << nlohmann::json{
            {"ok", false},
            {"error", "DEEPSEEK_API_KEY is not configured"}
        }.dump() << '\n';
        return 1;
    }

    satellite::core::registry::AgentRegistry registry;
    satellite::core::catalog::AgentCatalog catalog(registry);
    satellite::llm::DeepSeekProvider provider(api_key);
    satellite::factory::AgentFactory factory(
        registry, work_dir, std::filesystem::path(SATELLITE_ROOT), "g++");
    satellite::factory::AgentExpander expander(registry, catalog, factory, provider);

    std::string error;
    const satellite::factory::ExpansionResult expansion = expander.expand(
        goal + " " + capability, error);
    if (!expansion.ok || expansion.created.empty())
    {
        nlohmann::json failures = nlohmann::json::array();
        for (const auto& failure : expansion.failed)
        {
            failures.push_back({{"capability", failure.first}, {"error", failure.second}});
        }
        std::cout << nlohmann::json{
            {"ok", false},
            {"error", error},
            {"created", expansion.created},
            {"skipped", expansion.skipped},
            {"failed", failures}
        }.dump() << '\n';
        return 1;
    }

    const satellite::core::agent::AgentID created_id = expansion.created.front();
    const auto descriptor = registry.find_agent(created_id);
    if (!descriptor)
    {
        std::cout << nlohmann::json{
            {"ok", false},
            {"error", "created agent descriptor not found"}
        }.dump() << '\n';
        return 1;
    }

    const std::filesystem::path library_path = work_dir / library_filename(created_id);
    std::cout << nlohmann::json{
        {"ok", true},
        {"descriptor", descriptor_json(*descriptor, library_path)},
        {"created", expansion.created},
        {"skipped", expansion.skipped}
    }.dump() << '\n';
    return 0;
}
