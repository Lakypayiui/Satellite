#pragma once

// Almacén de persistencia para especificaciones y registro de agentes (Fase X).
// El directorio .satellite pertenece al PROYECTO CONSUMIDOR; el framework solo lo administra;
// NUNCA escribir fuera de root().

#include <json.hpp>
#include <filesystem>
#include <vector>

#include "core/registry/AgentRegistry.h"
#include "factory/AgentFactory.h"

namespace satellite::persistence
{

using satellite::core::registry::AgentRegistry;
using satellite::factory::AgentFactory;
using satellite::factory::AgentSpec;

class AgentStore
{
public:
    explicit AgentStore(std::filesystem::path project_root);

    std::filesystem::path root() const;
    bool ensure_dirs() const;
    bool has_state() const;

    bool save_spec(const AgentSpec& spec) const;
    std::vector<AgentSpec> load_specs() const;

    bool save_registry(const AgentRegistry& registry) const;
    bool load_registry(AgentRegistry& registry) const;
    std::size_t rebuild_agents(AgentRegistry& registry, AgentFactory& factory) const;

private:
    std::filesystem::path project_root_;
};

} // namespace satellite::persistence