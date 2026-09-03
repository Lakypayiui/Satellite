#pragma once

#include <string>

namespace satellite::llm
{

inline std::string extract_json_substring(const std::string& raw)
{
    const std::size_t object_start = raw.find('{');
    const std::size_t array_start = raw.find('[');
    std::size_t start = std::string::npos;
    char closing = '\0';

    if (object_start == std::string::npos && array_start == std::string::npos)
    {
        return {};
    }
    if (array_start == std::string::npos || (object_start != std::string::npos && object_start < array_start))
    {
        start = object_start;
        closing = '}';
    }
    else
    {
        start = array_start;
        closing = ']';
    }

    const std::size_t end = raw.rfind(closing);
    if (end == std::string::npos || end < start)
    {
        return {};
    }

    return raw.substr(start, end - start + 1);
}

} // namespace satellite::llm
