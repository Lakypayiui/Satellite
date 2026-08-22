#pragma once

// AgentExpander: amplía el catálogo creando agentes para capacidades ausentes (Fase 14).
// Pipeline: detecta capacidades faltantes → pide spec al LLM → usa AgentFactory para compilar/test/registrar.
// Garantías: no recrea lo que ya existe (check previo contra catálogo/registry); fallos no dejan agentes registrados.

#include <json.hpp>
#include <string>
#include <vector>
#include <utility>

#include "core/agent/AgentID.h"
#include "core/registry/AgentRegistry.h"
#include "core/catalog/AgentCatalog.h"
#include "AgentFactory.h"
#include "llm/ILLMProvider.h"

namespace satellite::factory
{

using satellite::core::agent::AgentID;
using satellite::core::registry::AgentRegistry;
using satellite::core::catalog::AgentCatalog;
using satellite::llm::ILLMProvider;

struct ExpansionResult
{
    std::vector<AgentID> created;
    std::vector<std::string> skipped;
    std::vector<std::pair<std::string, std::string>> failed;
    bool ok = true;
};

class AgentExpander
{
public:
    AgentExpander(AgentRegistry& registry, AgentCatalog& catalog, AgentFactory& factory, ILLMProvider& llm);

    ExpansionResult expand(const std::string& goal, std::string& error);

    std::vector<std::string> missing_capabilities(const std::string& goal) const;

private:
    AgentRegistry& registry_;
    AgentCatalog& catalog_;
    AgentFactory& factory_;
    ILLMProvider& llm_;

    bool generate_spec(const std::string& goal, const std::string& capability, AgentSpec& spec, std::string& error) const;
};

} // namespace satellite::factory