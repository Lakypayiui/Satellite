#pragma once

// Motor de análisis de contexto de proyecto (Fase 8).
// Escanea un directorio de proyecto y extrae archivos, símbolos y dependencias
// mediante heurísticas basadas en expresiones regulares (sin parser real).

#include "ProjectContext.h"
#include <filesystem>
#include <string>
#include <vector>

namespace satellite::context
{

class ContextEngine
{
public:
    explicit ContextEngine(std::filesystem::path project_root);

    ProjectContext build() const;

    void set_ignore_dirs(std::vector<std::string> dirs);

    std::vector<std::string> ignore_dirs() const;

private:
    std::filesystem::path root_;
    std::vector<std::string> ignore_dirs_;

    static std::vector<std::string> default_ignore_dirs();

        static std::string detect_category(const std::filesystem::path& path);
    static std::string detect_type(const std::filesystem::path& path);

    static bool should_process_file(const std::filesystem::path& path);

    static std::vector<SymbolInfo> extract_symbols_cpp(const std::string& content,
                                                         const std::string& rel_path);
    static std::vector<SymbolInfo> extract_symbols_python(const std::string& content,
                                                            const std::string& rel_path);
    static std::vector<SymbolInfo> extract_symbols_js_ts(const std::string& content,
                                                           const std::string& rel_path);
    static std::vector<SymbolInfo> extract_symbols_java(const std::string& content,
                                                          const std::string& rel_path);
    static std::vector<SymbolInfo> extract_symbols_rust(const std::string& content,
                                                          const std::string& rel_path);
    static std::vector<SymbolInfo> extract_symbols_go(const std::string& content,
                                                        const std::string& rel_path);

    static std::vector<DependencyInfo> extract_deps_cpp(const std::string& content,
                                                          const std::string& rel_path);
    static std::vector<DependencyInfo> extract_deps_python(const std::string& content,
                                                             const std::string& rel_path);
    static std::vector<DependencyInfo> extract_deps_js_ts(const std::string& content,
                                                            const std::string& rel_path);
    static std::vector<DependencyInfo> extract_deps_java(const std::string& content,
                                                           const std::string& rel_path);
    static std::vector<DependencyInfo> extract_deps_rust(const std::string& content,
                                                           const std::string& rel_path);
    static std::vector<DependencyInfo> extract_deps_go(const std::string& content,
                                                         const std::string& rel_path);

    static std::string normalize_path(const std::filesystem::path& root,
                                       const std::filesystem::path& path);

    static std::size_t count_lines(const std::string& content);

    static std::string trim_signature(const std::string& line);
};

} // namespace satellite::context