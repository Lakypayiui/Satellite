#include "persistence/ProjectInitializer.h"

#include "persistence/AgentStore.h"
#include "core/agents/NativeAgents.h"
#include "core/registry/AgentRegistry.h"

#include <json.hpp>
#include <fstream>
#include <filesystem>

namespace satellite::persistence
{

bool ProjectInitializer::init(const std::filesystem::path& project_root, std::string& error)
{
    AgentStore store(project_root);

    if (store.has_state())
    {
        error = "project already initialized (.satellite exists)";
        return false;
    }

    if (!store.ensure_dirs())
    {
        error = "failed to create .satellite directories";
        return false;
    }

    // Escribir config.json por defecto
    nlohmann::json config;
    config["project"]["name"] = project_root.filename().string();
    config["llm"]["provider"] = "deepseek";
    config["llm"]["model"] = "deepseek-chat";
    config["llm"]["api_key_env"] = "DEEPSEEK_API_KEY";
    config["token_budget"]["max_tokens"] = 4000;
    config["optimizer"]["algorithm"] = "default";
    config["adapter"]["language"] = "auto";
    config["execution"]["backend"] = "native_process";
    config["security"]["allow"]["filesystem.read"] = true;
    config["security"]["allow"]["filesystem.write"] = true;
    config["security"]["allow"]["process.execute"] = true;
    config["security"]["allow"]["compiler.execute"] = true;
    config["security"]["allow"]["network.request"] = true;
    config["logging"]["level"] = "info";

    std::filesystem::path config_path = store.root() / "config" / "config.json";
    std::ofstream config_ofs(config_path, std::ios::trunc);
    if (!config_ofs)
    {
        error = "failed to write config.json";
        return false;
    }
    config_ofs << config.dump(2);

    // Registrar agentes nativos en el catálogo persistente
    satellite::core::registry::AgentRegistry reg;
    satellite::core::agents::register_native_agents(reg);
    if (!store.save_registry(reg))
    {
        error = "failed to save agent registry";
        return false;
    }

    // Escribir README.txt
    std::filesystem::path readme_path = store.root() / "README.txt";
    std::ofstream readme_ofs(readme_path, std::ios::trunc);
    if (!readme_ofs)
    {
        error = "failed to write README.txt";
        return false;
    }
    readme_ofs
        << "Este directorio pertenece al proyecto consumidor y es gestionado por el framework Satellite.\n"
        << "No editar a mano. Borrarlo elimina el estado de agentes del proyecto.\n";

    return true;
}

std::filesystem::path ProjectInitializer::project_config_path(const std::filesystem::path& project_root)
{
    return project_root / ".satellite" / "config" / "config.json";
}

} // namespace satellite::persistence