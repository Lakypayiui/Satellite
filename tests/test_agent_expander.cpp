// Test para factory/AgentExpander (FASE 14)

#include <iostream>
#include <string>
#include <filesystem>
#include <vector>
#include <utility>

#include <json.hpp>
#include "factory/AgentExpander.h"
#include "factory/AgentFactory.h"
#include "core/registry/AgentRegistry.h"
#include "core/dispatcher/Dispatcher.h"
#include "core/catalog/AgentCatalog.h"
#include "core/agent/AgentRequest.h"
#include "core/agent/AgentResult.h"
#include "core/agent/AgentStatus.h"
#include "core/agents/NativeAgents.h"
#include "llm/ILLMProvider.h"
#include "llm/LLMTypes.h"

using namespace satellite::factory;
using namespace satellite::core::registry;
using namespace satellite::core::dispatcher;
using namespace satellite::core::catalog;
using namespace satellite::core::agent;
using namespace satellite::core::agents;
using namespace satellite::core::protocol;
using namespace satellite::llm;

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

static int g_temp_counter = 0;

std::filesystem::path make_temp_workdir()
{
    std::filesystem::path p = std::filesystem::temp_directory_path() / ("satellite_expand_test_" + std::to_string(++g_temp_counter));
    std::filesystem::create_directories(p);
    return p;
}

class FakeExpanderProvider : public ILLMProvider
{
public:
    explicit FakeExpanderProvider(std::string json_text) : json_text_(std::move(json_text)) {}

    std::string name() const override { return "fake"; }

    LLMResponse complete(const LLMRequest& request) override
    {
        (void)request;
        LLMResponse resp;
        resp.ok = true;
        resp.text = json_text_;
        resp.finish_reason = "stop";
        resp.prompt_tokens = 0;
        resp.completion_tokens = 0;
        resp.total_tokens = 0;
        resp.error_message = "";
        return resp;
    }

private:
    std::string json_text_;
};

const std::string& get_factorial_json()
{
    static const std::string json = R"JSON({
"name": "factorial",
"description": "calcula el factorial de n",
"input_schema": {"type": "object", "properties": {"n": {"type": "number"}}, "required": ["n"]},
"output_schema": {"type": "object", "properties": {"result": {"type": "number"}}, "required": ["result"]},
"implementation_code": "#include \"core/agent/IAgent.h\"\n#include \"core/agent/AgentRequest.h\"\n#include \"core/agent/AgentResult.h\"\n#include <json.hpp>\nusing namespace satellite::core::agent;\nclass FactorialAgent : public IAgent {\npublic:\n    AgentResult execute(const AgentRequest& request) override {\n        long long n = (long long)request.input[\"n\"].get<double>();\n        long long r = 1;\n        for (long long i = 2; i <= n; ++i) r *= i;\n        return AgentResult{request.agent_id, AgentStatus::SUCCESS, {{\"result\", (double)r}}, {}, 0.0};\n    }\n};\nextern \"C\" IAgent* satellite_create_agent() { return new FactorialAgent(); }\nextern \"C\" void satellite_destroy_agent(IAgent* agent) { delete agent; }",
"test_cases": [{"input": {"n": 5}, "expected": {"result": 120}}, {"input": {"n": 3}, "expected": {"result": 6}}]
})JSON";
    return json;
}

const std::string& get_bad_syntax_json()
{
    static const std::string json = R"JSON({
"name": "broken",
"description": "agente roto",
"input_schema": {"type": "object", "properties": {"x": {"type": "number"}}, "required": ["x"]},
"output_schema": {"type": "object", "properties": {"result": {"type": "number"}}, "required": ["result"]},
"implementation_code": "class Roto {",
"test_cases": [{"input": {"x": 1}, "expected": {"result": 1}}]
})JSON";
    return json;
}

const std::string& get_not_json_text()
{
    static const std::string text = "esto no es json";
    return text;
}

void test_missing_capabilities()
{
    AgentRegistry reg;
    register_native_agents(reg);
    AgentCatalog cat(reg);

    std::filesystem::path workdir = make_temp_workdir();
    AgentFactory fac(reg, workdir, SATELLITE_ROOT, "g++");
    FakeExpanderProvider provider(get_factorial_json());
    AgentExpander expander(reg, cat, fac, provider);

    std::vector<std::string> caps = expander.missing_capabilities("calcular factorial de un numero");
    std::cerr << "DEBUG: caps size = " << caps.size() << "\n";
    for (const auto& c : caps) std::cout << "  cap: " << c << "\n";
    bool has_factorial = false;
    for (const auto& c : caps) {
        if (c == "factorial") { has_factorial = true; break; }
    }
    CHECK("missing_capabilities: contiene 'factorial'", has_factorial);

    std::vector<std::string> caps2 = expander.missing_capabilities("sumar dos numeros");
    std::cerr << "DEBUG: caps2 size = " << caps2.size() << "\n";
    for (const auto& c : caps2) std::cout << "  cap: " << c << "\n";
    bool has_sumar = false;
    for (const auto& c : caps2) {
        if (c == "sumar") { has_sumar = true; break; }
    }
    CHECK("missing_capabilities: 'sumar' no coincide por substring", has_sumar);

    std::vector<std::string> partial_matches = expander.missing_capabilities("subcadena averiguar");
    bool has_subcadena = false;
    bool has_averiguar = false;
    for (const auto& capability : partial_matches)
    {
        has_subcadena = has_subcadena || capability == "subcadena";
        has_averiguar = has_averiguar || capability == "averiguar";
    }
    CHECK("missing_capabilities: 'subcadena' no coincide con 'subtract'", has_subcadena);
    CHECK("missing_capabilities: 'averiguar' no coincide con 'average'", has_averiguar);

    fac.cleanup();
    std::filesystem::remove_all(workdir);
}

void test_expand_first_time()
{
    AgentRegistry reg;
    register_native_agents(reg);
    AgentCatalog cat(reg);

    std::filesystem::path workdir = make_temp_workdir();
    AgentFactory fac(reg, workdir, SATELLITE_ROOT, "g++");
    FakeExpanderProvider provider(get_factorial_json());
    AgentExpander expander(reg, cat, fac, provider);

    std::string error;
    ExpansionResult res = expander.expand("calcular factorial", error);
    std::cerr << "DEBUG: expand result ok=" << res.ok << " created=" << res.created.size()
              << " skipped=" << res.skipped.size() << " failed=" << res.failed.size() << " err='" << error << "'\n";
    for (const auto& f : res.failed)
    {
        std::cerr << "DEBUG: failed " << f.first << " -> " << f.second << "\n";
    }

    CHECK("expand first: ok == true", res.ok == true);
    CHECK("expand first: created.size() == 1", res.created.size() == 1);
    CHECK("expand first: agent registered", reg.has_agent(res.created[0]) == true);

    const auto desc = reg.find_agent(res.created[0]);
    bool cap_factorial = false;
    if (desc) {
        for (const auto& c : desc->capabilities) {
            if (c == "factorial") { cap_factorial = true; break; }
        }
    }
    CHECK("expand first: capability == 'factorial'", cap_factorial);

    Dispatcher disp(reg);
    AgentRequest req;
    req.agent_id = res.created[0];
    req.input = nlohmann::json::object({{"n", 5}});
    req.context = nlohmann::json::object();
    req.metadata = nlohmann::json::object();
    req.token_budget = TokenBudget{0};
    req.execution_metadata = ExecutionMetadata{};

    AgentResult result = disp.dispatch(req);
    CHECK("expand first: dispatch SUCCESS", result.status == AgentStatus::SUCCESS);
    CHECK("expand first: output result == 120.0", result.output["result"] == 120.0);

    fac.cleanup();
    std::filesystem::remove_all(workdir);
}

void test_expand_second_time_skipped()
{
    AgentRegistry reg;
    register_native_agents(reg);
    AgentCatalog cat(reg);

    std::filesystem::path workdir = make_temp_workdir();
    AgentFactory fac(reg, workdir, SATELLITE_ROOT, "g++");
    FakeExpanderProvider provider(get_factorial_json());
    AgentExpander expander(reg, cat, fac, provider);

    std::string error;
    ExpansionResult res1 = expander.expand("calcular factorial", error);
    CHECK("expand second: first call created 1", res1.created.size() == 1);

    AgentID first_id = res1.created[0];

    ExpansionResult res2 = expander.expand("calcular factorial", error);
    CHECK("expand second: created empty", res2.created.empty());
    CHECK("expand second: skipped contains 'factorial'", !res2.skipped.empty() && res2.skipped[0] == "factorial");

    Dispatcher disp(reg);
    AgentRequest req;
    req.agent_id = first_id;
    req.input = nlohmann::json::object({{"n", 5}});
    req.context = nlohmann::json::object();
    req.metadata = nlohmann::json::object();
    req.token_budget = TokenBudget{0};
    req.execution_metadata = ExecutionMetadata{};

    AgentResult result = disp.dispatch(req);
    CHECK("expand second: original agent still works", result.status == AgentStatus::SUCCESS && result.output["result"] == 120.0);

    fac.cleanup();
    std::filesystem::remove_all(workdir);
}

void test_expand_covered_goal()
{
    AgentRegistry reg;
    register_native_agents(reg);
    AgentCatalog cat(reg);

    std::filesystem::path workdir = make_temp_workdir();
    AgentFactory fac(reg, workdir, SATELLITE_ROOT, "g++");
    FakeExpanderProvider provider(get_factorial_json());
    AgentExpander expander(reg, cat, fac, provider);

    std::string error;
    ExpansionResult res = expander.expand("sumar dos numeros", error);

    CHECK("expand covered: ok == true", res.ok == true);
    CHECK("expand covered: created empty", res.created.empty());
    CHECK("expand covered: skipped reporta 'sumar' (ya cubierto)", !res.skipped.empty() && res.skipped[0] == "sumar");

    fac.cleanup();
    std::filesystem::remove_all(workdir);
}

void test_expand_compile_error()
{
    AgentRegistry reg;
    register_native_agents(reg);
    AgentCatalog cat(reg);

std::filesystem::path workdir = make_temp_workdir();
    AgentFactory fac(reg, workdir, SATELLITE_ROOT, "g++");
    FakeExpanderProvider provider(get_bad_syntax_json());
    AgentExpander expander(reg, cat, fac, provider);

    std::string error;
    ExpansionResult res = expander.expand("calcular factorial roto", error);

    CHECK("expand compile error: ok == false", res.ok == false);
    CHECK("expand compile error: failed.size() == 1", res.failed.size() == 1);
    CHECK("expand compile error: failed contains 'compile_test'", res.failed[0].second.find("compile_test") != std::string::npos);
    CHECK("expand compile error: no new agent registered", reg.list_agents().size() == 5);

    fac.cleanup();
    std::filesystem::remove_all(workdir);
}

void test_expand_invalid_json()
{
    AgentRegistry reg;
    register_native_agents(reg);
    AgentCatalog cat(reg);

    std::filesystem::path workdir = make_temp_workdir();
    AgentFactory fac(reg, workdir, SATELLITE_ROOT, "g++");
    FakeExpanderProvider provider(get_not_json_text());
    AgentExpander expander(reg, cat, fac, provider);

    std::string error;
    ExpansionResult res = expander.expand("calcular factorial invalido", error);

    CHECK("expand invalid json: ok == false", res.ok == false);
    CHECK("expand invalid json: failed.size() == 1", res.failed.size() == 1);
    CHECK("expand invalid json: failed contains 'spec'", res.failed[0].second.find("spec") != std::string::npos);
    CHECK("expand invalid json: no new agent registered", reg.list_agents().size() == 5);

    fac.cleanup();
    std::filesystem::remove_all(workdir);
}

void test_cleanup()
{
    AgentRegistry reg;
    register_native_agents(reg);
    AgentCatalog cat(reg);

    std::filesystem::path workdir = make_temp_workdir();
    AgentFactory fac(reg, workdir, SATELLITE_ROOT, "g++");
    FakeExpanderProvider provider(get_factorial_json());
    AgentExpander expander(reg, cat, fac, provider);

    std::string error;
    expander.expand("calcular factorial", error);

    CHECK("cleanup: fac.cleanup() no throw", true);
    fac.cleanup();

    CHECK("cleanup: remove_all no throw", true);
    std::filesystem::remove_all(workdir);
}

int main()
{
    std::cerr << "DEBUG: Starting tests\n";
    test_missing_capabilities();
    std::cerr << "DEBUG: test_missing_capabilities done\n";
    test_expand_first_time();
    std::cerr << "DEBUG: test_expand_first_time done\n";
    test_expand_second_time_skipped();
    std::cerr << "DEBUG: test_expand_second_time_skipped done\n";
    test_expand_covered_goal();
    std::cerr << "DEBUG: test_expand_covered_goal done\n";
    test_expand_compile_error();
    std::cerr << "DEBUG: test_expand_compile_error done\n";
    test_expand_invalid_json();
    std::cerr << "DEBUG: test_expand_invalid_json done\n";
    test_cleanup();
    std::cerr << "DEBUG: test_cleanup done\n";

    std::cout << g_passed << " passed, " << g_failed << " failed\n";
    return g_failed == 0 ? 0 : 1;
}