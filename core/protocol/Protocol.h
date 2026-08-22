#pragma once

// Protocolo estándar entre Orchestrator y Runtime (Fase 5).
// Define requests/responses estructurados con schemas estrictos.
//
// PROTOCOLO WIRE COMPLETO (JSON producido por LLM/Orchestrator):
// Request:
// {
//   "agent_id": 4,
//   "input": {...},
//   "context": {...},
//   "token_budget": {"max_tokens": 1000},
//   "metadata": {"provider": "deepseek", "model": "...", "execution_id": "exec_...", "timestamp_ms": ...}
// }
// Response:
// {
//   "agent_id": 4,
//   "status": "success",
//   "output": {...},
//   "error": null,
//   "duration_ms": 1.2,
//   "metadata": {"execution_id": "exec_...", "provider": "...", "model": "...", "timestamp_ms": ...}
// }
// (status usa los strings de to_string(AgentStatus) de Fase 1)

#include <cstdint>
#include <string>
#include <atomic>

#include <json.hpp>

namespace satellite::core::protocol
{

struct TokenBudget
{
    std::uint64_t max_tokens = 0; // 0 = sin límite
};

struct ExecutionMetadata
{
    std::string execution_id;   // vacío = el runtime lo genera
    std::string provider;       // proveedor LLM (opcional, observabilidad)
    std::string model;          // modelo LLM (opcional, observabilidad)
    std::int64_t timestamp_ms = 0;  // epoch ms
};

std::string make_execution_id();  // "exec_<timestamp_ms>_<contador>", única por proceso

// ADL to_json/from_json para TokenBudget
inline void to_json(nlohmann::json& j, const TokenBudget& tb)
{
    j = nlohmann::json{{"max_tokens", tb.max_tokens}};
}

inline void from_json(const nlohmann::json& j, TokenBudget& tb)
{
    tb.max_tokens = j.value("max_tokens", 0ULL);
}

// ADL to_json/from_json para ExecutionMetadata
inline void to_json(nlohmann::json& j, const ExecutionMetadata& em)
{
    j = nlohmann::json{
        {"execution_id", em.execution_id},
        {"provider", em.provider},
        {"model", em.model},
        {"timestamp_ms", em.timestamp_ms}
    };
}

inline void from_json(const nlohmann::json& j, ExecutionMetadata& em)
{
    em.execution_id = j.value("execution_id", std::string{});
    em.provider = j.value("provider", std::string{});
    em.model = j.value("model", std::string{});
    em.timestamp_ms = j.value("timestamp_ms", 0L);
}

} // namespace satellite::core::protocol