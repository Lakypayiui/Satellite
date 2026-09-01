#pragma once

#include "llm/ILLMProvider.h"
#include "llm/DeepSeekProvider.h"
#include "llm/OpenAICompatibleProvider.h"
#include "llm/AnthropicProvider.h"
#include "config/Config.h"
#include <json.hpp>
#include <string>

namespace satellite::llm
{

struct ProviderFactory
{
    static std::unique_ptr<ILLMProvider> create(const FrameworkConfig& config);
};

} // namespace satellite::llm