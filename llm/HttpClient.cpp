#include "llm/HttpClient.h"

#include <curl/curl.h>

namespace satellite::llm
{
namespace
{

class CurlGlobal
{
public:
    CurlGlobal()
    {
        result_ = curl_global_init(CURL_GLOBAL_DEFAULT);
    }

    ~CurlGlobal()
    {
        if (result_ == CURLE_OK)
        {
            curl_global_cleanup();
        }
    }

    CURLcode result() const
    {
        return result_;
    }

private:
    CURLcode result_ = CURLE_FAILED_INIT;
};

CurlGlobal& curl_global_state()
{
    static CurlGlobal state;
    return state;
}

size_t write_response(char* data, size_t size, size_t count, void* userdata)
{
    auto* response = static_cast<std::string*>(userdata);
    response->append(data, size * count);
    return size * count;
}

bool contains_line_break(const std::string& value)
{
    return value.find('\r') != std::string::npos || value.find('\n') != std::string::npos;
}

} // namespace

HttpResponse post_json(const std::string& url,
                       const std::vector<std::pair<std::string, std::string>>& headers,
                       const std::string& body,
                       long timeout_seconds)
{
    HttpResponse response;
    if (curl_global_state().result() != CURLE_OK)
    {
        response.error_message = "curl global initialization failed";
        return response;
    }

    CURL* handle = curl_easy_init();
    if (handle == nullptr)
    {
        response.error_message = "curl easy initialization failed";
        return response;
    }

    curl_slist* header_list = nullptr;
    auto cleanup = [&]()
    {
        curl_slist_free_all(header_list);
        curl_easy_cleanup(handle);
    };

    header_list = curl_slist_append(header_list, "Content-Type: application/json");
    if (header_list == nullptr)
    {
        response.error_message = "curl header allocation failed";
        cleanup();
        return response;
    }

    for (const auto& [name, value] : headers)
    {
        if (name.empty() || contains_line_break(name) || contains_line_break(value))
        {
            response.error_message = "invalid HTTP header";
            cleanup();
            return response;
        }

        const std::string header = name + ": " + value;
        curl_slist* updated_headers = curl_slist_append(header_list, header.c_str());
        if (updated_headers == nullptr)
        {
            response.error_message = "curl header allocation failed";
            cleanup();
            return response;
        }
        header_list = updated_headers;
    }

    curl_easy_setopt(handle, CURLOPT_URL, url.c_str());
    curl_easy_setopt(handle, CURLOPT_HTTPHEADER, header_list);
    curl_easy_setopt(handle, CURLOPT_POST, 1L);
    curl_easy_setopt(handle, CURLOPT_POSTFIELDS, body.data());
    curl_easy_setopt(handle, CURLOPT_POSTFIELDSIZE_LARGE, static_cast<curl_off_t>(body.size()));
    curl_easy_setopt(handle, CURLOPT_TIMEOUT, timeout_seconds);
    curl_easy_setopt(handle, CURLOPT_CONNECTTIMEOUT, 10L);
    curl_easy_setopt(handle, CURLOPT_NOSIGNAL, 1L);
    curl_easy_setopt(handle, CURLOPT_WRITEFUNCTION, write_response);
    curl_easy_setopt(handle, CURLOPT_WRITEDATA, &response.body);

    const CURLcode result = curl_easy_perform(handle);
    if (result != CURLE_OK)
    {
        response.error_message = curl_easy_strerror(result);
        cleanup();
        return response;
    }

    curl_easy_getinfo(handle, CURLINFO_RESPONSE_CODE, &response.status_code);
    response.transport_ok = true;
    cleanup();
    return response;
}

} // namespace satellite::llm
