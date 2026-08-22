// Test para factory/AgentFactory (FASE 13)

#include <iostream>
#include <string>
#include <filesystem>
#include <vector>
#include <utility>

#include <json.hpp>
#include "factory/AgentFactory.h"
#include "core/registry/AgentRegistry.h"
#include "core/dispatcher/Dispatcher.h"
#include "core/catalog/AgentCatalog.h"
#include "core/agent/AgentRequest.h"
#include "core/agent/AgentResult.h"
#include "core/agent/AgentStatus.h"
#include "core/protocol/Protocol.h"

using namespace satellite::factory;
using namespace satellite::core::registry;
using namespace satellite::core::dispatcher;
using namespace satellite::core::catalog;
using namespace satellite::core::agent;
using namespace satellite::core::protocol;

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
    std::filesystem::path p = std::filesystem::temp_directory_path() / ("satellite_factory_test_" + std::to_string(++g_temp_counter));
    std::filesystem::create_directories(p);
    return p;
}

AgentSpec make_double_spec(AgentID id, bool good_code = true)
{
    AgentSpec spec;
    spec.id = id;
    spec.name = "double";
    spec.description = "duplica un numero";
    spec.version = "1.0.0";
    spec.input_schema = nlohmann::json{
        {"type", "object"},
        {"properties", {
            {"x", {{"type", "number"}}}
        }},
        {"required", {"x"}}
    };
    spec.output_schema = nlohmann::json{
        {"type", "object"},
        {"properties", {
            {"result", {{"type", "number"}}}
        }},
        {"required", {"result"}}
    };
    spec.context_requirements = {};
    spec.capabilities = {"math.double"};

    if (good_code)
    {
        spec.implementation_code = R"AGENT(
#include "core/agent/IAgent.h"
#include "core/agent/AgentRequest.h"
#include "core/agent/AgentResult.h"
#include <json.hpp>
using namespace satellite::core::agent;
class DoubleAgent : public IAgent {
public:
    AgentResult execute(const AgentRequest& request) override {
        double x = request.input["x"].get<double>();
        return AgentResult{request.agent_id, AgentStatus::SUCCESS, {{"result", x * 2.0}}, {}, 0.0};
    }
};
extern "C" IAgent* satellite_create_agent() { return new DoubleAgent(); }
)AGENT";
    }
    else
    {
        spec.implementation_code = R"AGENT(
#include "core/agent/IAgent.h"
#include "core/agent/AgentRequest.h"
#include "core/agent/AgentResult.h"
#include <json.hpp>
using namespace satellite::core::agent;
class DoubleAgent {
public:
    AgentResult execute(const AgentRequest& request) override {
        double x = request.input["x"].get<double>();
        return AgentResult{request.agent_id, AgentStatus::SUCCESS, {{"result", x * 2.0}}, {}, 0.0};
    }
};
extern "C" IAgent* satellite_create_agent() { return new DoubleAgent(); }
)AGENT";
    }

    spec.test_cases = {
        {nlohmann::json::object({{"x", 2}}), nlohmann::json::object({{"result", 4}})},
        {nlohmann::json::object({{"x", -3}}), nlohmann::json::object({{"result", -6}})},
        {nlohmann::json::object({{"x", 0}}), nlohmann::json::object({{"result", 0}})}
    };

    return spec;
}

AgentSpec make_bad_logic_spec(AgentID id)
{
    AgentSpec spec = make_double_spec(id, true);
    spec.implementation_code = R"AGENT(
#include "core/agent/IAgent.h"
#include "core/agent/AgentRequest.h"
#include "core/agent/AgentResult.h"
#include <json.hpp>
using namespace satellite::core::agent;
class DoubleAgent : public IAgent {
public:
    AgentResult execute(const AgentRequest& request) override {
        double x = request.input["x"].get<double>();
        return AgentResult{request.agent_id, AgentStatus::SUCCESS, {{"result", x * 3.0}}, {}, 0.0};
    }
};
extern "C" IAgent* satellite_create_agent() { return new DoubleAgent(); }
)AGENT";
    return spec;
}

AgentSpec make_invalid_spec(AgentID id)
{
    AgentSpec spec = make_double_spec(id, true);
    spec.name = "";
    return spec;
}

void test_happy_path()
{
    std::filesystem::path workdir = make_temp_workdir();
    AgentRegistry reg;
    Dispatcher disp(reg);

    AgentFactory factory(reg, workdir, SATELLITE_ROOT, "g++");
    FactoryResult r = factory.create_agent(make_double_spec(100));

    CHECK("Happy path: ok == true", r.ok == true);
    CHECK("Happy path: stage == ok", r.stage == "ok");
    CHECK("Happy path: agent registered", reg.has_agent(100) == true);

    AgentRequest req;
    req.agent_id = 100;
    req.input = nlohmann::json::object({{"x", 5.0}});
    req.context = nlohmann::json::object();
    req.metadata = nlohmann::json::object();
    req.token_budget = TokenBudget{0};
    req.execution_metadata = ExecutionMetadata{};

    AgentResult result = disp.dispatch(req);
    CHECK("Happy path: dispatch SUCCESS", result.status == AgentStatus::SUCCESS);
    CHECK("Happy path: output result == 10.0", result.output["result"] == 10.0);

    const AgentDescriptor* desc = reg.find_agent(100);
    CHECK("Happy path: capability math.double", desc && desc->capabilities.size() > 0 && desc->capabilities[0] == "math.double");

    factory.cleanup();
}

void test_syntax_error()
{
    std::filesystem::path workdir = make_temp_workdir();
    AgentRegistry reg;

    AgentFactory factory(reg, workdir, SATELLITE_ROOT, "g++");
    FactoryResult r = factory.create_agent(make_double_spec(101, false));

    CHECK("Syntax error: ok == false", r.ok == false);
    CHECK("Syntax error: stage == compile_test", r.stage == "compile_test");
    CHECK("Syntax error: not registered", reg.has_agent(101) == false);

    factory.cleanup();
}

void test_run_tests_failure()
{
    std::filesystem::path workdir = make_temp_workdir();
    AgentRegistry reg;

    AgentFactory factory(reg, workdir, SATELLITE_ROOT, "g++");
    FactoryResult r = factory.create_agent(make_bad_logic_spec(102));

    CHECK("Run tests failure: ok == false", r.ok == false);
    CHECK("Run tests failure: stage == run_tests", r.stage == "run_tests");
    CHECK("Run tests failure: not registered", reg.has_agent(102) == false);

    factory.cleanup();
}

void test_invalid_spec()
{
    std::filesystem::path workdir = make_temp_workdir();
    AgentRegistry reg;

    AgentFactory factory(reg, workdir, SATELLITE_ROOT, "g++");
    FactoryResult r = factory.create_agent(make_invalid_spec(103));

    CHECK("Invalid spec: ok == false", r.ok == false);
    CHECK("Invalid spec: stage == validate", r.stage == "validate");
    CHECK("Invalid spec: not registered", reg.has_agent(103) == false);

    factory.cleanup();
}

void test_duplicate_registration()
{
    std::filesystem::path workdir1 = make_temp_workdir();
    std::filesystem::path workdir2 = make_temp_workdir();
    AgentRegistry reg;

    AgentFactory factory1(reg, workdir1, SATELLITE_ROOT, "g++");
    FactoryResult r1 = factory1.create_agent(make_double_spec(100));
    CHECK("Duplicate: first create ok", r1.ok == true && r1.stage == "ok");

    AgentFactory factory2(reg, workdir2, SATELLITE_ROOT, "g++");
    FactoryResult r2 = factory2.create_agent(make_double_spec(100));
    CHECK("Duplicate: second create fails", r2.ok == false && r2.stage == "register");

    AgentRequest req;
    req.agent_id = 100;
    req.input = nlohmann::json::object({{"x", 7.0}});
    req.context = nlohmann::json::object();
    req.metadata = nlohmann::json::object();
    req.token_budget = TokenBudget{0};
    req.execution_metadata = ExecutionMetadata{};

    Dispatcher disp(reg);
    AgentResult result = disp.dispatch(req);
    CHECK("Duplicate: first agent still works", result.status == AgentStatus::SUCCESS && result.output["result"] == 14.0);

    factory1.cleanup();
    factory2.cleanup();
}

void test_release_and_cleanup()
{
    std::filesystem::path workdir = make_temp_workdir();
    AgentRegistry reg;

    AgentFactory factory(reg, workdir, SATELLITE_ROOT, "g++");
    factory.create_agent(make_double_spec(100));

    CHECK("Release existing agent", factory.release_agent(100) == true);
    CHECK("Release non-existing agent", factory.release_agent(999) == false);
    CHECK("Cleanup no throw", true);

    factory.cleanup();
}

void test_schema_validation()
{
    std::filesystem::path workdir = make_temp_workdir();
    AgentRegistry reg;

    AgentFactory factory(reg, workdir, SATELLITE_ROOT, "g++");
    factory.create_agent(make_double_spec(100));

    Dispatcher disp(reg);

    AgentRequest req;
    req.agent_id = 100;
    req.input = nlohmann::json::object({{"x", "texto"}});
    req.context = nlohmann::json::object();
    req.metadata = nlohmann::json::object();
    req.token_budget = TokenBudget{0};
    req.execution_metadata = ExecutionMetadata{};

    AgentResult result = disp.dispatch(req);
    CHECK("Schema validation: VALIDATION_ERROR", result.status == AgentStatus::VALIDATION_ERROR);

    factory.cleanup();
}

void test_catalog_inclusion()
{
    std::filesystem::path workdir = make_temp_workdir();
    AgentRegistry reg;

    AgentFactory factory(reg, workdir, SATELLITE_ROOT, "g++");
    factory.create_agent(make_double_spec(100));

    AgentCatalog catalog(reg);
    nlohmann::json j = catalog.to_json();

    bool found = false;
    for (const auto& entry : j)
    {
        if (entry["name"] == "double" && entry["capabilities"].is_array())
        {
            for (const auto& cap : entry["capabilities"])
            {
                if (cap == "math.double")
                {
                    found = true;
                    break;
                }
            }
        }
    }

    CHECK("Catalog includes agent", found == true);

    factory.cleanup();
}

int main()
{
    test_happy_path();
    test_syntax_error();
    test_run_tests_failure();
    test_invalid_spec();
    test_duplicate_registration();
    test_release_and_cleanup();
    test_schema_validation();
    test_catalog_inclusion();

    std::cout << g_passed << " passed, " << g_failed << " failed\n";
    return g_failed == 0 ? 0 : 1;
}