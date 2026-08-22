// Mini framework de test para core::agent (FASE 1)
// Sin dependencias externas: solo C++17 estándar

#include <iostream>
#include <string>
#include <type_traits>
#include <cstdint>

#include <json.hpp>
#include "core/agent/agent.h"

using namespace satellite::core::agent;

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

void test_agent_id()
{
    // 1. static_assert AgentID == uint32_t y UNKNOWN_AGENT_ID == 0
    static_assert(std::is_same_v<AgentID, std::uint32_t>, "AgentID debe ser uint32_t");
    CHECK("AgentID es uint32_t", true);
    CHECK("UNKNOWN_AGENT_ID == 0", UNKNOWN_AGENT_ID == 0);
}

void test_agent_request()
{
    // 2. AgentRequest por defecto
    AgentRequest req;
    CHECK("AgentRequest default agent_id == UNKNOWN_AGENT_ID", req.agent_id == UNKNOWN_AGENT_ID);

    // Asignar input/context/metadata como nlohmann::json (objeto con campos)
    req.input = nlohmann::json::object({{"key1", "value1"}, {"num", 42}});
    req.context = nlohmann::json::object({{"ctx_key", "ctx_val"}});
    req.metadata = nlohmann::json::object({{"meta_key", "meta_val"}});

    CHECK("AgentRequest input lectura", req.input["key1"] == "value1" && req.input["num"] == 42);
    CHECK("AgentRequest context lectura", req.context["ctx_key"] == "ctx_val");
    CHECK("AgentRequest metadata lectura", req.metadata["meta_key"] == "meta_val");
}

void test_agent_status()
{
    // 3. AgentStatus: construir cada valor del enum y verificar to_string()
    CHECK("AgentStatus::IDLE -> \"idle\"", to_string(AgentStatus::IDLE) == "idle");
    CHECK("AgentStatus::RUNNING -> \"running\"", to_string(AgentStatus::RUNNING) == "running");
    CHECK("AgentStatus::SUCCESS -> \"success\"", to_string(AgentStatus::SUCCESS) == "success");
    CHECK("AgentStatus::FAILED -> \"failed\"", to_string(AgentStatus::FAILED) == "failed");
    CHECK("AgentStatus::UNKNOWN_AGENT -> \"unknown_agent\"", to_string(AgentStatus::UNKNOWN_AGENT) == "unknown_agent");
    CHECK("AgentStatus::VALIDATION_ERROR -> \"validation_error\"", to_string(AgentStatus::VALIDATION_ERROR) == "validation_error");
    CHECK("AgentStatus::DISABLED -> \"disabled\"", to_string(AgentStatus::DISABLED) == "disabled");
    CHECK("AgentStatus::TIMEOUT -> \"timeout\"", to_string(AgentStatus::TIMEOUT) == "timeout");
}

void test_agent_error()
{
    // 4. AgentError: código + mensaje
    AgentError err(AgentErrorCode::EXECUTION_FAILED, "Algo falló");
    CHECK("AgentError code", err.code == AgentErrorCode::EXECUTION_FAILED);
    CHECK("AgentError message", err.message == "Algo falló");

    // Default constructor
    AgentError err2;
    CHECK("AgentError default code == NONE", err2.code == AgentErrorCode::NONE);
    CHECK("AgentError default message empty", err2.message.empty());
}

void test_agent_result()
{
    // 5. AgentResult
    // Default status == FAILED
    AgentResult res1;
    CHECK("AgentResult default status == FAILED", res1.status == AgentStatus::FAILED);
    CHECK("AgentResult default agent_id == UNKNOWN_AGENT_ID", res1.agent_id == UNKNOWN_AGENT_ID);

    // SUCCESS con output JSON
    AgentResult res2;
    res2.agent_id = 42;
    res2.status = AgentStatus::SUCCESS;
    res2.output = nlohmann::json::object({{"result", 123}});
    CHECK("AgentResult SUCCESS status", res2.status == AgentStatus::SUCCESS);
    CHECK("AgentResult SUCCESS output", res2.output["result"] == 123);
    CHECK("AgentResult SUCCESS agent_id", res2.agent_id == 42);

    // Con error (optional tiene valor)
    AgentResult res3;
    res3.agent_id = 7;
    res3.status = AgentStatus::FAILED;
    res3.error = AgentError(AgentErrorCode::TIMEOUT, "Tiempo agotado");
    CHECK("AgentResult error has_value", res3.error.has_value());
    CHECK("AgentResult error code", res3.error->code == AgentErrorCode::TIMEOUT);
    CHECK("AgentResult error message", res3.error->message == "Tiempo agotado");
    CHECK("AgentResult error agent_id", res3.agent_id == 7);
}

void test_agent_metadata()
{
    // 6. AgentMetadata
    AgentMetadata meta;
    meta.execution_id = "exec-123";
    meta.agent_id = 99;
    meta.agent_version = "1.2.3";
    meta.timestamp = 1699999999;
    meta.duration_ms = 45.5;

    CHECK("AgentMetadata execution_id", meta.execution_id == "exec-123");
    CHECK("AgentMetadata agent_id", meta.agent_id == 99);
    CHECK("AgentMetadata agent_version", meta.agent_version == "1.2.3");
    CHECK("AgentMetadata timestamp", meta.timestamp == 1699999999);
    CHECK("AgentMetadata duration_ms", meta.duration_ms == 45.5);
}

void test_agent_descriptor()
{
    // 7. AgentDescriptor
    AgentDescriptor desc;
    desc.id = 10;
    desc.name = "TestAgent";
    desc.description = "Un agente de prueba";
    desc.version = "2.0.0";
    desc.input_schema = nlohmann::json::object({{"type", "object"}});
    desc.output_schema = nlohmann::json::object({{"type", "object"}});
    desc.context_requirements = {"req1", "req2"};
    desc.capabilities = {"cap1", "cap2"};

    CHECK("AgentDescriptor id", desc.id == 10);
    CHECK("AgentDescriptor name", desc.name == "TestAgent");
    CHECK("AgentDescriptor description", desc.description == "Un agente de prueba");
    CHECK("AgentDescriptor version", desc.version == "2.0.0");
    CHECK("AgentDescriptor input_schema", desc.input_schema["type"] == "object");
    CHECK("AgentDescriptor output_schema", desc.output_schema["type"] == "object");
    CHECK("AgentDescriptor context_requirements size", desc.context_requirements.size() == 2);
    CHECK("AgentDescriptor capabilities size", desc.capabilities.size() == 2);
    CHECK("AgentDescriptor agent == nullptr", desc.agent == nullptr);
}

// MockAgent local que implementa IAgent
class MockAgent : public IAgent
{
public:
    AgentResult execute(const AgentRequest&) override
    {
        AgentResult result;
        result.status = AgentStatus::SUCCESS;
        result.output = nlohmann::json::object({{"result", 42}});
        return result;
    }
};

void test_mock_agent()
{
    // 8. MockAgent: verificar polimorfismo vía IAgent*
    MockAgent mock;
    IAgent* agent_ptr = &mock;

    AgentRequest req;
    AgentResult result = agent_ptr->execute(req);

    CHECK("MockAgent IAgent* execute status SUCCESS", result.status == AgentStatus::SUCCESS);
    CHECK("MockAgent IAgent* execute output result == 42", result.output["result"] == 42);
}

int main()
{
    test_agent_id();
    test_agent_request();
    test_agent_status();
    test_agent_error();
    test_agent_result();
    test_agent_metadata();
    test_agent_descriptor();
    test_mock_agent();

    std::cout << g_passed << " passed, " << g_failed << " failed\n";
    return g_failed == 0 ? 0 : 1;
}