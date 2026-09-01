#include "SemanticContext.h"
#include <fstream>
#include <algorithm>

namespace satellite::context
{

IncrementalContextBuilder::IncrementalContextBuilder(std::filesystem::path project_root)
    : root_(std::filesystem::weakly_canonical(project_root))
{
}

bool IncrementalContextBuilder::path_in_index(const ProjectIndex& index,
                                               const std::string& path)
{
    return std::any_of(index.files.begin(), index.files.end(),
        [&path](const IndexedFile& f) { return f.path == path; });
}

std::string IncrementalContextBuilder::read_file_binary(
    const std::filesystem::path& full_path)
{
    std::ifstream ifs(full_path, std::ios::binary | std::ios::ate);
    if (!ifs)
    {
        return {};
    }
    const auto file_size = ifs.tellg();
    if (file_size <= 0)
    {
        return {};
    }
    ifs.seekg(0);
    std::string content(static_cast<std::size_t>(file_size), '\0');
    ifs.read(&content[0], file_size);
    return content;
}

SemanticContext IncrementalContextBuilder::build_for_paths(
    const ProjectIndex& index,
    const std::vector<std::string>& paths) const
{
    SemanticContext ctx;
    ctx.root = root_.lexically_normal().string();

    for (const auto& path : paths)
    {
        if (!path_in_index(index, path))
        {
            continue;
        }

        const IndexedFile* matched_file = nullptr;
        for (const auto& f : index.files)
        {
            if (f.path == path)
            {
                matched_file = &f;
                break;
            }
        }
        if (!matched_file)
        {
            continue;
        }

        const auto full_path = root_ / path;
        const auto content = read_file_binary(full_path);
        if (content.empty())
        {
            continue;
        }

        SemanticFile sf;
        sf.path = path;
        sf.language = matched_file->language;
        sf.content = content;
        ctx.files.push_back(std::move(sf));
        ctx.total_chars += content.size();
    }

    return ctx;
}

SemanticContext IncrementalContextBuilder::build_for_paths(
    const ProjectIndex& index,
    const std::vector<std::string>& paths,
    std::size_t max_total_chars) const
{
    SemanticContext ctx;
    ctx.root = root_.lexically_normal().string();

    for (const auto& path : paths)
    {
        if (!path_in_index(index, path))
        {
            continue;
        }

        const IndexedFile* matched_file = nullptr;
        for (const auto& f : index.files)
        {
            if (f.path == path)
            {
                matched_file = &f;
                break;
            }
        }
        if (!matched_file)
        {
            continue;
        }

        const auto full_path = root_ / path;
        const auto content = read_file_binary(full_path);
        if (content.empty())
        {
            continue;
        }

        if (ctx.total_chars + content.size() > max_total_chars)
        {
            continue;
        }

        SemanticFile sf;
        sf.path = path;
        sf.language = matched_file->language;
        sf.content = content;
        ctx.files.push_back(std::move(sf));
        ctx.total_chars += content.size();
    }

    return ctx;
}

} // namespace satellite::context