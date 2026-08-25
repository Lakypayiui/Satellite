#pragma once

// Definiciones de tipos para el contexto de proyecto (Fase 8).
// Representa la estructura extraída de un repositorio: archivos, símbolos y dependencias.

#include <string>
#include <vector>
#include <cstddef>

namespace satellite::context
{

enum class SymbolKind
{
    Function,
    Class,
    Variable,
    Other
};

struct SymbolInfo
{
    std::string name;
    SymbolKind kind = SymbolKind::Other;
    std::string file;
    std::size_t line = 0;
    std::string signature;
};

struct FileInfo
{
    std::string path;
    std::string category;
    std::string type;

    std::size_t size = 0;
    std::size_t lines = 0;
    std::vector<SymbolInfo> symbols;
};

struct DependencyInfo
{
    std::string from_file;
    std::string target;
    std::string kind;
    bool external = true;
};

struct ProjectContext
{
    std::string root;
    std::vector<FileInfo> files;
    std::vector<DependencyInfo> dependencies;
    std::size_t total_lines = 0;
    std::size_t total_files = 0;
};

} // namespace satellite::context