#include "ContextOptimizer.h"

#include <algorithm>
#include <cctype>
#include <chrono>
#include <set>
#include <sstream>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <vector>

namespace satellite::context
{

namespace
{

std::string to_lower(const std::string& s)
{
    std::string r;
    r.reserve(s.size());
    for (unsigned char c : s)
    {
        r.push_back(static_cast<char>(std::tolower(c)));
    }
    return r;
}

std::vector<std::string> split_words(const std::string& s)
{
    std::vector<std::string> words;
    std::string cur;
    for (unsigned char c : s)
    {
        if (std::isalnum(c))
        {
            cur.push_back(static_cast<char>(std::tolower(c)));
        }
        else
        {
            if (!cur.empty())
            {
                words.push_back(cur);
                cur.clear();
            }
        }
    }
    if (!cur.empty())
    {
        words.push_back(cur);
    }
    return words;
}

bool is_stopword(const std::string& w)
{
    static const std::unordered_set<std::string> stopwords = {
        "de", "la", "el", "del", "los", "las", "que", "para", "con", "por",
        "una", "un", "and", "the", "for", "with", "from", "into",
        "implement", "add", "create", "make", "fix", "change"
    };
    return stopwords.find(w) != stopwords.end();
}

std::size_t token_estimate(const FileInfo& file)
{
    return std::max<std::size_t>(1, file.size / 4);
}

bool contains_keyword(const std::string& haystack, const std::string& needle)
{
    if (needle.empty())
    {
        return false;
    }
    std::string h = to_lower(haystack);
    std::string n = to_lower(needle);
    return h.find(n) != std::string::npos;
}

} // namespace

ContextSelection DefaultContextOptimizer::optimize(const Task& task,
                                                   const satellite::core::agent::AgentDescriptor& agent,
                                                   const ProjectContext& project,
                                                   const satellite::core::protocol::TokenBudget& budget)
{
    auto start = std::chrono::steady_clock::now();

    ContextSelection selection;
    stats_ = OptimizationStats{};

    if (project.files.empty())
    {
        stats_.tokens_before = 0;
        stats_.tokens_after = 0;
        stats_.tokens_saved = 0;
        stats_.compression_ratio = 0.0;
        stats_.relevance_score = 0.0;
        auto end = std::chrono::steady_clock::now();
        stats_.optimization_time_ms = std::chrono::duration<double, std::milli>(end - start).count();
        return selection;
    }

    std::vector<std::string> keywords;

    for (const auto& kw : task.keywords)
    {
        std::string k = to_lower(kw);
        if (k.size() >= 3 && !is_stopword(k))
        {
            keywords.push_back(k);
        }
    }

    for (const auto& w : split_words(task.description))
    {
        if (w.size() >= 3 && !is_stopword(w))
        {
            keywords.push_back(w);
        }
    }

    for (const auto& req : agent.context_requirements)
    {
        std::string r = to_lower(req);
        if (r.size() >= 3 && !is_stopword(r))
        {
            keywords.push_back(r);
            keywords.push_back(r);
        }
    }

    std::sort(keywords.begin(), keywords.end());
    keywords.erase(std::unique(keywords.begin(), keywords.end()), keywords.end());

    std::vector<double> scores(project.files.size(), 0.0);
    double total_score = 0.0;

    for (std::size_t i = 0; i < project.files.size(); ++i)
    {
        const FileInfo& file = project.files[i];
        double score = 0.0;

        for (const auto& kw : keywords)
        {
            if (contains_keyword(file.path, kw))
            {
                score += 2.0;
            }
        }

        for (const auto& sym : file.symbols)
        {
            bool sym_matched = false;
            for (const auto& kw : keywords)
            {
                if (contains_keyword(sym.name, kw) || contains_keyword(sym.signature, kw))
                {
                    score += 3.0;
                    sym_matched = true;
                    break;
                }
            }
            if (sym_matched)
            {
                score += 1.0;
            }
        }

        std::size_t sym_keyword_count = 0;
        for (const auto& sym : file.symbols)
        {
            for (const auto& kw : keywords)
            {
                if (contains_keyword(sym.name, kw))
                {
                    ++sym_keyword_count;
                    break;
                }
            }
        }
        score += std::min<std::size_t>(sym_keyword_count, 5);

        scores[i] = score;
        total_score += score;
    }

    std::vector<std::size_t> local_targets;
    local_targets.reserve(project.files.size());
    for (std::size_t i = 0; i < project.files.size(); ++i)
    {
        if (scores[i] > 0.0)
        {
            local_targets.push_back(i);
        }
    }

    std::unordered_set<std::size_t> dep_boosted;
    for (const auto& dep : project.dependencies)
    {
        if (!dep.external)
        {
            for (std::size_t i = 0; i < project.files.size(); ++i)
            {
                if (project.files[i].path == dep.from_file)
                {
                    if (scores[i] > 0.0)
                    {
                        for (std::size_t j = 0; j < project.files.size(); ++j)
                        {
                            if (project.files[j].path == dep.target && dep_boosted.insert(j).second)
                            {
                                scores[j] += 2.0;
                                total_score += 2.0;
                            }
                        }
                    }
                    break;
                }
            }
        }
    }

    std::vector<std::size_t> indices(project.files.size());
    for (std::size_t i = 0; i < project.files.size(); ++i)
    {
        indices[i] = i;
    }

    std::sort(indices.begin(), indices.end(),
              [&](std::size_t a, std::size_t b)
              {
                  if (scores[a] != scores[b])
                  {
                      return scores[a] > scores[b];
                  }
                  return project.files[a].path < project.files[b].path;
              });

    std::vector<std::string> selected_paths;
    std::size_t accumulated_tokens = 0;
    double selected_score_sum = 0.0;

    for (std::size_t idx : indices)
    {
        const FileInfo& file = project.files[idx];
        std::size_t est = token_estimate(file);

        if (budget.max_tokens != 0 && accumulated_tokens + est > budget.max_tokens)
        {
            continue;
        }

        selected_paths.push_back(file.path);
        accumulated_tokens += est;
        selected_score_sum += scores[idx];
    }

    selection.selected_files = selected_paths;
    selection.estimated_tokens = accumulated_tokens;

    std::unordered_set<std::string> selected_file_set(selected_paths.begin(), selected_paths.end());

    std::vector<SymbolInfo> candidate_symbols;
    for (const auto& file : project.files)
    {
        if (selected_file_set.find(file.path) != selected_file_set.end())
        {
            for (const auto& sym : file.symbols)
            {
                bool has_kw = false;
                for (const auto& kw : keywords)
                {
                    if (contains_keyword(sym.name, kw))
                    {
                        has_kw = true;
                        break;
                    }
                }
                if (has_kw)
                {
                    candidate_symbols.push_back(sym);
                }
            }
        }
    }

    if (candidate_symbols.empty())
    {
        std::size_t limit_files = std::min<std::size_t>(2, selected_paths.size());
        for (std::size_t i = 0; i < limit_files; ++i)
        {
            for (const auto& file : project.files)
            {
                if (file.path == selected_paths[i])
                {
                    for (const auto& sym : file.symbols)
                    {
                        candidate_symbols.push_back(sym);
                    }
                    break;
                }
            }
        }
    }

    if (candidate_symbols.size() > 20)
    {
        candidate_symbols.resize(20);
    }
    selection.selected_symbols = candidate_symbols;

    for (const auto& dep : project.dependencies)
    {
        if (selected_file_set.find(dep.from_file) != selected_file_set.end())
        {
            selection.selected_dependencies.push_back(dep);
        }
    }

    for (const auto& req : agent.context_requirements)
    {
        bool found = false;
        std::string rlow = to_lower(req);
        for (const auto& path : selected_paths)
        {
            if (contains_keyword(path, rlow))
            {
                found = true;
                break;
            }
        }
        if (!found)
        {
            for (const auto& sym : selection.selected_symbols)
            {
                if (contains_keyword(sym.name, rlow) || contains_keyword(sym.signature, rlow))
                {
                    found = true;
                    break;
                }
            }
        }
        if (found)
        {
            selection.selected_constraints.push_back(req);
        }
    }

    if (selection.selected_constraints.empty())
    {
        selection.selected_constraints.push_back("sin restricciones detectadas");
    }

    stats_.tokens_before = 0;
    for (const auto& file : project.files)
    {
        stats_.tokens_before += token_estimate(file);
    }
    stats_.tokens_after = selection.estimated_tokens;
    stats_.tokens_saved = stats_.tokens_before > stats_.tokens_after
                              ? stats_.tokens_before - stats_.tokens_after
                              : 0;
    stats_.compression_ratio = stats_.tokens_before > 0
                                   ? static_cast<double>(stats_.tokens_saved) / stats_.tokens_before
                                   : 0.0;
    stats_.relevance_score = total_score > 0.0
                                 ? std::min(1.0, std::max(0.0, selected_score_sum / total_score))
                                 : 0.0;

    auto end = std::chrono::steady_clock::now();
    stats_.optimization_time_ms = std::chrono::duration<double, std::milli>(end - start).count();

    return selection;
}

OptimizationStats DefaultContextOptimizer::last_stats() const
{
    return stats_;
}

} // namespace satellite::context