#include "ProjectIndex.h"
#include "context/adapter/ProjectAdapter.h"
#include <fstream>
#include <regex>
#include <algorithm>
#include <chrono>
#include <sstream>
#include <iterator>

namespace satellite::context
{

namespace
{

std::vector<std::string> default_ignore_dirs()
{
    return {".git", "build", "node_modules", ".satellite", ".agent",
            "out", "dist", ".venv", "venv", "__pycache__", "CMakeFiles",
            ".idea", ".vscode"};
}

std::string detect_type(const std::filesystem::path& path)
{
    const auto filename = path.filename().string();
    const auto ext = path.extension().string();

    if (ext == ".cpp" || ext == ".cc" || ext == ".cxx" ||
        ext == ".hpp" || ext == ".hh" || ext == ".hxx")
    {
        return "C++";
    }

    if (ext == ".c")
    {
        return "C";
    }

    if (ext == ".h")
    {
        return "C++";
    }

    if (ext == ".py")
    {
        return "Python";
    }

    return "";
}

bool should_process_file(const std::filesystem::path& path)
{
    const auto type = detect_type(path);
    return type == "C" || type == "C++" || type == "Python";
}

std::size_t count_lines(const std::string& content)
{
    return std::count(content.begin(), content.end(), '\n') + (content.empty() ? 0 : 1);
}

std::string trim_signature(const std::string& line)
{
    std::string trimmed = line;
    const auto start = trimmed.find_first_not_of(" \t\r\n");
    if (start == std::string::npos)
    {
        return "";
    }
    trimmed = trimmed.substr(start);
    const auto end = trimmed.find_last_not_of(" \t\r\n");
    if (end != std::string::npos)
    {
        trimmed = trimmed.substr(0, end + 1);
    }
    if (trimmed.length() > 120)
    {
        trimmed = trimmed.substr(0, 120);
    }
    return trimmed;
}

std::vector<SymbolInfo> extract_symbols_cpp(const std::string& content,
                                            const std::string& rel_path)
{
    std::vector<SymbolInfo> symbols;
    std::size_t line_num = 0;

    static const std::regex class_regex(R"(^\s*(class|struct)\s+(\w+))");
    static const std::regex function_regex(R"(^\s*(?:[\w:<>*&,]+\s+)*(\w[\w:]*)\s*\([^)]*\)\s*(?:const\s*)?(?:override\s*)?(?:final\s*)?(?:;\s*|\{\s*))");
    static const std::regex control_keywords(R"(^\s*(if|for|while|switch|case|return|else)\b)");

    std::string line;
    std::istringstream iss(content);
    while (std::getline(iss, line))
    {
        ++line_num;

        std::smatch match;
        if (std::regex_search(line, match, class_regex))
        {
            SymbolInfo sym;
            sym.name = match[2].str();
            sym.kind = SymbolKind::Class;
            sym.file = rel_path;
            sym.line = line_num;
            sym.signature = trim_signature(line);
            symbols.push_back(std::move(sym));
            continue;
        }

        if (std::regex_search(line, control_keywords))
        {
            continue;
        }

        if (std::regex_search(line, match, function_regex))
        {
            std::string name = match[1].str();
            const auto pos = name.rfind("::");
            if (pos != std::string::npos)
            {
                name = name.substr(pos + 2);
            }

            SymbolInfo sym;
            sym.name = name;
            sym.kind = SymbolKind::Function;
            sym.file = rel_path;
            sym.line = line_num;
            sym.signature = trim_signature(line);
            symbols.push_back(std::move(sym));
        }
    }

    return symbols;
}

std::vector<SymbolInfo> extract_symbols_python(const std::string& content,
                                               const std::string& rel_path)
{
    std::vector<SymbolInfo> symbols;
    std::size_t line_num = 0;

    static const std::regex class_regex(R"(^class\s+(\w+))");
    static const std::regex function_regex(R"(^def\s+(\w+))");

    std::string line;
    std::istringstream iss(content);
    while (std::getline(iss, line))
    {
        ++line_num;

        std::smatch match;
        if (std::regex_search(line, match, class_regex))
        {
            SymbolInfo sym;
            sym.name = match[1].str();
            sym.kind = SymbolKind::Class;
            sym.file = rel_path;
            sym.line = line_num;
            sym.signature = trim_signature(line);
            symbols.push_back(std::move(sym));
            continue;
        }

        if (std::regex_search(line, match, function_regex))
        {
            SymbolInfo sym;
            sym.name = match[1].str();
            sym.kind = SymbolKind::Function;
            sym.file = rel_path;
            sym.line = line_num;
            sym.signature = trim_signature(line);
            symbols.push_back(std::move(sym));
        }
    }

    return symbols;
}

std::vector<DependencyInfo> extract_deps_cpp(const std::string& content,
                                             const std::string& rel_path)
{
    std::vector<DependencyInfo> deps;

    static const std::regex include_system_regex(R"(^\s*#include\s*<([^>]+)>)");
    static const std::regex include_local_regex(R"inc(^\s*#include\s*"([^"]+)"\s*$)inc");

    std::string line;
    std::istringstream iss(content);
    while (std::getline(iss, line))
    {
        std::smatch match;
        if (std::regex_search(line, match, include_system_regex))
        {
            DependencyInfo dep;
            dep.from_file = rel_path;
            dep.target = match[1].str();
            dep.kind = "include";
            dep.external = true;
            deps.push_back(std::move(dep));
        }
        else if (std::regex_search(line, match, include_local_regex))
        {
            DependencyInfo dep;
            dep.from_file = rel_path;
            dep.target = match[1].str();
            dep.kind = "include";
            dep.external = false;
            deps.push_back(std::move(dep));
        }
    }

    return deps;
}

std::vector<DependencyInfo> extract_deps_python(const std::string& content,
                                                const std::string& rel_path)
{
    std::vector<DependencyInfo> deps;

    static const std::regex import_regex(R"(^import\s+(\w+))");
    static const std::regex from_import_regex(R"(^from\s+(\w+)\s+import)");

    std::string line;
    std::istringstream iss(content);
    while (std::getline(iss, line))
    {
        std::smatch match;
        if (std::regex_search(line, match, import_regex))
        {
            DependencyInfo dep;
            dep.from_file = rel_path;
            dep.target = match[1].str();
            dep.kind = "import";
            dep.external = true;
            deps.push_back(std::move(dep));
        }
        else if (std::regex_search(line, match, from_import_regex))
        {
            DependencyInfo dep;
            dep.from_file = rel_path;
            dep.target = match[1].str();
            dep.kind = "import";
            dep.external = true;
            deps.push_back(std::move(dep));
        }
    }

    return deps;
}

std::string normalize_path(const std::filesystem::path& root,
                           const std::filesystem::path& path)
{
    try
    {
        auto rel = std::filesystem::relative(path, root);
        std::string result = rel.string();
        std::replace(result.begin(), result.end(), '\\', '/');
        return result;
    }
    catch (const std::filesystem::filesystem_error&)
    {
        return path.string();
    }
}

std::vector<SymbolInfo> extract_symbols_for_language(const std::string& content,
                                                     const std::string& language,
                                                     const std::string& rel_path)
{
    if (language == "C++")
    {
        return extract_symbols_cpp(content, rel_path);
    }
    if (language == "Python")
    {
        return extract_symbols_python(content, rel_path);
    }
    return {};
}

std::vector<DependencyInfo> extract_deps_for_language(const std::string& content,
                                                      const std::string& language,
                                                      const std::string& rel_path)
{
    if (language == "C++")
    {
        return extract_deps_cpp(content, rel_path);
    }
    if (language == "Python")
    {
        return extract_deps_python(content, rel_path);
    }
    return {};
}

} // anonymous namespace

ProjectIndexBuilder::ProjectIndexBuilder(std::filesystem::path project_root)
    : root_(std::filesystem::weakly_canonical(project_root))
{
}

ProjectIndex ProjectIndexBuilder::build() const
{
    ProjectIndex idx;
    idx.root = root_.lexically_normal().string();

    auto adapter = ProjectAdapterFactory::detect(root_);
    std::string adapter_language;
    if (adapter)
    {
        adapter_language = adapter->language();
    }

    auto now = std::chrono::system_clock::now();
    idx.build_timestamp = std::to_string(
        std::chrono::duration_cast<std::chrono::milliseconds>(
            now.time_since_epoch()
        ).count()
    );

    const auto ignore_dirs = default_ignore_dirs();
    std::vector<std::filesystem::path> files_to_process;

    try
    {
        for (auto it = std::filesystem::recursive_directory_iterator(root_);
             it != std::filesystem::recursive_directory_iterator{}; ++it)
        {
            const auto& entry = *it;
            const auto rel_path = entry.path().lexically_relative(root_);
            bool in_ignored_dir = false;
            for (const auto& part : rel_path)
            {
                if (std::find(ignore_dirs.begin(), ignore_dirs.end(),
                              part.string()) != ignore_dirs.end())
                {
                    in_ignored_dir = true;
                    break;
                }
            }
            if (in_ignored_dir)
            {
                if (entry.is_directory())
                {
                    it.disable_recursion_pending();
                }
                continue;
            }
            if (entry.is_regular_file())
            {
                if (should_process_file(entry.path()))
                {
                    files_to_process.push_back(entry.path());
                }
            }
        }
    }
    catch (const std::filesystem::filesystem_error&)
    {
    }

    for (const auto& file_path : files_to_process)
    {
        std::ifstream ifs(file_path, std::ios::binary | std::ios::ate);
        if (!ifs)
        {
            continue;
        }

        const auto file_size = ifs.tellg();
        if (file_size > 1024 * 1024)
        {
            continue;
        }

        ifs.seekg(0);
        std::string content(file_size, '\0');
        ifs.read(&content[0], file_size);

        const auto rel_path = normalize_path(root_, file_path);
        const auto language = detect_type(file_path);

        IndexedFile indexed_file;
        indexed_file.path = rel_path;
        indexed_file.language = language;
        indexed_file.size = static_cast<std::size_t>(file_size);
        indexed_file.lines = count_lines(content);

        auto symbols = extract_symbols_for_language(content, language, rel_path);
        for (const auto& sym : symbols)
        {
            indexed_file.symbols.push_back(sym.name);
        }

        auto deps = extract_deps_for_language(content, language, rel_path);
        for (const auto& dep : deps)
        {
            indexed_file.dependencies.push_back(dep.target);
        }

        std::int64_t mtime_val = 0;
        try
        {
            auto ft = std::filesystem::last_write_time(file_path);
            mtime_val = static_cast<std::int64_t>(ft.time_since_epoch().count());
        }
        catch (...) {}
        if (mtime_val <= 0)
        {
            mtime_val = static_cast<std::int64_t>(
                std::chrono::duration_cast<std::chrono::milliseconds>(
                    std::chrono::system_clock::now().time_since_epoch()
                ).count()
            );
        }
        indexed_file.mtime = mtime_val;

        idx.files.push_back(std::move(indexed_file));
        idx.total_lines += indexed_file.lines;
    }

    idx.total_files = idx.files.size();
    return idx;
}

inline void to_json(nlohmann::json& j, const IndexedFile& f)
{
    j = nlohmann::json{
        {"path", f.path},
        {"language", f.language},
        {"size", f.size},
        {"lines", f.lines},
        {"symbols", f.symbols},
        {"dependencies", f.dependencies},
        {"mtime", f.mtime}
    };
}

inline void from_json(const nlohmann::json& j, IndexedFile& f)
{
    j.at("path").get_to(f.path);
    j.at("language").get_to(f.language);
    j.at("size").get_to(f.size);
    j.at("lines").get_to(f.lines);
    j.at("symbols").get_to(f.symbols);
    j.at("dependencies").get_to(f.dependencies);
    j.at("mtime").get_to(f.mtime);
}

inline void to_json(nlohmann::json& j, const ProjectIndex& idx)
{
    j = nlohmann::json{
        {"root", idx.root},
        {"files", idx.files},
        {"total_lines", idx.total_lines},
        {"total_files", idx.total_files},
        {"build_timestamp", idx.build_timestamp}
    };
}

inline void from_json(const nlohmann::json& j, ProjectIndex& idx)
{
    j.at("root").get_to(idx.root);
    j.at("files").get_to(idx.files);
    j.at("total_lines").get_to(idx.total_lines);
    j.at("total_files").get_to(idx.total_files);
    j.at("build_timestamp").get_to(idx.build_timestamp);
}

void save(const ProjectIndex& idx, const std::filesystem::path& path)
{
    nlohmann::json j = idx;
    auto parent = path.parent_path();
    if (!parent.empty())
    {
        std::filesystem::create_directories(parent);
    }
    std::ofstream ofs(path);
    ofs << j.dump(2);
}

ProjectIndex load(const std::filesystem::path& path)
{
    if (!std::filesystem::exists(path))
    {
        throw std::runtime_error(
            "ProjectIndex::load: archivo no existe: " + path.string());
    }

    std::ifstream ifs(path);
    if (!ifs)
    {
        throw std::runtime_error(
            "ProjectIndex::load: no se pudo abrir: " + path.string());
    }

    try
    {
        nlohmann::json j;
        ifs >> j;
        ProjectIndex idx = j.get<ProjectIndex>();
        return idx;
    }
    catch (const std::exception& e)
    {
        throw std::runtime_error(
            "ProjectIndex::load: JSON inválido en '" + path.string() +
            "': " + e.what());
    }
}

} // namespace satellite::context