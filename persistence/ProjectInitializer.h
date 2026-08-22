#pragma once

// Inicializador de proyectos consumidores del framework Satellite (FASE 16).
// Convierte un repositorio existente en un proyecto consumidor creando la estructura
// .satellite/ con configuración por defecto y catálogo de agentes nativos.
// El framework permanece instalado externamente; todo lo que init crea vive bajo
// <project_root>/.satellite. Idempotente por protección: segundo init → error,
// nunca por sobrescritura.

#include <filesystem>
#include <string>

namespace satellite::persistence
{

class ProjectInitializer
{
public:
    // Convierte project_root en proyecto consumidor. Crea .satellite/ con estructura,
    // config.json por defecto y catálogo inicial (agentes nativos persistidos).
    // Si ya existe .satellite → false + error (NUNCA sobrescribe un proyecto ya inicializado).
    static bool init(const std::filesystem::path& project_root, std::string& error);

    // Devuelve la ruta del config del proyecto (no crea nada).
    static std::filesystem::path project_config_path(const std::filesystem::path& project_root);
};

} // namespace satellite::persistence