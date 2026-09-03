#pragma once

#include "llm/ILLMProvider.h"
#include "llm/HttpClient.h"
#include <json.hpp>
#include <string>

namespace satellite::llm
{

class AnthropicProvider : public ILLMProvider
{
public:
    /**
     * @param api_key Clave de API de Anthropic.
     * @param model Nombre del modelo (ej. "claude-3-opus-20240229").
     */
    AnthropicProvider(std::string api_key, std::string model);

    std::string name() const override;

    LLMResponse complete(const LLMRequest& request) override;

private:
    HttpResponse http_post_json(const std::string& url, const std::string& body);

    std::string api_key_;
    std::string model_;
};

} // namespace satellite::llm