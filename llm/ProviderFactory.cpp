#include "llm/ProviderFactory.h"

namespace satellite::llm
{

std::unique_ptr<ILLMProvider> ProviderFactory::create(const satellite::config::FrameworkConfig& config)
{
    std::string provider = config.llm_provider;

    if (provider == "deepseek")
    {
        return std::make_unique<DeepSeekProvider>(config.llm_api_key_env, config.llm_base_url, config.llm_model);
    }
    else if (provider == "openai" || provider == "openai-compatible")
    {
        std::string api_key = config.llm_api_key.empty() ? "" : config.llm_api_key;
        std::string base_url = config.llm_base_url.empty() ? "https://api.openai.com/v1" : config.llm_base_url;
        return std::make_unique<OpenAICompatibleProvider>("openai", base_url, api_key, config.llm_model);
    }
    else if (provider == "anthropic" || provider == "claude")
    {
        return std::make_unique<AnthropicProvider>(config.llm_api_key, config.llm_model);
    }
    else if (provider == "local")
    {
        std::string base_url = "http://localhost:" + std::to_string(config.local_llm_port);
        std::string api_key = config.local_llm_api_key;
        return std::make_unique<LocalLLMProvider>(base_url, api_key, config.llm_model,
                                                    config.local_llm_context_size);
    }

    return nullptr;
}

} // namespace satellite::llm