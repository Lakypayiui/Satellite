#pragma once

// Descripción estática de un agente para el Registry (Fase 2).
// Define la identidad, esquemas, capacidades y referencia a la implementación.

#include <json.hpp>
#include <string>
#include <vector>

#include "AgentID.h"

namespace satellite::core::agent
{

class IAgent;

struct AgentDescriptor
{
    AgentID id = UNKNOWN_AGENT_ID;
    std::string name;
    std::string description;
    std::string version = "0.1.0";
    nlohmann::json input_schema;
    nlohmann::json output_schema;
    std::vector<std::string> context_requirements;
    std::vector<std::string> capabilities;
    IAgent* agent = nullptr;
};

} // namespace satellite::core::agent