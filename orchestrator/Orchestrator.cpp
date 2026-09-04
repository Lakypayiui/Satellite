// Orchestrator.cpp
// Implementación del Orchestrator (Fase 11).

#include "orchestrator/Orchestrator.h"

#include <algorithm>
#include <cctype>
#include <chrono>
#include <sstream>
#include <unordered_set>

#include "core/registry/AgentRegistry.h"
#include "core/dispatcher/Dispatcher.h"
#include "core/catalog/AgentCatalog.h"
#include "context/optimizer/ContextOptimizer.h"
#include "context/engine/ProjectContext.h"
#include "llm/ILLMProvider.h"
#include "llm/LLMTypes.h"
#include "core/protocol/Protocol.h"
#include "observability/ExecutionLog.h"
#include "planner/Planner.h"

namespace satellite::orchestrator
{

Orchestrator::Orchestrator(satellite::core::registry::AgentRegistry& registry,
                           satellite::core::dispatcher::Dispatcher& dispatcher,
                           satellite::context::IContextOptimizer& optimizer,
                           satellite::llm::ILLMProvider* llm)
    : registry_(registry)
    , dispatcher_(dispatcher)
    , optimizer_(optimizer)
    , llm_(llm)
{
}

void Orchestrator::set_logger(satellite::observability::ExecutionLogger* logger)
{
    logger_ = logger;
}

OrchestrationResult Orchestrator::run_plan(const std::vector<OrchestrationStep>& plan,
                                           const ProjectContext& project,
                                           const TokenBudget& budget)
{
    OrchestrationResult result;
    result.ok = true;

    // Validación: pasos con agent_id == 0 → error
    for (std::size_t i = 0; i < plan.size(); ++i)
    {
        if (plan[i].agent_id == UNKNOWN_AGENT_ID)
        {
            result.ok = false;
            result.summary = "paso " + std::to_string(i) + ": agent_id == 0 (UNKNOWN_AGENT_ID)";
            return result;
        }
    }

    // Validación: dependencias dentro del rango y ya resueltas
    for (std::size_t i = 0; i < plan.size(); ++i)
    {
        for (AgentID dep_idx : plan[i].dependencies)
        {
            if (dep_idx >= plan.size())
            {
                result.ok = false;
                result.summary = "paso " + std::to_string(i) + ": dependencia " + std::to_string(dep_idx) + " fuera de rango";
                return result;
            }
            if (static_cast<std::size_t>(dep_idx) >= i)
            {
                result.ok = false;
                result.summary = "paso " + std::to_string(i) + ": dependencia " + std::to_string(dep_idx) +
                                 " no resuelta previamente (orden invalido o ciclo)";
                return result;
            }
        }
    }

    // Modelo de ejecución secuencial simple (no paralelo) — determinista.
    // Un paso se ejecuta solo cuando todos sus dependientes (índices) ya terminaron con status SUCCESS.
    // Si una dependencia falló → el paso se marca FAILED con error "dependency failed" SIN ejecutar el agente.
    // Si un paso falla → ok = false; abortar en la primera falla y marcar el resto como no ejecutados
    // (simplificación determinista). Los pasos restantes con dependencias del fallido no se ejecutan;
    // los independientes SÍ, pero en la práctica abortamos en la primera falla.

    std::vector<AgentResult> step_results;
    step_results.reserve(plan.size());

    for (std::size_t i = 0; i < plan.size(); ++i)
    {
        const OrchestrationStep& step = plan[i];

        // Verificar dependencias
        bool dependency_failed = false;
        for (AgentID dep_idx : step.dependencies)
        {
            if (step_results[static_cast<std::size_t>(dep_idx)].status != satellite::core::agent::AgentStatus::SUCCESS)
            {
                dependency_failed = true;
                break;
            }
        }

        AgentResult step_result;
        if (dependency_failed)
        {
            step_result.agent_id = step.agent_id;
            step_result.status = satellite::core::agent::AgentStatus::FAILED;
            step_result.error = satellite::core::agent::AgentError{satellite::core::agent::AgentErrorCode::EXECUTION_FAILED, "dependency failed"};
            step_result.duration_ms = 0.0;
        }
        else
        {
            step_result = execute_step(step, project, budget);
        }

        step_results.push_back(step_result);

        // Summary: una línea por paso "paso <i>: agent <nombre o id> -> <status>"
        const auto desc = registry_.find_agent(step.agent_id);
        std::string agent_name = desc ? desc->name : "id_" + std::to_string(step.agent_id);
        result.summary += "paso " + std::to_string(i) + ": agent " + agent_name + " -> " + std::string(satellite::core::agent::to_string(step_result.status));
        if (i + 1 < plan.size())
        {
            result.summary += "\n";
        }

        if (step_result.status != satellite::core::agent::AgentStatus::SUCCESS)
        {
            result.ok = false;
            // Abortar en la primera falla (simplificación determinista, documentado).
            break;
        }
    }

    result.results = std::move(step_results);
    return result;
}

OrchestrationResult Orchestrator::execute_goal(const std::string& goal,
                                               const ProjectContext& project,
                                               const TokenBudget& budget,
                                               const AgentCatalog& catalog)
{
    if (llm_ == nullptr)
    {
        return OrchestrationResult{false, {}, "LLM provider required for goal orchestration"};
    }

    std::string prompt = "Eres el orquestador de Satellite. Catálogo de agentes:\n";
    prompt += catalog.to_prompt();
    prompt += "\nObjetivo: ";
    prompt += goal;
    prompt += "\nResponde SOLO con JSON: {\"steps\": [{\"agent_id\": N, \"input\": {...}, \"dependencies\": [indices], \"description\": \"...\"}]}. Usa solo agentes del catálogo.";

    satellite::llm::LLMRequest request;
    request.system_prompt = "";
    request.user_prompt = prompt;
    request.max_tokens = 1500;
    request.temperature = 0.0;

    satellite::llm::LLMResponse response = llm_->complete(request);

    if (!response.ok)
    {
        return OrchestrationResult{false, {}, "LLM error: " + response.error_message};
    }

    nlohmann::json j = nlohmann::json::parse(response.text, nullptr, false);
    if (j.is_discarded() || !j.contains("steps") || !j["steps"].is_array())
    {
        return OrchestrationResult{false, {}, "invalid plan JSON"};
    }

    std::vector<OrchestrationStep> raw_steps;
    raw_steps.reserve(j["steps"].size());

    for (const auto& step_json : j["steps"])
    {
        OrchestrationStep step;
        step.agent_id = step_json.value("agent_id", UNKNOWN_AGENT_ID);
        step.input = step_json.value("input", nlohmann::json::object());
        step.dependencies = step_json.value("dependencies", std::vector<AgentID>{});
        step.description = step_json.value("description", std::string{});
        raw_steps.push_back(std::move(step));
    }

    satellite::planner::Planner planner;
    satellite::planner::Plan formal_plan;
    formal_plan.goal = goal;
    formal_plan.steps.reserve(raw_steps.size());

    for (std::size_t idx = 0; idx < raw_steps.size(); ++idx)
    {
        satellite::planner::PlanStep formal_step;
        formal_step.agent_id = raw_steps[idx].agent_id;
        formal_step.input = raw_steps[idx].input;
        formal_step.order = idx;
        formal_step.description = raw_steps[idx].description;
        for (AgentID dependency : raw_steps[idx].dependencies)
        {
            formal_step.dependencies.push_back(static_cast<std::size_t>(dependency));
        }
        formal_plan.steps.push_back(std::move(formal_step));
    }

    std::vector<std::size_t> order;
    std::string plan_error;
    if (!planner.validate(formal_plan, plan_error))
    {
        return OrchestrationResult{false, {}, "Error en plan: " + plan_error};
    }
    if (!planner.execution_order(formal_plan, order, plan_error))
    {
        return OrchestrationResult{false, {}, "Error en plan: " + plan_error};
    }

    std::unordered_map<std::size_t, std::size_t> old_to_new_idx;
    for (std::size_t new_idx = 0; new_idx < order.size(); ++new_idx)
    {
        old_to_new_idx[order[new_idx]] = new_idx;
    }

    std::vector<OrchestrationStep> ordered_plan;
    ordered_plan.reserve(order.size());
    for (std::size_t old_idx : order)
    {
        OrchestrationStep step = raw_steps[old_idx];
        for (AgentID& dependency : step.dependencies)
        {
            dependency = static_cast<AgentID>(old_to_new_idx.at(static_cast<std::size_t>(dependency)));
        }
        ordered_plan.push_back(std::move(step));
    }

    return run_plan(ordered_plan, project, budget);
}

std::vector<std::string> Orchestrator::detect_missing_capabilities(const std::string& goal,
                                                                   const AgentCatalog& catalog) const
{
    // Extraer keywords del goal (split no-alfanumérico, minúsculas, len>=3, stopwords básicas)
    std::vector<std::string> keywords;
    std::string word;
    for (char c : goal)
    {
        if (std::isalnum(static_cast<unsigned char>(c)))
        {
            word += std::tolower(static_cast<unsigned char>(c));
        }
        else if (!word.empty())
        {
            if (word.size() >= 3)
            {
                keywords.push_back(word);
            }
            word.clear();
        }
    }
    if (!word.empty() && word.size() >= 3)
    {
        keywords.push_back(word);
    }

    // Stopwords básicas
    static const std::unordered_set<std::string> stopwords = {
        "de", "la", "el", "del", "los", "las", "que", "para", "con", "por", "una", "un",
        "and", "the", "for", "with"
    };

    // Obtener todas las capabilities del catálogo
    std::vector<std::string> all_capabilities;
    auto catalog_json = catalog.to_json();
    if (catalog_json.is_array())
    {
        for (const auto& agent_json : catalog_json)
        {
            if (agent_json.contains("capabilities") && agent_json["capabilities"].is_array())
            {
                for (const auto& cap : agent_json["capabilities"])
                {
                    if (cap.is_string())
                    {
                        all_capabilities.push_back(cap.get<std::string>());
                    }
                }
            }
        }
    }

    // Para cada keyword, si NINGUNA capability del catálogo la contiene como substring → añadir
    std::vector<std::string> missing;
    for (const std::string& kw : keywords)
    {
        if (stopwords.count(kw))
        {
            continue;
        }

        bool found = false;
        for (const std::string& cap : all_capabilities)
        {
            if (cap.find(kw) != std::string::npos)
            {
                found = true;
                break;
            }
        }
        if (!found)
        {
            missing.push_back(kw);
        }
    }

    return missing;
}

AgentResult Orchestrator::execute_step(const OrchestrationStep& step,
                                       const ProjectContext& project,
                                       const TokenBudget& budget) const
{
    // 1. find_agent(step.agent_id) en registry_ → si no existe: AgentResult con UNKNOWN_AGENT
    const auto desc = registry_.find_agent(step.agent_id);
    if (!desc)
    {
        AgentResult result;
        result.agent_id = step.agent_id;
        result.status = satellite::core::agent::AgentStatus::UNKNOWN_AGENT;
        result.error = satellite::core::agent::AgentError{satellite::core::agent::AgentErrorCode::UNKNOWN_AGENT, "unknown agent"};
        result.duration_ms = 0.0;
        return result;
    }

    // 2. Task task{step.description, {}}; ContextSelection sel = optimizer_.optimize(task, *desc, project, budget);
    satellite::context::Task task;
    task.description = step.description;
    // keywords se completan desde description en el optimizador

    satellite::context::ContextSelection sel = optimizer_.optimize(task, *desc, project, budget);

    // 3. request.context = {{"selected_files", sel.selected_files}, {"selected_symbols", nombres de sel.selected_symbols},
    //    {"selected_dependencies", {from,target,kind} por cada dep}, {"estimated_tokens", sel.estimated_tokens}, {"relevance_score", sel.relevance_score}};
    nlohmann::json context_json;
    context_json["selected_files"] = sel.selected_files;

    nlohmann::json symbols_json = nlohmann::json::array();
    for (const auto& sym : sel.selected_symbols)
    {
        symbols_json.push_back(sym.name);
    }
    context_json["selected_symbols"] = symbols_json;

    nlohmann::json deps_json = nlohmann::json::array();
    for (const auto& dep : sel.selected_dependencies)
    {
        deps_json.push_back(nlohmann::json{{"from", dep.from_file}, {"target", dep.target}, {"kind", dep.kind}});
    }
    context_json["selected_dependencies"] = deps_json;

    context_json["estimated_tokens"] = sel.estimated_tokens;
    context_json["relevance_score"] = sel.relevance_score;

    // 4. request.input = step.input; request.agent_id = step.agent_id; request.token_budget = budget; request.execution_metadata.execution_id = make_execution_id();
    satellite::core::agent::AgentRequest request;
    request.agent_id = step.agent_id;
    request.input = step.input;
    request.context = context_json;
    request.token_budget = budget;
    request.execution_metadata.execution_id = satellite::core::protocol::make_execution_id();

    // 5. return dispatcher_.dispatch(request);
    AgentResult step_result = dispatcher_.dispatch(request);
    if (logger_ != nullptr)
    {
        satellite::observability::ExecutionRecord rec;
        rec.execution_id = request.execution_metadata.execution_id;
        rec.timestamp_ms = std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::system_clock::now().time_since_epoch()).count();
        rec.provider = request.execution_metadata.provider;
        rec.model = request.execution_metadata.model;
        rec.agent_id = request.agent_id;
        if (desc != nullptr)
        {
            rec.agent_version = desc->version;
        }
        rec.input = request.input;
        rec.context = request.context;
        rec.output = step_result.output;
        rec.duration_ms = step_result.duration_ms;
        rec.status = step_result.status;
        if (step_result.error)
        {
            rec.error_message = step_result.error->message;
        }
        satellite::context::OptimizationStats st = optimizer_.last_stats();
        rec.tokens_before = st.tokens_before;
        rec.tokens_after = st.tokens_after;
        rec.tokens_saved = st.tokens_saved;
        rec.compression_ratio = st.compression_ratio;
        rec.relevance_score = st.relevance_score;
        logger_->log(rec);
    }
    return step_result;
}

} // namespace satellite::orchestrator