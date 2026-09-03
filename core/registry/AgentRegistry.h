#pragma once

// Registro canónico de capacidades del runtime Satellite (Fase 2).
// Gestiona la inscripción, consulta y estado habilitado/deshabilitado de agentes.
// Las operaciones del registro son seguras para consultas y modificaciones concurrentes.

#include <unordered_map>
#include <unordered_set>
#include <vector>
#include <memory>
#include <shared_mutex>

#include "core/agent/AgentID.h"
#include "core/agent/AgentDescriptor.h"

namespace satellite::core::registry
{

using satellite::core::agent::AgentID;
using satellite::core::agent::AgentDescriptor;
using satellite::core::agent::UNKNOWN_AGENT_ID;

class AgentRegistry
{
private:
    std::unordered_map<AgentID, std::shared_ptr<const AgentDescriptor>> registry_;
    std::unordered_set<AgentID> disabled_;
    mutable std::shared_mutex mutex_;

public:
    // Inscribe un agente. Rechaza id == UNKNOWN_AGENT_ID (0) → false.
    // Rechaza id duplicado → false. Los agentes nuevos nacen habilitados.
    bool register_agent(const AgentDescriptor& descriptor);

    // Elimina un agente del registro y de disabled_. False si no existe.
    bool unregister_agent(AgentID id);

    // Busca un agente por id. nullptr si no existe (sin distinguir habilitado/deshabilitado).
    std::shared_ptr<const AgentDescriptor> find_agent(AgentID id) const;

    // Comprueba si existe un agente (habilitado o no).
    bool has_agent(AgentID id) const;

    // Lista TODOS los descriptores (incluidos deshabilitados), ordenados ASC por id.
    std::vector<AgentDescriptor> list_agents() const;

    // Habilita un agente. False si no existe; true si existe (idempotente).
    bool enable_agent(AgentID id);

    // Deshabilita un agente. False si no existe; true si existe (idempotente).
    bool disable_agent(AgentID id);

    // Comprueba si un agente está habilitado. False si no existe;
    // true solo si existe y no está en disabled_.
    bool is_enabled(AgentID id) const;
};

} // namespace satellite::core::registry