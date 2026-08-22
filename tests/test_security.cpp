// Tests para SecurityPolicy y Dispatcher con seguridad (FASE 18)
// Sin dependencias externas: solo C++17 estándar

#include <iostream>
#include <string>
#include <map>
#include <atomic>

#include <json.hpp>
#include "security/SecurityPolicy.h"
#include "core/dispatcher/Dispatcher.h"
#include "core/registry/AgentRegistry.h"
#include "core/agents/NativeAgents.h"
#include "core/agent/IAgent.h"
#include "core/agent/AgentRequest.h"
#include "core/agent/AgentResult.h"
#include "core/agent/AgentStatus.h"
#include "core/agent/AgentError.h"
#include "core/agent/AgentDescriptor.h"

using namespace satellite::security;
using namespace satellite::core::agent;
using namespace satellite::core::registry;
using namespace satellite::core::dispatcher;
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

struct MockCountingAgent : IAgent
{
    std::atomic<int>& call_count;

    MockCountingAgent(std::atomic<int>& counter) : call_count(counter) {}

    AgentResult execute(const AgentRequest&) override
    {
        ++call_count;
        AgentResult result;
        result.status = AgentStatus::SUCCESS;
        return result;
    }
};

} // namespace

void test_deny_by_default()
{
    SecurityPolicy p;

    CHECK("deny-by-default: filesystem.read == false", p.is_allowed("filesystem.read") == false);
    CHECK("deny-by-default: math.sum == false", p.is_allowed("math.sum") == false);
}

void test_load_defaults()
{
    SecurityPolicy p;
    p.load_defaults();

    CHECK("load_defaults: filesystem.read == true", p.is_allowed("filesystem.read") == true);
    CHECK("load_defaults: filesystem.write == false", p.is_allowed("filesystem.write") == false);
    CHECK("load_defaults: process.execute == false", p.is_allowed("process.execute") == false);
    CHECK("load_defaults: compiler.execute == false", p.is_allowed("compiler.execute") == false);
    CHECK("load_defaults: network.request == false", p.is_allowed("network.request") == false);
    CHECK("load_defaults: allow_map size == 5", p.allow_map().size() == 5);
}

void test_set_allowed()
{
    SecurityPolicy p;

    p.set_allowed("custom.op", true);
    CHECK("set_allowed true: is_allowed == true", p.is_allowed("custom.op") == true);

    p.set_allowed("custom.op", false);
    CHECK("set_allowed false: is_allowed == false", p.is_allowed("custom.op") == false);

    auto map = p.allow_map();
    CHECK("set_allowed: allow_map contains custom.op", map.find("custom.op") != map.end());
    CHECK("set_allowed: allow_map custom.op == false", map["custom.op"] == false);
}

void test_from_config()
{
    SecurityPolicy p;
    std::map<std::string, bool> config = {
        {"filesystem.read", true},
        {"network.request", true}
    };
    p.from_config(config);

    CHECK("from_config: filesystem.read == true", p.is_allowed("filesystem.read") == true);
    CHECK("from_config: network.request == true", p.is_allowed("network.request") == true);
    CHECK("from_config: process.execute == false (not in config)", p.is_allowed("process.execute") == false);
}

void test_validate_agent()
{
    // Política con math.sum=true, math.divide=false
    SecurityPolicy p1;
    p1.set_allowed("math.sum", true);
    p1.set_allowed("math.divide", false);

    AgentDescriptor desc1;
    desc1.capabilities = {"math.sum", "math.divide"};

    std::string denied;
    bool valid1 = p1.validate_agent(desc1, denied);
    CHECK("validate_agent mixed: returns false", valid1 == false);
    CHECK("validate_agent mixed: denied == math.divide", denied == "math.divide");

    // Política con ambas true
    SecurityPolicy p2;
    p2.set_allowed("math.sum", true);
    p2.set_allowed("math.divide", true);

    bool valid2 = p2.validate_agent(desc1, denied);
    CHECK("validate_agent both allowed: returns true", valid2 == true);

    // Descriptor con capabilities VACÍAS
    AgentDescriptor desc_empty;
    desc_empty.capabilities = {};

    bool valid3 = p1.validate_agent(desc_empty, denied);
    CHECK("validate_agent empty caps: returns true", valid3 == true);
}

void test_dispatcher_deny_default()
{
    AgentRegistry registry;
    register_native_agents(registry);

    SecurityPolicy policy; // deny-by-default, sin load_defaults
    Dispatcher dispatcher(registry, &policy);

    AgentRequest req;
    req.agent_id = 1; // sum
    req.input = nlohmann::json::object({{"a", 1}, {"b", 2}});

    AgentResult result = dispatcher.dispatch(req);

    CHECK("dispatcher deny default: status FAILED", result.status == AgentStatus::FAILED);
    CHECK("dispatcher deny default: error.code SECURITY_DENIED", result.error.has_value() && result.error->code == AgentErrorCode::SECURITY_DENIED);
    CHECK("dispatcher deny default: error.message contains math.sum", result.error.has_value() && result.error->message.find("math.sum") != std::string::npos);
    CHECK("dispatcher deny default: output empty", result.output.empty());
}

void test_dispatcher_allow_math_sum()
{
    AgentRegistry registry;
    register_native_agents(registry);

    SecurityPolicy policy;
    policy.load_defaults();
    policy.set_allowed("math.sum", true);
    // math.subtract sigue en false (load_defaults no lo habilita)

    Dispatcher dispatcher(registry, &policy);

    // Test math.sum permitido
    AgentRequest req1;
    req1.agent_id = 1; // sum
    req1.input = nlohmann::json::object({{"a", 1}, {"b", 2}});

    AgentResult result1 = dispatcher.dispatch(req1);

    CHECK("dispatcher allow math.sum: status SUCCESS", result1.status == AgentStatus::SUCCESS);
    CHECK("dispatcher allow math.sum: output.result == 3.0", result1.output["result"] == 3.0);

    // Test math.subtract denegado
    AgentRequest req2;
    req2.agent_id = 2; // subtract
    req2.input = nlohmann::json::object({{"a", 5}, {"b", 3}});

    AgentResult result2 = dispatcher.dispatch(req2);

    CHECK("dispatcher deny math.subtract: status FAILED", result2.status == AgentStatus::FAILED);
    CHECK("dispatcher deny math.subtract: error.code SECURITY_DENIED", result2.error.has_value() && result2.error->code == AgentErrorCode::SECURITY_DENIED);
}

void test_dispatcher_no_policy()
{
    AgentRegistry registry;
    register_native_agents(registry);

    // Dispatcher sin política (nullptr) - compatibilidad: no restringe
    Dispatcher dispatcher(registry, nullptr);

    AgentRequest req;
    req.agent_id = 1; // sum
    req.input = nlohmann::json::object({{"a", 1}, {"b", 2}});

    AgentResult result = dispatcher.dispatch(req);

    CHECK("dispatcher no policy: status SUCCESS", result.status == AgentStatus::SUCCESS);
    CHECK("dispatcher no policy: output.result == 3.0", result.output["result"] == 3.0);
}

void test_dispatcher_mock_counting_agent()
{
    AgentRegistry registry;
    std::atomic<int> counter{0};

    // Registrar MockCountingAgent con id 200 y capability "custom.op"
    MockCountingAgent counting_agent(counter);
    AgentDescriptor desc;
    desc.id = 200;
    desc.name = "CountingAgent";
    desc.description = "Test counting agent";
    desc.version = "1.0.0";
    desc.input_schema = nlohmann::json::object({{"type", "object"}});
    desc.output_schema = nlohmann::json::object({{"type", "object"}});
    desc.context_requirements = {};
    desc.capabilities = {"custom.op"};
    desc.agent = &counting_agent;

    CHECK("register mock counting agent", registry.register_agent(desc));

    // Política con custom.op = false
    SecurityPolicy policy_deny;
    policy_deny.set_allowed("custom.op", false);
    Dispatcher dispatcher_deny(registry, &policy_deny);

    AgentRequest req1;
    req1.agent_id = 200;
    req1.input = nlohmann::json::object();

    AgentResult result1 = dispatcher_deny.dispatch(req1);

    CHECK("mock agent deny: status FAILED", result1.status == AgentStatus::FAILED);
    CHECK("mock agent deny: error.code SECURITY_DENIED", result1.error.has_value() && result1.error->code == AgentErrorCode::SECURITY_DENIED);
    CHECK("mock agent deny: counter == 0 (agent NOT executed)", counter == 0);

    // Política con custom.op = true
    SecurityPolicy policy_allow;
    policy_allow.set_allowed("custom.op", true);
    Dispatcher dispatcher_allow(registry, &policy_allow);

    AgentRequest req2;
    req2.agent_id = 200;
    req2.input = nlohmann::json::object();

    AgentResult result2 = dispatcher_allow.dispatch(req2);

    CHECK("mock agent allow: status SUCCESS", result2.status == AgentStatus::SUCCESS);
    CHECK("mock agent allow: counter == 1 (agent executed)", counter == 1);
}

int main()
{
    test_deny_by_default();
    test_load_defaults();
    test_set_allowed();
    test_from_config();
    test_validate_agent();
    test_dispatcher_deny_default();
    test_dispatcher_allow_math_sum();
    test_dispatcher_no_policy();
    test_dispatcher_mock_counting_agent();

    std::cout << g_passed << " passed, " << g_failed << " failed\n";
    return g_failed == 0 ? 0 : 1;
}