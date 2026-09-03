#include "llm/AnthropicProvider.h"
#include <json.hpp>

namespace satellite::llm {

AnthropicProvider::AnthropicProvider(std::string api_key, std::string model)
    : api_key_(std::move(api_key))
    , model_(std::move(model))
{
}

std::string AnthropicProvider::name() const
{
    return "anthropic";
}

static nlohmann::json build_anthropic_payload(const LLMRequest& request, const std::string& model)
{
    nlohmann::json payload;
    payload["model"] = model;
    payload["max_tokens"] = request.max_tokens;
    payload["system"] = request.system_prompt;

    nlohmann::json messages = nlohmann::json::array();
    messages.push_back({{"role", "user"}, {"content", request.user_prompt}});
    payload["messages"] = messages;

    return payload;
}

static LLMResponse parse_anthropic_response(const nlohmann::json& response_json)
{
    LLMResponse response;

    if (response_json.contains("error"))
    {
        response.ok = false;
        if (response_json["error"].contains("message"))
        {
            response.error_message = response_json["error"]["message"].get<std::string>();
        }
        else
        {
            response.error_message = response_json["error"].dump();
        }
        return response;
    }

    if (!response_json.contains("content") || !response_json["content"].is_array() || response_json["content"].empty())
    {
        response.ok = false;
        response.error_message = "invalid response format: missing content";
        return response;
    }

    const auto& first_content = response_json["content"][0];
    if (!first_content.contains("text"))
    {
        response.ok = false;
        response.error_message = "invalid response format: missing text";
        return response;
    }

    response.ok = true;
    response.text = first_content["text"].get<std::string>();

    if (response_json.contains("usage"))
    {
        const auto& usage = response_json["usage"];
        if (usage.contains("input_tokens"))
        {
            response.prompt_tokens = usage["input_tokens"].get<std::int64_t>();
        }
        if (usage.contains("output_tokens"))
        {
            response.completion_tokens = usage["output_tokens"].get<std::int64_t>();
        }
    }

    if (response_json.contains("stop_reason"))
    {
        response.finish_reason = response_json["stop_reason"].get<std::string>();
    }

    return response;
}

LLMResponse AnthropicProvider::complete(const LLMRequest& request)
{
    // Sin API key -> error inmediato sin llamada de red
    if (api_key_.empty())
    {
        return LLMResponse{false, "", "", 0, 0, 0, "Autenticación requerida: falta API key de Anthropic"};
    }

    auto payload = build_anthropic_payload(request, model_);
    std::string body = payload.dump();

    std::string url = "https://api.anthropic.com/v1/messages";
    HttpResponse http_response = http_post_json(url, body);

    if (!http_response.transport_ok)
    {
        return LLMResponse{false, "", "", 0, 0, 0, "http request failed: " + http_response.error_message};
    }
    if (http_response.status_code < 200 || http_response.status_code >= 300)
    {
        return LLMResponse{false, "", "", 0, 0, 0, "http request returned status " + std::to_string(http_response.status_code)};
    }

    try
    {
        auto response_json = nlohmann::json::parse(http_response.body);
        return parse_anthropic_response(response_json);
    }
    catch (const nlohmann::json::parse_error&)
    {
        return LLMResponse{false, "", "", 0, 0, 0, "invalid json response"};
    }
}

HttpResponse AnthropicProvider::http_post_json(const std::string& url, const std::string& body)
{
    return post_json(url,
        {{"x-api-key", api_key_}, {"anthropic-version", "2023-06-01"}}, body, 60L);
}

} // namespace satellite::llm