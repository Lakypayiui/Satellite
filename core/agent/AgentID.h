#pragma once

// Identificador único de agente en el runtime Satellite.
// Sirve para referenciar agentes de forma determinista y eficiente.

#include <cstdint>

namespace satellite::core::agent
{

using AgentID = std::uint32_t;

inline constexpr AgentID UNKNOWN_AGENT_ID = 0;

} // namespace satellite::core::agent