#pragma once

#include <string>
#include <string_view>

namespace satellite::factory
{

enum class AgentExecutionBackend
{
    NativeProcess,
    Wasm
};

inline AgentExecutionBackend agent_execution_backend_from_string(std::string_view value)
{
    if (value == "wasm")
    {
        return AgentExecutionBackend::Wasm;
    }
    return AgentExecutionBackend::NativeProcess;
}

inline const char* agent_execution_backend_to_string(AgentExecutionBackend backend)
{
    return backend == AgentExecutionBackend::Wasm ? "wasm" : "native_process";
}

} // namespace satellite::factory
