#include "factory/ProcessAgentProxy.h"

#include <atomic>
#include <array>
#include <cstdio>
#include <fstream>
#include <memory>
#include <sstream>
#include <system_error>
#include <vector>

#ifdef _WIN32
#include <windows.h>
#else
#include <unistd.h>
#endif

namespace satellite::factory
{
namespace
{

#ifndef _WIN32
std::string shell_quote(const std::string& value)
{
    std::string quoted = "'";
    for (char c : value)
    {
        if (c == '\'')
        {
            quoted += "'\\''";
        }
        else
        {
            quoted += c;
        }
    }
    quoted += '\'';
    return quoted;
}
#endif

std::filesystem::path find_host(const std::filesystem::path& framework_root)
{
#ifdef _WIN32
    const std::string host_name = "satellite_agent_host.exe";
#else
    const std::string host_name = "satellite_agent_host";
#endif

    std::vector<std::filesystem::path> candidates = {
        framework_root / "build" / host_name,
        framework_root / host_name,
        std::filesystem::current_path() / host_name,
        std::filesystem::current_path().parent_path() / host_name,
        std::filesystem::current_path().parent_path().parent_path() / host_name
    };

    for (const auto& candidate : candidates)
    {
        std::error_code error;
        if (std::filesystem::is_regular_file(candidate, error))
        {
            return std::filesystem::absolute(candidate, error);
        }
    }
    return {};
}

std::string request_json(const satellite::core::agent::AgentRequest& request)
{
    nlohmann::json json_request = {
        {"agent_id", request.agent_id},
        {"input", request.input},
        {"context", request.context},
        {"metadata", request.metadata},
        {"token_budget", request.token_budget},
        {"execution_metadata", request.execution_metadata}
    };
    return json_request.dump();
}

#ifdef _WIN32
std::wstring widen_path(const std::string& value)
{
    const int length = MultiByteToWideChar(CP_UTF8, 0, value.c_str(), -1, nullptr, 0);
    if (length <= 0)
    {
        return std::wstring(value.begin(), value.end());
    }

    std::wstring result(static_cast<std::size_t>(length), L'\0');
    MultiByteToWideChar(CP_UTF8, 0, value.c_str(), -1, result.data(), length);
    result.resize(static_cast<std::size_t>(length - 1));
    return result;
}
#endif

satellite::core::agent::AgentResult failed_result(
    satellite::core::agent::AgentID agent_id,
    satellite::core::agent::AgentErrorCode code,
    const std::string& message)
{
    satellite::core::agent::AgentResult result;
    result.agent_id = agent_id;
    result.status = satellite::core::agent::AgentStatus::FAILED;
    result.error = satellite::core::agent::AgentError{code, message};
    return result;
}

} // namespace

ProcessAgentProxy::ProcessAgentProxy(satellite::core::agent::AgentID agent_id,
                                     std::filesystem::path library_path,
                                     std::filesystem::path framework_root)
    : agent_id_(agent_id)
    , library_path_(std::move(library_path))
    , framework_root_(std::move(framework_root))
{
}

satellite::core::agent::AgentResult ProcessAgentProxy::execute(
    const satellite::core::agent::AgentRequest& request)
{
    const std::filesystem::path host_path = find_host(framework_root_);
    if (host_path.empty())
    {
        return failed_result(agent_id_, satellite::core::agent::AgentErrorCode::EXECUTION_FAILED,
                             "satellite_agent_host not found");
    }

    static std::atomic<unsigned long long> request_counter{0};
#ifdef _WIN32
    const unsigned long long process_id = GetCurrentProcessId();
#else
    const unsigned long long process_id = static_cast<unsigned long long>(getpid());
#endif
    const auto request_id = std::to_string(process_id) + "_" +
                            std::to_string(++request_counter);
    const std::filesystem::path request_path =
        std::filesystem::temp_directory_path() / ("satellite_request_" + request_id + ".json");
    const std::filesystem::path output_path =
        std::filesystem::temp_directory_path() / ("satellite_response_" + request_id + ".log");

    {
        std::ofstream request_file(request_path, std::ios::binary);
        if (!request_file)
        {
            return failed_result(agent_id_, satellite::core::agent::AgentErrorCode::EXECUTION_FAILED,
                                 "failed to write agent request");
        }
        request_file << request_json(request);
    }

    std::string output;
    int status = -1;
#ifdef _WIN32
    SECURITY_ATTRIBUTES security_attributes{};
    security_attributes.nLength = sizeof(security_attributes);
    security_attributes.bInheritHandle = TRUE;

    HANDLE request_handle = CreateFileW(
        widen_path(request_path.string()).c_str(), GENERIC_READ, FILE_SHARE_READ,
        &security_attributes, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
    HANDLE output_handle = INVALID_HANDLE_VALUE;
    if (request_handle != INVALID_HANDLE_VALUE &&
        (output_handle = CreateFileW(
            widen_path(output_path.string()).c_str(), GENERIC_WRITE,
            FILE_SHARE_READ | FILE_SHARE_WRITE, &security_attributes,
            CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr)) != INVALID_HANDLE_VALUE)
    {
        std::wstring command_line = L"\"" + widen_path(host_path.string()) + L"\" \"" +
                                    widen_path(library_path_.string()) + L"\"";
        std::vector<wchar_t> mutable_command(command_line.begin(), command_line.end());
        mutable_command.push_back(L'\0');

        STARTUPINFOW startup_info{};
        startup_info.cb = sizeof(startup_info);
        startup_info.dwFlags = STARTF_USESTDHANDLES;
        startup_info.hStdInput = request_handle;
        startup_info.hStdOutput = output_handle;
        startup_info.hStdError = output_handle;
        PROCESS_INFORMATION process_info{};

        const BOOL started = CreateProcessW(
            nullptr, mutable_command.data(), nullptr, nullptr, TRUE,
            CREATE_NO_WINDOW, nullptr, nullptr, &startup_info, &process_info);
        const DWORD create_error = started ? ERROR_SUCCESS : GetLastError();
        CloseHandle(output_handle);
        output_handle = INVALID_HANDLE_VALUE;

        if (started)
        {
            constexpr DWORD timeout_ms = 10000;
            if (WaitForSingleObject(process_info.hProcess, timeout_ms) == WAIT_TIMEOUT)
            {
                TerminateProcess(process_info.hProcess, 1);
                WaitForSingleObject(process_info.hProcess, INFINITE);
                status = -2;
            }
            else
            {
                DWORD exit_code = 1;
                GetExitCodeProcess(process_info.hProcess, &exit_code);
                status = static_cast<int>(exit_code);
            }
            CloseHandle(process_info.hThread);
            CloseHandle(process_info.hProcess);
        }
        else
        {
            status = -1;
            output = "CreateProcessW failed: " + std::to_string(create_error);
        }
    }
    else
    {
        status = -1;
    }
    if (output_handle != INVALID_HANDLE_VALUE) CloseHandle(output_handle);
    if (request_handle != INVALID_HANDLE_VALUE) CloseHandle(request_handle);
    {
        std::ifstream output_file(output_path, std::ios::binary);
        if (output_file)
        {
            output.assign((std::istreambuf_iterator<char>(output_file)),
                          std::istreambuf_iterator<char>());
        }
    }
#else
    const std::string command = shell_quote(host_path.string()) + " " +
                                shell_quote(library_path_.string()) + " < " +
                                shell_quote(request_path.string());
    std::array<char, 4096> buffer{};
    std::unique_ptr<FILE, decltype(&pclose)> pipe(popen(command.c_str(), "r"), pclose);
    if (pipe)
    {
        while (fgets(buffer.data(), static_cast<int>(buffer.size()), pipe.get()) != nullptr)
        {
            output += buffer.data();
        }
        status = pclose(pipe.release());
    }
#endif
    std::error_code remove_error;
    std::filesystem::remove(request_path, remove_error);
    std::filesystem::remove(output_path, remove_error);

    if (status == -1)
    {
        return failed_result(agent_id_, satellite::core::agent::AgentErrorCode::EXECUTION_FAILED,
                             output.empty() ? "failed to start agent process" : output);
    }
    if (status == -2)
    {
        return failed_result(agent_id_, satellite::core::agent::AgentErrorCode::TIMEOUT,
                             "agent process timed out");
    }
    if (status != 0)
    {
        return failed_result(agent_id_, satellite::core::agent::AgentErrorCode::EXECUTION_FAILED,
                             output.empty() ? "agent process failed" : "agent process failed: " + output);
    }

    const std::size_t line_end = output.find_last_of("\r\n");
    if (line_end != std::string::npos)
    {
        output.erase(line_end);
    }
    if (output.empty())
    {
        return failed_result(agent_id_, satellite::core::agent::AgentErrorCode::EXECUTION_FAILED,
                             "agent process returned no result");
    }

    try
    {
        const nlohmann::json json_result = nlohmann::json::parse(output);
        satellite::core::agent::AgentResult result;
        result.agent_id = json_result.value("agent_id", agent_id_);
        result.status = static_cast<satellite::core::agent::AgentStatus>(
            json_result.value("status", static_cast<int>(satellite::core::agent::AgentStatus::FAILED)));
        result.output = json_result.value("output", nlohmann::json{});
        result.duration_ms = json_result.value("duration_ms", 0.0);
        if (json_result.contains("error") && !json_result["error"].is_null())
        {
            const auto& error = json_result["error"];
            result.error = satellite::core::agent::AgentError{
                static_cast<satellite::core::agent::AgentErrorCode>(error.value("code", 0)),
                error.value("message", std::string{})};
        }
        if (json_result.contains("execution_metadata"))
        {
            result.execution_metadata = json_result["execution_metadata"].get<
                satellite::core::protocol::ExecutionMetadata>();
        }
        return result;
    }
    catch (const std::exception&)
    {
        return failed_result(agent_id_, satellite::core::agent::AgentErrorCode::EXECUTION_FAILED,
                             "invalid result from agent process");
    }
}

} // namespace satellite::factory
