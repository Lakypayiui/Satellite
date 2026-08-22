// Catálogo compacto de capacidades para el LLM (Fase 7).

#include "core/catalog/AgentCatalog.h"

#include <algorithm>
#include <sstream>

namespace satellite::core::catalog
{

AgentCatalog::AgentCatalog(const AgentRegistry& registry)
    : registry_(registry)
{
}

std::size_t AgentCatalog::size() const
{
    std::size_t count = 0;
    const auto agents = registry_.list_agents();
    for (const auto& desc : agents)
    {
        if (registry_.is_enabled(desc.id))
        {
            ++count;
        }
    }
    return count;
}

nlohmann::json AgentCatalog::to_json() const
{
    nlohmann::json result = nlohmann::json::array();
    const auto agents = registry_.list_agents();

    for (const auto& desc : agents)
    {
        if (!registry_.is_enabled(desc.id))
        {
            continue;
        }

        nlohmann::json entry;
        entry["id"] = desc.id;
        entry["name"] = desc.name;
        entry["description"] = desc.description;
        entry["version"] = desc.version;
        entry["input_schema"] = desc.input_schema;
        entry["output_schema"] = desc.output_schema;
        entry["context_requirements"] = desc.context_requirements;
        entry["capabilities"] = desc.capabilities;

        result.push_back(entry);
    }

    return result;
}

std::string AgentCatalog::to_prompt() const
{
    std::ostringstream oss;
    const auto agents = registry_.list_agents();

    for (const auto& desc : agents)
    {
        if (!registry_.is_enabled(desc.id))
        {
            continue;
        }

        oss << '[' << desc.id << "] " << desc.name << " — " 
            << truncate_description(desc.description);

        if (!desc.capabilities.empty())
        {
            oss << " [";
            for (std::size_t i = 0; i < desc.capabilities.size(); ++i)
            {
                if (i > 0)
                {
                    oss << ",";
                }
                oss << desc.capabilities[i];
            }
            oss << "]";
        }

        oss << " in:" << compact_schema(desc.input_schema)
            << " out:" << compact_schema(desc.output_schema)
            << '\n';
    }

    return oss.str();
}

std::string AgentCatalog::describe_agent(AgentID id) const
{
    const auto* desc = registry_.find_agent(id);
    if (!desc || !registry_.is_enabled(id))
    {
        return "(no disponible)";
    }

    std::ostringstream oss;
    oss << '[' << desc->id << "] " << desc->name << " — " 
        << truncate_description(desc->description);

    if (!desc->capabilities.empty())
    {
        oss << " [";
        for (std::size_t i = 0; i < desc->capabilities.size(); ++i)
        {
            if (i > 0)
            {
                oss << ",";
            }
            oss << desc->capabilities[i];
        }
        oss << "]";
    }

    oss << " in:" << compact_schema(desc->input_schema)
        << " out:" << compact_schema(desc->output_schema);

    return oss.str();
}

std::string AgentCatalog::compact_schema(const nlohmann::json& schema)
{
    if (!schema.is_object())
    {
        return schema.dump();
    }

    if (!schema.contains("properties") || !schema["properties"].is_object())
    {
        return schema.dump();
    }

    std::ostringstream oss;
    bool first = true;
    for (const auto& [key, value] : schema["properties"].items())
    {
        if (!first)
        {
            oss << ",";
        }
        first = false;

        oss << key << ":";

        if (value.is_object() && value.contains("type"))
        {
            oss << value["type"];
        }
        else if (value.is_string())
        {
            oss << value;
        }
        else
        {
            oss << value.dump();
        }
    }

    return oss.str();
}

std::string AgentCatalog::truncate_description(const std::string& desc, std::size_t max_len)
{
    if (desc.length() <= max_len)
    {
        return desc;
    }
    return desc.substr(0, max_len - 3) + "...";
}

} // namespace satellite::core::catalog