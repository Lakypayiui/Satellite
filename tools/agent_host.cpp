#include "factory/AgentPlugin.h"
#include "core/agent/AgentSandbox.h"

#include <json.hpp>
#include <cstring>
#include <iostream>
#include <string>

#ifdef _WIN32
#include <windows.h>
#else
#include <dlfcn.h>
#endif

namespace
{

using CreateAgentFn = satellite::core::agent::IAgent* (*)();
using DestroyAgentFn = void (*)(satellite::core::agent::IAgent*);

nlohmann::json make_request(const nlohmann::json& json_request)
{
    return json_request;
}

} // namespace

int main(int argc, char* argv[])
{
    if (argc != 2)
    {
        std::cerr << "usage: satellite_agent_host <agent-library>\n";
        return 2;
    }

#ifdef _WIN32
    std::wstring library_path(argv[1], argv[1] + std::strlen(argv[1]));
    HMODULE library = LoadLibraryW(library_path.c_str());
    if (!library)
    {
        std::cerr << "failed to load agent library\n";
        return 3;
    }
    FARPROC create_symbol = GetProcAddress(library, "satellite_create_agent");
    FARPROC destroy_symbol = GetProcAddress(library, "satellite_destroy_agent");
    auto create_agent = reinterpret_cast<CreateAgentFn>(reinterpret_cast<void*>(create_symbol));
    auto destroy_agent = reinterpret_cast<DestroyAgentFn>(reinterpret_cast<void*>(destroy_symbol));
#else
    void* library = dlopen(argv[1], RTLD_NOW);
    if (!library)
    {
        std::cerr << "failed to load agent library: " << dlerror() << "\n";
        return 3;
    }
    auto create_agent = reinterpret_cast<CreateAgentFn>(dlsym(library, "satellite_create_agent"));
    auto destroy_agent = reinterpret_cast<DestroyAgentFn>(dlsym(library, "satellite_destroy_agent"));
#endif

    if (!create_agent || !destroy_agent)
    {
        std::cerr << "agent library has invalid ABI\n";
#ifdef _WIN32
        FreeLibrary(library);
#else
        dlclose(library);
#endif
        return 4;
    }

    satellite::core::agent::IAgent* agent = create_agent();
    if (!agent)
    {
        std::cerr << "agent factory returned null\n";
#ifdef _WIN32
        FreeLibrary(library);
#else
        dlclose(library);
#endif
        return 5;
    }

    std::string line;
    if (!std::getline(std::cin, line))
    {
        destroy_agent(agent);
#ifdef _WIN32
        FreeLibrary(library);
#else
        dlclose(library);
#endif
        return 6;
    }

    try
    {
        const nlohmann::json json_request = make_request(nlohmann::json::parse(line));
        satellite::core::agent::AgentRequest request;
        request.agent_id = json_request.value("agent_id", satellite::core::agent::UNKNOWN_AGENT_ID);
        request.input = json_request.value("input", nlohmann::json::object());
        request.context = json_request.value("context", nlohmann::json::object());
        request.metadata = json_request.value("metadata", nlohmann::json::object());
        if (json_request.contains("token_budget"))
        {
            request.token_budget = json_request["token_budget"].get<
                satellite::core::protocol::TokenBudget>();
        }
        if (json_request.contains("execution_metadata"))
        {
            request.execution_metadata = json_request["execution_metadata"].get<
                satellite::core::protocol::ExecutionMetadata>();
        }

        // Sandbox de efectos de sistema: el runtime Python pasa work_dir +
        // capabilities autorizadas. Si no viene, el agente queda en cómputo
        // puro (sin efectos).
        satellite::core::agent::AgentSandbox sandbox;
        if (json_request.contains("sandbox") && json_request["sandbox"].is_object())
        {
            const nlohmann::json& sb = json_request["sandbox"];
            sandbox.work_dir = sb.value("work_dir", std::filesystem::path("."));
            sandbox.allow_fs_write = sb.value("allow_fs_write", false);
            sandbox.allow_fs_read = sb.value("allow_fs_read", true);
            sandbox.allow_process = sb.value("allow_process", false);
            sandbox.allow_network = sb.value("allow_network", false);
            if (sb.contains("deny_write_prefixes") && sb["deny_write_prefixes"].is_array())
            {
                for (const auto& prefix : sb["deny_write_prefixes"])
                {
                    if (prefix.is_string())
                        sandbox.deny_write_prefixes.push_back(prefix.get<std::string>());
                }
            }
            request.sandbox = &sandbox;
        }

        const satellite::core::agent::AgentResult result = agent->execute(request);
        nlohmann::json json_result = {
            {"agent_id", result.agent_id},
            {"status", static_cast<int>(result.status)},
            {"output", result.output},
            {"duration_ms", result.duration_ms},
            {"execution_metadata", result.execution_metadata},
            {"error", nullptr}
        };
        if (result.error)
        {
            json_result["error"] = {
                {"code", static_cast<int>(result.error->code)},
                {"message", result.error->message}
            };
        }
        std::cout << json_result.dump() << '\n' << std::flush;
    }
    catch (const std::exception& error)
    {
        std::cerr << "agent execution failed: " << error.what() << '\n';
        destroy_agent(agent);
#ifdef _WIN32
        FreeLibrary(library);
#else
        dlclose(library);
#endif
        return 7;
    }

    destroy_agent(agent);
#ifdef _WIN32
    FreeLibrary(library);
#else
    dlclose(library);
#endif
    return 0;
}
