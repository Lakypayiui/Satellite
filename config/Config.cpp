#include "config/Config.h"

#include <json.hpp>
#include <fstream>
#include <filesystem>

namespace satellite::config
{

void FrameworkConfig::load_defaults()
{
    llm_provider = "deepseek";
    llm_model = "deepseek-chat";
    llm_api_key_env = "DEEPSEEK_API_KEY";
    llm_api_key = "";
    llm_base_url = "https://api.deepseek.com";
    token_budget_max_tokens = 4000;
    optimizer_algorithm = "default";
    adapter_language = "auto";
    agent_storage_dir = ".satellite";
    logging_level = "info";

    security_allow.clear();
    security_allow["filesystem.read"] = true;
    security_allow["filesystem.write"] = false;
    security_allow["process.execute"] = false;
    security_allow["compiler.execute"] = false;
    security_allow["network.request"] = false;
}

nlohmann::json FrameworkConfig::to_json() const
{
    nlohmann::json j;
    j["llm"]["provider"] = llm_provider;
    j["llm"]["model"] = llm_model;
    j["llm"]["api_key"] = llm_api_key;
    j["llm"]["api_key_env"] = llm_api_key_env;
    j["llm"]["base_url"] = llm_base_url;
    j["token_budget"]["max_tokens"] = token_budget_max_tokens;
    j["optimizer"]["algorithm"] = optimizer_algorithm;
    j["adapter"]["language"] = adapter_language;
    j["storage"]["agent_dir"] = agent_storage_dir;
    j["logging"]["level"] = logging_level;
    for (const auto& [cap, allowed] : security_allow)
    {
        j["security"]["allow"][cap] = allowed;
    }
    return j;
}

bool FrameworkConfig::merge_json(const nlohmann::json& j)
{
    if (j.contains("llm"))
    {
        const auto& llm = j["llm"];
        if (llm.contains("provider") && !llm["provider"].get<std::string>().empty())
            llm_provider = llm["provider"].get<std::string>();
        if (llm.contains("model") && !llm["model"].get<std::string>().empty())
            llm_model = llm["model"].get<std::string>();
        if (llm.contains("api_key") && !llm["api_key"].get<std::string>().empty())
            llm_api_key = llm["api_key"].get<std::string>();
        if (llm.contains("api_key_env") && !llm["api_key_env"].get<std::string>().empty())
            llm_api_key_env = llm["api_key_env"].get<std::string>();
        if (llm.contains("base_url") && !llm["base_url"].get<std::string>().empty())
            llm_base_url = llm["base_url"].get<std::string>();
    }

    if (j.contains("token_budget") && j["token_budget"].contains("max_tokens"))
    {
        std::uint64_t val = j["token_budget"]["max_tokens"].get<std::uint64_t>();
        if (val > 0)
            token_budget_max_tokens = val;
    }

    if (j.contains("optimizer") && j["optimizer"].contains("algorithm"))
    {
        std::string val = j["optimizer"]["algorithm"].get<std::string>();
        if (!val.empty())
            optimizer_algorithm = val;
    }

    if (j.contains("adapter") && j["adapter"].contains("language"))
    {
        std::string val = j["adapter"]["language"].get<std::string>();
        if (!val.empty())
            adapter_language = val;
    }

    if (j.contains("storage") && j["storage"].contains("agent_dir"))
    {
        std::string val = j["storage"]["agent_dir"].get<std::string>();
        if (!val.empty())
            agent_storage_dir = val;
    }

    if (j.contains("logging") && j["logging"].contains("level"))
    {
        std::string val = j["logging"]["level"].get<std::string>();
        if (!val.empty())
            logging_level = val;
    }

    if (j.contains("security") && j["security"].contains("allow"))
    {
        const auto& allow = j["security"]["allow"];
        for (auto it = allow.begin(); it != allow.end(); ++it)
        {
            security_allow[it.key()] = it.value().get<bool>();
        }
    }

    return true;
}

bool FrameworkConfig::load_from_file(const std::filesystem::path& path, FrameworkConfig& out, std::string& error)
{
    std::ifstream ifs(path);
    if (!ifs)
    {
        error = "failed to open config file: " + path.string();
        return false;
    }

    nlohmann::json j;
    try
    {
        ifs >> j;
    }
    catch (const std::exception& e)
    {
        error = "invalid config json: " + std::string(e.what());
        return false;
    }

    if (j.is_discarded())
    {
        error = "invalid config json";
        return false;
    }

    out.merge_json(j);
    return true;
}

nlohmann::json ProjectConfig::to_json() const
{
    nlohmann::json j;
    if (!project_name.empty())
        j["project"]["name"] = project_name;
    if (!llm_provider.empty())
        j["llm"]["provider"] = llm_provider;
    if (!llm_model.empty())
        j["llm"]["model"] = llm_model;
    if (!llm_api_key_env.empty())
        j["llm"]["api_key_env"] = llm_api_key_env;
    if (!llm_base_url.empty())
        j["llm"]["base_url"] = llm_base_url;
    if (has_token_budget && token_budget_max_tokens > 0)
        j["token_budget"]["max_tokens"] = token_budget_max_tokens;
    if (!optimizer_algorithm.empty())
        j["optimizer"]["algorithm"] = optimizer_algorithm;
    if (!adapter_language.empty())
        j["adapter"]["language"] = adapter_language;
    if (!logging_level.empty())
        j["logging"]["level"] = logging_level;
    for (const auto& [cap, allowed] : security_allow)
    {
        j["security"]["allow"][cap] = allowed;
    }
    return j;
}

bool ProjectConfig::from_json(const nlohmann::json& j)
{
    if (j.contains("project") && j["project"].contains("name"))
        project_name = j["project"]["name"].get<std::string>();

    if (j.contains("llm"))
    {
        const auto& llm = j["llm"];
        if (llm.contains("provider"))
            llm_provider = llm["provider"].get<std::string>();
        if (llm.contains("model"))
            llm_model = llm["model"].get<std::string>();
        if (llm.contains("api_key_env"))
            llm_api_key_env = llm["api_key_env"].get<std::string>();
        if (llm.contains("base_url"))
            llm_base_url = llm["base_url"].get<std::string>();
    }

    if (j.contains("token_budget") && j["token_budget"].contains("max_tokens"))
    {
        token_budget_max_tokens = j["token_budget"]["max_tokens"].get<std::uint64_t>();
        has_token_budget = true;
    }

    if (j.contains("optimizer") && j["optimizer"].contains("algorithm"))
        optimizer_algorithm = j["optimizer"]["algorithm"].get<std::string>();

    if (j.contains("adapter") && j["adapter"].contains("language"))
        adapter_language = j["adapter"]["language"].get<std::string>();

    if (j.contains("logging") && j["logging"].contains("level"))
        logging_level = j["logging"]["level"].get<std::string>();

    if (j.contains("security") && j["security"].contains("allow"))
    {
        const auto& allow = j["security"]["allow"];
        for (auto it = allow.begin(); it != allow.end(); ++it)
        {
            security_allow[it.key()] = it.value().get<bool>();
        }
    }

    return true;
}

bool ProjectConfig::load_from_project(const std::filesystem::path& project_root, ProjectConfig& out, std::string& error)
{
    std::filesystem::path config_path = project_root / ".satellite" / "config" / "config.json";

    if (!std::filesystem::exists(config_path))
    {
        error = "project not initialized or config missing";
        return false;
    }

    std::ifstream ifs(config_path);
    if (!ifs)
    {
        error = "failed to open project config: " + config_path.string();
        return false;
    }

    nlohmann::json j;
    try
    {
        ifs >> j;
    }
    catch (const std::exception& e)
    {
        error = "invalid project config json: " + std::string(e.what());
        return false;
    }

    if (j.is_discarded())
    {
        error = "invalid project config json";
        return false;
    }

    out.from_json(j);
    return true;
}

FrameworkConfig ProjectConfig::merged(const FrameworkConfig& global) const
{
    FrameworkConfig result = global;

    if (!project_name.empty())
    {
    }

    if (!llm_provider.empty())
        result.llm_provider = llm_provider;
    if (!llm_model.empty())
        result.llm_model = llm_model;
    if (!llm_api_key_env.empty())
        result.llm_api_key_env = llm_api_key_env;
    if (!llm_base_url.empty())
        result.llm_base_url = llm_base_url;

    if (has_token_budget && token_budget_max_tokens > 0)
        result.token_budget_max_tokens = token_budget_max_tokens;

    if (!optimizer_algorithm.empty())
        result.optimizer_algorithm = optimizer_algorithm;
    if (!adapter_language.empty())
        result.adapter_language = adapter_language;
    if (!logging_level.empty())
        result.logging_level = logging_level;

    for (const auto& [cap, allowed] : security_allow)
    {
        result.security_allow[cap] = allowed;
    }

    return result;
}

} // namespace satellite::config