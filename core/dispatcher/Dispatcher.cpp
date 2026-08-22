#include "core/dispatcher/Dispatcher.h"
#include "core/agent/AgentResult.h"
#include "core/agent/AgentError.h"
#include "core/agent/IAgent.h"
#include "core/registry/AgentRegistry.h"
#include "core/validation/InputValidator.h"
#include "core/protocol/Protocol.h"
#include "security/SecurityPolicy.h"
#include <chrono>
#include <exception>
#include <string>
#include <json.hpp>

namespace satellite::core::dispatcher
{

Dispatcher::Dispatcher(satellite::core::registry::AgentRegistry& registry, const satellite::security::SecurityPolicy* security)
    : registry_(registry)
    , security_(security)
{
}

satellite::core::agent::AgentResult Dispatcher::dispatch(const satellite::core::agent::AgentRequest& request)
{
    using satellite::core::agent::AgentResult;
    using satellite::core::agent::AgentStatus;
    using satellite::core::agent::AgentError;
    using satellite::core::agent::AgentErrorCode;
    using satellite::core::agent::UNKNOWN_AGENT_ID;

    const satellite::core::agent::AgentDescriptor* desc = registry_.find_agent(request.agent_id);
    if (desc == nullptr)
    {
        return AgentResult
        {
            request.agent_id,
            AgentStatus::UNKNOWN_AGENT,
            nlohmann::json(),
            AgentError{AgentErrorCode::UNKNOWN_AGENT, "unknown agent id: " + std::to_string(request.agent_id)},
            0.0,
        {}
        };
    }

    if (!registry_.is_enabled(request.agent_id))
    {
        return AgentResult
        {
            request.agent_id,
            AgentStatus::DISABLED,
            nlohmann::json(),
            AgentError{AgentErrorCode::DISABLED_AGENT, "agent disabled: " + std::to_string(request.agent_id)},
            0.0,
        {}
        };
    }

    if (security_ != nullptr)
    {
        std::string denied;
        if (!security_->validate_agent(*desc, denied))
        {
            return AgentResult
            {
                request.agent_id,
                AgentStatus::FAILED,
                nlohmann::json(),
                AgentError{AgentErrorCode::SECURITY_DENIED, "capability denied: " + denied},
                0.0,
            {}
            };
        }
    }

    if (desc->agent == nullptr)
    {
        return AgentResult
        {
            request.agent_id,
            AgentStatus::FAILED,
            nlohmann::json(),
            AgentError{AgentErrorCode::INTERNAL_ERROR, "agent has no implementation"},
            0.0,
        {}
        };
    }

    std::string err;
    if (!satellite::core::validation::InputValidator::validate(request.input, desc->input_schema, err))
    {
        return AgentResult
        {
            request.agent_id,
            AgentStatus::VALIDATION_ERROR,
            nlohmann::json(),
            AgentError{AgentErrorCode::VALIDATION_ERROR, err},
            0.0,
        {}
        };
    }

    auto t0 = std::chrono::steady_clock::now();
    AgentResult result;
    try
    {
        result = desc->agent->execute(request);
    }
    catch (const std::exception& e)
    {
        double duration_ms = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - t0).count();
        result = AgentResult
        {
            request.agent_id,
            AgentStatus::FAILED,
            nlohmann::json(),
            AgentError{AgentErrorCode::EXECUTION_FAILED, e.what()},
            duration_ms,
            {}
        };
    }

    double duration_ms = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - t0).count();
    if (result.agent_id == UNKNOWN_AGENT_ID)
    {
        result.agent_id = request.agent_id;
    }
    if (result.duration_ms <= 0.0)
    {
        result.duration_ms = duration_ms;
    }

    // Normalización de execution_metadata (Fase 5)
    if (result.execution_metadata.execution_id.empty())
    {
        if (!request.execution_metadata.execution_id.empty())
        {
            result.execution_metadata.execution_id = request.execution_metadata.execution_id;
        }
        else
        {
            result.execution_metadata.execution_id = satellite::core::protocol::make_execution_id();
        }
    }
    if (result.execution_metadata.provider.empty())
    {
        result.execution_metadata.provider = request.execution_metadata.provider;
    }
    if (result.execution_metadata.model.empty())
    {
        result.execution_metadata.model = request.execution_metadata.model;
    }

    return result;
}

} // namespace satellite::core::dispatcher