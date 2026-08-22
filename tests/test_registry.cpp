// Mini framework de test para core::registry (FASE 2)
// Sin dependencias externas: solo C++17 estándar

#include <iostream>
#include <string>
#include <vector>

#include <json.hpp>
#include "core/registry/AgentRegistry.h"
#include "core/agent/AgentDescriptor.h"

using namespace satellite::core::agent;
using namespace satellite::core::registry;

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

AgentDescriptor make_descriptor(AgentID id, const std::string& name)
{
    AgentDescriptor desc;
    desc.id = id;
    desc.name = name;
    desc.description = "Test agent " + std::to_string(id);
    desc.version = "1.0.0";
    desc.input_schema = nlohmann::json::object({{"type", "object"}});
    desc.output_schema = nlohmann::json::object({{"type", "object"}});
    desc.context_requirements = {};
    desc.capabilities = {"test.cap"};
    desc.agent = nullptr;
    return desc;
}

void test_empty_registry()
{
    AgentRegistry registry;

    CHECK("Empty registry: has_agent(1) == false", !registry.has_agent(1));
    CHECK("Empty registry: find_agent(1) == nullptr", registry.find_agent(1) == nullptr);
    CHECK("Empty registry: list_agents().empty()", registry.list_agents().empty());
    CHECK("Empty registry: is_enabled(1) == false", !registry.is_enabled(1));
    CHECK("Empty registry: enable_agent(1) == false", !registry.enable_agent(1));
    CHECK("Empty registry: disable_agent(1) == false", !registry.disable_agent(1));
    CHECK("Empty registry: unregister_agent(1) == false", !registry.unregister_agent(1));
}

void test_register_three_agents()
{
    AgentRegistry registry;

    AgentDescriptor d1 = make_descriptor(1, "agent_1");
    AgentDescriptor d2 = make_descriptor(2, "agent_2");
    AgentDescriptor d3 = make_descriptor(3, "agent_3");

    CHECK("Register agent 1 -> true", registry.register_agent(d1));
    CHECK("Register agent 2 -> true", registry.register_agent(d2));
    CHECK("Register agent 3 -> true", registry.register_agent(d3));

    CHECK("has_agent(1) == true", registry.has_agent(1));
    CHECK("has_agent(2) == true", registry.has_agent(2));
    CHECK("has_agent(3) == true", registry.has_agent(3));

    const AgentDescriptor* found = registry.find_agent(2);
    CHECK("find_agent(2)->name == \"agent_2\"", found != nullptr && found->name == "agent_2");

    CHECK("list_agents().size() == 3", registry.list_agents().size() == 3);
}

void test_duplicate_registration()
{
    AgentRegistry registry;

    AgentDescriptor d1 = make_descriptor(1, "agent_1");
    AgentDescriptor d2 = make_descriptor(2, "agent_2");

    registry.register_agent(d1);
    registry.register_agent(d2);

    AgentDescriptor dup = make_descriptor(2, "agent_2_dup");
    CHECK("Register duplicate id 2 -> false", !registry.register_agent(dup));

    AgentDescriptor zero = make_descriptor(UNKNOWN_AGENT_ID, "agent_zero");
    CHECK("Register id 0 (UNKNOWN_AGENT_ID) -> false", !registry.register_agent(zero));
}

void test_find_agent_fields_intact()
{
    AgentRegistry registry;

    AgentDescriptor desc = make_descriptor(42, "agent_42");
    desc.version = "2.5.0";
    desc.capabilities = {"cap.a", "cap.b"};
    desc.input_schema = nlohmann::json::object({{"type", "array"}});

    registry.register_agent(desc);

    const AgentDescriptor* found = registry.find_agent(42);

    CHECK("find_agent returns correct version", found != nullptr && found->version == "2.5.0");
    CHECK("find_agent returns correct capabilities[0]", found != nullptr && found->capabilities.size() > 0 && found->capabilities[0] == "cap.a");
    CHECK("find_agent returns correct input_schema[type]", found != nullptr && found->input_schema["type"] == "array");
}

void test_unregister_agent()
{
    AgentRegistry registry;

    AgentDescriptor d1 = make_descriptor(1, "agent_1");
    AgentDescriptor d2 = make_descriptor(2, "agent_2");
    AgentDescriptor d3 = make_descriptor(3, "agent_3");

    registry.register_agent(d1);
    registry.register_agent(d2);
    registry.register_agent(d3);

    CHECK("unregister_agent(2) -> true", registry.unregister_agent(2));
    CHECK("has_agent(2) -> false", !registry.has_agent(2));
    CHECK("find_agent(2) == nullptr", registry.find_agent(2) == nullptr);
    CHECK("unregister_agent(2) again -> false", !registry.unregister_agent(2));
    CHECK("list_agents().size() == 2", registry.list_agents().size() == 2);
}

void test_enable_disable()
{
    AgentRegistry registry;

    AgentDescriptor d3 = make_descriptor(3, "agent_3");
    registry.register_agent(d3);

    CHECK("disable_agent(3) -> true", registry.disable_agent(3));
    CHECK("is_enabled(3) -> false", !registry.is_enabled(3));
    CHECK("find_agent(3) != nullptr (still visible)", registry.find_agent(3) != nullptr);
    CHECK("disable_agent(3) again (idempotent) -> true", registry.disable_agent(3));
    CHECK("enable_agent(3) -> true", registry.enable_agent(3));
    CHECK("is_enabled(3) -> true", registry.is_enabled(3));
    CHECK("enable_agent(999) -> false", !registry.enable_agent(999));
    CHECK("disable_agent(999) -> false", !registry.disable_agent(999));
}

void test_list_agents_sorted()
{
    AgentRegistry registry;

    AgentDescriptor d1 = make_descriptor(1, "agent_1");
    registry.register_agent(d1);

    AgentDescriptor d5 = make_descriptor(5, "agent_5");
    AgentDescriptor d4 = make_descriptor(4, "agent_4");
    registry.register_agent(d5);
    registry.register_agent(d4);

    std::vector<AgentDescriptor> list = registry.list_agents();

    CHECK("list_agents size == 3", list.size() == 3);
    CHECK("list_agents[0].id == 1", list.size() >= 1 && list[0].id == 1);
    CHECK("list_agents[1].id == 4", list.size() >= 2 && list[1].id == 4);
    CHECK("list_agents[2].id == 5", list.size() >= 3 && list[2].id == 5);
}

void test_disabled_agent_in_list()
{
    AgentRegistry registry;

    AgentDescriptor d1 = make_descriptor(1, "agent_1");
    AgentDescriptor d5 = make_descriptor(5, "agent_5");
    registry.register_agent(d1);
    registry.register_agent(d5);

    registry.disable_agent(5);

    std::vector<AgentDescriptor> list = registry.list_agents();

    bool has_id_5 = false;
    for (const auto& a : list) {
        if (a.id == 5) {
            has_id_5 = true;
            break;
        }
    }

    CHECK("Disabled agent 5 still in list_agents", has_id_5);
}

int main()
{
    test_empty_registry();
    test_register_three_agents();
    test_duplicate_registration();
    test_find_agent_fields_intact();
    test_unregister_agent();
    test_enable_disable();
    test_list_agents_sorted();
    test_disabled_agent_in_list();

    std::cout << g_passed << " passed, " << g_failed << " failed\n";
    return g_failed == 0 ? 0 : 1;
}