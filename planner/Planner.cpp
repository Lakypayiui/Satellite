#include "Planner.h"
#include "llm/JsonExtraction.h"

#include <algorithm>
#include <queue>
#include <set>
#include <sstream>

namespace satellite::planner
{

bool Planner::validate(const Plan& plan, std::string& error) const
{
    const std::size_t n = plan.steps.size();

    // Plan vacío (0 steps) → VÁLIDO (no-op)
    if (n == 0)
    {
        return true;
    }

    // Validar cada step
    for (std::size_t i = 0; i < n; ++i)
    {
        const PlanStep& step = plan.steps[i];

        // agent_id != UNKNOWN_AGENT_ID (0)
        if (step.agent_id == UNKNOWN_AGENT_ID)
        {
            error = "step " + std::to_string(i) + ": agent_id must not be UNKNOWN_AGENT_ID (0)";
            return false;
        }

        // dependencies: cada índice < steps.size() y != índice propio; SIN duplicados
        std::set<std::size_t> seen_deps;
        for (std::size_t dep : step.dependencies)
        {
            if (dep >= n)
            {
                error = "step " + std::to_string(i) + ": dependency index " + std::to_string(dep) + " out of bounds (steps=" + std::to_string(n) + ")";
                return false;
            }
            if (dep == i)
            {
                error = "step " + std::to_string(i) + ": dependency cannot be self";
                return false;
            }
            if (!seen_deps.insert(dep).second)
            {
                error = "step " + std::to_string(i) + ": duplicate dependency " + std::to_string(dep);
                return false;
            }
        }
    }

    // order: los valores deben ser 0..n-1 exactamente una vez (permutación)
    std::vector<bool> order_seen(n, false);
    for (std::size_t i = 0; i < n; ++i)
    {
        std::size_t ord = plan.steps[i].order;
        if (ord >= n)
        {
            error = "step " + std::to_string(i) + ": order " + std::to_string(ord) + " out of bounds (steps=" + std::to_string(n) + ")";
            return false;
        }
        if (order_seen[ord])
        {
            error = "invalid order: duplicate order value " + std::to_string(ord);
            return false;
        }
        order_seen[ord] = true;
    }
    for (std::size_t i = 0; i < n; ++i)
    {
        if (!order_seen[i])
        {
            error = "invalid order: missing order value " + std::to_string(i);
            return false;
        }
    }

    // Detectar ciclos vía execution_order
    std::vector<std::size_t> exec_order;
    if (!execution_order(plan, exec_order, error))
    {
        return false;
    }

    return true;
}

bool Planner::from_json(const nlohmann::json& j, Plan& plan, std::string& error) const
{
    if (!j.is_object())
    {
        error = "plan JSON must be an object";
        return false;
    }

    // goal: opcional, default ""
    if (j.contains("goal") && j["goal"].is_string())
    {
        plan.goal = j["goal"].get<std::string>();
    }
    else
    {
        plan.goal = "";
    }

    // steps: obligatorio, array
    if (!j.contains("steps") || !j["steps"].is_array())
    {
        error = "missing steps";
        return false;
    }

    const nlohmann::json& steps_json = j["steps"];
    plan.steps.clear();
    plan.steps.reserve(steps_json.size());

    for (std::size_t i = 0; i < steps_json.size(); ++i)
    {
        const nlohmann::json& step_json = steps_json[i];
        if (!step_json.is_object())
        {
            error = "step " + std::to_string(i) + " must be an object";
            return false;
        }

        PlanStep step;

        // agent_id: obligatorio, entero
        if (!step_json.contains("agent_id") || !step_json["agent_id"].is_number_integer())
        {
            error = "step " + std::to_string(i) + ": missing or invalid agent_id";
            return false;
        }
        step.agent_id = static_cast<AgentID>(step_json["agent_id"].get<std::uint32_t>());

        // input: opcional, objeto
        if (step_json.contains("input"))
        {
            if (!step_json["input"].is_object())
            {
                error = "step " + std::to_string(i) + ": input must be an object";
                return false;
            }
            step.input = step_json["input"];
        }
        else
        {
            step.input = nlohmann::json::object();
        }

        // dependencies: opcional, array de enteros
        if (step_json.contains("dependencies") && step_json["dependencies"].is_array())
        {
            for (const auto& dep : step_json["dependencies"])
            {
                if (!dep.is_number_integer())
                {
                    error = "step " + std::to_string(i) + ": dependency must be integer";
                    return false;
                }
                step.dependencies.push_back(static_cast<std::size_t>(dep.get<std::uint32_t>()));
            }
        }

        // conditions: opcional, array de strings
        if (step_json.contains("conditions") && step_json["conditions"].is_array())
        {
            for (const auto& cond : step_json["conditions"])
            {
                if (!cond.is_string())
                {
                    error = "step " + std::to_string(i) + ": condition must be string";
                    return false;
                }
                step.conditions.push_back(cond.get<std::string>());
            }
        }

        // order: opcional, entero, default posición en el array
        if (step_json.contains("order") && step_json["order"].is_number_integer())
        {
            step.order = static_cast<std::size_t>(step_json["order"].get<std::uint32_t>());
        }
        else
        {
            step.order = i;
        }

        // description: opcional
        if (step_json.contains("description") && step_json["description"].is_string())
        {
            step.description = step_json["description"].get<std::string>();
        }

        plan.steps.push_back(std::move(step));
    }

    // Validar el plan resultante
    return validate(plan, error);
}

bool Planner::execution_order(const Plan& plan, std::vector<std::size_t>& order, std::string& error) const
{
    const std::size_t n = plan.steps.size();
    order.clear();

    if (n == 0)
    {
        return true;
    }

    // Calcular indegree basado en dependencies
    std::vector<std::size_t> indegree(n, 0);
    std::vector<std::vector<std::size_t>> adj(n);

    for (std::size_t i = 0; i < n; ++i)
    {
        for (std::size_t dep : plan.steps[i].dependencies)
        {
            adj[dep].push_back(i);
            indegree[i]++;
        }
    }

    // Cola de pasos sin dependencias pendientes
    std::queue<std::size_t> q;
    for (std::size_t i = 0; i < n; ++i)
    {
        if (indegree[i] == 0)
        {
            q.push(i);
        }
    }

    // Kahn's algorithm
    while (!q.empty())
    {
        std::size_t u = q.front();
        q.pop();
        order.push_back(u);

        for (std::size_t v : adj[u])
        {
            indegree[v]--;
            if (indegree[v] == 0)
            {
                q.push(v);
            }
        }
    }

    // Si no se emitieron todos → ciclo
    if (order.size() != n)
    {
        error = "cycle detected in plan dependencies";
        return false;
    }

    return true;
}

bool Planner::plan_goal(const std::string& goal, const AgentCatalog& catalog, ILLMProvider& llm, Plan& plan, std::string& error) const
{
    std::ostringstream prompt_stream;
    prompt_stream << "Eres el planificador de Satellite. Catálogo de agentes:\n"
                  << catalog.to_prompt()
                  << "\nObjetivo: "
                  << goal
                  << "\nResponde SOLO con JSON: {\"goal\": \"...\", \"steps\": [{\"agent_id\": N, \"input\": {...}, \"dependencies\": [índices], \"description\": \"...\"}]}"
                  << ". Usa solo agentes del catálogo.";

    std::string prompt = prompt_stream.str();

    satellite::llm::LLMRequest request;
    request.system_prompt = ""; // El prompt completo va en user_prompt
    request.user_prompt = prompt;
    request.max_tokens = 1500;
    request.temperature = 0.0;

    satellite::llm::LLMResponse response = llm.complete(request);

    if (!response.ok)
    {
        error = "LLM error: " + response.error_message;
        return false;
    }

    const std::string json_text = satellite::llm::extract_json_substring(response.text);
    nlohmann::json j = nlohmann::json::parse(json_text, nullptr, false);
    if (j.is_discarded())
    {
        error = "invalid plan JSON";
        return false;
    }

    // from_json + validate
    if (!from_json(j, plan, error))
    {
        return false;
    }

    return true;
}

} // namespace satellite::planner