#include "llm/LocalLLMProvider.h"
#include <atomic>
#include <chrono>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <sstream>

namespace satellite::llm
{

LocalLLMProvider::LocalLLMProvider(std::string base_url, std::string api_key, std::string model,
                                     std::uint64_t context_size)
    : base_url_(std::move(base_url))
    , api_key_(std::move(api_key))
    , model_(std::move(model))
    , context_size_(context_size)
{
}

std::string LocalLLMProvider::name() const
{
    return "local";
}

std::uint64_t LocalLLMProvider::context_size() const
{
    return context_size_;
}

std::string LocalLLMProvider::build_payload(const LLMRequest& request)
{
    nlohmann::json payload;
    payload["model"] = model_;
    payload["messages"] = nlohmann::json::array();
    payload["messages"].push_back({{"role", "system"}, {"content", request.system_prompt}});
    payload["messages"].push_back({{"role", "user"}, {"content", request.user_prompt}});
    if (request.max_tokens > 0)
    {
        payload["max_tokens"] = request.max_tokens;
    }
    if (request.temperature > 0.0)
    {
        payload["temperature"] = request.temperature;
    }
    payload["stream"] = false;
    payload["options"]["num_ctx"] = static_cast<nlohmann::json::number_integer_t>(context_size_);
    return payload.dump();
}

LLMResponse LocalLLMProvider::complete(const LLMRequest& request)
{
    auto payload = build_payload(request);
    std::string body = payload;

    std::string auth_header = "Authorization: Bearer " + api_key_;
    std::string url = base_url_ + "/v1/chat/completions";

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
        if (response_json.contains("error"))
        {
            return LLMResponse{false, "", "", 0, 0, 0,
                response_json["error"].contains("message")
                    ? response_json["error"]["message"].get<std::string>()
                    : response_json["error"].dump()};
        }
        if (!response_json.contains("choices") || !response_json["choices"].is_array()
            || response_json["choices"].empty())
        {
            return LLMResponse{false, "", "", 0, 0, 0, "invalid response: missing choices"};
        }
        const auto& first_choice = response_json["choices"][0];
        if (!first_choice.contains("message") || !first_choice["message"].contains("content"))
        {
            return LLMResponse{false, "", "", 0, 0, 0, "invalid response: missing message.content"};
        }
        LLMResponse response;
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
                response.prompt_tokens = usage["prompt_tokens"].get<std::int64_t>();
            if (usage.contains("completion_tokens"))
                response.completion_tokens = usage["completion_tokens"].get<std::int64_t>();
            if (usage.contains("total_tokens"))
                response.total_tokens = usage["total_tokens"].get<std::int64_t>();
        }
        return response;
    }
    catch (const nlohmann::json::parse_error&)
    {
        return LLMResponse{false, "", "", 0, 0, 0, "invalid json response"};
    }
}

HttpResponse LocalLLMProvider::http_post_json(const std::string& url,
                                               const std::string& body)
{
    return post_json(url, {{"Authorization", "Bearer " + api_key_}}, body, 120L);
}

} // namespace satellite::llm