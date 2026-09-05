// Tests del sandbox de efectos (AgentSandbox.h)
// Sin dependencias externas: solo C++17 estándar.

#include <fstream>
#include <iostream>
#include <string>

#include "core/agent/AgentSandbox.h"

using namespace satellite::core::agent;

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

int main()
{
    const std::filesystem::path tmp = std::filesystem::temp_directory_path() /
        ("satellite_sandbox_test_" + std::to_string(::time(nullptr)));
    std::filesystem::create_directories(tmp);

    // --- resolución dentro del work_dir ---
    AgentSandbox sb;
    sb.work_dir = tmp;
    CHECK("sandbox_resolve acepta path interno",
          sandbox_resolve(sb, "sub/archivo.txt") == (tmp / "sub/archivo.txt"));
    CHECK("sandbox_resolve rechaza escape con ..",
          sandbox_resolve(sb, "../fuera.txt").empty());
    CHECK("sandbox_resolve con path absoluto interno queda dentro",
          sandbox_resolve(sb, (tmp / "otros").string()) == (tmp / "otros"));
    // La ruta absoluta no escapa: se resuelve fuera del work_dir.
    CHECK("sandbox_resolve rechaza path absoluto del sistema",
          sandbox_resolve(sb, "C:/Windows/win.ini").empty());

    // --- escritura/lectura con deny_write_prefix (p.ej. .satellite/) ---
    sb.allow_fs_write = true;
    const auto dot_satellite = tmp / ".satellite";
    std::filesystem::create_directories(dot_satellite);
    sb.deny_write_prefixes.push_back(dot_satellite);
    CHECK("sandbox_write_file escribe dentro del work_dir",
          sandbox_write_file(sb, "hola.txt", "contenido"));
    CHECK("sandbox_write_file creó el archivo",
          sandbox_read_file(sb, "hola.txt") == "contenido");
    CHECK("sandbox_write_file rechaza .satellite/",
          !sandbox_write_file(sb, ".satellite/config.json", "{"));
    CHECK("sandbox_write_file con allow_fs_write=false rechaza",
          !sandbox_write_file([sb] {
              AgentSandbox s = sb; s.allow_fs_write = false; return s;
          }(), "otro.txt", "x"));
    CHECK("sandbox_read_file no permitido devuelve vacío",
          sandbox_read_file([sb] {
              AgentSandbox s = sb; s.allow_fs_read = false; return s;
          }(), "hola.txt").empty());

    // --- ejecución de proceso ---
    sb.allow_process = true;
#ifdef _WIN32
    const std::string cmd = "echo hola-sandbox";
#else
    const std::string cmd = "echo hola-sandbox";
#endif
    ProcessResult pr = sandbox_run_process(sb, cmd);
    CHECK("sandbox_run_process devuelve salida", pr.output.find("hola-sandbox") != std::string::npos);
    CHECK("sandbox_run_process no ejecuta sin allow_process",
          sandbox_run_process([sb] {
              AgentSandbox s = sb; s.allow_process = false; return s;
          }(), cmd).output.empty());
    // comando inexistente: exit_code != 0 y salida capturada (o vacía).
    ProcessResult pr_fail = sandbox_run_process(sb, "comando_que_no_existe_xyz");
    CHECK("sandbox_run_process comando inexistente falla",
          pr_fail.exit_code != 0);

    // --- http_get ---
    sb.allow_network = true;
    CHECK("sandbox_http_get rechaza URL no http",
          sandbox_http_get(sb, "file:///etc/passwd").empty());
    CHECK("sandbox_http_get sin allow_network devuelve vacío",
          sandbox_http_get([sb] {
              AgentSandbox s = sb; s.allow_network = false; return s;
          }(), "http://example.com").empty());

    // Limpieza.
    std::error_code ec;
    std::filesystem::remove_all(tmp, ec);

    std::cout << (g_failed == 0 ? "ALL PASSED" : "SOME FAILED")
              << " - " << g_passed << " passed, " << g_failed << " failed\n";
    return g_failed == 0 ? 0 : 1;
}
