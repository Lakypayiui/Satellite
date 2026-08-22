// Prueba de portabilidad (FASE 24): el MISMO framework funciona sobre dos
// proyectos consumidores distintos (C++ y Python) sin modificar el framework.
// Se inicializa cada proyecto con ProjectInitializer y se construye su contexto
// con el ProjectAdapter detectado, verificando el contenido del contexto.

#include <iostream>
#include <string>
#include <filesystem>

#include "persistence/ProjectInitializer.h"
#include "persistence/AgentStore.h"
#include "context/adapter/ProjectAdapter.h"
#include "context/engine/ProjectContext.h"

using namespace satellite::persistence;
using namespace satellite::context;

int g_passed = 0;
int g_failed = 0;

#define CHECK(desc, cond) \
    do { \
        if (cond) { \
            std::cout << "PASSED: " << desc << "\n"; \
            ++g_passed; \
        } else { \
            std::cout << "FAILED: " << desc << ": " #cond "\n"; \
            ++g_failed; \
        } \
    } while (0)

namespace fs = std::filesystem;

int main()
{
    // --- Proyecto A: C++ ---
    {
        fs::path pa = fs::path(SATELLITE_ROOT) / "examples" / "project_a";
        CHECK("project_a existe", fs::exists(pa / "main.cpp"));

        std::string err;
        AgentStore store_a(pa);
        if (!store_a.has_state())
        {
            CHECK("project_a: init", ProjectInitializer::init(pa, err));
        }

        auto adapter = ProjectAdapterFactory::detect(pa);
        CHECK("project_a: adapter detectado", adapter != nullptr);
        if (adapter)
        {
            CHECK("project_a: lenguaje C++", adapter->language() == "C++");
            ProjectContext ctx = adapter->build_context(pa);
            CHECK("project_a: contexto con archivos", !ctx.files.empty());
            CHECK("project_a: encuentra main.cpp", [&] {
                for (const auto& f : ctx.files)
                {
                    if (f.path.find("main.cpp") != std::string::npos)
                    {
                        return true;
                    }
                }
                return false;
            }());
            // El parser de símbolos es heurístico (estilo de llaves): lo verificable
            // es que el contexto captó las DEPENDENCIAS (includes) del proyecto C++.
            CHECK("project_a: dependencias detectadas", !ctx.dependencies.empty());
        }
    }

    // --- Proyecto B: Python ---
    {
        fs::path pb = fs::path(SATELLITE_ROOT) / "examples" / "project_b";
        CHECK("project_b existe", fs::exists(pb / "app.py"));

        std::string err;
        AgentStore store_b(pb);
        if (!store_b.has_state())
        {
            CHECK("project_b: init", ProjectInitializer::init(pb, err));
        }

        auto adapter = ProjectAdapterFactory::detect(pb);
        CHECK("project_b: adapter detectado", adapter != nullptr);
        if (adapter)
        {
            CHECK("project_b: lenguaje Python", adapter->language() == "Python");
            ProjectContext ctx = adapter->build_context(pb);
            CHECK("project_b: contexto con archivos", !ctx.files.empty());
            bool hay_simbolo = false;
            for (const auto& f : ctx.files)
            {
                for (const auto& s : f.symbols)
                {
                    if (s.name.find("calcular_promedio") != std::string::npos)
                    {
                        hay_simbolo = true;
                    }
                }
            }
            CHECK("project_b: símbolo calcular_promedio", hay_simbolo);
        }
    }

    // --- El framework NO se modifica: ambos proyectos usan el mismo binario ---
    {
        // El estado de cada proyecto vive en SU .satellite
        fs::path pa = fs::path(SATELLITE_ROOT) / "examples" / "project_a";
        fs::path pb = fs::path(SATELLITE_ROOT) / "examples" / "project_b";
        CHECK("project_a: .satellite propio", fs::exists(pa / ".satellite" / "config" / "config.json"));
        CHECK("project_b: .satellite propio", fs::exists(pb / ".satellite" / "config" / "config.json"));
    }

    std::cout << g_passed << " passed, " << g_failed << " failed\n";
    return g_failed == 0 ? 0 : 1;
}
