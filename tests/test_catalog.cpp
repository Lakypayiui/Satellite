// Mini framework de test para core::catalog (FASE 7)
// Sin dependencias externas: solo C++17 estándar

#include <iostream>
#include <string>
#include <vector>

#include <json.hpp>
#include "core/catalog/AgentCatalog.h"
#include "core/registry/AgentRegistry.h"
#include "core/agents/NativeAgents.h"

using namespace satellite::core::agent;
using namespace satellite::core::registry;
using namespace satellite::core::catalog;
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

void test_catalog_with_native_agents()
{
    AgentRegistry registry;
    register_native_agents(registry);

    AgentCatalog c(registry);

    // c.size() == 5
    CHECK("Catalog size == 5", c.size() == 5);

    // to_json(): array de 5
    nlohmann::json json_result = c.to_json();
    CHECK("to_json() es array", json_result.is_array());
    CHECK("to_json() size == 5", json_result.size() == 5);

    // Cada entrada tiene las claves requeridas
    for (const auto& entry : json_result)
    {
        CHECK("Entry tiene id", entry.contains("id"));
        CHECK("Entry tiene name", entry.contains("name"));
        CHECK("Entry tiene description", entry.contains("description"));
        CHECK("Entry tiene input_schema", entry.contains("input_schema"));
        CHECK("Entry tiene output_schema", entry.contains("output_schema"));
        CHECK("Entry tiene capabilities", entry.contains("capabilities"));
        CHECK("Entry tiene context_requirements", entry.contains("context_requirements"));
        CHECK("Entry NO tiene clave agent", !entry.contains("agent"));
    }

    // NO contiene el string "IAgent" en el JSON serializado
    std::string json_str = json_result.dump();
    CHECK("JSON no contiene string \"IAgent\"", json_str.find("IAgent") == std::string::npos);

    // El id de la entrada 0 == 1 (orden por id, el registry ya ordena)
    CHECK("Entry 0 id == 1", json_result[0]["id"] == 1);

    // capabilities[0] de la entrada de id 4 == "math.divide"
    bool found_id4 = false;
    for (const auto& entry : json_result)
    {
        if (entry["id"] == 4)
        {
            found_id4 = true;
            CHECK("Entry id 4 capabilities[0] == math.divide", entry["capabilities"].size() > 0 && entry["capabilities"][0] == "math.divide");
            break;
        }
    }
    CHECK("Encontrada entrada id 4", found_id4);

    // to_prompt(): contiene "sum", "subtract", "multiply", "divide", "average", "math.sum", "math.divide", "[4]" y "[1]"
    std::string prompt = c.to_prompt();
    CHECK("to_prompt() contiene sum", prompt.find("sum") != std::string::npos);
    CHECK("to_prompt() contiene subtract", prompt.find("subtract") != std::string::npos);
    CHECK("to_prompt() contiene multiply", prompt.find("multiply") != std::string::npos);
    CHECK("to_prompt() contiene divide", prompt.find("divide") != std::string::npos);
    CHECK("to_prompt() contiene average", prompt.find("average") != std::string::npos);
    CHECK("to_prompt() contiene math.sum", prompt.find("math.sum") != std::string::npos);
    CHECK("to_prompt() contiene math.divide", prompt.find("math.divide") != std::string::npos);
    CHECK("to_prompt() contiene [4]", prompt.find("[4]") != std::string::npos);
    CHECK("to_prompt() contiene [1]", prompt.find("[1]") != std::string::npos);

    // NO contiene "IAgent" ni "NativeAgents"
    CHECK("to_prompt() NO contiene IAgent", prompt.find("IAgent") == std::string::npos);
    CHECK("to_prompt() NO contiene NativeAgents", prompt.find("NativeAgents") == std::string::npos);

    // describe_agent(1) contiene "sum"
    std::string desc1 = c.describe_agent(1);
    CHECK("describe_agent(1) contiene sum", desc1.find("sum") != std::string::npos);

    // describe_agent(999) == "(no disponible)"
    std::string desc999 = c.describe_agent(999);
    CHECK("describe_agent(999) == (no disponible)", desc999 == "(no disponible)");
}

void test_catalog_with_disabled_agent()
{
    AgentRegistry registry;
    register_native_agents(registry);

    // Deshabilitar agente 3 (multiply)
    registry.disable_agent(3);

    // c2 (nuevo catálogo sobre el mismo registry) -> size() == 4
    AgentCatalog c2(registry);
    CHECK("Catalog con agente deshabilitado size == 4", c2.size() == 4);

    // to_prompt() NO contiene "multiply"
    std::string prompt = c2.to_prompt();
    CHECK("to_prompt() NO contiene multiply", prompt.find("multiply") == std::string::npos);

    // describe_agent(3) == "(no disponible)"
    std::string desc3 = c2.describe_agent(3);
    CHECK("describe_agent(3) == (no disponible)", desc3 == "(no disponible)");
}

void test_catalog_empty_registry()
{
    AgentRegistry registry; // vacío
    AgentCatalog c(registry);

    // size() == 0
    CHECK("Empty catalog size == 0", c.size() == 0);

    // to_json() == []
    nlohmann::json json_result = c.to_json();
    CHECK("Empty catalog to_json() es array", json_result.is_array());
    CHECK("Empty catalog to_json() == []", json_result.empty());

    // to_prompt() vacío (o solo cabecera si la define) - verificar que no contenga "[1]"
    std::string prompt = c.to_prompt();
    CHECK("Empty catalog to_prompt() no contiene [1]", prompt.find("[1]") == std::string::npos);
}

void test_catalog_json_roundtrip()
{
    AgentRegistry registry;
    register_native_agents(registry);

    AgentCatalog c(registry);
    nlohmann::json json_result = c.to_json();

    // Serializar a string
    std::string json_str = json_result.dump();

    // Parsear de vuelta
    nlohmann::json parsed = nlohmann::json::parse(json_str);

    // Mismo tamaño de array
    CHECK("Round-trip JSON size igual", parsed.size() == json_result.size());
    CHECK("Round-trip JSON size == 5", parsed.size() == 5);
}

int main()
{
    test_catalog_with_native_agents();
    test_catalog_with_disabled_agent();
    test_catalog_empty_registry();
    test_catalog_json_roundtrip();

    std::cout << g_passed << " passed, " << g_failed << " failed\n";
    return g_failed == 0 ? 0 : 1;
}