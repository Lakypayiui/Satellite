#pragma once

// Registro de ejecución para observabilidad (Fase 19).
// Captura todos los datos de una invocación de agente para análisis posterior.

#include <filesystem>
#include <string>
#include <vector>
#include <cstdint>
#include <cstddef>

#include <json.hpp>

#include "core/agent/AgentID.h"
#include "core/agent/AgentStatus.h"
#include "core/protocol/Protocol.h"

namespace satellite::observability
{

struct ExecutionRecord
{
    std::string execution_id;
    std::int64_t timestamp_ms = 0;
    std::string provider;
    std::string model;
    satellite::core::agent::AgentID agent_id = satellite::core::agent::UNKNOWN_AGENT_ID;
    std::string agent_version;
    nlohmann::json input;
    nlohmann::json context;
    nlohmann::json output;
    double duration_ms = 0.0;
    satellite::core::agent::AgentStatus status = satellite::core::agent::AgentStatus::FAILED;
    std::string error_message;
    std::size_t tokens_before = 0;
    std::size_t tokens_after = 0;
    std::size_t tokens_saved = 0;
    double compression_ratio = 0.0;
    double relevance_score = 0.0;
};

void to_json(nlohmann::json& j, const ExecutionRecord& r);
void from_json(const nlohmann::json& j, ExecutionRecord& r);

class ExecutionLogger
{
public:
    explicit ExecutionLogger(std::filesystem::path log_dir);

    bool log(const ExecutionRecord& record) const;
    std::vector<ExecutionRecord> load_all() const;
    std::size_t count() const;
    std::filesystem::path dir() const;

private:
    std::filesystem::path log_dir_;
};

} // namespace satellite::observability