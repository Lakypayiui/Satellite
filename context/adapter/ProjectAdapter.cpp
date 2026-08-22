#include "ProjectAdapter.h"
#include <filesystem>
#include <algorithm>

namespace satellite::context
{

namespace
{

bool is_ignored_dir(const std::filesystem::path& part, const std::vector<std::string>& ignore_dirs)
{
    return std::find(ignore_dirs.begin(), ignore_dirs.end(), part.string()) != ignore_dirs.end();
}

bool has_ignored_dir(const std::filesystem::path& rel_path, const std::vector<std::string>& ignore_dirs)
{
    for (const auto& part : rel_path)
    {
        if (is_ignored_dir(part, ignore_dirs))
        {
            return true;
        }
    }
    return false;
}

std::vector<std::string> default_ignore_dirs()
{
    return {".git", "build", "node_modules", ".satellite", ".agent",
            "out", "dist", ".venv", "venv", "__pycache__", "CMakeFiles",
            ".idea", ".vscode"};
}

bool has_cpp_extension(const std::filesystem::path& path)
{
    const auto ext = path.extension().string();
    return ext == ".cpp" || ext == ".cc" || ext == ".cxx" ||
           ext == ".h" || ext == ".hpp" || ext == ".hh";
}

bool has_python_extension(const std::filesystem::path& path)
{
    const auto ext = path.extension().string();
    return ext == ".py";
}

bool is_cpp_config_file(const std::filesystem::path& path)
{
    const auto filename = path.filename().string();
    return filename == "CMakeLists.txt" || filename == "Makefile";
}

bool is_python_config_file(const std::filesystem::path& path)
{
    const auto filename = path.filename().string();
    return filename == "setup.py" || filename == "pyproject.toml" || filename == "requirements.txt";
}

} // anonymous namespace

std::string CppProjectAdapter::language() const
{
    return "C++";
}

bool CppProjectAdapter::supports(const std::filesystem::path& root) const
{
    const auto ignore_dirs = default_ignore_dirs();

    try
    {
        for (auto it = std::filesystem::recursive_directory_iterator(root);
             it != std::filesystem::recursive_directory_iterator{}; ++it)
        {
            const auto& entry = *it;
            const auto rel_path = entry.path().lexically_relative(root);

            if (has_ignored_dir(rel_path, ignore_dirs))
            {
                if (entry.is_directory())
                {
                    it.disable_recursion_pending();
                }
                continue;
            }

            if (entry.is_regular_file())
            {
                if (has_cpp_extension(entry.path()) || is_cpp_config_file(entry.path()))
                {
                    return true;
                }
            }
        }
    }
    catch (const std::filesystem::filesystem_error&)
    {
    }

    return false;
}

ProjectContext CppProjectAdapter::build_context(const std::filesystem::path& root) const
{
    ProjectContext ctx = ContextEngine(root).build();

    std::vector<FileInfo> filtered_files;
    filtered_files.reserve(ctx.files.size());

    for (const auto& file : ctx.files)
    {
        if (file.language == language())
        {
            filtered_files.push_back(file);
        }
    }

    std::vector<DependencyInfo> filtered_deps;
    filtered_deps.reserve(ctx.dependencies.size());

    for (const auto& dep : ctx.dependencies)
    {
        bool from_file_exists = false;
        for (const auto& file : filtered_files)
        {
            if (file.path == dep.from_file)
            {
                from_file_exists = true;
                break;
            }
        }
        if (from_file_exists)
        {
            filtered_deps.push_back(dep);
        }
    }

    std::size_t total_lines = 0;
    for (const auto& file : filtered_files)
    {
        total_lines += file.lines;
    }

    ctx.files = std::move(filtered_files);
    ctx.dependencies = std::move(filtered_deps);
    ctx.total_lines = total_lines;
    ctx.total_files = ctx.files.size();

    return ctx;
}

std::string PythonProjectAdapter::language() const
{
    return "Python";
}

bool PythonProjectAdapter::supports(const std::filesystem::path& root) const
{
    const auto ignore_dirs = default_ignore_dirs();

    try
    {
        for (auto it = std::filesystem::recursive_directory_iterator(root);
             it != std::filesystem::recursive_directory_iterator{}; ++it)
        {
            const auto& entry = *it;
            const auto rel_path = entry.path().lexically_relative(root);

            if (has_ignored_dir(rel_path, ignore_dirs))
            {
                if (entry.is_directory())
                {
                    it.disable_recursion_pending();
                }
                continue;
            }

            if (entry.is_regular_file())
            {
                if (has_python_extension(entry.path()) || is_python_config_file(entry.path()))
                {
                    return true;
                }
            }
        }
    }
    catch (const std::filesystem::filesystem_error&)
    {
    }

    return false;
}

ProjectContext PythonProjectAdapter::build_context(const std::filesystem::path& root) const
{
    ProjectContext ctx = ContextEngine(root).build();

    std::vector<FileInfo> filtered_files;
    filtered_files.reserve(ctx.files.size());

    for (const auto& file : ctx.files)
    {
        if (file.language == language())
        {
            filtered_files.push_back(file);
        }
    }

    std::vector<DependencyInfo> filtered_deps;
    filtered_deps.reserve(ctx.dependencies.size());

    for (const auto& dep : ctx.dependencies)
    {
        bool from_file_exists = false;
        for (const auto& file : filtered_files)
        {
            if (file.path == dep.from_file)
            {
                from_file_exists = true;
                break;
            }
        }
        if (from_file_exists)
        {
            filtered_deps.push_back(dep);
        }
    }

    std::size_t total_lines = 0;
    for (const auto& file : filtered_files)
    {
        total_lines += file.lines;
    }

    ctx.files = std::move(filtered_files);
    ctx.dependencies = std::move(filtered_deps);
    ctx.total_lines = total_lines;
    ctx.total_files = ctx.files.size();

    return ctx;
}

std::unique_ptr<IProjectAdapter> ProjectAdapterFactory::detect(const std::filesystem::path& project_root)
{
    auto cpp_adapter = std::make_unique<CppProjectAdapter>();
    if (cpp_adapter->supports(project_root))
    {
        return cpp_adapter;
    }

    auto python_adapter = std::make_unique<PythonProjectAdapter>();
    if (python_adapter->supports(project_root))
    {
        return python_adapter;
    }

    return nullptr;
}

} // namespace satellite::context