#pragma once

// Abstracción para adaptadores de proyecto (Fase 10).
// Permite al framework trabajar con distintos tipos de repositorio sin acoplamiento
// a una aplicación concreta. Extensible para C++, Python, JavaScript, TypeScript, Java, etc.

#include "context/engine/ContextEngine.h"
#include "context/engine/ProjectContext.h"
#include <memory>
#include <filesystem>
#include <string>

namespace satellite::context
{

class IProjectAdapter
{
public:
    virtual ~IProjectAdapter() = default;

    virtual std::string language() const = 0;

    virtual bool supports(const std::filesystem::path& project_root) const = 0;

    virtual ProjectContext build_context(const std::filesystem::path& project_root) const = 0;
};

class CppProjectAdapter : public IProjectAdapter
{
public:
    std::string language() const override;

    bool supports(const std::filesystem::path& root) const override;

    ProjectContext build_context(const std::filesystem::path& root) const override;
};

class PythonProjectAdapter : public IProjectAdapter
{
public:
    std::string language() const override;

    bool supports(const std::filesystem::path& root) const override;

    ProjectContext build_context(const std::filesystem::path& root) const override;
};

class ProjectAdapterFactory
{
public:
    static std::unique_ptr<IProjectAdapter> detect(const std::filesystem::path& project_root);
    // Prueba en orden: CppProjectAdapter, PythonProjectAdapter; devuelve el primero con supports()==true; nullptr si ninguno.
};

} // namespace satellite::context