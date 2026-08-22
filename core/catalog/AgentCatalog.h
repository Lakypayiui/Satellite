#pragma once

// Catálogo compacto de capacidades para el LLM (Fase 7).
// Expone solo agentes HABILITADOS (is_enabled) con metadatos esenciales:
// id, name, description, version, input_schema, output_schema,
// context_requirements, capabilities.
// NO expone punteros IAgent* ni código fuente.
// to_prompt() genera una línea por agente en formato compacto:
//   [id] name — description [capabilities] in:{input_schema} out:{output_schema}
// Descripción truncada a ~80 caracteres. Schemas: si hay properties, lista "clave:tipo"
// separadas por coma; si no, dump del schema.

#include <json.hpp>
#include <string>
#include <vector>

#include "core/agent/AgentID.h"
#include "core/registry/AgentRegistry.h"

namespace satellite::core::catalog
{

using satellite::core::agent::AgentID;
using satellite::core::registry::AgentRegistry;

class AgentCatalog
{
private:
    const AgentRegistry& registry_;

    static std::string compact_schema(const nlohmann::json& schema);
    static std::string truncate_description(const std::string& desc, std::size_t max_len = 80);

public:
    explicit AgentCatalog(const AgentRegistry& registry);

    std::size_t size() const;
    nlohmann::json to_json() const;
    std::string to_prompt() const;
    std::string describe_agent(AgentID id) const;
};

} // namespace satellite::core::catalog