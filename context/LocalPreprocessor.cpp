#include "context/LocalPreprocessor.h"
#include "context/engine/ProjectIndex.h"
#include "context/engine/SemanticContext.h"
#include <json.hpp>
#include <algorithm>
#include <iostream>
#include <sstream>

namespace satellite::context
{

// Prompt de sistema: el modelo local solo DECIDE (estructura JSON),
// nunca ejecuta ni planifica la tarea.
static const char* kLocalSystemPrompt =
    "Eres un preprocesador de contexto. Analizas tareas de desarrollo y decides "
    "que contexto del proyecto hace falta. Responde SOLO con JSON: "
    "{\"category\": \"...\", \"files_needed\": [\"ruta/relativa\", ...], "
    "\"symbols_needed\": [\"nombre_simbolo\", ...], \"description\": \"...\", "
    "\"sufficient\": true|false}. "
    "Usa \"sufficient\": true cuando el contexto proporcionado ya basta. "
    "Nunca escribas codigo ni ejecutes nada; solo decides.";

LocalPreprocessor::LocalPreprocessor(llm::ILLMProvider* local_llm,
                                         const ProjectContext& project,
                                         std::filesystem::path project_root)
    : local_llm_(local_llm)
    , project_(project)
    , project_root_(std::move(project_root))
{
}

// Extrae el primer objeto JSON balanceado de un texto (robusto ante
// prólogos/epílogos que el modelo local pueda añadir).
static nlohmann::json extract_first_json(const std::string& text)
{
    const std::size_t start = text.find('{');
    if (start == std::string::npos)
    {
        return nlohmann::json();
    }
    int depth = 0;
    bool in_string = false;
    bool escaped = false;
    for (std::size_t i = start; i < text.size(); ++i)
    {
        const char c = text[i];
        if (escaped) { escaped = false; continue; }
        if (in_string && c == '\\') { escaped = true; continue; }
        if (c == '"') { in_string = !in_string; continue; }
        if (in_string) { continue; }
        if (c == '{') { ++depth; }
        else if (c == '}' && --depth == 0)
        {
            return nlohmann::json::parse(text.substr(start, i - start + 1),
                                         nullptr, false);
        }
    }
    return nlohmann::json();
}

static NeedInfo parse_need_info(const nlohmann::json& j, bool& sufficient_out)
{
    NeedInfo needs;
    sufficient_out = false;
    if (j.is_discarded())
    {
        return needs;
    }
    if (j.contains("category"))
    {
        needs.category = j["category"].get<std::string>();
    }
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
    if (j.contains("description"))
    {
        needs.description = j["description"].get<std::string>();
    }
    if (j.contains("sufficient") && j["sufficient"].is_boolean())
    {
        sufficient_out = j["sufficient"].get<bool>();
    }
    return needs;
}

satellite::context::Result LocalPreprocessor::preprocess(const std::string& user_goal,
                                                        int max_rounds)
{
    Result result;
    const int rounds = std::max(1, max_rounds);
    std::string gathered_context;
    NeedInfo last_needs;

    for (int round = 0; round < rounds; ++round)
    {
        // [1] El modelo local decide qué contexto falta (o si ya basta).
        last_needs = analyze_needs(user_goal, gathered_context);
        if (last_needs.category == "__error__")
        {
            result.needs_user_input = true;
            result.user_prompt = last_needs.description;
            return result;
        }

        // Nada que traer del proyecto: suficiente o sin pedidos concretos.
        if (has_enough_context(last_needs))
        {
            result.refined_prompt = build_refined_prompt(user_goal,
                                                         gathered_context,
                                                         last_needs);
            return result;
        }

        // [2] El runtime trae el contexto pedido (contenido real de archivos).
        std::string still_missing;
        const std::string found = gather_from_project(last_needs, still_missing);

        if (!found.empty())
        {
            if (!gathered_context.empty())
            {
                gathered_context += "\n";
            }
            gathered_context += found;
            // Sigue el bucle: el modelo local re-evalúa con más contexto.
            continue;
        }

        // No se pudo traer nada de lo pedido: registrarlo en missing_info.
        result.missing_info.push_back(
            still_missing.empty() ? last_needs.category : still_missing);

        // Última ronda sin poder satisfacer al modelo: preguntar al usuario.
        if (round == rounds - 1)
        {
            result.needs_user_input = true;
            result.user_prompt = gather_from_user(last_needs);
            return result;
        }
    }

    result.refined_prompt = build_refined_prompt(user_goal, gathered_context,
                                                 last_needs);
    return result;
}

satellite::context::NeedInfo LocalPreprocessor::analyze_needs(
    const std::string& user_goal,
    const std::string& context_so_far)
{
    std::ostringstream user_prompt;
    user_prompt << "Tarea: " << user_goal << "\n"
                << "Archivos del proyecto: " << project_.files.size() << "\n"
                << "Total lineas: " << project_.total_lines << "\n";
    if (context_so_far.empty())
    {
        user_prompt << "Que informacion del proyecto necesitas para esta tarea?";
    }
    else
    {
        user_prompt << "Contexto ya disponible:\n" << context_so_far << "\n"
                    << "Es suficiente? Si no, que archivos o simbolos faltan?";
    }

    llm::LLMRequest request;
    request.system_prompt = kLocalSystemPrompt;
    request.user_prompt = user_prompt.str();
    request.max_tokens = 500;
    request.temperature = 0.0;

    llm::LLMResponse response = local_llm_->complete(request);
    if (!response.ok)
    {
        NeedInfo error_info;
        error_info.category = "__error__";
        error_info.description =
            "Error al analizar la tarea con el modelo local: " + response.error_message;
        return error_info;
    }

    bool sufficient = false;
    NeedInfo needs = parse_need_info(extract_first_json(response.text), sufficient);

    if (needs.category.empty() && needs.files_needed.empty() &&
        needs.symbols_needed.empty() && needs.description.empty())
    {
        // El modelo no devolvió JSON parseable: su texto pasa como
        // descripción y se asume contexto suficiente (no bloquear el flujo).
        needs.category = "general";
        needs.description = response.text;
        sufficient = true;
    }
    if (sufficient)
    {
        needs.files_needed.clear();
        needs.symbols_needed.clear();
    }
    return needs;
}

bool LocalPreprocessor::has_enough_context(const NeedInfo& needs) const
{
    return needs.files_needed.empty() && needs.symbols_needed.empty();
}

std::string LocalPreprocessor::gather_from_project(const NeedInfo& needs,
                                                   std::string& not_found_out)
{
    // Resuelve los pedidos contra el índice del proyecto
    // (.satellite/context/index.json) y devuelve CONTENIDO real.
    const std::filesystem::path index_path =
        project_root_ / ".satellite" / "context" / "index.json";

    if (std::filesystem::exists(index_path))
    {
        ProjectIndex index = load(index_path);
        std::vector<std::string> resolved_paths;

        auto take = [&](const std::string& path) {
            resolved_paths.push_back(path);
        };
        auto match_path = [&](const std::string& want) {
            for (const auto& f : index.files)
            {
                if (f.path == want || f.path.find(want) != std::string::npos)
                {
                    take(f.path);
                    return true;
                }
            }
            return false;
        };
        auto match_symbol = [&](const std::string& sym) {
            for (const auto& f : index.files)
            {
                if (std::find(f.symbols.begin(), f.symbols.end(), sym) !=
                    f.symbols.end())
                {
                    take(f.path);
                    return true;
                }
            }
            return false;
        };

        std::ostringstream missing;
        for (const auto& f : needs.files_needed)
        {
            if (!match_path(f)) { missing << " archivo:" << f; }
        }
        for (const auto& s : needs.symbols_needed)
        {
            if (!match_symbol(s)) { missing << " simbolo:" << s; }
        }
        not_found_out = missing.str();

        std::sort(resolved_paths.begin(), resolved_paths.end());
        resolved_paths.erase(
            std::unique(resolved_paths.begin(), resolved_paths.end()),
            resolved_paths.end());
        if (resolved_paths.empty())
        {
            return "";
        }

        IncrementalContextBuilder builder(project_root_);
        SemanticContext semantic =
            builder.build_for_paths(index, resolved_paths, 60000);

        std::ostringstream oss;
        oss << "Contenido del proyecto (" << semantic.files.size()
            << " archivos, " << semantic.total_chars << " chars):\n";
        for (const auto& sf : semantic.files)
        {
            oss << "=== " << sf.path << " (" << sf.language << ") ===\n"
                << sf.content << "\n";
        }
        return oss.str();
    }

    // Sin índice: caer al ProjectContext ya construido (metadatos+símbolos).
    std::ostringstream oss;
    bool any = false;
    for (const auto& f : project_.files)
    {
        bool relevant = false;
        for (const auto& want : needs.files_needed)
        {
            if (f.path == want || f.path.find(want) != std::string::npos)
            {
                relevant = true;
                break;
            }
        }
        if (!relevant)
        {
            continue;
        }
        any = true;
        oss << "Archivo: " << f.path << " (" << f.type << ", "
            << f.lines << " lineas)\n";
        for (const auto& sym : f.symbols)
        {
            oss << "  - " << static_cast<int>(sym.kind) << " " << sym.name << "\n";
        }
    }
    if (!any)
    {
        return "";
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
    std::cout << oss.str() << std::flush;

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