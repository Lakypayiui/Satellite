#pragma once

// Interfaz de plugin para agentes generados dinámicamente.
// El código generado DEBE implementar ambas funciones con linkage C.

#include "core/agent/IAgent.h"

// La funcion de fabrica del plugin se declara en scope GLOBAL (linkage "C"):
// el codigo generado y el harness la referencian sin calificar.
extern "C" satellite::core::agent::IAgent* satellite_create_agent();
extern "C" void satellite_destroy_agent(satellite::core::agent::IAgent* agent);