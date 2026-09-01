#pragma once

// Contexto semántico bajo demanda (Etapa 2b).
// Lee el contenido de los archivos que una tarea necesita,
// usando el índice estructural existente como guía.

#include "ProjectIndex.h"
#include <string>
#include <vector>
#include <cstddef>
#include <filesystem>

namespace satellite::context
{

struct SemanticFile
{
    std::string path;
    std::string language;
    std::string content;
};

struct SemanticContext
{
    std::string root;
    std::vector<SemanticFile> files;
    std::size_t total_chars = 0;
};

class IncrementalContextBuilder
{
public:
    explicit IncrementalContextBuilder(std::filesystem::path project_root);

    SemanticContext build_for_paths(const ProjectIndex& index,
                                   const std::vector<std::string>& paths) const;

    SemanticContext build_for_paths(const ProjectIndex& index,
                                   const std::vector<std::string>& paths,
                                   std::size_t max_total_chars) const;

private:
    std::filesystem::path root_;

    static bool path_in_index(const ProjectIndex& index, const std::string& path);
    static std::string read_file_binary(const std::filesystem::path& full_path);
};

} // namespace satellite::context