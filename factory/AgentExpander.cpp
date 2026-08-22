// AgentExpander.cpp: implementación de la expansión automática de catálogo (Fase 14).

#include "AgentExpander.h"

#include <algorithm>
#include <cctype>
#include <set>
#include <sstream>

namespace satellite::factory
{

AgentExpander::AgentExpander(AgentRegistry& registry, AgentCatalog& catalog, AgentFactory& factory, ILLMProvider& llm)
    : registry_(registry)
    , catalog_(catalog)
    , factory_(factory)
    , llm_(llm)
{
}

std::vector<std::string> AgentExpander::missing_capabilities(const std::string& goal) const
{
    std::vector<std::string> result;

    static const std::set<std::string> stopwords =
    {
        "de", "la", "el", "del", "los", "las", "que", "para", "con", "por", "una", "un",
        "and", "the", "for", "with", "into", "implement", "add", "create", "make", "fix", "change"
    , "calcular", "calcula", "calcule", "numero", "numeros", "valor", "usar", "usando", "hacer", "haga", "obtener", "devuelve", "nuevo", "nueva", "tarea", "funcion", "roto", "invalido", "sistema", "usuario", "palabra", "algoritmo", "uno", "dos", "tres", "cuatro", "cinco", "seis", "siete", "ocho", "nueve", "diez", "valores", "valor"};

    std::string lower_goal;
    lower_goal.reserve(goal.size());
    for (char c : goal)
    {
        lower_goal.push_back(std::tolower(static_cast<unsigned char>(c)));
    }

    std::vector<std::string> tokens;
    std::string current;
    for (char c : lower_goal)
    {
        if (std::isalnum(static_cast<unsigned char>(c)))
        {
            current.push_back(c);
        }
        else
        {
            if (current.size() >= 3 && stopwords.find(current) == stopwords.end())
            {
                tokens.push_back(current);
            }
            current.clear();
        }
    }
    if (current.size() >= 3 && stopwords.find(current) == stopwords.end())
    {
        tokens.push_back(current);
    }

    nlohmann::json catalog_json = catalog_.to_json();
    std::set<std::string> existing_capabilities;
    for (const auto& agent : catalog_json)
    {
        if (agent.contains("capabilities") && agent["capabilities"].is_array())
        {
            for (const auto& cap : agent["capabilities"])
            {
                if (cap.is_string())
                {
                    existing_capabilities.insert(cap.get<std::string>());
                }
            }
        }
    }

    for (const auto& token : tokens)
    {
        bool found = false;
        for (const auto& existing : existing_capabilities)
        {
            if (existing.find(token) != std::string::npos || (token.size() >= 3 && existing.find(token.substr(0, 3)) != std::string::npos))
            {
                found = true;
                break;
            }
        }
        if (!found)
        {
            result.push_back(token);
        }
    }

    return result;
}

bool AgentExpander::generate_spec(const std::string& goal, const std::string& capability, AgentSpec& spec, std::string& error) const
{
    std::string prompt = "Genera la especificación de un microagente C++ para Satellite. Objetivo: " + goal + ". Capacidad requerida: " + capability + ". Responde SOLO con JSON: {\"name\": \"...\", \"description\": \"...\", \"input_schema\": {...}, \"output_schema\": {...}, \"implementation_code\": \"...\", \"test_cases\": [{\"input\": {...}, \"expected\": {...}}]}. El implementation_code es una clase que implementa satellite::core::agent::IAgent (método execute(const AgentRequest&)) + la función extern \"C\" IAgent* satellite_create_agent(). El JSON del implementation_code debe tener los saltos de línea como \\n escapados.";

    satellite::llm::LLMRequest request;
    request.system_prompt = "";
    request.user_prompt = prompt;
    request.max_tokens = 2500;
    request.temperature = 0.0;

    satellite::llm::LLMResponse response = llm_.complete(request);
    if (!response.ok)
    {
        error = "LLM error: " + response.error_message;
        return false;
    }

    nlohmann::json json_spec = nlohmann::json::parse(response.text, nullptr, false);
    if (json_spec.is_discarded())
    {
        error = "invalid spec JSON";
        return false;
    }

    if (!json_spec.contains("name") || !json_spec["name"].is_string())
    {
        error = "missing field name";
        return false;
    }
    if (!json_spec.contains("input_schema") || !json_spec["input_schema"].is_object())
    {
        error = "missing field input_schema";
        return false;
    }
    if (!json_spec.contains("implementation_code") || !json_spec["implementation_code"].is_string())
    {
        error = "missing field implementation_code";
        return false;
    }
    if (!json_spec.contains("test_cases") || !json_spec["test_cases"].is_array())
    {
        error = "missing field test_cases";
        return false;
    }

    spec.name = json_spec["name"].get<std::string>();
    spec.description = json_spec.value("description", std::string());
    spec.version = json_spec.value("version", std::string("1.0.0"));
    spec.input_schema = json_spec["input_schema"];
    spec.output_schema = json_spec.value("output_schema", nlohmann::json::object());
    spec.context_requirements = json_spec.value("context_requirements", std::vector<std::string>());
    spec.implementation_code = json_spec["implementation_code"].get<std::string>();

    spec.test_cases.clear();
    for (const auto& tc : json_spec["test_cases"])
    {
        if (tc.contains("input") && tc.contains("expected"))
        {
            spec.test_cases.emplace_back(tc["input"], tc["expected"]);
        }
    }

    return true;
}

ExpansionResult AgentExpander::expand(const std::string& goal, std::string& error)
{
    ExpansionResult result;
    error.clear();

    // Palabras clave del goal (sin stopwords):
    std::vector<std::string> words;
    {
        static const std::vector<std::string> stopwords = {"de", "la", "el", "del", "los", "las", "que", "para", "con", "por", "una", "un", "and", "the", "for", "with", "into", "implement", "add", "create", "make", "fix", "change", "calcular", "calcula", "calcule", "numero", "numeros", "valor", "usar", "usando", "hacer", "haga", "obtener", "devuelve", "nuevo", "nueva", "tarea", "funcion", "roto", "invalido", "sistema", "usuario", "palabra", "algoritmo", "uno", "dos", "tres", "cuatro", "cinco", "seis", "siete", "ocho", "nueve", "diez", "valores", "valor"};
        std::string word;
        for (char ch : goal)
        {
            if (std::isalnum(static_cast<unsigned char>(ch)))
            {
                word.push_back(static_cast<char>(std::tolower(static_cast<unsigned char>(ch))));
            }
            else if (!word.empty())
            {
                if (word.size() >= 3 && std::find(stopwords.begin(), stopwords.end(), word) == stopwords.end())
                {
                    words.push_back(word);
                }
                word.clear();
            }
        }
        if (!word.empty() && word.size() >= 3 && std::find(stopwords.begin(), stopwords.end(), word) == stopwords.end())
        {
            words.push_back(word);
        }
    }

    // Capacidades existentes en el catalogo:
    std::set<std::string> existing_capabilities;
    for (const auto& agent : catalog_.to_json())
    {
        if (agent.contains("capabilities") && agent["capabilities"].is_array())
        {
            for (const auto& cap : agent["capabilities"])
            {
                if (cap.is_string())
                {
                    existing_capabilities.insert(cap.get<std::string>());
                }
            }
        }
    }

    // Proximo id libre:
    AgentID next_id = 6;
    for (const auto& desc : registry_.list_agents())
    {
        if (desc.id >= next_id)
        {
            next_id = desc.id + 1;
        }
    }

    for (const auto& word : words)
    {
        bool covered = false;
        for (const auto& existing : existing_capabilities)
        {
            if (existing.find(word) != std::string::npos || (word.size() >= 3 && existing.find(word.substr(0, 3)) != std::string::npos))
            {
                covered = true;
                break;
            }
        }
        if (covered)
        {
            result.skipped.push_back(word);
            continue;
        }

        AgentSpec spec;
        std::string spec_error;
        if (!generate_spec(goal, word, spec, spec_error))
        {
            result.failed.emplace_back(word, "spec: " + spec_error);
            result.ok = false;
            continue;
        }

        spec.capabilities = {word};
        if (spec.id == satellite::core::agent::UNKNOWN_AGENT_ID)
        {
            spec.id = next_id++;
        }

        FactoryResult fr = factory_.create_agent(spec);
        if (fr.ok)
        {
            result.created.push_back(spec.id);
            existing_capabilities.insert(word);
        }
        else
        {
            result.failed.emplace_back(word, fr.stage + ": " + fr.message);
            result.ok = false;
        }
    }

    return result;
}

} // namespace satellite::factory