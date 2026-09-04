#pragma once

// Interfaz de línea de comandos del framework Satellite (FASE 20 parte A).
// Subcomandos soportados en esta parte: init, agents, agent info <id>, agent enable <id>, agent disable <id>, doctor.
// context build/inspect, agent create/test y run se añaden en la parte B.

#include <filesystem>

namespace satellite::cli
{

class SatelliteCLI
{
public:
    SatelliteCLI(std::filesystem::path project_root, std::filesystem::path framework_root);
    int run(int argc, char* argv[]);   // despacha subcomandos; 0 = OK, != 0 = error

private:
    std::filesystem::path project_root_;
    std::filesystem::path framework_root_;

    // helpers (implementados en el .cpp):
    int cmd_init(int argc, char* argv[]);
    int cmd_agents();
    int cmd_agent_info(int argc, char* argv[]);
    int cmd_agent_enable_disable(int argc, char* argv[], bool enable);
    int cmd_version();
    int cmd_doctor();

    // FASE 20 parte B:
    int cmd_context_build();
    int cmd_context_inspect();
    int cmd_context_get(int argc, char* argv[]);
    int cmd_agent_create(int argc, char* argv[]);
    int cmd_agent_test(int argc, char* argv[]);
    int cmd_run(int argc, char* argv[]);

    // Unificación con el runtime Python: despacha UN request (agent_id+input)
    // por stdin (JSON) y devuelve el AgentResult como JSON por stdout. Permite
    // que satellite_py ejecute agentes nativos 1-5 (in-process en C++) y
    // agentes reconstruidos desde specs dentro del binario C++.
    int cmd_dispatch_step();
};

} // namespace satellite::cli