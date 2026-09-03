#pragma once

#include <string>
#include <utility>
#include <vector>

namespace satellite::llm
{

struct HttpResponse
{
    bool transport_ok = false;
    long status_code = 0;
    std::string body;
    std::string error_message;
};

HttpResponse post_json(const std::string& url,
                       const std::vector<std::pair<std::string, std::string>>& headers,
                       const std::string& body,
                       long timeout_seconds);

} // namespace satellite::llm
