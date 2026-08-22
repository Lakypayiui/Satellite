#include "llm/DeepSeekProvider.h"
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

namespace satellite::llm
{

nlohmann::json build_deepseek_payload(const LLMRequest& request, const std::string& model)
{
    nlohmann::json payload;
    payload["model"] = model;
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
    return payload;
}

LLMResponse parse_deepseek_response(const nlohmann::json& response_json)
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

DeepSeekProvider::DeepSeekProvider(std::string api_key, std::string base_url, std::string model)
    : api_key_(std::move(api_key))
    , base_url_(std::move(base_url))
    , model_(std::move(model))
{
}

std::string DeepSeekProvider::name() const
{
    return "deepseek";
}

LLMResponse DeepSeekProvider::complete(const LLMRequest& request)
{
    auto payload = build_deepseek_payload(request, model_);
    std::string body = payload.dump();

    std::string auth_header = "Authorization: Bearer " + api_key_;
    std::string url = base_url_ + "/chat/completions";

    std::string raw = http_post_json(url, auth_header, body);

    if (raw.empty())
    {
        return LLMResponse{false, "", "", 0, 0, 0, "http request failed (curl)"};
    }

    try
    {
        auto response_json = nlohmann::json::parse(raw);
        return parse_deepseek_response(response_json);
    }
    catch (const nlohmann::json::parse_error&)
    {
        return LLMResponse{false, "", "", 0, 0, 0, "invalid json response"};
    }
}

std::string DeepSeekProvider::http_post_json(const std::string& url, const std::string& auth_header, const std::string& body)
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
    cmd = "curl -s --max-time 60 -X POST \"" + url + "\" -H \"Content-Type: application/json\" -H \"" + auth_header + "\" --data @\"" + payload_file.string() + "\" > \"" + output_file.string() + "\" 2>nul";
#else
    cmd = "curl -s --max-time 60 -X POST \"" + url + "\" -H \"Content-Type: application/json\" -H \"" + auth_header + "\" --data @\"" + payload_file.string() + "\" > \"" + output_file.string() + "\" 2>/dev/null";
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