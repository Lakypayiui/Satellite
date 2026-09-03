#pragma once

#include "llm/ILLMProvider.h"
#include <json.hpp>
#include <string>
#include <cstdint>

namespace satellite::llm
{

class LocalLLMProvider : public ILLMProvider
{
public:
    LocalLLMProvider(std::string base_url, std::string api_key, std::string model,
                     std::uint64_t context_size = 131072);

    std::string name() const override;

    LLMResponse complete(const LLMRequest& request) override;

    std::uint64_t context_size() const;

private:
    std::string http_post_json(const std::string& url, const std::string& auth_header, const std::string& body);

    std::string build_payload(const LLMRequest& request);

    std::string base_url_;
    std::string api_key_;
    std::string model_;
    std::uint64_t context_size_;
};

} // namespace satellite::llm