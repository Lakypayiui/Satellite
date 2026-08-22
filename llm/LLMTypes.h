#pragma once

#include <string>
#include <cstdint>

namespace satellite::llm
{

struct LLMRequest
{
    std::string system_prompt;
    std::string user_prompt;
    std::uint32_t max_tokens = 0;
    double temperature = 0.0;
};

struct LLMResponse
{
    bool ok = false;
    std::string text;
    std::string finish_reason;
    std::int64_t prompt_tokens = 0;
    std::int64_t completion_tokens = 0;
    std::int64_t total_tokens = 0;
    std::string error_message;
};

} // namespace satellite::llm