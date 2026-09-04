#pragma once

#include "llm/ILLMProvider.h"
#include "context/engine/ProjectContext.h"
#include <string>
#include <vector>
#include <filesystem>
#include <iostream>

namespace satellite::context
{

struct NeedInfo
{
    std::string category;
    std::vector<std::string> files_needed;
    std::vector<std::string> symbols_needed;
    std::string description;
};

struct Result
{
    std::string refined_prompt;
    bool needs_user_input = false;
    std::string user_prompt;
    std::vector<std::string> missing_info;
};

class LocalPreprocessor
{
public:
    LocalPreprocessor(llm::ILLMProvider* local_llm,
                         const ProjectContext& project,
                         std::filesystem::path project_root);

    /// Preprocesa el objetivo del usuario con el modelo local.
    /// Itera (hasta max_rounds): el modelo local decide qué contexto falta,
    /// el runtime lo trae del índice del proyecto, y se re-evalúa.
    Result preprocess(const std::string& user_goal, int max_rounds = 3);

private:
    NeedInfo analyze_needs(const std::string& user_goal,
                           const std::string& context_so_far);
    bool has_enough_context(const NeedInfo& needs) const;
    /// Traduce files_needed/symbols_needed a contenido real del proyecto
    /// vía el índice incremental (.satellite/context/index.json).
    std::string gather_from_project(const NeedInfo& needs,
                                    std::string& not_found_out);
    std::string gather_from_user(const NeedInfo& needs);
    std::string build_refined_prompt(const std::string& user_goal,
                                        const std::string& project_context,
                                        const NeedInfo& needs);

    llm::ILLMProvider* local_llm_;
    const ProjectContext& project_;
    std::filesystem::path project_root_;
};

} // namespace satellite::context