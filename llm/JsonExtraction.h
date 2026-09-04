#pragma once

#include <cctype>
#include <string>

namespace satellite::llm
{

inline std::string extract_json_substring(const std::string& raw)
{
    std::size_t code_start = raw.find("```json");
    if (code_start == std::string::npos)
    {
        code_start = raw.find("```");
    }
    if (code_start != std::string::npos)
    {
        const std::size_t body_start = raw.find('\n', code_start);
        if (body_start != std::string::npos)
        {
            const std::size_t code_end = raw.find("```", body_start + 1);
            if (code_end != std::string::npos)
            {
                std::size_t body_end = code_end;
                while (body_end > body_start + 1 &&
                       std::isspace(static_cast<unsigned char>(raw[body_end - 1])))
                {
                    --body_end;
                }
                return raw.substr(body_start + 1, body_end - body_start - 1);
            }
        }
    }

    const auto find_balanced = [&raw](char opening, char closing) -> std::string
    {
        const std::size_t start = raw.find(opening);
        if (start == std::string::npos)
        {
            return {};
        }

        int depth = 0;
        bool in_string = false;
        bool escaped = false;
        for (std::size_t i = start; i < raw.size(); ++i)
        {
            const char current = raw[i];
            if (escaped)
            {
                escaped = false;
                continue;
            }
            if (in_string && current == '\\')
            {
                escaped = true;
                continue;
            }
            if (current == '"')
            {
                in_string = !in_string;
                continue;
            }
            if (in_string)
            {
                continue;
            }
            if (current == opening)
            {
                ++depth;
            }
            else if (current == closing)
            {
                --depth;
                if (depth == 0)
                    return raw.substr(start, i - start + 1);
            }
        }
        return {};
    };

    std::string result = find_balanced('{', '}');
    if (!result.empty())
    {
        return result;
    }

    return find_balanced('[', ']');
}

} // namespace satellite::llm
