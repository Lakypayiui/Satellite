// Test para config::FrameworkConfig y config::ProjectConfig (FASE 17)
// Convención: mini-framework CHECK (patrón test_agent_core.cpp)

#include <iostream>
#include <string>
#include <filesystem>
#include <fstream>
#include <chrono>

#include <json.hpp>
#include "config/Config.h"
#include "persistence/ProjectInitializer.h"

using namespace satellite::config;
using namespace satellite::persistence;
using namespace std::filesystem;

int g_passed = 0;
int g_failed = 0;

#define CHECK(desc, cond) \
    do { \
        if (cond) { \
            std::cout << "PASSED: " << desc << "\n"; \
            ++g_passed; \
        } else { \
            std::cout << "FAILED: " << desc << ": " #cond "\n"; \
            ++g_failed; \
        } \
    } while (false)

std::string read_file(const std::filesystem::path& p)
{
    std::ifstream ifs(p);
    if (!ifs)
        return "";
    return std::string((std::istreambuf_iterator<char>(ifs)), std::istreambuf_iterator<char>());
}

std::filesystem::path make_temp_project(const std::string& name = "repo")
{
    std::filesystem::path tmp_base = std::filesystem::temp_directory_path() / ("satellite_config_test_" + std::to_string(std::chrono::steady_clock::now().time_since_epoch().count()));
    std::filesystem::create_directories(tmp_base / name);
    return tmp_base / name;
}

void cleanup_temp(const std::filesystem::path& proj)
{
    std::filesystem::remove_all(proj.parent_path());
}

void test_load_defaults()
{
    FrameworkConfig f;
    f.load_defaults();

    CHECK("llm_provider == \"deepseek\"", f.llm_provider == "deepseek");
    CHECK("llm_model == \"deepseek-chat\"", f.llm_model == "deepseek-chat");
    CHECK("llm_api_key_env == \"DEEPSEEK_API_KEY\"", f.llm_api_key_env == "DEEPSEEK_API_KEY");
    CHECK("token_budget_max_tokens == 4000", f.token_budget_max_tokens == 4000);
    CHECK("optimizer_algorithm == \"default\"", f.optimizer_algorithm == "default");
    CHECK("adapter_language == \"auto\"", f.adapter_language == "auto");
    CHECK("agent_storage_dir == \".satellite\"", f.agent_storage_dir == ".satellite");
    CHECK("logging_level == \"info\"", f.logging_level == "info");

    CHECK("security_allow[filesystem.read] == true", f.security_allow["filesystem.read"] == true);
    CHECK("security_allow[filesystem.write] == false", f.security_allow["filesystem.write"] == false);
    CHECK("security_allow[process.execute] == false", f.security_allow["process.execute"] == false);
    CHECK("security_allow[compiler.execute] == false", f.security_allow["compiler.execute"] == false);
    CHECK("security_allow[network.request] == false", f.security_allow["network.request"] == false);
    CHECK("security_allow size == 5", f.security_allow.size() == 5);
}

void test_roundtrip()
{
    FrameworkConfig f;
    f.load_defaults();

    nlohmann::json j = f.to_json();

    FrameworkConfig f2;
    f2.load_defaults();
    f2.merge_json(j);

    CHECK("llm_provider preserved", f2.llm_provider == f.llm_provider);
    CHECK("token_budget_max_tokens == 4000", f2.token_budget_max_tokens == 4000);
    CHECK("security_allow size preserved", f2.security_allow.size() == f.security_allow.size());
    CHECK("security_allow values preserved", f2.security_allow == f.security_allow);
}

void test_partial_merge()
{
    FrameworkConfig g;
    g.load_defaults();

    nlohmann::json patch = {
        {"llm", {{"model", "mi-modelo"}}},
        {"token_budget", {{"max_tokens", 2048}}}
    };
    g.merge_json(patch);

    CHECK("llm_model == \"mi-modelo\"", g.llm_model == "mi-modelo");
    CHECK("llm_provider unchanged", g.llm_provider == "deepseek");
    CHECK("token_budget_max_tokens == 2048", g.token_budget_max_tokens == 2048);
    CHECK("security_allow size unchanged (5)", g.security_allow.size() == 5);
    CHECK("security_allow[filesystem.read] still true", g.security_allow["filesystem.read"] == true);
    CHECK("security_allow[filesystem.write] still false", g.security_allow["filesystem.write"] == false);
}

void test_real_project_init()
{
    std::filesystem::path proj = make_temp_project();
    std::string error;

    bool result = ProjectInitializer::init(proj, error);
    CHECK("ProjectInitializer::init returns true", result);
    CHECK("error empty", error.empty());

    // Via from_json reading the created config.json
    std::filesystem::path config_path = proj / ".satellite" / "config" / "config.json";
    std::string content = read_file(config_path);
    CHECK("config.json readable", !content.empty());

    nlohmann::json j = nlohmann::json::parse(content);
    ProjectConfig pc;
    bool parsed = pc.from_json(j);
    CHECK("from_json returns true", parsed);
    CHECK("project_name == \"repo\"", pc.project_name == "repo");
    CHECK("llm_provider == \"deepseek\"", pc.llm_provider == "deepseek");
    CHECK("token_budget_max_tokens == 4000", pc.token_budget_max_tokens == 4000);
    CHECK("has_token_budget == true", pc.has_token_budget);

    // Via load_from_project
    ProjectConfig pc2;
    error.clear();
    bool loaded = ProjectConfig::load_from_project(proj, pc2, error);
    CHECK("load_from_project returns true", loaded);
    CHECK("error empty", error.empty());
    CHECK("pc2.project_name == \"repo\"", pc2.project_name == "repo");

    cleanup_temp(proj);
}

void test_merged_config()
{
    FrameworkConfig glob;
    glob.load_defaults();
    glob.llm_model = "modelo-global";

    ProjectConfig proj_cfg;
    proj_cfg.llm_model = "modelo-proyecto";
    proj_cfg.llm_provider = "openai";

    FrameworkConfig m = proj_cfg.merged(glob);

    CHECK("merged.llm_model == \"modelo-proyecto\" (proyecto gana)", m.llm_model == "modelo-proyecto");
    CHECK("merged.llm_provider == \"openai\" (proyecto gana)", m.llm_provider == "openai");
    CHECK("merged.token_budget_max_tokens == 4000 (global, proyecto no definió)", m.token_budget_max_tokens == 4000);
    CHECK("merged.optimizer_algorithm == \"default\" (global)", m.optimizer_algorithm == "default");
}

void test_load_from_project_missing()
{
    std::filesystem::path proj = make_temp_project();
    // No llamar a init, así no hay .satellite

    ProjectConfig pc;
    std::string error;
    bool loaded = ProjectConfig::load_from_project(proj, pc, error);

    CHECK("load_from_project returns false", !loaded);
    CHECK("error not empty", !error.empty());

    cleanup_temp(proj);
}

void test_load_from_file_missing()
{
    FrameworkConfig out;
    std::string error;
    std::filesystem::path missing = std::filesystem::temp_directory_path() / "nonexistent_config_12345.json";

    bool loaded = FrameworkConfig::load_from_file(missing, out, error);

    CHECK("load_from_file returns false", !loaded);
    CHECK("error not empty", !error.empty());
}

int main()
{
    test_load_defaults();
    test_roundtrip();
    test_partial_merge();
    test_real_project_init();
    test_merged_config();
    test_load_from_project_missing();
    test_load_from_file_missing();

    std::cout << g_passed << " passed, " << g_failed << " failed\n";
    return g_failed == 0 ? 0 : 1;
}