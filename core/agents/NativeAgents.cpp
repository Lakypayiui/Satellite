#include "core/agents/NativeAgents.h"

using namespace satellite::core::agent;

namespace satellite::core::agents
{

AgentResult SumAgent::execute(const AgentRequest& request)
{
    const auto& input = request.input;

    if (!input.contains("a") || !input.contains("b"))
    {
        return AgentResult
        {
            request.agent_id,
            AgentStatus::FAILED,
            nlohmann::json(),
            AgentError{AgentErrorCode::INVALID_REQUEST, "missing required input"},
            0.0,
            {}
        };
    }

    double a = input["a"].get<double>();
    double b = input["b"].get<double>();

    nlohmann::json output;
    output["result"] = a + b;

    return AgentResult
    {
        request.agent_id,
        AgentStatus::SUCCESS,
        output,
        std::nullopt,
        0.0,
        {}
    };
}

AgentDescriptor SumAgent::descriptor(IAgent* impl)
{
    AgentDescriptor desc;
    desc.id = 1;
    desc.name = "sum";
    desc.description = "Suma dos números";
    desc.version = "1.0.0";
    desc.input_schema = nlohmann::json
    {
        {"type", "object"},
        {"properties",
            nlohmann::json
            {
                {"a", {{"type", "number"}}},
                {"b", {{"type", "number"}}}
            }
        },
        {"required", nlohmann::json::array({"a", "b"})}
    };
    desc.output_schema = nlohmann::json
    {
        {"type", "object"},
        {"properties",
            nlohmann::json
            {
                {"result", {{"type", "number"}}}
            }
        },
        {"required", nlohmann::json::array({"result"})}
    };
    desc.context_requirements = {};
    desc.capabilities = {"math.sum"};
    desc.agent = impl;
    return desc;
}

AgentResult SubtractAgent::execute(const AgentRequest& request)
{
    const auto& input = request.input;

    if (!input.contains("a") || !input.contains("b"))
    {
        return AgentResult
        {
            request.agent_id,
            AgentStatus::FAILED,
            nlohmann::json(),
            AgentError{AgentErrorCode::INVALID_REQUEST, "missing required input"},
            0.0,
            {}
        };
    }

    double a = input["a"].get<double>();
    double b = input["b"].get<double>();

    nlohmann::json output;
    output["result"] = a - b;

    return AgentResult
    {
        request.agent_id,
        AgentStatus::SUCCESS,
        output,
        std::nullopt,
        0.0,
        {}
    };
}

AgentDescriptor SubtractAgent::descriptor(IAgent* impl)
{
    AgentDescriptor desc;
    desc.id = 2;
    desc.name = "subtract";
    desc.description = "Resta dos números";
    desc.version = "1.0.0";
    desc.input_schema = nlohmann::json
    {
        {"type", "object"},
        {"properties",
            nlohmann::json
            {
                {"a", {{"type", "number"}}},
                {"b", {{"type", "number"}}}
            }
        },
        {"required", nlohmann::json::array({"a", "b"})}
    };
    desc.output_schema = nlohmann::json
    {
        {"type", "object"},
        {"properties",
            nlohmann::json
            {
                {"result", {{"type", "number"}}}
            }
        },
        {"required", nlohmann::json::array({"result"})}
    };
    desc.context_requirements = {};
    desc.capabilities = {"math.subtract"};
    desc.agent = impl;
    return desc;
}

AgentResult MultiplyAgent::execute(const AgentRequest& request)
{
    const auto& input = request.input;

    if (!input.contains("a") || !input.contains("b"))
    {
        return AgentResult
        {
            request.agent_id,
            AgentStatus::FAILED,
            nlohmann::json(),
            AgentError{AgentErrorCode::INVALID_REQUEST, "missing required input"},
            0.0,
            {}
        };
    }

    double a = input["a"].get<double>();
    double b = input["b"].get<double>();

    nlohmann::json output;
    output["result"] = a * b;

    return AgentResult
    {
        request.agent_id,
        AgentStatus::SUCCESS,
        output,
        std::nullopt,
        0.0,
        {}
    };
}

AgentDescriptor MultiplyAgent::descriptor(IAgent* impl)
{
    AgentDescriptor desc;
    desc.id = 3;
    desc.name = "multiply";
    desc.description = "Multiplica dos números";
    desc.version = "1.0.0";
    desc.input_schema = nlohmann::json
    {
        {"type", "object"},
        {"properties",
            nlohmann::json
            {
                {"a", {{"type", "number"}}},
                {"b", {{"type", "number"}}}
            }
        },
        {"required", nlohmann::json::array({"a", "b"})}
    };
    desc.output_schema = nlohmann::json
    {
        {"type", "object"},
        {"properties",
            nlohmann::json
            {
                {"result", {{"type", "number"}}}
            }
        },
        {"required", nlohmann::json::array({"result"})}
    };
    desc.context_requirements = {};
    desc.capabilities = {"math.multiply"};
    desc.agent = impl;
    return desc;
}

AgentResult DivideAgent::execute(const AgentRequest& request)
{
    const auto& input = request.input;

    if (!input.contains("a") || !input.contains("b"))
    {
        return AgentResult
        {
            request.agent_id,
            AgentStatus::FAILED,
            nlohmann::json(),
            AgentError{AgentErrorCode::INVALID_REQUEST, "missing required input"},
            0.0,
            {}
        };
    }

    double a = input["a"].get<double>();
    double b = input["b"].get<double>();

    if (b == 0.0)
    {
        return AgentResult
        {
            request.agent_id,
            AgentStatus::FAILED,
            nlohmann::json(),
            AgentError{AgentErrorCode::EXECUTION_FAILED, "division by zero"},
            0.0,
            {}
        };
    }

    nlohmann::json output;
    output["result"] = a / b;

    return AgentResult
    {
        request.agent_id,
        AgentStatus::SUCCESS,
        output,
        std::nullopt,
        0.0,
        {}
    };
}

AgentDescriptor DivideAgent::descriptor(IAgent* impl)
{
    AgentDescriptor desc;
    desc.id = 4;
    desc.name = "divide";
    desc.description = "Divide dos números";
    desc.version = "1.0.0";
    desc.input_schema = nlohmann::json
    {
        {"type", "object"},
        {"properties",
            nlohmann::json
            {
                {"a", {{"type", "number"}}},
                {"b", {{"not", {{"const", 0}}}}}
            }
        },
        {"required", nlohmann::json::array({"a", "b"})}
    };
    desc.output_schema = nlohmann::json
    {
        {"type", "object"},
        {"properties",
            nlohmann::json
            {
                {"result", {{"type", "number"}}}
            }
        },
        {"required", nlohmann::json::array({"result"})}
    };
    desc.context_requirements = {};
    desc.capabilities = {"math.divide"};
    desc.agent = impl;
    return desc;
}

AgentResult AverageAgent::execute(const AgentRequest& request)
{
    const auto& input = request.input;

    if (!input.contains("values"))
    {
        return AgentResult
        {
            request.agent_id,
            AgentStatus::FAILED,
            nlohmann::json(),
            AgentError{AgentErrorCode::INVALID_REQUEST, "missing required input"},
            0.0,
            {}
        };
    }

    const auto& values = input["values"];

    if (!values.is_array() || values.empty())
    {
        return AgentResult
        {
            request.agent_id,
            AgentStatus::FAILED,
            nlohmann::json(),
            AgentError{AgentErrorCode::EXECUTION_FAILED, "empty values"},
            0.0,
            {}
        };
    }

    double sum = 0.0;
    std::size_t count = 0;

    for (const auto& v : values)
    {
        sum += v.get<double>();
        ++count;
    }

    nlohmann::json output;
    output["result"] = sum / static_cast<double>(count);

    return AgentResult
    {
        request.agent_id,
        AgentStatus::SUCCESS,
        output,
        std::nullopt,
        0.0,
        {}
    };
}

AgentDescriptor AverageAgent::descriptor(IAgent* impl)
{
    AgentDescriptor desc;
    desc.id = 5;
    desc.name = "average";
    desc.description = "Calcula el promedio de un array de números";
    desc.version = "1.0.0";
    desc.input_schema = nlohmann::json
    {
        {"type", "object"},
        {"properties",
            nlohmann::json
            {
                {"values",
                    nlohmann::json
                    {
                        {"type", "array"},
                        {"items", {{"type", "number"}}},
                        {"minItems", 1}
                    }
                }
            }
        },
        {"required", nlohmann::json::array({"values"})}
    };
    desc.output_schema = nlohmann::json
    {
        {"type", "object"},
        {"properties",
            nlohmann::json
            {
                {"result", {{"type", "number"}}}
            }
        },
        {"required", nlohmann::json::array({"result"})}
    };
    desc.context_requirements = {};
    desc.capabilities = {"math.average"};
    desc.agent = impl;
    return desc;
}

void register_native_agents(satellite::core::registry::AgentRegistry& registry)
{
    static SumAgent s1;
    static SubtractAgent s2;
    static MultiplyAgent s3;
    static DivideAgent s4;
    static AverageAgent s5;

    registry.register_agent(SumAgent::descriptor(&s1));
    registry.register_agent(SubtractAgent::descriptor(&s2));
    registry.register_agent(MultiplyAgent::descriptor(&s3));
    registry.register_agent(DivideAgent::descriptor(&s4));
    registry.register_agent(AverageAgent::descriptor(&s5));
}

} // namespace satellite::core::agents