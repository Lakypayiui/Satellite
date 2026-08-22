#pragma once

// Código y descripción de error de ejecución de agente.
// Se utiliza en AgentResult para reportar fallos de forma estructurada.

#include <cstdint>
#include <string>

namespace satellite::core::agent
{

enum class AgentErrorCode : std::uint32_t
{
    NONE = 0,
    UNKNOWN_AGENT,
    INVALID_REQUEST,
    VALIDATION_ERROR,
    EXECUTION_FAILED,
    DISABLED_AGENT,
    TIMEOUT,
    INTERNAL_ERROR
};

struct AgentError
{
    AgentErrorCode code = AgentErrorCode::NONE;
    std::string message;

    AgentError() = default;
    AgentError(AgentErrorCode code_, std::string message_)
        : code(code_), message(std::move(message_))
    {}
};

} // namespace satellite::core::agent