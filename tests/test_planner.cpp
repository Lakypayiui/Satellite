// Mini framework de test para planner (FASE 7)
// Sin dependencias externas: solo C++17 estándar

#include <iostream>
#include <string>
#include <vector>
#include <memory>

#include <json.hpp>
#include "planner/Planner.h"
#include "core/catalog/AgentCatalog.h"
#include "core/agents/NativeAgents.h"
#include "core/registry/AgentRegistry.h"
#include "llm/ILLMProvider.h"
#include "llm/LLMTypes.h"

using namespace satellite::planner;
using namespace satellite::core::agent;
using namespace satellite::core::catalog;
using namespace satellite::core::agents;
using namespace satellite::core::registry;
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

// FakeProvider para tests de plan_goal y polimorfismo
class FakeProvider : public ILLMProvider
{
public:
    std::string name() const override { return "fake"; }

    LLMResponse complete(const LLMRequest&) override
    {
        return response_;
    }

    void set_response(const LLMResponse& resp) { response_ = resp; }

private:
    LLMResponse response_;
};

void test_validate()
{
    Planner planner;

    // 1. plan vacío true
    Plan empty_plan;
    empty_plan.goal = "";
    std::string err;
    CHECK("validate: empty plan", planner.validate(empty_plan, err));

    // 2. paso válido {agent_id 1, order 0} true
    Plan valid_plan;
    valid_plan.goal = "test";
    PlanStep step;
    step.agent_id = 1;
    step.order = 0;
    step.description = "valid step";
    valid_plan.steps.push_back(step);
    err.clear();
    CHECK("validate: valid step agent_id=1 order=0", planner.validate(valid_plan, err));

    // 3. agent_id 0 false
    Plan bad_agent_id;
    bad_agent_id.goal = "test";
    PlanStep step_bad;
    step_bad.agent_id = 0;
    step_bad.order = 0;
    bad_agent_id.steps.push_back(step_bad);
    err.clear();
    CHECK("validate: agent_id 0 rejects", !planner.validate(bad_agent_id, err));

    // 4. dep 5 en plan de 1 paso false
    Plan bad_dep;
    bad_dep.goal = "test";
    PlanStep step_dep;
    step_dep.agent_id = 1;
    step_dep.order = 0;
    step_dep.dependencies.push_back(5);
    bad_dep.steps.push_back(step_dep);
    err.clear();
    CHECK("validate: dependency index >= steps.size() rejects", !planner.validate(bad_dep, err));

    // 5. auto-dep {0} en step 0 false
    Plan self_dep;
    self_dep.goal = "test";
    PlanStep step_self;
    step_self.agent_id = 1;
    step_self.order = 0;
    step_self.dependencies.push_back(0);
    self_dep.steps.push_back(step_self);
    err.clear();
    CHECK("validate: self-dependency rejects", !planner.validate(self_dep, err));

    // 6. deps {0,0} false (duplicados)
    Plan dup_dep;
    dup_dep.goal = "test";
    PlanStep step_dup;
    step_dup.agent_id = 1;
    step_dup.order = 0;
    step_dup.dependencies = {0, 0};
    dup_dep.steps.push_back(step_dup);
    err.clear();
    CHECK("validate: duplicate dependencies rejects", !planner.validate(dup_dep, err));

    // 7. orders {0,0} en 2 pasos false (duplicados)
    Plan dup_order;
    dup_order.goal = "test";
    PlanStep s1, s2;
    s1.agent_id = 1;
    s1.order = 0;
    s2.agent_id = 2;
    s2.order = 0;
    dup_order.steps = {s1, s2};
    err.clear();
    CHECK("validate: duplicate orders rejects", !planner.validate(dup_order, err));

    // 8. orders {1,0} true (permutación válida)
    Plan valid_order;
    valid_order.goal = "test";
    PlanStep s3, s4;
    s3.agent_id = 1;
    s3.order = 1;
    s4.agent_id = 2;
    s4.order = 0;
    valid_order.steps = {s3, s4};
    err.clear();
    CHECK("validate: valid order permutation {1,0}", planner.validate(valid_order, err));
}

void test_execution_order()
{
    Planner planner;

    // 1. lineal [A{}, B{0}, C{1}] → {0,1,2}
    Plan lineal;
    lineal.goal = "lineal";
    PlanStep A, B, C;
    A.agent_id = 1; A.order = 0; A.description = "A";
    B.agent_id = 2; B.order = 1; B.dependencies = {0}; B.description = "B";
    C.agent_id = 3; C.order = 2; C.dependencies = {1}; C.description = "C";
    lineal.steps = {A, B, C};
    std::string err;
    std::vector<std::size_t> order;
    CHECK("execution_order: lineal returns true", planner.execution_order(lineal, order, err));
    CHECK("execution_order: lineal size 3", order.size() == 3);
    CHECK("execution_order: lineal [0,1,2]", order[0] == 0 && order[1] == 1 && order[2] == 2);

    // 2. diamante [A{}, B{0}, C{0}, D{1,2}] → first==0, last==3, {1,2} en medio
    Plan diamond;
    diamond.goal = "diamond";
    PlanStep DA, DB, DC, DD;
    DA.agent_id = 1; DA.order = 0; DA.description = "A";
    DB.agent_id = 2; DB.order = 1; DB.dependencies = {0}; DB.description = "B";
    DC.agent_id = 3; DC.order = 2; DC.dependencies = {0}; DC.description = "C";
    DD.agent_id = 4; DD.order = 3; DD.dependencies = {1, 2}; DD.description = "D";
    diamond.steps = {DA, DB, DC, DD};
    err.clear();
    order.clear();
    CHECK("execution_order: diamond returns true", planner.execution_order(diamond, order, err));
    CHECK("execution_order: diamond size 4", order.size() == 4);
    CHECK("execution_order: diamond first==0", order[0] == 0);
    CHECK("execution_order: diamond last==3", order[3] == 3);
    // 1 y 2 pueden estar en cualquier orden en el medio
    CHECK("execution_order: diamond middle has 1 and 2",
          (order[1] == 1 && order[2] == 2) || (order[1] == 2 && order[2] == 1));

    // 3. ciclo [A{1}, B{0}] → false + err no vacío
    Plan cycle;
    cycle.goal = "cycle";
    PlanStep cycA, cycB;
    cycA.agent_id = 1; cycA.order = 0; cycA.dependencies = {1}; cycA.description = "A";
    cycB.agent_id = 2; cycB.order = 1; cycB.dependencies = {0}; cycB.description = "B";
    cycle.steps = {cycA, cycB};
    err.clear();
    order.clear();
    CHECK("execution_order: cycle returns false", !planner.execution_order(cycle, order, err));
    CHECK("execution_order: cycle err not empty", !err.empty());
}

void test_from_json()
{
    Planner planner;

    // 1. JSON válido con goal y steps
    nlohmann::json j = {
        {"goal", "sumar"},
        {"steps", {
            {{"agent_id", 1}, {"input", {{"a", 2}, {"b", 3}}}},
            {{"agent_id", 3}, {"dependencies", {0}}}
        }}
    };
    Plan plan;
    std::string err;
    CHECK("from_json: valid returns true", planner.from_json(j, plan, err));
    CHECK("from_json: goal is sumar", plan.goal == "sumar");
    CHECK("from_json: 2 steps", plan.steps.size() == 2);
    CHECK("from_json: step 0 agent_id 1", plan.steps[0].agent_id == 1);
    CHECK("from_json: step 0 input a=2", plan.steps[0].input["a"] == 2);
    CHECK("from_json: step 1 dependencies[0]=0", plan.steps[1].dependencies[0] == 0);

    // 2. sin "steps" false
    nlohmann::json no_steps = {{"goal", "test"}};
    err.clear();
    CHECK("from_json: missing steps rejects", !planner.from_json(no_steps, plan, err));

    // 3. step sin agent_id false
    nlohmann::json no_agent_id = {
        {"goal", "test"},
        {"steps", {{{"input", {}}}}}
    };
    err.clear();
    CHECK("from_json: step missing agent_id rejects", !planner.from_json(no_agent_id, plan, err));

    // 4. texto no-JSON false
    nlohmann::json not_json = "no es un objeto";
    err.clear();
    CHECK("from_json: non-object rejects", !planner.from_json(not_json, plan, err));
}

void test_plan_goal()
{
    // FakeProvider que devuelve JSON válido
    FakeProvider provider_ok;
    provider_ok.set_response(LLMResponse{
        true,
        "{\"goal\":\"g\",\"steps\":[{\"agent_id\":1,\"input\":{\"a\":1,\"b\":2}}]}",
        "", 0, 0, 0, ""
    });

    AgentRegistry registry;
    register_native_agents(registry);
    AgentCatalog catalog(registry);

    Planner planner;
    Plan plan;
    std::string err;

    // 1. ok con JSON válido
    CHECK("plan_goal: fake provider ok returns true",
          planner.plan_goal("test goal", catalog, provider_ok, plan, err));
    CHECK("plan_goal: 1 step", plan.steps.size() == 1);
    CHECK("plan_goal: step agent_id 1", plan.steps[0].agent_id == 1);

    // 2. FakeProvider con text "no json" → false
    FakeProvider provider_bad_json;
    provider_bad_json.set_response(LLMResponse{
        true,
        "no json",
        "", 0, 0, 0, ""
    });
    err.clear();
    CHECK("plan_goal: bad json returns false", !planner.plan_goal("test", catalog, provider_bad_json, plan, err));

    // 3. FakeProvider con ok=false, error_message "boom" → false y err contiene "boom"
    FakeProvider provider_fail;
    provider_fail.set_response(LLMResponse{
        false,
        "",
        "", 0, 0, 0, "boom"
    });
    err.clear();
    CHECK("plan_goal: llm error returns false", !planner.plan_goal("test", catalog, provider_fail, plan, err));
    CHECK("plan_goal: err contains boom", err.find("boom") != std::string::npos);
}

void test_llm_provider_polymorphism()
{
    // 5. ILLMProvider* polimórfico con FakeProvider → name() == "fake"
    FakeProvider fake;
    ILLMProvider* provider_ptr = &fake;
    CHECK("ILLMProvider* name() == fake", provider_ptr->name() == "fake");

    // Verificar que complete() también funciona polimórficamente
    LLMResponse resp = provider_ptr->complete(LLMRequest{});
    CHECK("ILLMProvider* complete() works", resp.ok == false); // default response is ok=false
}

int main()
{
    test_validate();
    test_execution_order();
    test_from_json();
    test_plan_goal();
    test_llm_provider_polymorphism();

    std::cout << g_passed << " passed, " << g_failed << " failed\n";
    return g_failed == 0 ? 0 : 1;
}