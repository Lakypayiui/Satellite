// Tests para agentes nativos (FASE 4)
// Sin dependencias externas: solo C++17 estándar

#include <iostream>
#include <string>

#include <json.hpp>
#include "core/agents/NativeAgents.h"
#include "core/registry/AgentRegistry.h"
#include "core/dispatcher/Dispatcher.h"
#include "core/agent/AgentRequest.h"
#include "core/agent/AgentResult.h"
#include "core/agent/AgentStatus.h"
#include "core/agent/AgentError.h"

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

void test_register_native_agents()
{
    AgentRegistry registry;
    register_native_agents(registry);

    // 1. has_agent(1..5) todos true
    CHECK("has_agent(1) true", registry.has_agent(1));
    CHECK("has_agent(2) true", registry.has_agent(2));
    CHECK("has_agent(3) true", registry.has_agent(3));
    CHECK("has_agent(4) true", registry.has_agent(4));
    CHECK("has_agent(5) true", registry.has_agent(5));

    // 2. find_agent nombres correctos
    const auto d1 = registry.find_agent(1);
    const auto d4 = registry.find_agent(4);
    const auto d5 = registry.find_agent(5);

    CHECK("find_agent(1)->name == \"sum\"", d1 != nullptr && d1->name == "sum");
    CHECK("find_agent(4)->name == \"divide\"", d4 != nullptr && d4->name == "divide");
    CHECK("find_agent(5)->name == \"average\"", d5 != nullptr && d5->name == "average");

    // 3. capabilities correctas
    const auto d3 = registry.find_agent(3);
    CHECK("find_agent(3)->capabilities[0] == \"math.multiply\"", d3 != nullptr && d3->capabilities.size() >= 1 && d3->capabilities[0] == "math.multiply");
    CHECK("find_agent(1)->capabilities[0] == \"math.sum\"", d1 != nullptr && d1->capabilities.size() >= 1 && d1->capabilities[0] == "math.sum");
    CHECK("find_agent(2)->capabilities[0] == \"math.subtract\"", registry.find_agent(2) != nullptr && registry.find_agent(2)->capabilities.size() >= 1 && registry.find_agent(2)->capabilities[0] == "math.subtract");
    CHECK("find_agent(4)->capabilities[0] == \"math.divide\"", d4 != nullptr && d4->capabilities.size() >= 1 && d4->capabilities[0] == "math.divide");
    CHECK("find_agent(5)->capabilities[0] == \"math.average\"", d5 != nullptr && d5->capabilities.size() >= 1 && d5->capabilities[0] == "math.average");
}

AgentResult dispatch_request(Dispatcher& dispatcher, AgentID id, const nlohmann::json& input)
{
    AgentRequest req;
    req.agent_id = id;
    req.input = input;
    return dispatcher.dispatch(req);
}

void test_dispatcher_sum()
{
    AgentRegistry registry;
    register_native_agents(registry);
    Dispatcher dispatcher(registry);

    // sum: {"a":3,"b":4} → SUCCESS, output["result"] == 7.0
    AgentResult r1 = dispatch_request(dispatcher, 1, {{"a", 3}, {"b", 4}});
    CHECK("sum 3+4: status SUCCESS", r1.status == AgentStatus::SUCCESS);
    CHECK("sum 3+4: output.result == 7.0", r1.output.contains("result") && r1.output["result"] == 7.0);
    CHECK("sum 3+4: agent_id == 1", r1.agent_id == 1);

    // sum: {"a":-1,"b":-2} → -3.0
    AgentResult r2 = dispatch_request(dispatcher, 1, {{"a", -1}, {"b", -2}});
    CHECK("sum -1+-2: status SUCCESS", r2.status == AgentStatus::SUCCESS);
    CHECK("sum -1+-2: output.result == -3.0", r2.output.contains("result") && r2.output["result"] == -3.0);
    CHECK("sum -1+-2: agent_id == 1", r2.agent_id == 1);
}

void test_dispatcher_subtract()
{
    AgentRegistry registry;
    register_native_agents(registry);
    Dispatcher dispatcher(registry);

    // subtract: {"a":10,"b":4} → 6.0
    AgentResult r1 = dispatch_request(dispatcher, 2, {{"a", 10}, {"b", 4}});
    CHECK("subtract 10-4: status SUCCESS", r1.status == AgentStatus::SUCCESS);
    CHECK("subtract 10-4: output.result == 6.0", r1.output.contains("result") && r1.output["result"] == 6.0);
    CHECK("subtract 10-4: agent_id == 2", r1.agent_id == 2);

    // subtract: {"a":3,"b":10} → -7.0
    AgentResult r2 = dispatch_request(dispatcher, 2, {{"a", 3}, {"b", 10}});
    CHECK("subtract 3-10: status SUCCESS", r2.status == AgentStatus::SUCCESS);
    CHECK("subtract 3-10: output.result == -7.0", r2.output.contains("result") && r2.output["result"] == -7.0);
    CHECK("subtract 3-10: agent_id == 2", r2.agent_id == 2);
}

void test_dispatcher_multiply()
{
    AgentRegistry registry;
    register_native_agents(registry);
    Dispatcher dispatcher(registry);

    // multiply: {"a":3,"b":4} → 12.0
    AgentResult r = dispatch_request(dispatcher, 3, {{"a", 3}, {"b", 4}});
    CHECK("multiply 3*4: status SUCCESS", r.status == AgentStatus::SUCCESS);
    CHECK("multiply 3*4: output.result == 12.0", r.output.contains("result") && r.output["result"] == 12.0);
    CHECK("multiply 3*4: agent_id == 3", r.agent_id == 3);
}

void test_dispatcher_divide()
{
    AgentRegistry registry;
    register_native_agents(registry);
    Dispatcher dispatcher(registry);

    // divide: {"a":10,"b":4} → 2.5
    AgentResult r1 = dispatch_request(dispatcher, 4, {{"a", 10}, {"b", 4}});
    CHECK("divide 10/4: status SUCCESS", r1.status == AgentStatus::SUCCESS);
    CHECK("divide 10/4: output.result == 2.5", r1.output.contains("result") && r1.output["result"] == 2.5);
    CHECK("divide 10/4: agent_id == 4", r1.agent_id == 4);

    // divide: {"a":-10,"b":2} → -5.0
    AgentResult r2 = dispatch_request(dispatcher, 4, {{"a", -10}, {"b", 2}});
    CHECK("divide -10/2: status SUCCESS", r2.status == AgentStatus::SUCCESS);
    CHECK("divide -10/2: output.result == -5.0", r2.output.contains("result") && r2.output["result"] == -5.0);
    CHECK("divide -10/2: agent_id == 4", r2.agent_id == 4);

    // divide: {"a":10,"b":-2} → -5.0
    AgentResult r3 = dispatch_request(dispatcher, 4, {{"a", 10}, {"b", -2}});
    CHECK("divide 10/-2: status SUCCESS", r3.status == AgentStatus::SUCCESS);
    CHECK("divide 10/-2: output.result == -5.0", r3.output.contains("result") && r3.output["result"] == -5.0);
    CHECK("divide 10/-2: agent_id == 4", r3.agent_id == 4);

    // divide: {"a":0,"b":5} → 0.0
    AgentResult r4 = dispatch_request(dispatcher, 4, {{"a", 0}, {"b", 5}});
    CHECK("divide 0/5: status SUCCESS", r4.status == AgentStatus::SUCCESS);
    CHECK("divide 0/5: output.result == 0.0", r4.output.contains("result") && r4.output["result"] == 0.0);
    CHECK("divide 0/5: agent_id == 4", r4.agent_id == 4);

    // divide con b==0: {"a":1,"b":0} → VALIDATION_ERROR (schema lo captura)
    AgentResult r5 = dispatch_request(dispatcher, 4, {{"a", 1}, {"b", 0}});
    CHECK("divide 1/0: status VALIDATION_ERROR", r5.status == AgentStatus::VALIDATION_ERROR);
    CHECK("divide 1/0: error.code VALIDATION_ERROR", r5.error.has_value() && r5.error->code == AgentErrorCode::VALIDATION_ERROR);
    CHECK("divide 1/0: agent_id == 4", r5.agent_id == 4);
}

void test_dispatcher_average()
{
    AgentRegistry registry;
    register_native_agents(registry);
    Dispatcher dispatcher(registry);

    // average: {"values":[1,2,3]} → 2.0
    AgentResult r1 = dispatch_request(dispatcher, 5, {{"values", nlohmann::json::array({1, 2, 3})}});
    CHECK("average [1,2,3]: status SUCCESS", r1.status == AgentStatus::SUCCESS);
    CHECK("average [1,2,3]: output.result == 2.0", r1.output.contains("result") && r1.output["result"] == 2.0);
    CHECK("average [1,2,3]: agent_id == 5", r1.agent_id == 5);

    // average: {"values":[1.5,2.5]} → 2.0
    AgentResult r2 = dispatch_request(dispatcher, 5, {{"values", nlohmann::json::array({1.5, 2.5})}});
    CHECK("average [1.5,2.5]: status SUCCESS", r2.status == AgentStatus::SUCCESS);
    CHECK("average [1.5,2.5]: output.result == 2.0", r2.output.contains("result") && r2.output["result"] == 2.0);
    CHECK("average [1.5,2.5]: agent_id == 5", r2.agent_id == 5);

    // average: {"values":[-2,0,2]} → 0.0
    AgentResult r3 = dispatch_request(dispatcher, 5, {{"values", nlohmann::json::array({-2, 0, 2})}});
    CHECK("average [-2,0,2]: status SUCCESS", r3.status == AgentStatus::SUCCESS);
    CHECK("average [-2,0,2]: output.result == 0.0", r3.output.contains("result") && r3.output["result"] == 0.0);
    CHECK("average [-2,0,2]: agent_id == 5", r3.agent_id == 5);

    // average vacío: {"values":[]} → VALIDATION_ERROR (minItems 1)
    AgentResult r4 = dispatch_request(dispatcher, 5, {{"values", nlohmann::json::array()}});
    CHECK("average []: status VALIDATION_ERROR", r4.status == AgentStatus::VALIDATION_ERROR);
    CHECK("average []: error.code VALIDATION_ERROR", r4.error.has_value() && r4.error->code == AgentErrorCode::VALIDATION_ERROR);
    CHECK("average []: agent_id == 5", r4.agent_id == 5);

    // average con elemento no numérico: {"values":[1,"x"]} → VALIDATION_ERROR
    AgentResult r5 = dispatch_request(dispatcher, 5, {{"values", nlohmann::json::array({1, "x"})}});
    CHECK("average [1,\"x\"]: status VALIDATION_ERROR", r5.status == AgentStatus::VALIDATION_ERROR);
    CHECK("average [1,\"x\"]: error.code VALIDATION_ERROR", r5.error.has_value() && r5.error->code == AgentErrorCode::VALIDATION_ERROR);
    CHECK("average [1,\"x\"]: agent_id == 5", r5.agent_id == 5);
}

void test_direct_sum_agent()
{
    SumAgent agent;
    AgentRequest req;
    req.agent_id = 1;
    req.input = nlohmann::json::object(); // sin claves

    AgentResult result = agent.execute(req);

    CHECK("SumAgent directo sin input: status FAILED", result.status == AgentStatus::FAILED);
    CHECK("SumAgent directo sin input: error.code INVALID_REQUEST", result.error.has_value() && result.error->code == AgentErrorCode::INVALID_REQUEST);
    CHECK("SumAgent directo sin input: agent_id == 1", result.agent_id == 1);
}

void test_direct_divide_agent_div_by_zero()
{
    DivideAgent agent;
    AgentRequest req;
    req.agent_id = 4;
    req.input = {{"a", 1}, {"b", 0}};

    AgentResult result = agent.execute(req);

    CHECK("DivideAgent directo div by zero: status FAILED", result.status == AgentStatus::FAILED);
    CHECK("DivideAgent directo div by zero: error.code EXECUTION_FAILED", result.error.has_value() && result.error->code == AgentErrorCode::EXECUTION_FAILED);
    CHECK("DivideAgent directo div by zero: agent_id == 4", result.agent_id == 4);
}

void test_direct_average_agent_empty()
{
    AverageAgent agent;
    AgentRequest req;
    req.agent_id = 5;
    req.input = {{"values", nlohmann::json::array()}};

    AgentResult result = agent.execute(req);

    CHECK("AverageAgent directo empty: status FAILED", result.status == AgentStatus::FAILED);
    CHECK("AverageAgent directo empty: error.code EXECUTION_FAILED", result.error.has_value() && result.error->code == AgentErrorCode::EXECUTION_FAILED);
    CHECK("AverageAgent directo empty: agent_id == 5", result.agent_id == 5);
}

int main()
{
    test_register_native_agents();
    test_dispatcher_sum();
    test_dispatcher_subtract();
    test_dispatcher_multiply();
    test_dispatcher_divide();
    test_dispatcher_average();
    test_direct_sum_agent();
    test_direct_divide_agent_div_by_zero();
    test_direct_average_agent_empty();

    std::cout << g_passed << " passed, " << g_failed << " failed\n";
    return g_failed == 0 ? 0 : 1;
}