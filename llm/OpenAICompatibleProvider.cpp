#include "llm/OpenAICompatibleProvider.h"
#include <json.hpp>

namespace satellite::llm
{

OpenAICompatibleProvider::OpenAICompatibleProvider(std::string name, std::string base_url, std::string api_key, std::string model)
    : name_(std::move(name))
    , base_url_(std::move(base_url))
    , api_key_(std::move(api_key))
    , model_(std::move(model))
{
}

std::string OpenAICompatibleProvider::name() const
{
    return name_;
}

static nlohmann::json build_openai_compatible_payload(const LLMRequest& request, const std::string& model)
{
    nlohmann::json payload;
    payload["model"] = model;
    nlohmann::json messages = nlohmann::json::array();

    if (!request.system_prompt.empty())
    {
        messages.push_back({{"role", "system"}, {"content", request.system_prompt}});
    }
    messages.push_back({{"role", "user"}, {"content", request.user_prompt}});

    payload["messages"] = messages;
    if (request.max_tokens > 0)
    {
        payload["max_tokens"] = request.max_tokens;
    }
    if (request.temperature > 0.0)
    {
        payload["temperature"] = request.temperature;
    }
    payload["stream"] = false;
    return payload;
}

static LLMResponse parse_openai_compatible_response(const nlohmann::json& response_json)
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

    if (!response_json.contains("choices") || !response_json["choices"].is_array() || response_json["choices"].empty())
    {
        response.ok = false;
        response.error_message = "invalid response format: missing choices";
        return response;
    }

    const auto& first_choice = response_json["choices"][0];
    if (!first_choice.contains("message") || !first_choice["message"].contains("content"))
    {
        response.ok = false;
        response.error_message = "invalid response format: missing message.content";
        return response;
    }

    response.ok = true;
    response.text = first_choice["message"]["content"].get<std::string>();

    if (first_choice.contains("finish_reason"))
    {
        response.finish_reason = first_choice["finish_reason"].get<std::string>();
    }

    if (response_json.contains("usage"))
    {
        const auto& usage = response_json["usage"];
        if (usage.contains("prompt_tokens"))
        {
            response.prompt_tokens = usage["prompt_tokens"].get<std::int64_t>();
        }
        if (usage.contains("completion_tokens"))
        {
            response.completion_tokens = usage["completion_tokens"].get<std::int64_t>();
        }
        if (usage.contains("total_tokens"))
        {
            response.total_tokens = usage["total_tokens"].get<std::int64_t>();
        }
    }

    return response;
}

LLMResponse OpenAICompatibleProvider::complete(const LLMRequest& request)
{
    auto payload = build_openai_compatible_payload(request, model_);
    std::string body = payload.dump();

    std::string url = base_url_ + "/chat/completions";

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
        return parse_openai_compatible_response(response_json);
    }
    catch (const nlohmann::json::parse_error&)
    {
        return LLMResponse{false, "", "", 0, 0, 0, "invalid json response"};
    }
}

HttpResponse OpenAICompatibleProvider::http_post_json(const std::string& url, const std::string& body)
{
    return post_json(url, {{"Authorization", "Bearer " + api_key_}}, body, 60L);
}

} // namespace satellite::llm