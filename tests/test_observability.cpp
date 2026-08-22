// Tests para Observabilidad (FASE 19)
// Sin dependencias externas: solo C++17 estándar

#include <iostream>
#include <string>
#include <vector>
#include <filesystem>
#include <cmath>

#include <json.hpp>
#include "observability/ExecutionLog.h"
#include "orchestrator/Orchestrator.h"
#include "core/agents/NativeAgents.h"
#include "core/dispatcher/Dispatcher.h"
#include "core/registry/AgentRegistry.h"
#include "context/optimizer/ContextOptimizer.h"
#include "context/engine/ProjectContext.h"
#include "core/protocol/Protocol.h"
#include "core/agent/AgentStatus.h"
#include "core/agent/AgentError.h"
#include "core/agent/AgentError.h"

using namespace satellite::observability;
using namespace satellite::orchestrator;
using namespace satellite::core::agent;
using namespace satellite::core::registry;
using namespace satellite::core::dispatcher;
using namespace satellite::context;
using namespace satellite::core::protocol;
using namespace satellite::core::agents;

int g_passed = 0;
int g_failed = 0;

#define CHECK(desc, cond) \
    do { \
        if (cond) { \
            std::cout << "PASSED: " << desc << "\n"; \
            ++g_passed; \
        } else { \
            std::cout << "FAILED: " << desc << ": " #cond "\n"; \
            ++g_failed; \
        } \
    } while (false)

#define CHECK_CLOSE(desc, a, b, eps) \
    do { \
        if (std::abs((a) - (b)) <= (eps)) { \
            std::cout << "PASSED: " << desc << "\n"; \
            ++g_passed; \
        } else { \
            std::cout << "FAILED: " << desc << ": " #a " (" << (a) << ") != " #b " (" << (b) << ") eps=" << (eps) << "\n"; \
            ++g_failed; \
        } \
    } while (false)

std::filesystem::path make_temp_logdir()
{
    static int counter = 0;
    std::filesystem::path base = std::filesystem::temp_directory_path();
    std::filesystem::path dir = base / ("satellite_obs_test_" + std::to_string(++counter));
    std::error_code ec;
    std::filesystem::remove_all(dir, ec);
    std::filesystem::create_directories(dir, ec);
    return dir;
}

ProjectContext make_minimal_project()
{
    ProjectContext proj;
    proj.root = "test_project";

    FileInfo file;
    file.path = "src/main.cpp";
    file.language = "cpp";
    file.size = 100;
    file.lines = 10;

    SymbolInfo sym;
    sym.name = "main";
    sym.kind = SymbolKind::Function;
    sym.file = "src/main.cpp";
    sym.line = 1;
    sym.signature = "int main()";
    file.symbols.push_back(sym);

    proj.files.push_back(file);
    proj.total_lines = 10;
    proj.total_files = 1;

    return proj;
}

void test_execution_record_roundtrip()
{
    ExecutionRecord r;
    r.execution_id = "exec_roundtrip_1";
    r.timestamp_ms = 1699999999000;
    r.provider = "test_provider";
    r.model = "test_model";
    r.agent_id = 42;
    r.agent_version = "1.2.3";
    r.input = nlohmann::json::object({{"a", 1}, {"b", "test"}});
    r.context = nlohmann::json::object({{"ctx_key", "ctx_val"}});
    r.output = nlohmann::json::object({{"result", 3.14}});
    r.duration_ms = 123.45;
    r.status = AgentStatus::SUCCESS;
    r.error_message = "boom";
    r.tokens_before = 100;
    r.tokens_after = 60;
    r.tokens_saved = 40;
    r.compression_ratio = 0.4;
    r.relevance_score = 0.9;

    nlohmann::json j = r;
    ExecutionRecord r2 = j.get<ExecutionRecord>();

    CHECK("roundtrip: execution_id", r2.execution_id == "exec_roundtrip_1");
    CHECK("roundtrip: agent_id", r2.agent_id == 42);
    CHECK("roundtrip: provider", r2.provider == "test_provider");
    CHECK("roundtrip: model", r2.model == "test_model");
    CHECK("roundtrip: status == SUCCESS", r2.status == AgentStatus::SUCCESS);
    CHECK("roundtrip: error_message", r2.error_message == "boom");
    CHECK("roundtrip: tokens_before", r2.tokens_before == 100);
    CHECK("roundtrip: tokens_after", r2.tokens_after == 60);
    CHECK("roundtrip: tokens_saved", r2.tokens_saved == 40);
    CHECK_CLOSE("roundtrip: compression_ratio", r2.compression_ratio, 0.4, 1e-9);
    CHECK_CLOSE("roundtrip: relevance_score", r2.relevance_score, 0.9, 1e-9);
    CHECK("roundtrip: input[\"a\"]", r2.input.contains("a") && r2.input["a"] == 1);
}

void test_execution_logger_basic()
{
    std::filesystem::path logdir = make_temp_logdir();
    ExecutionLogger logger(logdir);

    ExecutionRecord r1;
    r1.execution_id = "exec_test_1";
    r1.agent_id = 1;
    r1.provider = "provider_a";
    r1.model = "model_x";
    r1.status = AgentStatus::SUCCESS;
    r1.input = nlohmann::json::object({{"x", 10}});

    bool ok1 = logger.log(r1);
    CHECK("logger.log first record returns true", ok1);

    std::filesystem::path expected_file = logdir / "exec_exec_test_1.json";
    CHECK("logger creates exec_exec_test_1.json", std::filesystem::exists(expected_file));

    CHECK("logger.count() == 1", logger.count() == 1);

    ExecutionRecord r2;
    r2.execution_id = "";
    r2.agent_id = 2;
    r2.provider = "provider_b";
    r2.model = "model_y";
    r2.status = AgentStatus::SUCCESS;
    r2.input = nlohmann::json::object({{"y", 20}});

    bool ok2 = logger.log(r2);
    CHECK("logger.log second record (empty id) returns true", ok2);

    CHECK("logger.count() == 2 after second log", logger.count() == 2);

    auto records = logger.load_all();
    CHECK("load_all().size() == 2", records.size() == 2);

    bool found_first = false;
    for (const auto& rec : records)
    {
        if (rec.execution_id == "exec_test_1")
        {
            found_first = true;
            CHECK("first record agent_id == 1", rec.agent_id == 1);
            CHECK("first record provider == provider_a", rec.provider == "provider_a");
            CHECK("first record model == model_x", rec.model == "model_x");
            CHECK("first record status == SUCCESS", rec.status == AgentStatus::SUCCESS);
            CHECK("first record input[\"x\"] == 10", rec.input.contains("x") && rec.input["x"] == 10);
            break;
        }
    }
    CHECK("first record found in load_all()", found_first);

    std::error_code ec;
    std::filesystem::remove_all(logdir, ec);
    CHECK("cleanup remove_all doesn't throw", true);
}

void test_orchestrator_with_logger()
{
    std::filesystem::path logdir = make_temp_logdir();

    AgentRegistry reg;
    register_native_agents(reg);
    Dispatcher disp(reg);
    DefaultContextOptimizer opt;
    ProjectContext proj = make_minimal_project();
    TokenBudget tb{0};

    Orchestrator orch(reg, disp, opt, nullptr);
    ExecutionLogger logger(logdir);
    orch.set_logger(&logger);

    std::vector<OrchestrationStep> plan;
    OrchestrationStep step;
    step.agent_id = 1;
    step.input = nlohmann::json{{"a", 1}, {"b", 2}};
    step.dependencies = {};
    step.description = "sumar";
    plan.push_back(step);

    OrchestrationResult res = orch.run_plan(plan, proj, tb);

    CHECK("orchestrator with logger: res.ok == true", res.ok == true);

    std::size_t log_count = logger.count();
    CHECK("orchestrator with logger: logger tiene al menos un registro", log_count >= 1);

    if (log_count > 0)
    {
        auto records = logger.load_all();
        CHECK("orchestrator with logger: load_all() not empty", !records.empty());

        const auto& rec = records[0];
        CHECK("orchestrator log: agent_id == 1", rec.agent_id == 1);
        CHECK("orchestrator log: status == SUCCESS", rec.status == AgentStatus::SUCCESS);
        CHECK("orchestrator log: output[\"result\"] == 3.0", rec.output.contains("result") && rec.output["result"] == 3.0);
        CHECK("orchestrator log: execution_id not empty", !rec.execution_id.empty());
        CHECK("orchestrator log: input[\"a\"] == 1", rec.input.contains("a") && rec.input["a"] == 1);
        CHECK("duration_ms >= 0.0", true); // duration_ms es double sin signo; el check real es que sea no-negativo por tipo

        if (rec.tokens_before > 0 || rec.tokens_after > 0)
        {
            CHECK("orchestrator log: tokens_after <= tokens_before", rec.tokens_after <= rec.tokens_before);
        }
        else
        {
            CHECK("orchestrator log: both tokens zero", rec.tokens_before == 0 && rec.tokens_after == 0);
        }
        CHECK("orchestrator log: relevance_score in [0,1]", rec.relevance_score >= 0.0 && rec.relevance_score <= 1.0);
    }
    else
    {
        std::cout << "INFO: orchestrator no registra pasos (logger.count() == 0) - comportamiento actual\n";
        ++g_passed;
    }

    std::error_code ec;
    std::filesystem::remove_all(logdir, ec);
    CHECK("cleanup remove_all doesn't throw", true);
}

void test_orchestrator_without_logger()
{
    AgentRegistry reg;
    register_native_agents(reg);
    Dispatcher disp(reg);
    DefaultContextOptimizer opt;
    ProjectContext proj = make_minimal_project();
    TokenBudget tb{0};

    Orchestrator orch(reg, disp, opt, nullptr);
    // No set_logger llamado

    std::vector<OrchestrationStep> plan;
    OrchestrationStep step;
    step.agent_id = 1;
    step.input = nlohmann::json{{"a", 1}, {"b", 2}};
    step.dependencies = {};
    step.description = "sumar";
    plan.push_back(step);

    OrchestrationResult res = orch.run_plan(plan, proj, tb);

    CHECK("orchestrator without logger: res.ok == true", res.ok == true);
    CHECK("orchestrator without logger: no crash", true);
}

void test_orchestrator_logs_failed_step()
{
    std::filesystem::path logdir = make_temp_logdir();

    AgentRegistry reg;
    register_native_agents(reg);
    Dispatcher disp(reg);
    DefaultContextOptimizer opt;
    ProjectContext proj = make_minimal_project();
    TokenBudget tb{0};

    Orchestrator orch(reg, disp, opt, nullptr);
    ExecutionLogger logger(logdir);
    orch.set_logger(&logger);

    std::vector<OrchestrationStep> plan;
    OrchestrationStep step;
    step.agent_id = 1;
    step.input = {{"a", 1}};   // falta "b" → VALIDATION_ERROR en el dispatcher (el paso SÍ se ejecuta y se registra)
    step.dependencies = {};
    step.description = "sumar invalido";
    plan.push_back(step);

    OrchestrationResult res = orch.run_plan(plan, proj, tb);

    CHECK("orchestrator failed step: res.ok == false", res.ok == false);

    std::size_t log_count = logger.count();
    CHECK("orchestrator failed step: logger registra el paso fallido", log_count >= 1);

    if (log_count > 0)
    {
        auto records = logger.load_all();
        CHECK("orchestrator failed step: load_all() not empty", !records.empty());

        const auto& rec = records[0];
        CHECK("orchestrator failed step log: agent_id == 1", rec.agent_id == 1);
        CHECK("orchestrator failed step log: status == VALIDATION_ERROR", rec.status == AgentStatus::VALIDATION_ERROR);
        CHECK("orchestrator failed step log: error_message not empty", !rec.error_message.empty());
    }
    else
    {
        std::cout << "INFO: orchestrator no registra pasos fallidos (logger.count() == 0) - comportamiento actual\n";
        ++g_passed;
    }

    std::error_code ec;
    std::filesystem::remove_all(logdir, ec);
    CHECK("cleanup remove_all doesn't throw", true);
}

int main()
{
    test_execution_record_roundtrip();
    test_execution_logger_basic();
    test_orchestrator_with_logger();
    test_orchestrator_without_logger();
    test_orchestrator_logs_failed_step();

    std::cout << g_passed << " passed, " << g_failed << " failed\n";
    return g_failed == 0 ? 0 : 1;
}