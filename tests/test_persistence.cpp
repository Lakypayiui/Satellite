// Test para persistence/AgentStore (FASE 15)

#include <iostream>
#include <string>
#include <filesystem>
#include <vector>
#include <utility>

#include <json.hpp>
#include "persistence/AgentStore.h"
#include "factory/AgentFactory.h"
#include "core/registry/AgentRegistry.h"
#include "core/agents/NativeAgents.h"
#include "core/dispatcher/Dispatcher.h"
#include "core/agent/AgentRequest.h"
#include "core/agent/AgentResult.h"
#include "core/agent/AgentStatus.h"
#include "core/protocol/Protocol.h"

using namespace satellite::persistence;
using namespace satellite::factory;
using namespace satellite::core::registry;
using namespace satellite::core::agents;
using namespace satellite::core::dispatcher;
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

std::filesystem::path make_temp_project_root()
{
    std::filesystem::path p = std::filesystem::temp_directory_path() / ("satellite_persist_test_" + std::to_string(++g_temp_counter));
    std::filesystem::create_directories(p);
    return p;
}

AgentSpec make_double_spec(AgentID id)
{
    AgentSpec spec;
    spec.id = id;
    spec.name = "double";
    spec.description = "d";
    spec.version = "1.0.0";
    spec.input_schema = nlohmann::json::object({{"type", "object"}});
    spec.output_schema = nlohmann::json::object({{"type", "object"}});
    spec.context_requirements = {};
    spec.capabilities = {"math.double"};
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
extern "C" void satellite_destroy_agent(IAgent* agent) { delete agent; }
)AGENT";
    spec.test_cases = {
        {nlohmann::json::object({{"x", 2}}), nlohmann::json::object({{"result", 4}})}
    };
    return spec;
}

void test_case_1_estructura()
{
    std::filesystem::path proj_root = make_temp_project_root();
    AgentStore store(proj_root);

    CHECK("Caso 1: has_state() inicial false", !store.has_state());
    CHECK("Caso 1: ensure_dirs() true", store.ensure_dirs());
    CHECK("Caso 1: has_state() true tras ensure_dirs", store.has_state());

    std::filesystem::path root = store.root();
    CHECK("Caso 1: existe config", std::filesystem::exists(root / "config"));
    CHECK("Caso 1: existe registry", std::filesystem::exists(root / "registry"));
    CHECK("Caso 1: existe agents", std::filesystem::exists(root / "agents"));
    CHECK("Caso 1: existe context", std::filesystem::exists(root / "context"));
    CHECK("Caso 1: existe executions", std::filesystem::exists(root / "executions"));

    std::filesystem::remove_all(proj_root);
}

void test_case_2_roundtrip_spec()
{
    std::filesystem::path proj_root = make_temp_project_root();
    AgentStore store(proj_root);
    store.ensure_dirs();

    AgentSpec spec = make_double_spec(100);

    CHECK("Caso 2: save_spec true", store.save_spec(spec));

    auto specs = store.load_specs();
    CHECK("Caso 2: load_specs size == 1", specs.size() == 1);

    if (!specs.empty())
    {
        CHECK("Caso 2: spec.id == 100", specs[0].id == 100);
        CHECK("Caso 2: spec.name == double", specs[0].name == "double");
        CHECK("Caso 2: spec.capabilities[0] == math.double", specs[0].capabilities.size() > 0 && specs[0].capabilities[0] == "math.double");
        CHECK("Caso 2: spec.implementation_code presente", specs[0].implementation_code.find("DoubleAgent") != std::string::npos);
        CHECK("Caso 2: spec.test_cases size == 1", specs[0].test_cases.size() == 1);
        if (!specs[0].test_cases.empty())
        {
            CHECK("Caso 2: test_cases[0].first[x] == 2", specs[0].test_cases[0].first["x"] == 2);
            CHECK("Caso 2: test_cases[0].second[result] == 4", specs[0].test_cases[0].second["result"] == 4);
        }
    }

    std::filesystem::remove_all(proj_root);
}

void test_case_3_registry_persistente()
{
    std::filesystem::path proj_root = make_temp_project_root();
    AgentStore store(proj_root);
    store.ensure_dirs();

    AgentRegistry reg;
    register_native_agents(reg);
    reg.disable_agent(3);

    CHECK("Caso 3: save_registry true", store.save_registry(reg));

    AgentRegistry reg2;
    CHECK("Caso 3: load_registry true", store.load_registry(reg2));

    CHECK("Caso 3: reg2 has_agent 1..5", reg2.has_agent(1) && reg2.has_agent(2) && reg2.has_agent(3) && reg2.has_agent(4) && reg2.has_agent(5));

    const auto d1 = reg2.find_agent(1);
    CHECK("Caso 3: find_agent(1)->name == sum", d1 && d1->name == "sum");
    CHECK("Caso 3: is_enabled(3) == false", reg2.is_enabled(3) == false);
    CHECK("Caso 3: is_enabled(1) == true", reg2.is_enabled(1) == true);
    CHECK("Caso 3: descriptor agent == nullptr", d1 && d1->agent == nullptr);

    std::filesystem::remove_all(proj_root);
}

void test_case_4_rebuild_agents()
{
    std::filesystem::path proj_root = make_temp_project_root();
    AgentStore store(proj_root);
    store.ensure_dirs();

    AgentSpec spec = make_double_spec(100);
    store.save_spec(spec);

    AgentRegistry reg3;
    std::filesystem::path workdir = std::filesystem::temp_directory_path() / ("satellite_factory_rebuild_" + std::to_string(++g_temp_counter));
    std::filesystem::create_directories(workdir);
    AgentFactory fac(reg3, workdir, SATELLITE_ROOT, "g++");

    std::size_t n = store.rebuild_agents(reg3, fac);
    CHECK("Caso 4: rebuild_agents n == 1", n == 1);
    CHECK("Caso 4: reg3 has_agent(100)", reg3.has_agent(100));

    Dispatcher disp(reg3);
    AgentRequest req;
    req.agent_id = 100;
    req.input = nlohmann::json::object({{"x", 5}});
    req.context = nlohmann::json::object();
    req.metadata = nlohmann::json::object();
    req.token_budget = TokenBudget{0};
    req.execution_metadata = ExecutionMetadata{};

    AgentResult result = disp.dispatch(req);
    CHECK("Caso 4: dispatch SUCCESS", result.status == AgentStatus::SUCCESS);
    CHECK("Caso 4: result == 10.0", result.output["result"] == 10.0);

    fac.cleanup();
    std::filesystem::remove_all(workdir);
    std::filesystem::remove_all(proj_root);
}

void test_case_5_rebuild_no_duplica()
{
    std::filesystem::path proj_root = make_temp_project_root();
    AgentStore store(proj_root);
    store.ensure_dirs();

    AgentSpec spec = make_double_spec(100);
    store.save_spec(spec);

    AgentRegistry reg3;
    std::filesystem::path workdir = std::filesystem::temp_directory_path() / ("satellite_factory_rebuild2_" + std::to_string(++g_temp_counter));
    std::filesystem::create_directories(workdir);
    AgentFactory fac(reg3, workdir, SATELLITE_ROOT, "g++");

    std::size_t n1 = store.rebuild_agents(reg3, fac);
    CHECK("Caso 5: primera llamada n == 1", n1 == 1);

    std::size_t n2 = store.rebuild_agents(reg3, fac);
    CHECK("Caso 5: segunda llamada n == 0", n2 == 0);

    fac.cleanup();
    std::filesystem::remove_all(workdir);
    std::filesystem::remove_all(proj_root);
}

void test_case_6_edge_cases()
{
    std::filesystem::path proj_root = make_temp_project_root();
    AgentStore store(proj_root);
    store.ensure_dirs();

    AgentRegistry reg;
    CHECK("Caso 6: load_registry archivo inexistente false", !store.load_registry(reg));

    AgentSpec bad_spec = make_double_spec(0);
    CHECK("Caso 6: save_spec id 0 true (comportamiento real)", store.save_spec(bad_spec));

    std::filesystem::remove_all(proj_root);
}

void test_case_7_estado_dentro_proyecto()
{
    std::filesystem::path proj_root = make_temp_project_root();
    AgentStore store(proj_root);
    store.ensure_dirs();

    AgentSpec spec = make_double_spec(100);
    store.save_spec(spec);

    AgentRegistry reg;
    register_native_agents(reg);
    store.save_registry(reg);

    CHECK("Caso 7: NO existe proj_root/agents.json", !std::filesystem::exists(proj_root / "agents.json"));
    CHECK("Caso 7: NO existe proj_root/registry", !std::filesystem::exists(proj_root / "registry"));
    CHECK("Caso 7: TODO bajo .satellite", std::filesystem::exists(store.root()));

    std::filesystem::remove_all(proj_root);
}

int main()
{
    test_case_1_estructura();
    test_case_2_roundtrip_spec();
    test_case_3_registry_persistente();
    test_case_4_rebuild_agents();
    test_case_5_rebuild_no_duplica();
    test_case_6_edge_cases();
    test_case_7_estado_dentro_proyecto();

    std::cout << g_passed << " passed, " << g_failed << " failed\n";
    return g_failed == 0 ? 0 : 1;
}