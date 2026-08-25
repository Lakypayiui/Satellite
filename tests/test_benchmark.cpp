// Test del benchmark (FASE 23): ejecuta el benchmark y verifica que produce
// métricas coherentes: contexto B <= contexto A, tokens_saved > 0, ratio > 0,
// success_rate == 1 (agentes nativos funcionando).

#include <iostream>
#include <string>
#include <sstream>
#include <filesystem>
#include <cstdio>

#include "context/engine/ProjectContext.h"
#include "context/optimizer/ContextOptimizer.h"
#include "core/registry/AgentRegistry.h"
#include "core/agents/NativeAgents.h"
#include "core/dispatcher/Dispatcher.h"
#include "core/protocol/Protocol.h"

using namespace satellite::core::registry;
using namespace satellite::core::agents;
using namespace satellite::core::dispatcher;
using namespace satellite::core::agent;
using namespace satellite::core::protocol;
using namespace satellite::context;

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
    } while (0)

namespace fs = std::filesystem;

int main()
{
    // --- 1. El optimizador reduce el contexto (núcleo del MODELO B) ---
    {
        ProjectContext ctx;
        ctx.root = "sintetico";
        ctx.total_files = 3;
        ctx.total_lines = 90;
        for (int i = 0; i < 3; ++i)
        {
            FileInfo fi;
            fi.path = "modulo_" + std::to_string(i) + ".cpp";
            fi.type = "C++";
            fi.lines = 30;
            fi.size = 900;
            fi.symbols.push_back(SymbolInfo{"funcion_" + std::to_string(i), SymbolKind::Function, fi.path, 1, ""});
            ctx.files.push_back(fi);
        }

        AgentRegistry reg;
        register_native_agents(reg);
        DefaultContextOptimizer optimizer;
        Task task;
        task.description = "operar con modulo_1";

        ContextSelection sel = optimizer.optimize(task, *reg.find_agent(1), ctx, TokenBudget{500});
        OptimizationStats st = optimizer.last_stats();

        CHECK("optimizer: selecciona archivos", !sel.selected_files.empty());
        CHECK("optimizer: tokens_before > 0", st.tokens_before > 0);
        CHECK("optimizer: tokens_after <= tokens_before", st.tokens_after <= st.tokens_before);
        CHECK("optimizer: tokens_saved > 0", st.tokens_saved > 0);
        CHECK("optimizer: compression_ratio > 0", st.compression_ratio > 0.0);
        CHECK("optimizer: relevance_score >= 0 y <= 1", st.relevance_score >= 0.0 && st.relevance_score <= 1.0);
        CHECK("optimizer: optimization_time_ms >= 0", st.optimization_time_ms >= 0.0);
    }

    // --- 2. Los microagentes ejecutan con éxito (runtime del MODELO B) ---
    {
        AgentRegistry reg;
        register_native_agents(reg);
        Dispatcher disp(reg);

        AgentResult r1 = disp.dispatch(AgentRequest{1, {{"a", 10}, {"b", 5}}, {}, {}, {}});
        CHECK("agente sum: SUCCESS", r1.status == AgentStatus::SUCCESS);
        CHECK("agente sum: resultado 15", r1.output["result"] == 15.0);

        AgentResult r2 = disp.dispatch(AgentRequest{5, {{"values", {1.0, 2.0, 3.0}}}, {}, {}, {}});
        CHECK("agente average: SUCCESS", r2.status == AgentStatus::SUCCESS);
        CHECK("agente average: resultado 2", r2.output["result"] == 2.0);

        // MODELO A (monolítico) recibiría todo el contexto: aquí solo se verifica
        // que el runtime es determinista (mismo input → mismo output).
        AgentResult r3 = disp.dispatch(AgentRequest{1, {{"a", 10}, {"b", 5}}, {}, {}, {}});
        CHECK("determinismo: mismo input → mismo output", r3.output == r1.output);
    }

    std::cout << g_passed << " passed, " << g_failed << " failed\n";
    return g_failed == 0 ? 0 : 1;
}
