// Test para persistence::ProjectInitializer (FASE 16)
// Convención: mini-framework CHECK (patrón test_agent_core.cpp)

#include <iostream>
#include <string>
#include <filesystem>
#include <fstream>

#include <json.hpp>
#include "persistence/ProjectInitializer.h"

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

void test_init_creates_structure()
{
    // Setup: temp directory with subdir "repo"
    std::filesystem::path tmp_base = std::filesystem::temp_directory_path() / ("satellite_init_test_" + std::to_string(std::chrono::steady_clock::now().time_since_epoch().count()));
    std::filesystem::create_directories(tmp_base / "repo");
    std::filesystem::path proj = tmp_base / "repo";

    std::string error;
    bool result = ProjectInitializer::init(proj, error);

    CHECK("init returns true", result);
    CHECK("error is empty", error.empty());

    // Verificar estructura .satellite
    std::filesystem::path sat = proj / ".satellite";
    CHECK(".satellite exists", std::filesystem::exists(sat) && std::filesystem::is_directory(sat));

    CHECK("config dir exists", std::filesystem::exists(sat / "config") && std::filesystem::is_directory(sat / "config"));
    CHECK("registry dir exists", std::filesystem::exists(sat / "registry") && std::filesystem::is_directory(sat / "registry"));
    CHECK("agents dir exists", std::filesystem::exists(sat / "agents") && std::filesystem::is_directory(sat / "agents"));
    CHECK("context dir exists", std::filesystem::exists(sat / "context") && std::filesystem::is_directory(sat / "context"));
    CHECK("executions dir exists", std::filesystem::exists(sat / "executions") && std::filesystem::is_directory(sat / "executions"));

    CHECK("README.txt exists", std::filesystem::exists(sat / "README.txt"));
    CHECK("config.json exists", std::filesystem::exists(sat / "config" / "config.json"));

    // Cleanup
    std::filesystem::remove_all(tmp_base);
}

void test_config_json_content()
{
    std::filesystem::path tmp_base = std::filesystem::temp_directory_path() / ("satellite_init_test_" + std::to_string(std::chrono::steady_clock::now().time_since_epoch().count()));
    std::filesystem::create_directories(tmp_base / "repo");
    std::filesystem::path proj = tmp_base / "repo";

    std::string error;
    ProjectInitializer::init(proj, error);

    std::filesystem::path config_path = proj / ".satellite" / "config" / "config.json";
    std::string content = read_file(config_path);
    CHECK("config.json readable", !content.empty());

    nlohmann::json j = nlohmann::json::parse(content);

    CHECK("project.name == \"repo\"", j["project"]["name"] == "repo");
    CHECK("llm.provider == \"deepseek\"", j["llm"]["provider"] == "deepseek");
    CHECK("llm.api_key_env == \"DEEPSEEK_API_KEY\"", j["llm"]["api_key_env"] == "DEEPSEEK_API_KEY");
    CHECK("token_budget.max_tokens == 4000", j["token_budget"]["max_tokens"] == 4000);
    CHECK("security.allow.filesystem.read == true", j["security"]["allow"]["filesystem.read"] == true);
    CHECK("security.allow.process.execute == false", j["security"]["allow"]["process.execute"] == false);
    CHECK("optimizer.algorithm == \"default\"", j["optimizer"]["algorithm"] == "default");

    std::filesystem::remove_all(tmp_base);
}

void test_registry_agents_json()
{
    std::filesystem::path tmp_base = std::filesystem::temp_directory_path() / ("satellite_init_test_" + std::to_string(std::chrono::steady_clock::now().time_since_epoch().count()));
    std::filesystem::create_directories(tmp_base / "repo");
    std::filesystem::path proj = tmp_base / "repo";

    std::string error;
    ProjectInitializer::init(proj, error);

    std::filesystem::path registry_path = proj / ".satellite" / "registry" / "agents.json";
    std::string content = read_file(registry_path);
    CHECK("agents.json readable", !content.empty());

    nlohmann::json arr = nlohmann::json::parse(content);
    CHECK("agents.json is array", arr.is_array());
    CHECK("array has 5 entries", arr.size() == 5);

    bool has_ids_1_to_5 = true;
    bool id1_name_sum = false;
    bool all_enabled = true;

    for (const auto& entry : arr)
    {
        int id = entry.value("id", -1);
        if (id >= 1 && id <= 5)
        {
            // check all present
        }
        else
        {
            has_ids_1_to_5 = false;
        }

        if (id == 1 && entry.value("name", "") == "sum")
        {
            id1_name_sum = true;
        }

        if (entry.value("enabled", true) != true)
        {
            all_enabled = false;
        }
    }

    CHECK("contains ids 1..5", has_ids_1_to_5);
    CHECK("entry id 1 has name \"sum\"", id1_name_sum);
    CHECK("all entries enabled == true", all_enabled);

    std::filesystem::remove_all(tmp_base);
}

void test_second_init_fails_and_preserves_config()
{
    std::filesystem::path tmp_base = std::filesystem::temp_directory_path() / ("satellite_init_test_" + std::to_string(std::chrono::steady_clock::now().time_since_epoch().count()));
    std::filesystem::create_directories(tmp_base / "repo");
    std::filesystem::path proj = tmp_base / "repo";

    std::string error;
    ProjectInitializer::init(proj, error);

    std::filesystem::path config_path = proj / ".satellite" / "config" / "config.json";
    std::string config_before = read_file(config_path);

    // Segundo init
    std::string error2;
    bool result2 = ProjectInitializer::init(proj, error2);

    CHECK("second init returns false", !result2);
    CHECK("error contains \"already\"", error2.find("already") != std::string::npos);

    std::string config_after = read_file(config_path);
    CHECK("config.json unchanged", config_before == config_after);

    std::filesystem::remove_all(tmp_base);
}

void test_init_with_existing_custom_config_preserves_content()
{
    std::filesystem::path tmp_base = std::filesystem::temp_directory_path() / ("satellite_init_test_" + std::to_string(std::chrono::steady_clock::now().time_since_epoch().count()));
    std::filesystem::create_directories(tmp_base / "repo");
    std::filesystem::path proj = tmp_base / "repo";

    // Crear .satellite/config/config.json personalizado ANTES de init
    std::filesystem::create_directories(proj / ".satellite" / "config");
    std::filesystem::path config_path = proj / ".satellite" / "config" / "config.json";
    {
        std::ofstream ofs(config_path, std::ios::trunc);
        ofs << "{\"custom\": true}";
    }
    std::string custom_content = read_file(config_path);
    CHECK("custom config created", custom_content == "{\"custom\": true}");

    // Intentar init
    std::string error;
    bool result = ProjectInitializer::init(proj, error);

    CHECK("init returns false", !result);
    CHECK("error contains \"already\"", error.find("already") != std::string::npos);

    std::string content_after = read_file(config_path);
    CHECK("custom content preserved", content_after == "{\"custom\": true}");

    std::filesystem::remove_all(tmp_base);
}

void test_project_config_path()
{
    std::filesystem::path tmp_base = std::filesystem::temp_directory_path() / ("satellite_init_test_" + std::to_string(std::chrono::steady_clock::now().time_since_epoch().count()));
    std::filesystem::create_directories(tmp_base / "repo");
    std::filesystem::path proj = tmp_base / "repo";

    std::filesystem::path config_path = ProjectInitializer::project_config_path(proj);

    CHECK("project_config_path ends with .satellite/config/config.json", 
          config_path.filename() == "config.json" &&
          config_path.parent_path().filename() == "config" &&
          config_path.parent_path().parent_path().filename() == ".satellite");

    std::filesystem::remove_all(tmp_base);
}

void test_nothing_created_outside_satellite()
{
    std::filesystem::path tmp_base = std::filesystem::temp_directory_path() / ("satellite_init_test_" + std::to_string(std::chrono::steady_clock::now().time_since_epoch().count()));
    std::filesystem::create_directories(tmp_base / "repo");
    std::filesystem::path proj = tmp_base / "repo";

    std::string error;
    ProjectInitializer::init(proj, error);

    // proj/agents.json no existe
    CHECK("proj/agents.json does not exist", !std::filesystem::exists(proj / "agents.json"));

    // tmp_base/satellite.json no existe
    CHECK("tmp_base/satellite.json does not exist", !std::filesystem::exists(tmp_base / "satellite.json"));

    std::filesystem::remove_all(tmp_base);
}

int main()
{
    test_init_creates_structure();
    test_config_json_content();
    test_registry_agents_json();
    test_second_init_fails_and_preserves_config();
    test_init_with_existing_custom_config_preserves_content();
    test_project_config_path();
    test_nothing_created_outside_satellite();

    std::cout << g_passed << " passed, " << g_failed << " failed\n";
    return g_failed == 0 ? 0 : 1;
}