#pragma once

// Estado de ejecución de un agente.
// Representa el ciclo de vida de una invocación de agente en el runtime.

#include <cstdint>
#include <string_view>

namespace satellite::core::agent
{

enum class AgentStatus : std::uint8_t
{
    IDLE,
    RUNNING,
    SUCCESS,
    FAILED,
    UNKNOWN_AGENT,
    VALIDATION_ERROR,
    DISABLED,
    TIMEOUT
};

inline std::string_view to_string(AgentStatus status)
{
    switch (status)
    {
        case AgentStatus::IDLE:             return "idle";
        case AgentStatus::RUNNING:          return "running";
        case AgentStatus::SUCCESS:          return "success";
        case AgentStatus::FAILED:           return "failed";
        case AgentStatus::UNKNOWN_AGENT:    return "unknown_agent";
        case AgentStatus::VALIDATION_ERROR: return "validation_error";
        case AgentStatus::DISABLED:         return "disabled";
        case AgentStatus::TIMEOUT:          return "timeout";
    }
    return "unknown";
}

} // namespace satellite::core::agent