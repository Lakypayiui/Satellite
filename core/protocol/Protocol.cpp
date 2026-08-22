// Implementación del protocolo estándar (Fase 5).

#include "Protocol.h"

#include <chrono>

namespace satellite::core::protocol
{

std::string make_execution_id()
{
    static std::atomic<std::uint64_t> counter{0};
    const auto now = std::chrono::system_clock::now();
    const auto ms = std::chrono::duration_cast<std::chrono::milliseconds>(now.time_since_epoch()).count();
    const auto cnt = counter.fetch_add(1, std::memory_order_relaxed);
    return "exec_" + std::to_string(ms) + "_" + std::to_string(cnt);
}

} // namespace satellite::core::protocol