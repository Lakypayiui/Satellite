// Tests de la CLI de Satellite (FASE 20)
// La CLI se usa como biblioteca: SatelliteCLI::run(argc, argv) sobre un proyecto temporal.

#include <iostream>
#include <sstream>
#include <string>
#include <vector>
#include <filesystem>
#include <fstream>
#include <cstdlib>
#include <cstring>
#include <cstdio>

#include "cli/SatelliteCLI.h"
#include <json.hpp>

using satellite::cli::SatelliteCLI;
#ifdef _WIN32
#define popen _popen
#define pclose _pclose
#endif

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
    } while (0)

namespace fs = std::filesystem;

static int g_counter = 0;

static void safe_remove_all(const fs::path& p)
{
    try
    {
        fs::remove_all(p);
    }
    catch (const std::exception&)
    {
        // El cleanup puede fallar si un plugin DLL sigue cargado en el proceso; no es una aserción.
    }
}

// Crea un directorio de proyecto temporal y devuelve su ruta
static fs::path make_project(bool create_main_cpp)
{
    ++g_counter;
    fs::path dir = fs::temp_directory_path() / ("satellite_cli_test_" + std::to_string(g_counter));
    safe_remove_all(dir);
    fs::create_directories(dir);
    if (create_main_cpp)
    {
        std::ofstream out(dir / "main.cpp");
        out << "int main() { return 0; }\n";
        out.close();
    }
    return dir;
}

// Ejecuta la CLI capturando la salida; devuelve {exit_code, salida}
static std::pair<int, std::string> run_cli(const fs::path& project, const std::vector<std::string>& args)
{
    std::vector<char*> argv;
    argv.push_back(const_cast<char*>("satellite"));
    for (const auto& a : args)
    {
        argv.push_back(const_cast<char*>(a.c_str()));
    }
    std::ostringstream captured;
    auto* old = std::cout.rdbuf(captured.rdbuf());
    SatelliteCLI cli(project, fs::path(SATELLITE_ROOT));
    int rc = cli.run(static_cast<int>(argv.size()), argv.data());
    std::cout.rdbuf(old);
    return {rc, captured.str()};
}

int main()
{
    std::cout.setf(std::ios::unitbuf);
    std::cerr.setf(std::ios::unitbuf);

    // El test de "run" sin LLM requiere que DEEPSEEK_API_KEY NO esté definida.
    // Se limpia SOLO en el entorno de este proceso de test.
#ifdef _WIN32
    _putenv_s("DEEPSEEK_API_KEY", "");
#else
    unsetenv("DEEPSEEK_API_KEY");
#endif

    // --- init ---
    {
        fs::path proj = make_project(false);
        auto [rc, out] = run_cli(proj, {"init"});
        CHECK("init: exit 0", rc == 0);
        CHECK("init: crea .satellite", fs::exists(proj / ".satellite"));
        CHECK("init: crea config/", fs::is_directory(proj / ".satellite" / "config"));
        CHECK("init: crea registry/", fs::is_directory(proj / ".satellite" / "registry"));
        CHECK("init: crea agents/", fs::is_directory(proj / ".satellite" / "agents"));
        CHECK("init: crea context/", fs::is_directory(proj / ".satellite" / "context"));
        CHECK("init: crea executions/", fs::is_directory(proj / ".satellite" / "executions"));
        CHECK("init: config.json existe", fs::exists(proj / ".satellite" / "config" / "config.json"));

        auto [rc2, out2] = run_cli(proj, {"init"});
        CHECK("init repetido: exit != 0", rc2 != 0);
        CHECK("init repetido: mensaje already", out2.find("already") != std::string::npos);
        safe_remove_all(proj);
    }

    // --- agents + agent info/enable/disable ---
    {
        fs::path proj = make_project(false);
        run_cli(proj, {"init"});

        auto [rc, out] = run_cli(proj, {"agents"});
        CHECK("agents: exit 0", rc == 0);
        CHECK("agents: lista sum", out.find("sum") != std::string::npos);
        CHECK("agents: lista average", out.find("average") != std::string::npos);

        auto [rc3, out3] = run_cli(proj, {"agent", "info", "1"});
        CHECK("agent info 1: exit 0", rc3 == 0);
        CHECK("agent info 1: nombre sum", out3.find("sum") != std::string::npos);

        auto [rc4, out4] = run_cli(proj, {"agent", "info", "999"});
        CHECK("agent info 999: exit != 0", rc4 != 0);

        auto [rc5, out5] = run_cli(proj, {"agent", "disable", "2"});
        CHECK("agent disable 2: exit 0", rc5 == 0);
        // el estado persiste en registry/agents.json
        {
            std::ifstream f(proj / ".satellite" / "registry" / "agents.json");
            std::stringstream ss;
            ss << f.rdbuf();
            auto j = nlohmann::json::parse(ss.str(), nullptr, false);
            CHECK("disable: agents.json parseable", !j.is_discarded());
            bool found_disabled = false;
            for (const auto& e : j)
            {
                if (e["id"] == 2 && e["enabled"] == false)
                {
                    found_disabled = true;
                }
            }
            CHECK("disable: id 2 persisted disabled", found_disabled);
        }

        auto [rc6, out6] = run_cli(proj, {"agent", "enable", "2"});
        CHECK("agent enable 2: exit 0", rc6 == 0);
        safe_remove_all(proj);
    }

    // --- context build/inspect ---
    {
        fs::path proj = make_project(true);   // main.cpp
        run_cli(proj, {"init"});
        auto [rc, out] = run_cli(proj, {"context", "build"});
        CHECK("context build: exit 0", rc == 0);
        CHECK("context build: indice creado", out.find("Indice construido") != std::string::npos);
        CHECK("context build: cache json", fs::exists(proj / ".satellite" / "context" / "index.json"));

        auto [rc2, out2] = run_cli(proj, {"context", "inspect"});
        CHECK("context inspect: exit 0", rc2 == 0);
        CHECK("context inspect: muestra main.cpp", out2.find("main.cpp") != std::string::npos);

        // build segundo llamado -> cache hit (proj aún existe)
        auto [rc4, out4] = run_cli(proj, {"context", "build"});
        CHECK("context build cache hit: exit 0", rc4 == 0);
        CHECK("context build cache hit: sin escaneo", out4.find("Sin escaneo") != std::string::npos);

        safe_remove_all(proj);

        // inspect sin build previo -> error
        fs::path proj2 = make_project(true);
        run_cli(proj2, {"init"});
        auto [rc3, out3] = run_cli(proj2, {"context", "inspect"});
        CHECK("context inspect sin build: exit != 0", rc3 != 0);
        safe_remove_all(proj2);
    }

    // --- doctor ---
    {
        fs::path proj = make_project(true);
        run_cli(proj, {"init"});
        auto [rc, out] = run_cli(proj, {"doctor"});
        CHECK("doctor inicializado: exit 0", rc == 0);
        CHECK("doctor: todo correcto", out.find("todo correcto") != std::string::npos);
        safe_remove_all(proj);

        fs::path proj2 = make_project(false);
        auto [rc2, out2] = run_cli(proj2, {"doctor"});
        CHECK("doctor sin init: exit != 0", rc2 != 0);
        safe_remove_all(proj2);
    }

    // --- agent create + agent test (factory real con g++) ---
    // NOTA: el test del agente se ejecuta en un SUBPROCESO (el binario real satellite.exe):
    // in-process, la DLL del plugin quedaría cargada y Windows bloquea su recompilación
    // durante el rebuild que hace cmd_agent_test.
    {
        fs::path proj = make_project(false);
        run_cli(proj, {"init"});
        // spec de un agente "doblar" (multiplica por 2)
        nlohmann::json spec;
        spec["id"] = 100;
        spec["name"] = "double";
        spec["description"] = "Devuelve el doble de x";
        spec["version"] = "1.0.0";
        spec["input_schema"] = {{"type", "object"}, {"properties", {{"x", {{"type", "number"}}}}}, {"required", {"x"}}};
        spec["output_schema"] = {{"type", "object"}};
        spec["capabilities"] = {"math.double"};
        spec["context_requirements"] = nlohmann::json::array();
        spec["implementation_code"] =
            "#include \"core/agent/IAgent.h\"\n"
            "#include <json.hpp>\n"
            "using namespace satellite::core::agent;\n"
            "class DoubleAgent : public IAgent\n"
            "{\n"
            "public:\n"
            "    AgentResult execute(const AgentRequest& request) override\n"
            "    {\n"
            "        double x = request.input[\"x\"].get<double>();\n"
            "        return AgentResult{request.agent_id, AgentStatus::SUCCESS, {{\"result\", 2.0 * x}}, {}, 0.0, {}};\n"
            "    }\n"
            "};\n"
            "extern \"C\" IAgent* satellite_create_agent() { return new DoubleAgent(); }\n"
            "extern \"C\" void satellite_destroy_agent(IAgent* agent) { delete agent; }\n";
        spec["test_cases"] = {
            {{"input", {{"x", 4}}}, {"expected", {{"result", 8.0}}}},
            {{"input", {{"x", -3}}}, {"expected", {{"result", -6.0}}}}
        };
        {
            std::ofstream f(proj / "spec.json");
            f << spec.dump(2);
            f.close();
        }
        // create y test se ejecutan en SUBPROCESOS (el binario real satellite.exe):
        // in-process, la DLL del plugin quedaría cargada y Windows bloquea su
        // recompilación durante el rebuild que hace cmd_agent_test.
        #ifdef _WIN32
            fs::path bin = fs::path(SATELLITE_ROOT) / "build" / "satellite.exe";
        #else
            fs::path bin = fs::path(SATELLITE_ROOT) / "build" / "satellite";
        #endif

        std::string bin_native = bin.string();
        auto run_bin = [&](const std::string& args) -> std::string
        {
            #ifdef _WIN32
                std::string cmd = "cmd /c \"cd /d " + proj.string() + " && " +
                                bin_native + " " + args + "\"";
            #else
                std::string cmd = "cd \"" + proj.string() + "\" && " +
                                bin_native + " " + args;
            #endif

                std::string result;

                char buf[512];

                FILE* pipe = popen(cmd.c_str(), "r");

                while (pipe && fgets(buf, sizeof(buf), pipe))
                {
                    result += buf;
                }

                if (pipe)
                {
                    pclose(pipe);
                }

                return result;
        };

        std::string create_out = run_bin("agent create spec.json");
        CHECK("agent create (subproceso): exit 0", create_out.find("creado") != std::string::npos);
        CHECK("agent create: spec persistida", fs::exists(proj / ".satellite" / "agents" / "agent_100.json"));

        {
            std::ofstream f(proj / "input.json");
            f << R"({"x": 21})";
            f.close();
        }
        std::string out2 = run_bin("agent test 100 input.json");
        bool test_ok = out2.find("status: 2") != std::string::npos;
        CHECK("agent test 100: exit 0 (subproceso)", test_ok);
        CHECK("agent test 100: status success (status: 2)", test_ok);
        CHECK("agent test 100: resultado 42", out2.find("42") != std::string::npos);

        // el agente queda visible en agents
        auto [rc3, out3] = run_cli(proj, {"agents"});
        CHECK("agents tras create: incluye double", out3.find("double") != std::string::npos);
        safe_remove_all(proj);
    }

    // --- run sin DEEPSEEK_API_KEY -> error claro ---
    {
        fs::path proj = make_project(true);
        run_cli(proj, {"init"});
        auto [rc, out] = run_cli(proj, {"run", "sumar", "dos", "numeros"});
        CHECK("run sin key: exit != 0", rc != 0);
        CHECK("run sin key: mensaje DEEPSEEK", out.find("DEEPSEEK") != std::string::npos);
        safe_remove_all(proj);
    }

    // --- uso sin argv -> exit != 0 ---
    {
        fs::path proj = make_project(false);
        auto [rc, out] = run_cli(proj, {});
        CHECK("sin comando: exit != 0", rc != 0);
        safe_remove_all(proj);
    }

    std::cout << g_passed << " passed, " << g_failed << " failed\n";
    return g_failed == 0 ? 0 : 1;
}
