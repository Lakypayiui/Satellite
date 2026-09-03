#include <map>
#include <set>
// Implementación de la CLI del framework Satellite (FASE 20 parte A + B).
// La CLI usa el framework como BIBLIOTECA (no duplica lógica).

#include "cli/SatelliteCLI.h"

#include <iostream>
#include <string>
#include <vector>
#include <cstdlib>
#include <fstream>
#include <sstream>
#include <cstring>
#include "persistence/ProjectInitializer.h"
#include "persistence/AgentStore.h"
#include "core/registry/AgentRegistry.h"
#include "core/agents/NativeAgents.h"
#include "core/dispatcher/Dispatcher.h"
#include "core/agent/AgentRequest.h"
#include "core/agent/AgentResult.h"
#include "core/agent/AgentStatus.h"
#include "config/Config.h"
#include "security/SecurityPolicy.h"
#include "context/adapter/ProjectAdapter.h"
#include "context/engine/ProjectContext.h"
#include "context/engine/ProjectIndex.h"
#include "context/engine/SemanticContext.h"
#include "factory/AgentFactory.h"
#include "core/catalog/AgentCatalog.h"
#include "orchestrator/Orchestrator.h"
#include "llm/ProviderFactory.h"
#include "context/LocalPreprocessor.h"
#include "llm/LocalLLMProvider.h"
#include "context/optimizer/ContextOptimizer.h"
#include <json.hpp>

namespace satellite::cli
{


SatelliteCLI::SatelliteCLI(std::filesystem::path project_root, std::filesystem::path framework_root)
    : project_root_(std::move(project_root))
    , framework_root_(std::move(framework_root))
{
}

int SatelliteCLI::run(int argc, char* argv[])
{
    if (argc < 2)
    {
        std::cout << "Uso: satellite <comando> [args...]\n";
        std::cout << "Comandos:\n";
        std::cout << "  init                         Inicializa el proyecto en el directorio actual\n";
        std::cout << "  agents                       Lista todos los agentes registrados\n";
        std::cout << "  agent info <id>              Muestra información detallada de un agente\n";
        std::cout << "  agent enable <id>            Habilita un agente\n";
        std::cout << "  agent disable <id>           Deshabilita un agente\n";
        std::cout << "  doctor                       Ejecuta diagnósticos del entorno\n";
        std::cout << "  context build                Construye el contexto del proyecto (indice con mtimes)\n";
        std::cout << "  context get --paths <archivos>  Contexto semantico bajo demanda\n";
        std::cout << "  context inspect              Inspecciona el contexto guardado\n";
        std::cout << "  agent create <spec.json>     Crea un agente desde spec\n";
        std::cout << "  agent test <id> [input.json] Prueba un agente\n";
        std::cout << "  run <objetivo>               Ejecuta el orquestador con un objetivo\n";
        return 1;
    }

    std::string subcommand = argv[1];

    if (subcommand == "init")
    {
        return cmd_init(argc, argv);
    }
    else if (subcommand == "agents")
    {
        return cmd_agents();
    }
    else if (subcommand == "agent")
    {
        if (argc < 3)
        {
            std::cout << "Uso: satellite agent info|enable|disable|create|test <id|spec.json> [args...]\n";
            return 1;
        }
        std::string agent_sub = argv[2];
        if (agent_sub == "info")
        {
            return cmd_agent_info(argc, argv);
        }
        else if (agent_sub == "enable")
        {
            return cmd_agent_enable_disable(argc, argv, true);
        }
        else if (agent_sub == "disable")
        {
            return cmd_agent_enable_disable(argc, argv, false);
        }
        else if (agent_sub == "create")
        {
            return cmd_agent_create(argc, argv);
        }
        else if (agent_sub == "test")
        {
            return cmd_agent_test(argc, argv);
        }
        else
        {
            std::cout << "Subcomando desconocido: " << agent_sub << "\n";
            std::cout << "Uso: satellite agent info|enable|disable|create|test ...\n";
            return 1;
        }
    }
    else if (subcommand == "context")
    {
        if (argc < 3)
        {
            std::cout << "Uso: satellite context build|inspect\n";
            return 1;
        }
        std::string ctx_sub = argv[2];
        if (ctx_sub == "build")
        {
            return cmd_context_build();
        }
        else if (ctx_sub == "get")
        {
            return cmd_context_get(argc - 3, argv + 3);
        }
        else if (ctx_sub == "inspect")
        {
            return cmd_context_inspect();
        }
        else
        {
            std::cout << "Subcomando desconocido: " << ctx_sub << "\n";
            std::cout << "Uso: satellite context build|get|inspect\n";
            return 1;
        }
    }
    else if (subcommand == "run")
    {
        return cmd_run(argc, argv);
    }
    else if (subcommand == "doctor")
    {
        return cmd_doctor();
    }
    else if (subcommand == "version" || subcommand == "--version" || subcommand == "-v")
    {
        return cmd_version();
    }
    else
    {
        std::cout << "Comando desconocido: " << subcommand << "\n";
        std::cout << "Comandos: init, agents, agent info|enable|disable|create|test, context build|inspect, run, doctor\n";
        return 1;
    }
}

int SatelliteCLI::cmd_init(int argc, char* argv[])
{
    (void)argc;
    (void)argv;
    std::string err;
    if (satellite::persistence::ProjectInitializer::init(project_root_, err))
    {
        std::cout << "Proyecto inicializado en " << project_root_ << "\n";
        return 0;
    }
    else
    {
        std::cout << "Error: " << err << "\n";
        return 1;
    }
}

int SatelliteCLI::cmd_agents()
{
    satellite::persistence::AgentStore store(project_root_);
    if (!store.has_state())
    {
        std::cout << "Error: proyecto no inicializado. Ejecuta: satellite init\n";
        return 1;
    }

    satellite::core::registry::AgentRegistry registry;
    satellite::core::agents::register_native_agents(registry);
    store.load_registry(registry);

    std::cout << "ID  NOMBRE  CAPACIDADES  HABILITADO\n";
    auto agents = registry.list_agents();
    for (const auto& agent : agents)
    {
        std::string caps;
        for (size_t i = 0; i < agent.capabilities.size(); ++i)
        {
            if (i > 0) caps += ",";
            caps += agent.capabilities[i];
        }
        std::cout << agent.id << "  " << agent.name << "  " << caps << "  " << (registry.is_enabled(agent.id) ? "si" : "no") << "\n";
    }
    return 0;
}

int SatelliteCLI::cmd_agent_info(int argc, char* argv[])
{
    if (argc < 4)
    {
        std::cout << "Uso: satellite agent info <id>\n";
        return 1;
    }

    satellite::core::agent::AgentID id = static_cast<satellite::core::agent::AgentID>(std::stoul(argv[3]));

    satellite::core::registry::AgentRegistry registry;
    satellite::core::agents::register_native_agents(registry);
    satellite::persistence::AgentStore store(project_root_);
    store.load_registry(registry);

    const satellite::core::agent::AgentDescriptor* d = registry.find_agent(id);
    if (!d)
    {
        std::cout << "Agente " << id << " no existe\n";
        return 1;
    }

    std::cout << "id: " << d->id << "\n";
    std::cout << "name: " << d->name << "\n";
    std::cout << "description: " << d->description << "\n";
    std::cout << "version: " << d->version << "\n";
    std::cout << "input_schema: " << d->input_schema.dump(2) << "\n";
    std::cout << "output_schema: " << d->output_schema.dump(2) << "\n";
    std::cout << "capabilities: ";
    for (size_t i = 0; i < d->capabilities.size(); ++i)
    {
        if (i > 0) std::cout << ", ";
        std::cout << d->capabilities[i];
    }
    std::cout << "\n";
    std::cout << "context_requirements: ";
    for (size_t i = 0; i < d->context_requirements.size(); ++i)
    {
        if (i > 0) std::cout << ", ";
        std::cout << d->context_requirements[i];
    }
    std::cout << "\n";
    std::cout << "enabled: " << (registry.is_enabled(d->id) ? "si" : "no") << "\n";
    return 0;
}

int SatelliteCLI::cmd_agent_enable_disable(int argc, char* argv[], bool enable)
{
    if (argc < 4)
    {
        std::cout << "Uso: satellite agent " << (enable ? "enable" : "disable") << " <id>\n";
        return 1;
    }

    satellite::core::agent::AgentID id = static_cast<satellite::core::agent::AgentID>(std::stoul(argv[3]));

    satellite::core::registry::AgentRegistry registry;
    satellite::core::agents::register_native_agents(registry);
    satellite::persistence::AgentStore store(project_root_);
    store.load_registry(registry);

    if (!registry.has_agent(id))
    {
        std::cout << "Agente " << id << " no existe\n";
        return 1;
    }

    if (enable)
    {
        registry.enable_agent(id);
    }
    else
    {
        registry.disable_agent(id);
    }

    if (store.save_registry(registry))
    {
        std::cout << "Agente " << id << " " << (enable ? "habilitado" : "deshabilitado") << "\n";
        return 0;
    }
    else
    {
        std::cout << "Error: no se pudo guardar el registro\n";
        return 1;
    }
}

int SatelliteCLI::cmd_version()
{
    std::cout << "satellite " << SATELLITE_VERSION << "\n";
    return 0;
}

int SatelliteCLI::cmd_doctor()
{
    int failures = 0;

    // 1. Compilador
    std::cout << "[";
    int compiler_check = system("g++ --version >nul 2>&1");
    if (compiler_check == 0)
    {
        std::cout << "OK] compilador g++\n";
    }
    else
    {
        std::cout << "FAIL] compilador g++\n";
        failures++;
    }

    // 2. Proyecto inicializado
    satellite::persistence::AgentStore store(project_root_);
    std::cout << "[";
    if (store.has_state())
    {
        std::cout << "OK] proyecto inicializado\n";
    }
    else
    {
        std::cout << "FAIL] proyecto inicializado\n";
        failures++;
    }

    // 3. Config del proyecto parseable
    satellite::config::ProjectConfig pc;
    std::string err;
    std::cout << "[";
    if (satellite::config::ProjectConfig::load_from_project(project_root_, pc, err))
    {
        std::cout << "OK] config del proyecto parseable\n";
    }
    else
    {
        std::cout << "FAIL] config del proyecto parseable\n";
        failures++;
    }

    // 4. Registry cargable
    satellite::core::registry::AgentRegistry temp_registry;
    satellite::core::agents::register_native_agents(temp_registry);
    satellite::persistence::AgentStore temp_store(project_root_);
    std::cout << "[";
    if (temp_store.load_registry(temp_registry) && temp_registry.list_agents().size() > 0)
    {
        std::cout << "OK] registry cargable\n";
    }
    else
    {
        std::cout << "FAIL] registry cargable\n";
        failures++;
    }

    // 5. Agente de prueba (sum)
    satellite::core::registry::AgentRegistry test_registry;
    satellite::core::agents::register_native_agents(test_registry);
    satellite::core::dispatcher::Dispatcher dispatcher(test_registry);
    satellite::core::agent::AgentRequest request;
    request.agent_id = 1; // sum agent
    request.input = nlohmann::json{{"a", 1}, {"b", 2}};
    satellite::core::agent::AgentResult result = dispatcher.dispatch(request);
    std::cout << "[";
    if (result.status == satellite::core::agent::AgentStatus::SUCCESS)
    {
        std::cout << "OK] agente de prueba\n";
    }
    else
    {
        std::cout << "FAIL] agente de prueba\n";
        failures++;
    }

    if (failures == 0)
    {
        std::cout << "doctor: todo correcto\n";
        return 0;
    }
    else
    {
        std::cout << "doctor: " << failures << " fallo(s)\n";
        return 1;
    }
}

// FASE 20 parte B

int SatelliteCLI::cmd_context_build()
{
    auto index_path = project_root_ / ".satellite" / "context" / "index.json";
    std::filesystem::create_directories(project_root_ / ".satellite" / "context");

    satellite::context::ProjectIndex saved_index;
    bool has_cached = false;

    if (std::filesystem::exists(index_path))
    {
        try
        {
            saved_index = satellite::context::load(index_path);
            has_cached = true;
        }
        catch (const std::exception& e)
        {
            std::cout << "Advertencia: indice cacheado invalido (" << e.what() << "), reconstruyendo.\n";
        }
    }

    satellite::context::ProjectIndexBuilder builder(project_root_);

    if (has_cached && !builder.is_stale(saved_index, project_root_))
    {
        std::cout << "Contexto al dia. Sin escaneo (indice cacheado).\n";
        std::cout << "Archivos: " << saved_index.total_files << ", Lineas: " << saved_index.total_lines << "\n";
        return 0;
    }

    if (has_cached)
    {
        auto changes = builder.changed_paths(saved_index, project_root_);
        std::cout << "Indice obsoleto. Archivos modificados (" << changes.size() << "):\n";
        for (const auto& p : changes)
        {
            std::cout << "  - " << p << "\n";
        }
    }

    satellite::context::ProjectIndex idx = builder.build();
    satellite::context::save(idx, index_path);

    std::cout << "Indice construido: " << idx.total_files << " archivos, "
              << idx.total_lines << " lineas.\n";
    std::cout << "Guardado en: " << index_path << "\n";

    return 0;
}

int SatelliteCLI::cmd_context_inspect()
{
    auto index_path = project_root_ / ".satellite" / "context" / "index.json";
    if (!std::filesystem::exists(index_path))
    {
        std::cout << "Error: ejecuta primero: satellite context build\n";
        return 1;
    }

    try
    {
        satellite::context::ProjectIndex idx = satellite::context::load(index_path);

        std::cout << "Root: " << idx.root << "\n";
        std::cout << "Archivos: " << idx.total_files << ", Lineas: " << idx.total_lines << "\n";
        std::cout << "Timestamp: " << idx.build_timestamp << "\n";

        for (const auto& f : idx.files)
        {
            std::cout << "  " << f.path << " (" << f.language << ", "
                      << f.lines << " lineas, " << f.size << " bytes, "
                      << "mtime=" << f.mtime << ")\n";
            for (const auto& sym : f.symbols)
            {
                std::cout << "    simbolo: " << sym << "\n";
            }
            for (const auto& dep : f.dependencies)
            {
                std::cout << "    deps: " << dep << "\n";
            }
        }
    }
    catch (const std::exception& e)
    {
        std::cout << "Error: " << e.what() << "\n";
        return 1;
    }

    return 0;
}

int SatelliteCLI::cmd_context_get(int argc, char* argv[])
{
    std::vector<std::string> paths;
    std::size_t max_chars = 0;

    for (int i = 0; i < argc; ++i)
    {
        std::string arg = argv[i];
        if (arg == "--paths")
        {
            while (i + 1 < argc)
            {
                std::string next = argv[i + 1];
                if (next == "--max-chars" || next == "--")
                {
                    break;
                }
                paths.push_back(next);
                ++i;
            }
        }
        else if (arg == "--max-chars" && i + 1 < argc)
        {
            max_chars = static_cast<std::size_t>(std::stoul(argv[i + 1]));
        }
    }

    if (paths.empty())
    {
        std::cout << "Uso: satellite context get --paths <archivos...>\n";
        std::cout << "Opciones:\n";
        std::cout << "  --paths a.cpp b.hpp   archivos a cargar (relativos al proyecto)\n";
        std::cout << "  --max-chars N         presupuesto maximo de caracteres\n";
        return 1;
    }

    auto index_path = project_root_ / ".satellite" / "context" / "index.json";
    if (!std::filesystem::exists(index_path))
    {
        std::cout << "Error: ejecuta primero: satellite context build\n";
        return 1;
    }

    try
    {
        satellite::context::ProjectIndex idx = satellite::context::load(index_path);
        satellite::context::IncrementalContextBuilder ib(project_root_);

        satellite::context::SemanticContext ctx;
        if (max_chars > 0)
        {
            ctx = ib.build_for_paths(idx, paths, max_chars);
        }
        else
        {
            ctx = ib.build_for_paths(idx, paths);
        }

        if (ctx.files.empty())
        {
            std::cout << "No se encontro contexto para los caminos especificados.\n";
            return 1;
        }

        for (const auto& sf : ctx.files)
        {
            std::cout << "Archivo: " << sf.path << " (" << sf.language << ")\n";
            std::cout << sf.content << "\n";
        }

        std::cout << "\nTotal archivos: " << ctx.files.size()
                  << ", Total chars: " << ctx.total_chars << "\n";
    }
    catch (const std::exception& e)
    {
        std::cout << "Error: " << e.what() << "\n";
        return 1;
    }

    return 0;
}

int SatelliteCLI::cmd_agent_create(int argc, char* argv[])
{
    if (argc < 4)
    {
        std::cout << "Uso: satellite agent create <spec.json>\n";
        return 1;
    }

    satellite::persistence::AgentStore store(project_root_);
    if (!store.has_state())
    {
        std::cout << "Error: proyecto no inicializado. Ejecuta: satellite init\n";
        return 1;
    }

    std::filesystem::path spec_path = argv[3];
    std::cout << "DEBUG CLI: spec_path = " << spec_path << "\n" << std::flush;
    std::ifstream ifs(spec_path);
    if (!ifs.is_open())
    {
        std::cout << "Error: no se pudo abrir " << spec_path << "\n";
        return 1;
    }

    std::string content((std::istreambuf_iterator<char>(ifs)), std::istreambuf_iterator<char>());
    std::cout << "DEBUG CLI: content length = " << content.length() << "\n" << std::flush;
    nlohmann::json j = nlohmann::json::parse(content, nullptr, false);
    std::cout << "DEBUG CLI: json parsed, discarded = " << j.is_discarded() << "\n" << std::flush;
    if (j.is_discarded())
    {
        std::cout << "Error: spec.json inválido\n";
        return 1;
    }

    std::cout << "DEBUG CLI: getting AgentSpec\n" << std::flush;
    satellite::factory::AgentSpec spec = j.get<satellite::factory::AgentSpec>();
    std::cout << "DEBUG CLI: got AgentSpec, test_cases size = " << spec.test_cases.size() << "\n" << std::flush;
    if (spec.name.empty())
    {
        std::cout << "Error: spec inválida (falta name)\n";
        return 1;
    }

    satellite::core::registry::AgentRegistry registry;
    satellite::core::agents::register_native_agents(registry);
    store.load_registry(registry);

    std::filesystem::path work_dir = project_root_ / ".satellite" / "agents" / "work";
    std::filesystem::create_directories(work_dir);

    satellite::factory::AgentFactory factory(registry, work_dir, framework_root_, "g++");
    satellite::factory::FactoryResult fr = factory.create_agent(spec);

    if (!fr.ok)
    {
        std::cout << "Error: " << fr.stage << ": " << fr.message << "\n";
        return 1;
    }

    store.save_spec(spec);
    store.save_registry(registry);

    std::cout << "Agente " << std::to_string(spec.id) << " creado y registrado\n";
    return 0;
}

int SatelliteCLI::cmd_agent_test(int argc, char* argv[])
{
    if (argc < 3)
    {
        std::cout << "Uso: satellite agent test <id> [input.json]\n";
        return 1;
    }

    satellite::persistence::AgentStore store(project_root_);
    if (!store.has_state())
    {
        std::cout << "Error: proyecto no inicializado. Ejecuta: satellite init\n";
        return 1;
    }

    satellite::core::agent::AgentID id = static_cast<satellite::core::agent::AgentID>(std::stoul(argv[3]));

    nlohmann::json input = nlohmann::json::object();
    if (argc >= 5)
    {
        std::filesystem::path input_path = argv[4];
        std::ifstream ifs(input_path);
        if (!ifs.is_open())
        {
            std::cout << "Error: no se pudo abrir " << input_path << "\n";
            return 1;
        }
        std::string content((std::istreambuf_iterator<char>(ifs)), std::istreambuf_iterator<char>());
        nlohmann::json j = nlohmann::json::parse(content, nullptr, false);
        if (j.is_discarded())
        {
            std::cout << "Error: input.json inválido\n";
            return 1;
        }
        input = j;
    }

    satellite::core::registry::AgentRegistry registry;
    satellite::core::agents::register_native_agents(registry);
    store.load_registry(registry);

    std::filesystem::path work_dir = project_root_ / ".satellite" / "agents" / "work";
    std::filesystem::create_directories(work_dir);
    satellite::factory::AgentFactory factory(registry, work_dir, framework_root_, "g++");
    store.rebuild_agents(registry, factory);

    // Workaround: rebuild_agents salta agentes con agent=nullptr si ya existe capability en registry.
    // Cargamos specs y recreamos los que falten implementación (unregister + create).
    auto specs = store.load_specs();
    for (const auto& spec : specs)
    {
        const auto* desc = registry.find_agent(spec.id);
        if (desc && desc->agent == nullptr)
        {
            registry.unregister_agent(spec.id);
            factory.create_agent(spec);
        }
    }

    satellite::core::dispatcher::Dispatcher dispatcher(registry);
    satellite::core::agent::AgentRequest request;
    request.agent_id = id;
    request.input = input;
    request.token_budget.max_tokens = 4000;

    satellite::core::agent::AgentResult result = dispatcher.dispatch(request);

    std::cout << "status: " << static_cast<int>(result.status) << "\n";
    std::cout << "output: " << result.output.dump(2) << "\n";
    std::cout << "duration_ms: " << result.duration_ms << "\n";
    if (result.error)
    {
        std::cout << "error: " << result.error->message << "\n";
    }

    return (result.status == satellite::core::agent::AgentStatus::SUCCESS ? 0 : 1);
}

int SatelliteCLI::cmd_run(int argc, char* argv[])
{
    if (argc < 2)
    {
        std::cout << "Uso: satellite run <objetivo>\n";
        return 1;
    }

    satellite::persistence::AgentStore store(project_root_);
    if (!store.has_state())
    {
        std::cout << "Error: proyecto no inicializado. Ejecuta: satellite init\n";
        return 1;
    }

    const char* key = std::getenv("DEEPSEEK_API_KEY");
    if (!key || std::strlen(key) == 0)
    {
        std::cout << "Error: DEEPSEEK_API_KEY no definida (el orquestador necesita el LLM)\n";
        return 1;
    }

    std::string goal;
    for (int i = 2; i < argc; ++i)
    {
        if (i > 2) goal += " ";
        goal += argv[i];
    }

    satellite::core::registry::AgentRegistry registry;
    satellite::core::agents::register_native_agents(registry);
    store.load_registry(registry);

    std::filesystem::path work_dir = project_root_ / ".satellite" / "agents" / "work";
    std::filesystem::create_directories(work_dir);

    satellite::factory::AgentFactory factory(registry, work_dir, framework_root_, "g++");
    store.rebuild_agents(registry, factory);

    satellite::core::dispatcher::Dispatcher dispatcher(registry);
    satellite::context::DefaultContextOptimizer optimizer;
    satellite::config::FrameworkConfig config;
    config.llm_api_key_env = key;
    auto provider = satellite::llm::ProviderFactory::create(config);
    if (!provider)
    {
        std::cout << "Error: proveedor LLM desconocido en la configuración\n";
        return 1;
    }

    auto config_path = project_root_ / ".satellite" / "config" / "config.json";
    if (std::filesystem::exists(config_path))
    {
        satellite::config::FrameworkConfig file_config;
        std::string config_err;
        if (satellite::config::FrameworkConfig::load_from_file(config_path, file_config, config_err))
        {
            config = file_config;
            config.llm_api_key_env = key;
        }
    }

    auto adapter = satellite::context::ProjectAdapterFactory::detect(project_root_);
    satellite::context::ProjectContext proj = adapter ? adapter->build_context(project_root_) : satellite::context::ProjectContext{};

    std::string refined_goal = goal;
    if (config.use_local_llm)
    {
        satellite::llm::LocalLLMProvider local_provider(
            "http://localhost:" + std::to_string(config.local_llm_port),
            config.local_llm_api_key,
            config.local_llm_model,
            config.local_llm_context_size);

        satellite::context::LocalPreprocessor preprocessor(
            &local_provider,
            proj,
            project_root_);

        auto result = preprocessor.preprocess(goal);

        if (result.needs_user_input)
        {
            refined_goal = result.user_prompt;
        }
        else
        {
            refined_goal = result.refined_prompt;
        }
    }

    satellite::orchestrator::Orchestrator orchestrator(registry, dispatcher, optimizer, provider.get());

    satellite::core::catalog::AgentCatalog catalog(registry);
    satellite::core::protocol::TokenBudget token_budget{4000};

    auto result = orchestrator.execute_goal(refined_goal, proj, token_budget, catalog);

    std::cout << result.summary << "\n";

    if (!result.ok)
    {
        return 1;
    }

    for (const auto& step_result : result.results)
    {
        std::cout << "paso -> " << step_result.output.dump(2) << "\n";
    }

    return 0;
}

} // namespace satellite::cli