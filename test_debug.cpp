#include <iostream>
#include <sstream>
#include <string>
#include <vector>
#include <filesystem>
#include <fstream>
#include <json.hpp>
#include "cli/SatelliteCLI.h"

using satellite::cli::SatelliteCLI;
namespace fs = std::filesystem;

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
    SatelliteCLI cli(project, fs::path("C:/Users/Ian/Desktop/Satellite"));
    int rc = 0;
    try {
        rc = cli.run(static_cast<int>(argv.size()), argv.data());
    } catch (const std::exception& e) {
        std::cout.rdbuf(old);
        std::cerr << "EXCEPTION: " << e.what() << std::endl;
        throw;
    }
    std::cout.rdbuf(old);
    return {rc, captured.str()};
}

int main()
{
    fs::path proj = fs::temp_directory_path() / "satellite_debug_test";
    fs::remove_all(proj);
    fs::create_directories(proj);
    
    run_cli(proj, {"init"});
    
    nlohmann::json spec;
    spec["id"] = 100;
    spec["name"] = "double";
    spec["description"] = "Devuelve el doble de x";
    spec["version"] = "1.0.0";
    spec["input_schema"] = {{"type", "object"}, {"properties", {{"x", {{"type", "number"}}}}}, {"required", {"x"}}};
    spec["output_schema"] = {{"type", "object"}};
    spec["capabilities"] = {"math.double"};
    spec["context_requirements"] = {};
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
        "extern \"C\" IAgent* satellite_create_agent() { return new DoubleAgent(); }\n";
    spec["test_cases"] = {
        {{"input", {{"x", 4}}}, {"expected", {{"result", 8.0}}}},
        {{"input", {{"x", -3}}}, {"expected", {{"result", -6.0}}}}
    };
    {
        std::ofstream f(proj / "spec.json");
        f << spec.dump(2);
        f.close();
    }
    
    std::cout << "=== Running agent create ===" << std::endl;
    try {
        auto [rc, out] = run_cli(proj, {"agent", "create", (proj / "spec.json").string()});
        std::cout << "Exit code: " << rc << std::endl;
        std::cout << "Output: " << out << std::endl;
    } catch (...) {
        std::cout << "Caught exception during agent create" << std::endl;
    }
    
    fs::remove_all(proj);
    return 0;
}
