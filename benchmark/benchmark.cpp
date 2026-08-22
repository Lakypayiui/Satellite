// Benchmark del framework Satellite (FASE 23).
// Compara dos modelos de uso del contexto:
//   MODELO A: un agente monolítico recibe TODO el contexto del proyecto.
//   MODELO B: orquestador + microagentes + Context Optimizer (contexto seleccionado).
// No asume que B es mejor: produce métricas (tokens, latencia, ratio) para demostrarlo.
//
// Métricas medidas:
//   - input tokens (contexto): A = proyecto completo; B = contexto optimizado.
//   - output tokens: tokens de los resultados de los agentes.
//   - latencia: A = lectura+indexado del contexto; B = optimize + ejecución de pasos.
//   - tokens_saved, compression_ratio, relevance_score (del optimizador).
//   - success rate: pasos de agentes que terminan en SUCCESS.

#include <iostream>
#include <string>
#include <vector>
#include <filesystem>
#include <fstream>
#include <chrono>

#include "core/registry/AgentRegistry.h"
#include "core/agents/NativeAgents.h"
#include "core/dispatcher/Dispatcher.h"
#include "core/catalog/AgentCatalog.h"
#include "core/agent/AgentResult.h"
#include "core/agent/AgentStatus.h"
#include "core/agent/AgentRequest.h"
#include "context/engine/ProjectContext.h"
#include "context/optimizer/ContextOptimizer.h"
#include "core/protocol/Protocol.h"
#include <json.hpp>

using namespace satellite::core::agent;
using namespace satellite::core::registry;
using namespace satellite::core::agents;
using namespace satellite::core::dispatcher;
using namespace satellite::core::protocol;
using namespace satellite::context;

namespace fs = std::filesystem;

// Estimación simple de tokens: 1 token ≈ 4 caracteres.
static std::size_t estimate_tokens(const std::string& text)
{
    return text.size() / 4 + 1;
}

int main()
{
    // --- Proyecto sintético: 20 archivos con símbolos ---
    fs::path proj = fs::temp_directory_path() / "satellite_benchmark_project";
    fs::remove_all(proj);
    fs::create_directories(proj);
    std::size_t total_lines = 0;
    for (int i = 0; i < 20; ++i)
    {
        std::ofstream f(proj / ("modulo_" + std::to_string(i) + ".cpp"));
        f << "// Modulo " << i << "\n";
        for (int j = 0; j < 30; ++j)
        {
            f << "int funcion_" << i << "_" << j << "(int x) { return x * " << i << " + " << j << "; }\n";
            ++total_lines;
        }
        f.close();
    }
    std::cout << "[bench] proyecto sintetico: 20 archivos, " << total_lines << " lineas\n";

    // --- Contexto del proyecto (una sola vez, ambos modelos lo usan) ---
    ProjectContext ctx;
    ctx.root = proj.string();
    for (const auto& entry : fs::directory_iterator(proj))
    {
        if (entry.path().extension() == ".cpp")
        {
            FileInfo fi;
            fi.path = entry.path().filename().string();
            fi.language = "C++";
            fi.size = entry.file_size();
            std::ifstream in(entry.path());
            std::string line;
            while (std::getline(in, line))
            {
                ++fi.lines;
            }
            fi.symbols.push_back(SymbolInfo{"funcion_" + fi.path.substr(7, 1) + "_0", SymbolKind::Function, fi.path, 1, ""});
            ctx.files.push_back(fi);
            ctx.total_lines += fi.lines;
            ctx.total_files += 1;
        }
    }

    // ===== MODELO A: monolítico (todo el contexto) =====
    auto t0 = std::chrono::steady_clock::now();
    std::size_t a_tokens = 0;
    for (const auto& f : ctx.files)
    {
        a_tokens += estimate_tokens(f.path + " " + std::to_string(f.lines) + " lineas");
        for (const auto& s : f.symbols)
        {
            a_tokens += estimate_tokens(s.name);
        }
    }
    auto t1 = std::chrono::steady_clock::now();
    double a_latency_ms = std::chrono::duration<double, std::milli>(t1 - t0).count();

    // ===== MODELO B: orquestador + microagentes + Context Optimizer =====
    AgentRegistry registry;
    register_native_agents(registry);
    Dispatcher dispatcher(registry);
    DefaultContextOptimizer optimizer;

    Task task;
    task.description = "calcular la suma de los valores de entrada";

    auto t2 = std::chrono::steady_clock::now();
    AgentDescriptor desc = *registry.find_agent(1);
    ContextSelection sel = optimizer.optimize(task, desc, ctx, TokenBudget{2048});
    OptimizationStats stats = optimizer.last_stats();

    // pasos de microagentes: sum + divide + average (como haría el orquestador)
    int success = 0;
    int total = 3;
    AgentResult r1 = dispatcher.dispatch(AgentRequest{1, {{"a", 10}, {"b", 5}}, {}, {}, {}});
    if (r1.status == AgentStatus::SUCCESS)
    {
        ++success;
    }
    AgentResult r2 = dispatcher.dispatch(AgentRequest{4, {{"a", 10}, {"b", 2}}, {}, {}, {}});
    if (r2.status == AgentStatus::SUCCESS)
    {
        ++success;
    }
    AgentResult r3 = dispatcher.dispatch(AgentRequest{5, {{"values", {1.0, 2.0, 3.0}}}, {}, {}, {}});
    if (r3.status == AgentStatus::SUCCESS)
    {
        ++success;
    }
    auto t3 = std::chrono::steady_clock::now();
    double b_latency_ms = std::chrono::duration<double, std::milli>(t3 - t2).count();

    std::size_t b_tokens = sel.estimated_tokens;

    // ===== Reporte =====
    // La comparación de tokens usa la MISMA base (estimación del optimizador):
    // A = contexto completo (tokens_before), B = contexto seleccionado (tokens_after).
    double compression_ratio = stats.compression_ratio;

    nlohmann::json report;
    report["modelo_a"] = {
        {"contexto_tokens", stats.tokens_before},
        {"latencia_ms", a_latency_ms},
        {"output_tokens", estimate_tokens(r1.output.dump() + r2.output.dump() + r3.output.dump())}
    };
    report["modelo_b"] = {
        {"contexto_tokens", stats.tokens_after},
        {"latencia_ms", b_latency_ms},
        {"output_tokens", estimate_tokens(r1.output.dump() + r2.output.dump() + r3.output.dump())},
        {"tokens_before", stats.tokens_before},
        {"tokens_after", stats.tokens_after},
        {"tokens_saved", stats.tokens_saved},
        {"compression_ratio", compression_ratio},
        {"relevance_score", stats.relevance_score},
        {"success_rate", static_cast<double>(success) / static_cast<double>(total)}
    };

    std::cout << "[bench] MODELO A (monolitico):  contexto=" << stats.tokens_before << " tokens, latencia=" << a_latency_ms << " ms\n";
    std::cout << "[bench] MODELO B (microagentes): contexto=" << stats.tokens_after << " tokens, latencia=" << b_latency_ms << " ms\n";
    std::cout << "[bench] tokens_saved=" << stats.tokens_saved
              << " compression_ratio=" << compression_ratio
              << " relevance=" << stats.relevance_score
              << " success_rate=" << (double)success / (double)total << "\n";
    std::cout << "[bench] report=" << report.dump(2) << "\n";

    fs::remove_all(proj);
    return 0;
}
