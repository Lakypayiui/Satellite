#pragma once

// Índice estructural del proyecto (Etapa 2).
// Caminos, símbolos y dependencias — barato y SIEMPRE disponible,
// separado del contexto semántico (contenido de archivos — bajo demanda).

#include "ProjectContext.h"
#include <json.hpp>
#include <filesystem>
#include <string>
#include <vector>
#include <cstddef>
#include <cstdint>
#include <chrono>

namespace satellite::context
{

struct IndexedFile
{
    std::string path;
    std::string language;
    std::size_t size = 0;
    std::size_t lines = 0;
    std::vector<std::string> symbols;
    std::vector<std::string> dependencies;
    std::int64_t mtime = 0;
};

struct ProjectIndex
{
    std::string root;
    std::vector<IndexedFile> files;
    std::size_t total_lines = 0;
    std::size_t total_files = 0;
    std::string build_timestamp;
};

class ProjectIndexBuilder
{
public:
    explicit ProjectIndexBuilder(std::filesystem::path project_root);
    ProjectIndex build() const;
    std::vector<std::string> changed_paths(const ProjectIndex& saved, const std::filesystem::path& project_root) const;
    bool is_stale(const ProjectIndex& saved, const std::filesystem::path& project_root) const;

private:
    std::filesystem::path root_;
};

inline void to_json(nlohmann::json& j, const IndexedFile& f);
inline void from_json(const nlohmann::json& j, IndexedFile& f);
inline void to_json(nlohmann::json& j, const ProjectIndex& idx);
inline void from_json(const nlohmann::json& j, ProjectIndex& idx);

void save(const ProjectIndex& idx, const std::filesystem::path& path);
ProjectIndex load(const std::filesystem::path& path);

} // namespace satellite::context