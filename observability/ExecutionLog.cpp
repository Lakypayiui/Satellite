// ExecutionLog.cpp
// Implementación del registro de ejecución para observabilidad (Fase 19).

#include "observability/ExecutionLog.h"

#include <fstream>
#include <chrono>
#include <iostream>

#include "core/protocol/Protocol.h"

namespace satellite::observability
{

ExecutionLogger::ExecutionLogger(std::filesystem::path log_dir)
    : log_dir_(std::move(log_dir))
{
    std::error_code ec;
    std::filesystem::create_directories(log_dir_, ec);
}

bool ExecutionLogger::log(const ExecutionRecord& record) const
{
    std::string exec_id = record.execution_id;
    if (exec_id.empty())
    {
        exec_id = satellite::core::protocol::make_execution_id();
    }

    std::filesystem::path file_path = log_dir_ / ("exec_" + exec_id + ".json");

    nlohmann::json j = record;

    std::ofstream ofs(file_path);
    if (!ofs)
    {
        return false;
    }

    ofs << j.dump(2);
    return true;
}

std::vector<ExecutionRecord> ExecutionLogger::load_all() const
{
    std::vector<ExecutionRecord> records;

    std::error_code ec;
    if (!std::filesystem::exists(log_dir_, ec))
    {
        return records;
    }

    for (const auto& entry : std::filesystem::directory_iterator(log_dir_, ec))
    {
        if (entry.is_regular_file() && entry.path().extension() == ".json")
        {
            std::ifstream ifs(entry.path());
            if (!ifs)
            {
                continue;
            }

            nlohmann::json j = nlohmann::json::parse(ifs, nullptr, false);
            if (j.is_discarded())
            {
                continue;
            }

            ExecutionRecord record;
            try
            {
                from_json(j, record);
                records.push_back(std::move(record));
            }
            catch (...)
            {
                // Skip invalid records
            }
        }
    }

    return records;
}

std::size_t ExecutionLogger::count() const
{
    std::size_t cnt = 0;
    std::error_code ec;
    if (!std::filesystem::exists(log_dir_, ec))
    {
        return 0;
    }

    for (const auto& entry : std::filesystem::directory_iterator(log_dir_, ec))
    {
        if (entry.is_regular_file() && entry.path().extension() == ".json")
        {
            ++cnt;
        }
    }
    return cnt;
}

std::filesystem::path ExecutionLogger::dir() const
{
    return log_dir_;
}

void to_json(nlohmann::json& j, const ExecutionRecord& r)
{
    j = nlohmann::json{
        {"execution_id", r.execution_id},
        {"timestamp_ms", r.timestamp_ms},
        {"provider", r.provider},
        {"model", r.model},
        {"agent_id", r.agent_id},
        {"agent_version", r.agent_version},
        {"input", r.input},
        {"context", r.context},
        {"output", r.output},
        {"duration_ms", r.duration_ms},
        {"status", satellite::core::agent::to_string(r.status)},
        {"tokens_before", r.tokens_before},
        {"tokens_after", r.tokens_after},
        {"tokens_saved", r.tokens_saved},
        {"compression_ratio", r.compression_ratio},
        {"relevance_score", r.relevance_score}
    };

    if (!r.error_message.empty())
    {
        j["error_message"] = r.error_message;
    }
}

void from_json(const nlohmann::json& j, ExecutionRecord& r)
{
    r.execution_id = j.value("execution_id", std::string{});
    r.timestamp_ms = j.value("timestamp_ms", 0L);
    r.provider = j.value("provider", std::string{});
    r.model = j.value("model", std::string{});
    r.agent_id = j.value("agent_id", satellite::core::agent::UNKNOWN_AGENT_ID);
    r.agent_version = j.value("agent_version", std::string{});
    r.input = j.value("input", nlohmann::json::object());
    r.context = j.value("context", nlohmann::json::object());
    r.output = j.value("output", nlohmann::json::object());
    r.duration_ms = j.value("duration_ms", 0.0);

    std::string status_str = j.value("status", std::string{"failed"});
    if (status_str == "idle") r.status = satellite::core::agent::AgentStatus::IDLE;
    else if (status_str == "running") r.status = satellite::core::agent::AgentStatus::RUNNING;
    else if (status_str == "success") r.status = satellite::core::agent::AgentStatus::SUCCESS;
    else if (status_str == "failed") r.status = satellite::core::agent::AgentStatus::FAILED;
    else if (status_str == "unknown_agent") r.status = satellite::core::agent::AgentStatus::UNKNOWN_AGENT;
    else if (status_str == "validation_error") r.status = satellite::core::agent::AgentStatus::VALIDATION_ERROR;
    else if (status_str == "disabled") r.status = satellite::core::agent::AgentStatus::DISABLED;
    else if (status_str == "timeout") r.status = satellite::core::agent::AgentStatus::TIMEOUT;
    else r.status = satellite::core::agent::AgentStatus::FAILED;

    r.error_message = j.value("error_message", std::string{});
    r.tokens_before = j.value("tokens_before", 0ULL);
    r.tokens_after = j.value("tokens_after", 0ULL);
    r.tokens_saved = j.value("tokens_saved", 0ULL);
    r.compression_ratio = j.value("compression_ratio", 0.0);
    r.relevance_score = j.value("relevance_score", 0.0);
}

} // namespace satellite::observability