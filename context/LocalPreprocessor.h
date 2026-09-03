#pragma once

#include "llm/ILLMProvider.h"
#include "context/engine/ProjectContext.h"
#include <string>
#include <vector>
#include <filesystem>
#include <iostream>

namespace satellite::context
{

class LocalPreprocessor
{
public:
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

    LocalPreprocessor(llm::ILLMProvider* local_llm,
                         const ProjectContext& project,
                         std::filesystem::path project_root);

    Result preprocess(const std::string& user_goal);

private:
    NeedInfo analyze_needs(const std::string& user_goal);
    bool has_enough_context(const NeedInfo& needs);
    std::string gather_from_project(const NeedInfo& needs);
    std::string gather_from_user(const NeedInfo& needs);
    std::string build_refined_prompt(const std::string& user_goal,
                                        const std::string& project_context,
                                        const NeedInfo& needs);

    llm::ILLMProvider* local_llm_;
    const ProjectContext& project_;
    std::filesystem::path project_root_;
};

} // namespace satellite::context