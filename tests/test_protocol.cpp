// Tests para Protocol (FASE 5)
// Mini framework CHECK (patrón de tests/test_agent_core.cpp)

#include <iostream>
#include <string>
#include <atomic>

#include <json.hpp>
#include "core/protocol/Protocol.h"
#include "core/agent/AgentRequest.h"
#include "core/agent/AgentResult.h"
#include "core/agent/AgentStatus.h"
#include "core/agent/AgentError.h"
#include "core/dispatcher/Dispatcher.h"
#include "core/registry/AgentRegistry.h"
#include "core/agents/NativeAgents.h"

using namespace satellite::core::protocol;
using namespace satellite::core::agent;
using namespace satellite::core::dispatcher;
using namespace satellite::core::registry;
using namespace satellite::core::agents;

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

namespace
{

struct MockTokenBudgetAgent : IAgent
{
    AgentResult execute(const AgentRequest& request) override
    {
        AgentResult result;
        result.status = AgentStatus::SUCCESS;
        result.output = nlohmann::json{{"token_budget", request.token_budget.max_tokens}};
        return result;
    }
};

} // namespace

void test_token_budget_default()
{
    // 1. TokenBudget por defecto: max_tokens == 0
    TokenBudget tb1;
    CHECK("TokenBudget default max_tokens == 0", tb1.max_tokens == 0);

    // TokenBudget{500}: max_tokens == 500
    TokenBudget tb2{500};
    CHECK("TokenBudget{500} max_tokens == 500", tb2.max_tokens == 500);
}

void test_execution_metadata_default()
{
    // 2. ExecutionMetadata por defecto
    ExecutionMetadata em;
    CHECK("ExecutionMetadata default execution_id empty", em.execution_id.empty());
    CHECK("ExecutionMetadata default provider empty", em.provider.empty());
    CHECK("ExecutionMetadata default model empty", em.model.empty());
    CHECK("ExecutionMetadata default timestamp_ms == 0", em.timestamp_ms == 0);
}

void test_make_execution_id()
{
    // 3. make_execution_id: dos llamadas -> ids distintos y no vacíos; empiezan con "exec_"
    std::string id1 = make_execution_id();
    std::string id2 = make_execution_id();

    CHECK("make_execution_id no vacío 1", !id1.empty());
    CHECK("make_execution_id no vacío 2", !id2.empty());
    CHECK("make_execution_id distintos", id1 != id2);
    CHECK("make_execution_id empieza con exec_ 1", id1.rfind("exec_", 0) == 0);
    CHECK("make_execution_id empieza con exec_ 2", id2.rfind("exec_", 0) == 0);
}

void test_token_budget_json_roundtrip()
{
    // 4. Round-trip JSON TokenBudget
    TokenBudget tb{500};
    nlohmann::json j = tb;
    CHECK("TokenBudget to_json max_tokens == 500", j["max_tokens"] == 500);

    TokenBudget tb2 = j.get<TokenBudget>();
    CHECK("TokenBudget from_json max_tokens == 500", tb2.max_tokens == 500);
}

void test_execution_metadata_json_roundtrip()
{
    // 5. Round-trip JSON ExecutionMetadata
    // Usar valor que quepa en long (32-bit en Windows) por limitación de Protocol.h:0L
    ExecutionMetadata em;
    em.execution_id = "exec_test_123";
    em.provider = "deepseek";
    em.model = "v4";
    em.timestamp_ms = 1699999999L;

    nlohmann::json j = em;
    ExecutionMetadata em2 = j.get<ExecutionMetadata>();

    CHECK("ExecutionMetadata roundtrip execution_id", em2.execution_id == "exec_test_123");
    CHECK("ExecutionMetadata roundtrip provider", em2.provider == "deepseek");
    CHECK("ExecutionMetadata roundtrip model", em2.model == "v4");
    CHECK("ExecutionMetadata roundtrip timestamp_ms", em2.timestamp_ms == 1699999999L);
}

void test_agent_request_manual_json()
{
    // 6. Round-trip AgentRequest - construyendo JSON manualmente
    // NOTA: to_json para AgentRequest NO existe aún, solo para TokenBudget y ExecutionMetadata
    // Construimos el wire JSON manualmente y testeamos lo que existe

    TokenBudget tb{300};
    ExecutionMetadata em;
    em.execution_id = "exec_test";

    AgentRequest req;
    req.agent_id = 7;
    req.input = nlohmann::json{{"a", 1}};
    req.context = nlohmann::json{{"file", "x.cpp"}};
    req.metadata = nlohmann::json{{"k", "v"}};
    req.token_budget = tb;
    req.execution_metadata = em;

    // Construcción manual del JSON wire (simula lo que enviaría el orchestrator)
    nlohmann::json j = {
        {"agent_id", 7},
        {"input", {{"a", 1}}},
        {"context", {{"file", "x.cpp"}}},
        {"metadata", {{"k", "v"}}},
        {"token_budget", req.token_budget},  // usa ADL de TokenBudget
        {"execution_metadata", req.execution_metadata}  // usa ADL de ExecutionMetadata
    };

    CHECK("AgentRequest manual json agent_id == 7", j["agent_id"] == 7);
    CHECK("AgentRequest manual json input.a == 1", j["input"]["a"] == 1);
    CHECK("AgentRequest manual json token_budget.max_tokens == 300", j["token_budget"]["max_tokens"] == 300);
    CHECK("AgentRequest manual json execution_metadata.execution_id == exec_test", j["execution_metadata"]["execution_id"] == "exec_test");

    // from_json para TokenBudget y ExecutionMetadata desde el JSON manual
    TokenBudget tb2 = j["token_budget"].get<TokenBudget>();
    ExecutionMetadata em2 = j["execution_metadata"].get<ExecutionMetadata>();

    CHECK("AgentRequest manual from_json token_budget.max_tokens", tb2.max_tokens == 300);
    CHECK("AgentRequest manual from_json execution_metadata.execution_id", em2.execution_id == "exec_test");
}

void test_dispatcher_fills_execution_id()
{
    // 7. Dispatcher rellena execution_id cuando request lo tiene vacío
    AgentRegistry registry;
    register_native_agents(registry);

    Dispatcher dispatcher(registry);

    AgentRequest req;
    req.agent_id = 1;  // SumAgent
    req.input = nlohmann::json{{"a", 1}, {"b", 2}};
    req.execution_metadata.execution_id = "";  // vacío

    AgentResult result = dispatcher.dispatch(req);

    CHECK("Dispatcher fills execution_id: status SUCCESS", result.status == AgentStatus::SUCCESS);
    CHECK("Dispatcher fills execution_id: not empty", !result.execution_metadata.execution_id.empty());
    CHECK("Dispatcher fills execution_id: starts with exec_", result.execution_metadata.execution_id.rfind("exec_", 0) == 0);
}

void test_dispatcher_respects_request_execution_id()
{
    // 8. Dispatcher respeta execution_id del request
    AgentRegistry registry;
    register_native_agents(registry);

    Dispatcher dispatcher(registry);

    AgentRequest req;
    req.agent_id = 1;
    req.input = nlohmann::json{{"a", 1}, {"b", 2}};
    req.execution_metadata.execution_id = "exec_mio";

    AgentResult result = dispatcher.dispatch(req);

    CHECK("Dispatcher respects request execution_id: status SUCCESS", result.status == AgentStatus::SUCCESS);
    CHECK("Dispatcher respects request execution_id: matches", result.execution_metadata.execution_id == "exec_mio");
}

void test_dispatcher_propagates_provider_model()
{
    // 9. Dispatcher propaga provider/model
    AgentRegistry registry;
    register_native_agents(registry);

    Dispatcher dispatcher(registry);

    AgentRequest req;
    req.agent_id = 1;
    req.input = nlohmann::json{{"a", 1}, {"b", 2}};
    req.execution_metadata.provider = "deepseek";
    req.execution_metadata.model = "v4";

    AgentResult result = dispatcher.dispatch(req);

    CHECK("Dispatcher propagates provider: status SUCCESS", result.status == AgentStatus::SUCCESS);
    CHECK("Dispatcher propagates provider: provider == deepseek", result.execution_metadata.provider == "deepseek");
    CHECK("Dispatcher propagates model: model == v4", result.execution_metadata.model == "v4");
}

void test_dispatcher_propagates_token_budget()
{
    // 10. Dispatcher propaga token_budget al agente
    AgentRegistry registry;
    static MockTokenBudgetAgent mock_agent;

    nlohmann::json schema = nlohmann::json{
        {"type", "object"},
        {"properties", {
            {"a", {{"type", "number"}}},
            {"b", {{"type", "number"}}}
        }},
        {"required", {"a", "b"}}
    };

    AgentDescriptor desc;
    desc.id = 42;
    desc.name = "TokenBudgetAgent";
    desc.description = "Test agent";
    desc.version = "1.0.0";
    desc.input_schema = schema;
    desc.output_schema = nlohmann::json::object({{"type", "object"}});
    desc.capabilities = {"test.token_budget"};
    desc.context_requirements = {};
    desc.agent = &mock_agent;

    CHECK("register mock token budget agent", registry.register_agent(desc));

    Dispatcher dispatcher(registry);

    AgentRequest req;
    req.agent_id = 42;
    req.input = nlohmann::json{{"a", 1}, {"b", 2}};
    req.token_budget.max_tokens = 777;

    AgentResult result = dispatcher.dispatch(req);

    CHECK("Dispatcher propagates token_budget: status SUCCESS", result.status == AgentStatus::SUCCESS);
    CHECK("Dispatcher propagates token_budget: agent receives 777", result.output["token_budget"] == 777);
}

int main()
{
    test_token_budget_default();
    test_execution_metadata_default();
    test_make_execution_id();
    test_token_budget_json_roundtrip();
    test_execution_metadata_json_roundtrip();
    test_agent_request_manual_json();
    test_dispatcher_fills_execution_id();
    test_dispatcher_respects_request_execution_id();
    test_dispatcher_propagates_provider_model();
    test_dispatcher_propagates_token_budget();

    std::cout << g_passed << " passed, " << g_failed << " failed\n";
    return g_failed == 0 ? 0 : 1;
}