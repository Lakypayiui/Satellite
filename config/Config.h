#pragma once

#include <json.hpp>
#include <filesystem>
#include <string>
#include <map>
#include <cstdint>

namespace satellite::config
{

struct FrameworkConfig
{
    std::string llm_provider = "deepseek";
    std::string llm_model = "deepseek-chat";
    std::string llm_api_key_env = "DEEPSEEK_API_KEY";
    std::string llm_base_url = "https://api.deepseek.com";
    std::uint64_t token_budget_max_tokens = 4000;
    std::string optimizer_algorithm = "default";
    std::string adapter_language = "auto";
    std::string agent_storage_dir = ".satellite";
    std::string logging_level = "info";
    std::map<std::string, bool> security_allow;

    void load_defaults();
    nlohmann::json to_json() const;
    bool merge_json(const nlohmann::json& j);
    static bool load_from_file(const std::filesystem::path& path, FrameworkConfig& out, std::string& error);
};

struct ProjectConfig
{
    std::string project_name;
    std::string llm_provider;
    std::string llm_model;
    std::string llm_api_key_env;
    std::string llm_base_url;
    std::uint64_t token_budget_max_tokens = 0;
    std::string optimizer_algorithm;
    std::string adapter_language;
    std::string logging_level;
    std::map<std::string, bool> security_allow;
    bool has_token_budget = false;

    nlohmann::json to_json() const;
    bool from_json(const nlohmann::json& j);
    static bool load_from_project(const std::filesystem::path& project_root, ProjectConfig& out, std::string& error);
    FrameworkConfig merged(const FrameworkConfig& global) const;
};

} // namespace satellite::config