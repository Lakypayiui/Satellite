#include "ContextEngine.h"
#include <filesystem>
#include <fstream>
#include <regex>
#include <algorithm>
#include <cctype>

namespace satellite::context
{

ContextEngine::ContextEngine(std::filesystem::path project_root)
    : root_(std::filesystem::weakly_canonical(project_root))
    , ignore_dirs_(default_ignore_dirs())
{
}

ProjectContext ContextEngine::build() const
{
    ProjectContext ctx;
    ctx.root = root_.lexically_normal().string();

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
                if (std::find(ignore_dirs_.begin(), ignore_dirs_.end(), part.string()) != ignore_dirs_.end())
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
        const auto language = detect_language(file_path);

        FileInfo file_info;
        file_info.path = rel_path;
        file_info.language = language;
        file_info.size = static_cast<std::size_t>(file_size);
        file_info.lines = count_lines(content);

        if (language == "C++")
        {
            file_info.symbols = extract_symbols_cpp(content, rel_path);
            auto deps = extract_deps_cpp(content, rel_path);
            ctx.dependencies.insert(ctx.dependencies.end(), deps.begin(), deps.end());
        }
        else if (language == "Python")
        {
            file_info.symbols = extract_symbols_python(content, rel_path);
            auto deps = extract_deps_python(content, rel_path);
            ctx.dependencies.insert(ctx.dependencies.end(), deps.begin(), deps.end());
        }

        ctx.files.push_back(std::move(file_info));
        ctx.total_lines += file_info.lines;
    }

    ctx.total_files = ctx.files.size();
    return ctx;
}

void ContextEngine::set_ignore_dirs(std::vector<std::string> dirs)
{
    ignore_dirs_ = std::move(dirs);
}

std::vector<std::string> ContextEngine::ignore_dirs() const
{
    return ignore_dirs_;
}

std::vector<std::string> ContextEngine::default_ignore_dirs()
{
    return {".git", "build", "node_modules", ".satellite", ".agent",
            "out", "dist", ".venv", "venv", "__pycache__", "CMakeFiles",
            ".idea", ".vscode"};
}

std::string ContextEngine::detect_language(const std::filesystem::path& path)
{
    const auto ext = path.extension().string();
    if (ext == ".cpp" || ext == ".cc" || ext == ".cxx" ||
        ext == ".h" || ext == ".hpp" || ext == ".hh")
    {
        return "C++";
    }
    if (ext == ".py")
    {
        return "Python";
    }
    return "";
}

bool ContextEngine::should_process_file(const std::filesystem::path& path)
{
    const auto lang = detect_language(path);
    return !lang.empty();
}

std::vector<SymbolInfo> ContextEngine::extract_symbols_cpp(const std::string& content,
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

std::vector<SymbolInfo> ContextEngine::extract_symbols_python(const std::string& content,
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

std::vector<SymbolInfo> ContextEngine::extract_symbols_js_ts(const std::string&,
                                                              const std::string&)
{
    return {};
}

std::vector<SymbolInfo> ContextEngine::extract_symbols_java(const std::string&,
                                                             const std::string&)
{
    return {};
}

std::vector<SymbolInfo> ContextEngine::extract_symbols_rust(const std::string&,
                                                             const std::string&)
{
    return {};
}

std::vector<SymbolInfo> ContextEngine::extract_symbols_go(const std::string&,
                                                           const std::string&)
{
    return {};
}

std::vector<DependencyInfo> ContextEngine::extract_deps_cpp(const std::string& content,
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

std::vector<DependencyInfo> ContextEngine::extract_deps_python(const std::string& content,
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

std::vector<DependencyInfo> ContextEngine::extract_deps_js_ts(const std::string&,
                                                               const std::string&)
{
    return {};
}

std::vector<DependencyInfo> ContextEngine::extract_deps_java(const std::string&,
                                                              const std::string&)
{
    return {};
}

std::vector<DependencyInfo> ContextEngine::extract_deps_rust(const std::string&,
                                                              const std::string&)
{
    return {};
}

std::vector<DependencyInfo> ContextEngine::extract_deps_go(const std::string&,
                                                            const std::string&)
{
    return {};
}

std::string ContextEngine::normalize_path(const std::filesystem::path& root,
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

std::size_t ContextEngine::count_lines(const std::string& content)
{
    return std::count(content.begin(), content.end(), '\n') + (content.empty() ? 0 : 1);
}

std::string ContextEngine::trim_signature(const std::string& line)
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

} // namespace satellite::context