// Punto de entrada de la CLI Satellite (FASE 20 parte A).
// El ejecutable se llama 'satellite'.

#include "cli/SatelliteCLI.h"
#include <filesystem>

int main(int argc, char* argv[])
{
    std::filesystem::path project = std::filesystem::current_path();
    std::filesystem::path framework = SATELLITE_ROOT;   // macro definida por CMake
    satellite::cli::SatelliteCLI cli(project, framework);
    return cli.run(argc, argv);
}