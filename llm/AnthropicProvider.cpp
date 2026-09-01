#include "llm/AnthropicProvider.h"
#include <atomic>
#include <chrono>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <sstream>
#include <json.hpp>
#include <fstream>
#include <sstream>
#include <filesystem>
#include <cstdlib>
#include <cstdio>

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
    std::string headers = "x-api-key: " + api_key_ + "\r\nanthropic-version: 2023-06-01\r\ncontent-type: application/json";

    std::string raw = http_post_json(url, headers, body);

    if (raw.empty())
    {
        return LLMResponse{false, "", "", 0, 0, 0, "http request failed (curl)"};
    }

    try
    {
        auto response_json = nlohmann::json::parse(raw);
        return parse_anthropic_response(response_json);
    }
    catch (const nlohmann::json::parse_error&)
    {
        return LLMResponse{false, "", "", 0, 0, 0, "invalid json response"};
    }
}

std::string AnthropicProvider::http_post_json(const std::string& url, const std::string& headers, const std::string& body)
{
    namespace fs = std::filesystem;

    static std::atomic<int> counter{0};
    int pid = 0;
#if defined(_WIN32)
    pid = _getpid();
#else
    pid = getpid();
#endif
    int cnt = counter.fetch_add(1, std::memory_order_relaxed);

    fs::path temp_dir = fs::temp_directory_path();
    fs::path payload_file = temp_dir / ("satellite_payload_" + std::to_string(pid) + "_" + std::to_string(cnt) + ".json");
    fs::path output_file = temp_dir / ("satellite_response_" + std::to_string(pid) + "_" + std::to_string(cnt) + ".txt");

    {
        std::ofstream ofs(payload_file, std::ios::binary);
        if (!ofs)
        {
            return "";
        }
        ofs << body;
    }

    std::string cmd;
#if defined(_WIN32)
    cmd = "curl -s --max-time 60 -X POST \"" + url + "\" -H \"" + headers + "\" -H \"Content-Type: application/json\" --data @\"" + payload_file.string() + "\" > \"" + output_file.string() + "\" 2>nul";
#else
    cmd = "curl -s --max-time 60 -X POST \"" + url + "\" -H \"" + headers + "\" -H \"Content-Type: application/json\" --data @\"" + payload_file.string() + "\" > \"" + output_file.string() + "\" 2>/dev/null";
#endif

    int result = std::system(cmd.c_str());
    (void)result;

    std::string response;
    {
        std::ifstream ifs(output_file, std::ios::binary);
        if (ifs)
        {
            std::ostringstream oss;
            oss << ifs.rdbuf();
            response = oss.str();
        }
    }

    std::error_code ec;
    fs::remove(payload_file, ec);
    fs::remove(output_file, ec);

    return response;
}

} // namespace satellite::llm