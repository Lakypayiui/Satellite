#pragma once

// Política de seguridad por capabilities (Fase 18).
// Deny-by-default: whitelist vacía al construir.
// Los agentes generados por LLM (AgentFactory) declaran capabilities en su spec y se validan igual.
// El aislamiento por proceso de los plugins se documenta para la FASE 21 (distribución).
// Aquí la POLÍTICA se aplica en el runtime antes de ejecutar.

#include <map>
#include <string>

namespace satellite::core::agent
{
    struct AgentDescriptor;
}

namespace satellite::security
{

class SecurityPolicy
{
public:
    SecurityPolicy();
    void set_allowed(const std::string& capability, bool allowed);
    bool is_allowed(const std::string& capability) const;
    void load_defaults();
    void from_config(const std::map<std::string, bool>& allow_map);
    bool validate_agent(const satellite::core::agent::AgentDescriptor& descriptor, std::string& denied_capability) const;
    std::map<std::string, bool> allow_map() const;

private:
    std::map<std::string, bool> allow_;
};

} // namespace satellite::security