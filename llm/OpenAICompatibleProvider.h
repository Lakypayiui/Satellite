#pragma once

#include "llm/ILLMProvider.h"
#include "llm/HttpClient.h"
#include <json.hpp>
#include <string>

namespace satellite::llm
{

class OpenAICompatibleProvider : public ILLMProvider
{
public:
    /**
     * @param name Proveedor identificador (ej. "openai").
     * @param base_url URL base de la API (por defecto https://api.openai.com/v1).
     * @param api_key Clave de API.
     * @param model Nombre del modelo (ej. "gpt-4o").
     */
    OpenAICompatibleProvider(std::string name, std::string base_url, std::string api_key, std::string model);

    std::string name() const override;

    LLMResponse complete(const LLMRequest& request) override;

private:
    HttpResponse http_post_json(const std::string& url, const std::string& body);

    std::string name_;
    std::string base_url_;
    std::string api_key_;
    std::string model_;
};

} // namespace satellite::llm