#include "context/LocalPreprocessor.h"
#include "context/engine/ContextEngine.h"
#include "context/engine/ProjectIndex.h"
#include <json.hpp>
#include <algorithm>
#include <cctype>
#include <sstream>

namespace satellite::context
{

LocalPreprocessor::LocalPreprocessor(llm::ILLMProvider* local_llm,
                                         const ProjectContext& project,
                                         std::filesystem::path project_root)
    : local_llm_(local_llm)
    , project_(project)
    , project_root_(std::move(project_root))
{
}

LocalPreprocessor::Result LocalPreprocessor::preprocess(const std::string& user_goal)
{
    Result result;

    std::string system_prompt = "Eres un asistente que analiza tareas de desarrollo de software y determina "
                                "que informacion del proyecto es necesaria para completarlas. "
                                "Responde SOLO con JSON: {\"category\": \"...\", "
                                "\"files_needed\": [\"...\"], \"symbols_needed\": [\"...\"], "
                                "\"description\": \"...\"}. No ejecutes ninguna tarea, solo analiza.";

    std::string user_prompt = "Tarea: " + user_goal + "\n"
                              "Archivos del proyecto: " + std::to_string(project_.files.size()) + "\n"
                              "Total lineas: " + std::to_string(project_.total_lines) + "\n"
                              "Que informacion del proyecto necesitas para completar esta tarea?";

    llm::LLMRequest request;
    request.system_prompt = system_prompt;
    request.user_prompt = user_prompt;
    request.max_tokens = 500;
    request.temperature = 0.0;

    llm::LLMResponse response = local_llm_->complete(request);

    if (!response.ok)
    {
        result.needs_user_input = true;
        result.user_prompt = "Error al analizar la tarea con el modelo local: " + response.error_message;
        return result;
    }

    NeedInfo needs;
    try
    {
        auto j = nlohmann::json::parse(response.text, nullptr, false);
        if (!j.is_discarded())
        {
            if (j.contains("category")) needs.category = j["category"].get<std::string>();
            if (j.contains("files_needed") && j["files_needed"].is_array())
            {
                for (const auto& f : j["files_needed"])
                {
                    needs.files_needed.push_back(f.get<std::string>());
                }
            }
            if (j.contains("symbols_needed") && j["symbols_needed"].is_array())
            {
                for (const auto& s : j["symbols_needed"])
                {
                    needs.symbols_needed.push_back(s.get<std::string>());
                }
            }
            if (j.contains("description")) needs.description = j["description"].get<std::string>();
        }
    }
    catch (const nlohmann::json::parse_error&)
    {
        needs.description = response.text;
    }

    bool has_enough = has_enough_context(needs);

    std::string project_context_str;
    if (!has_enough)
    {
        project_context_str = gather_from_project(needs);
    }

    if (!has_enough && project_context_str.empty())
    {
        result.needs_user_input = true;
        result.user_prompt = gather_from_user(needs);
        return result;
    }

    result.refined_prompt = build_refined_prompt(user_goal, project_context_str, needs);
    return result;
}

LocalPreprocessor::NeedInfo LocalPreprocessor::analyze_needs(const std::string& user_goal)
{
    NeedInfo needs;
    needs.category = "general";
    needs.description = user_goal;
    return needs;
}

bool LocalPreprocessor::has_enough_context(const NeedInfo& needs)
{
    if (needs.files_needed.empty() && needs.symbols_needed.empty())
    {
        return true;
    }

    for (const auto& file_path : needs.files_needed)
    {
        bool found = false;
        for (const auto& f : project_.files)
        {
            if (f.path == file_path || f.path.find(file_path) != std::string::npos)
            {
                found = true;
                break;
            }
        }
        if (!found)
        {
            return false;
        }
    }

    return true;
}

std::string LocalPreprocessor::gather_from_project(const NeedInfo& needs)
{
    std::ostringstream oss;
    oss << "Contexto del proyecto (" << project_.total_files << " archivos, "
        << project_.total_lines << " lineas):\n";

    for (const auto& f : project_.files)
    {
        bool relevant = false;
        for (const auto& need : needs.files_needed)
        {
            if (f.path.find(need) != std::string::npos)
            {
                relevant = true;
                break;
            }
        }
        if (needs.files_needed.empty() || relevant)
        {
            oss << "Archivo: " << f.path << " (" << f.type << ", "
                << f.lines << " lineas)\n";
            for (const auto& sym : f.symbols)
            {
                oss << "  - " << sym.name << " (" << static_cast<int>(sym.kind) << ")\n";
            }
        }
    }

    return oss.str();
}

std::string LocalPreprocessor::gather_from_user(const NeedInfo& needs)
{
    std::ostringstream oss;
    oss << "Para completar la tarea se necesita informacion adicional del proyecto.\n";
    oss << "Categoria: " << needs.category << "\n";
    if (!needs.files_needed.empty())
    {
        oss << "Archivos necesarios: ";
        for (size_t i = 0; i < needs.files_needed.size(); ++i)
        {
            if (i > 0) oss << ", ";
            oss << needs.files_needed[i];
        }
        oss << "\n";
    }
    if (!needs.symbols_needed.empty())
    {
        oss << "Simbolos necesarios: ";
        for (size_t i = 0; i < needs.symbols_needed.size(); ++i)
        {
            if (i > 0) oss << ", ";
            oss << needs.symbols_needed[i];
        }
        oss << "\n";
    }
    oss << "Por favor proporciona la siguiente informacion:\n";
    if (needs.files_needed.empty() && needs.symbols_needed.empty())
    {
        oss << "- Descripcion de la tarea: ";
    }
    else
    {
        oss << "- Informacion adicional del proyecto: ";
    }
    std::string user_input;
    std::getline(std::cin, user_input);
    return user_input;
}

std::string LocalPreprocessor::build_refined_prompt(const std::string& user_goal,
                                                      const std::string& project_context,
                                                      const NeedInfo& needs)
{
    std::ostringstream oss;
    oss << "Contexto del proyecto:\n" << project_context << "\n\n";
    oss << "Tarea del usuario: " << user_goal << "\n";
    if (!needs.description.empty())
    {
        oss << "Analisis del modelo local: " << needs.description << "\n";
    }
    oss << "\nResponde SOLO con JSON: {\"steps\": [{\"agent_id\": N, \"input\": {...}, "
           "\"dependencies\": [indices], \"description\": \"...\"}]}. "
           "Usa solo agentes del catalogo disponible. NO ejecutes tareas directamente.";
    return oss.str();
}

} // namespace satellite::context