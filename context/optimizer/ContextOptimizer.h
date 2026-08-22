#pragma once

// Optimizador de contexto para maximizar relevancia bajo presupuesto de tokens (Fase 9).
// El algoritmo es REEMPLAZABLE: cualquier implementación de IContextOptimizer.
// DefaultContextOptimizer es determinista y SIN LLM (el runtime no depende del LLM para optimizar).

#include <string>
#include <vector>
#include <cstddef>

#include "../engine/ProjectContext.h"
#include "../../core/agent/AgentDescriptor.h"
#include "../../core/protocol/Protocol.h"

namespace satellite::context
{

struct Task
{
    std::string description;           // descripción de la tarea en lenguaje natural
    std::vector<std::string> keywords; // palabras clave explícitas (opcional; se completan desde description)
};

struct ContextSelection
{
    std::vector<std::string> selected_files;        // paths relativos
    std::vector<SymbolInfo> selected_symbols;
    std::vector<DependencyInfo> selected_dependencies;
    std::vector<std::string> selected_constraints;  // restricciones relevantes (texto)
    std::size_t estimated_tokens = 0;
    double relevance_score = 0.0;                   // [0,1]
};

struct OptimizationStats
{
    std::size_t tokens_before = 0;
    std::size_t tokens_after = 0;
    std::size_t tokens_saved = 0;
    double compression_ratio = 0.0;                 // tokens_saved / tokens_before
    double relevance_score = 0.0;
    double optimization_time_ms = 0.0;
};

class IContextOptimizer
{
public:
    virtual ~IContextOptimizer() = default;
    virtual ContextSelection optimize(const Task& task, const satellite::core::agent::AgentDescriptor& agent,
                                      const ProjectContext& project, const satellite::core::protocol::TokenBudget& budget) = 0;
    virtual OptimizationStats last_stats() const = 0;
};

class DefaultContextOptimizer : public IContextOptimizer
{
public:
    ContextSelection optimize(const Task& task, const satellite::core::agent::AgentDescriptor& agent,
                              const ProjectContext& project, const satellite::core::protocol::TokenBudget& budget) override;
    OptimizationStats last_stats() const override;

private:
    OptimizationStats stats_;
};

} // namespace satellite::context