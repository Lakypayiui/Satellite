// Tests para Dispatcher (FASE 3)
// Sin dependencias externas: solo C++17 estándar

#include <iostream>
#include <string>
#include <atomic>

#include <json.hpp>
#include "core/dispatcher/Dispatcher.h"
#include "core/registry/AgentRegistry.h"
#include "core/agent/IAgent.h"
#include "core/agent/AgentRequest.h"
#include "core/agent/AgentResult.h"
#include "core/agent/AgentStatus.h"
#include "core/agent/AgentError.h"
#include "core/agent/AgentDescriptor.h"

using namespace satellite::core::agent;
using namespace satellite::core::registry;
using namespace satellite::core::dispatcher;

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

struct MockSumAgent : IAgent
{
    AgentResult execute(const AgentRequest& request) override
    {
        AgentResult result;
        result.status = AgentStatus::SUCCESS;
        double a = request.input["a"].get<double>();
        double b = request.input["b"].get<double>();
        result.output = nlohmann::json::object({{"result", a + b}});
        return result;
    }
};

struct MockCountingAgent : IAgent
{
    static std::atomic<int> call_count;

    AgentResult execute(const AgentRequest&) override
    {
        ++call_count;
        AgentResult result;
        result.status = AgentStatus::SUCCESS;
        return result;
    }
};

std::atomic<int> MockCountingAgent::call_count{0};

struct MockThrowingAgent : IAgent
{
    AgentResult execute(const AgentRequest&) override
    {
        throw std::runtime_error("boom");
    }
};

struct MockNullResultAgent : IAgent
{
    AgentResult execute(const AgentRequest&) override
    {
        AgentResult result;
        result.agent_id = UNKNOWN_AGENT_ID;
        result.duration_ms = 0.0;
        result.status = AgentStatus::SUCCESS;
        return result;
    }
};

AgentDescriptor make_descriptor(AgentID id, const std::string& name, const nlohmann::json& schema, IAgent* impl)
{
    AgentDescriptor desc;
    desc.id = id;
    desc.name = name;
    desc.description = "Test agent";
    desc.version = "1.0.0";
    desc.input_schema = schema;
    desc.output_schema = nlohmann::json::object({{"type", "object"}});
    desc.capabilities = {"test.cap"};
    desc.context_requirements = {};
    desc.agent = impl;
    return desc;
}

nlohmann::json make_sum_schema()
{
    return nlohmann::json{
        {"type", "object"},
        {"properties", {
            {"a", {{"type", "number"}}},
            {"b", {{"type", "number"}}}
        }},
        {"required", {"a", "b"}}
    };
}

} // namespace

void test_unknown_agent()
{
    AgentRegistry registry;
    Dispatcher dispatcher(registry);

    AgentRequest req;
    req.agent_id = 999;
    req.input = nlohmann::json::object();

    AgentResult result = dispatcher.dispatch(req);

    CHECK("unknown agent: status UNKNOWN_AGENT", result.status == AgentStatus::UNKNOWN_AGENT);
    CHECK("unknown agent: error.code UNKNOWN_AGENT", result.error.has_value() && result.error->code == AgentErrorCode::UNKNOWN_AGENT);
    CHECK("unknown agent: output empty", result.output.empty());
    CHECK("unknown agent: agent_id preserved", result.agent_id == 999);
}

void test_disabled_agent()
{
    AgentRegistry registry;
    MockCountingAgent counting_agent;
    MockCountingAgent::call_count = 0;

    nlohmann::json schema = make_sum_schema();
    AgentDescriptor desc = make_descriptor(2, "CountingAgent", schema, &counting_agent);
    CHECK("register counting agent", registry.register_agent(desc));
    CHECK("disable agent 2", registry.disable_agent(2));

    Dispatcher dispatcher(registry);

    AgentRequest req;
    req.agent_id = 2;
    req.input = {{"a", 1}, {"b", 2}};

    AgentResult result = dispatcher.dispatch(req);

    CHECK("disabled agent: status DISABLED", result.status == AgentStatus::DISABLED);
    CHECK("disabled agent: error.code DISABLED_AGENT", result.error.has_value() && result.error->code == AgentErrorCode::DISABLED_AGENT);
    CHECK("disabled agent: agent not called", MockCountingAgent::call_count == 0);
}

void test_invalid_input()
{
    AgentRegistry registry;
    MockSumAgent sum_agent;
    int call_count = 0;

    struct CountingSumAgent : MockSumAgent
    {
        int& counter;
        CountingSumAgent(int& c) : counter(c) {}
        AgentResult execute(const AgentRequest& request) override
        {
            ++counter;
            return MockSumAgent::execute(request);
        }
    } counting_sum_agent(call_count);

    nlohmann::json schema = make_sum_schema();
    AgentDescriptor desc = make_descriptor(3, "SumAgent", schema, &counting_sum_agent);
    CHECK("register sum agent", registry.register_agent(desc));

    Dispatcher dispatcher(registry);

    AgentRequest req;
    req.agent_id = 3;
    req.input = {{"a", 1}}; // falta b

    AgentResult result = dispatcher.dispatch(req);

    CHECK("invalid input: status VALIDATION_ERROR", result.status == AgentStatus::VALIDATION_ERROR);
    CHECK("invalid input: error.code VALIDATION_ERROR", result.error.has_value() && result.error->code == AgentErrorCode::VALIDATION_ERROR);
    CHECK("invalid input: agent not called", call_count == 0);
}

void test_valid_input()
{
    AgentRegistry registry;
    MockSumAgent sum_agent;
    int call_count = 0;

    struct CountingSumAgent : MockSumAgent
    {
        int& counter;
        CountingSumAgent(int& c) : counter(c) {}
        AgentResult execute(const AgentRequest& request) override
        {
            ++counter;
            return MockSumAgent::execute(request);
        }
    } counting_sum_agent(call_count);

    nlohmann::json schema = make_sum_schema();
    AgentDescriptor desc = make_descriptor(4, "SumAgent", schema, &counting_sum_agent);
    CHECK("register sum agent", registry.register_agent(desc));

    Dispatcher dispatcher(registry);

    AgentRequest req;
    req.agent_id = 4;
    req.input = {{"a", 3}, {"b", 4}};

    AgentResult result = dispatcher.dispatch(req);

    CHECK("valid input: status SUCCESS", result.status == AgentStatus::SUCCESS);
    CHECK("valid input: output.result == 7.0", result.output["result"] == 7.0);
    CHECK("valid input: agent called", call_count == 1);
}

void test_null_agent_impl()
{
    AgentRegistry registry;
    nlohmann::json schema = make_sum_schema();
    AgentDescriptor desc = make_descriptor(5, "NullAgent", schema, nullptr);
    CHECK("register null agent", registry.register_agent(desc));

    Dispatcher dispatcher(registry);

    AgentRequest req;
    req.agent_id = 5;
    req.input = {{"a", 1}, {"b", 2}};

    AgentResult result = dispatcher.dispatch(req);

    CHECK("null agent impl: status FAILED", result.status == AgentStatus::FAILED);
    CHECK("null agent impl: error.code INTERNAL_ERROR", result.error.has_value() && result.error->code == AgentErrorCode::INTERNAL_ERROR);
}

void test_throwing_agent()
{
    AgentRegistry registry;
    MockThrowingAgent throwing_agent;

    nlohmann::json schema = make_sum_schema();
    AgentDescriptor desc = make_descriptor(6, "ThrowingAgent", schema, &throwing_agent);
    CHECK("register throwing agent", registry.register_agent(desc));

    Dispatcher dispatcher(registry);

    AgentRequest req;
    req.agent_id = 6;
    req.input = {{"a", 1}, {"b", 2}};

    AgentResult result = dispatcher.dispatch(req);

    CHECK("throwing agent: status FAILED", result.status == AgentStatus::FAILED);
    CHECK("throwing agent: error.code EXECUTION_FAILED", result.error.has_value() && result.error->code == AgentErrorCode::EXECUTION_FAILED);
    CHECK("throwing agent: error message contains boom", result.error.has_value() && result.error->message.find("boom") != std::string::npos);
}

void test_null_result_agent_normalization()
{
    AgentRegistry registry;
    MockNullResultAgent null_result_agent;

    nlohmann::json schema = make_sum_schema();
    AgentDescriptor desc = make_descriptor(7, "NullResultAgent", schema, &null_result_agent);
    CHECK("register null result agent", registry.register_agent(desc));

    Dispatcher dispatcher(registry);

    AgentRequest req;
    req.agent_id = 7;
    req.input = {{"a", 1}, {"b", 2}};

    AgentResult result = dispatcher.dispatch(req);

    CHECK("null result agent: status SUCCESS", result.status == AgentStatus::SUCCESS);
    CHECK("null result agent: agent_id normalized to request id", result.agent_id == 7);
    CHECK("null result agent: duration_ms > 0", result.duration_ms > 0.0);
}

void test_registry_unchanged_after_dispatch()
{
    AgentRegistry registry;
    MockSumAgent sum_agent;

    nlohmann::json schema = make_sum_schema();
    AgentDescriptor desc = make_descriptor(8, "SumAgent", schema, &sum_agent);
    CHECK("register sum agent", registry.register_agent(desc));

    Dispatcher dispatcher(registry);

    AgentRequest req;
    req.agent_id = 8;
    req.input = {{"a", 1}, {"b", 2}};

    AgentResult result = dispatcher.dispatch(req);

    CHECK("dispatch success", result.status == AgentStatus::SUCCESS);
    CHECK("registry still has agent", registry.has_agent(8));
}

int main()
{
    test_unknown_agent();
    test_disabled_agent();
    test_invalid_input();
    test_valid_input();
    test_null_agent_impl();
    test_throwing_agent();
    test_null_result_agent_normalization();
    test_registry_unchanged_after_dispatch();

    std::cout << g_passed << " passed, " << g_failed << " failed\n";
    return g_failed == 0 ? 0 : 1;
}