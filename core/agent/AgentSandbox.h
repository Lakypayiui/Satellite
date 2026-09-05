#pragma once

// Sandbox de efectos de sistema para microagentes.
//
// Satellite aísla cada agente en su propio proceso (satellite_agent_host) y le
// da un `AgentRequest`. Por defecto el agente solo hace cómputo puro (JSON in ->
// JSON out). Con este header, el host puede inyectar un *sandbox* que acota y
// autoriza efectos de sistema: escribir/leer archivos, ejecutar comandos y
// llamar por red. Cada efecto requiere la capability correspondiente habilitada
// por el SecurityPolicy del proyecto y NUNCA puede salir del `work_dir`.
//
// Deny-by-default: si la capability no está permitida o el path escapa del
// work_dir (o cae en un prefijo denegado como `.satellite/`), la llamada
// devuelve false / cadena vacía (sin efectos).

#include <cstdio>
#include <filesystem>
#include <fstream>
#include <sstream>
#include <string>
#include <vector>

#ifndef _WIN32
#include <sys/wait.h>
#endif

#include "AgentRequest.h"

namespace satellite::core::agent
{

struct AgentSandbox
{
    // Directorio de trabajo acotado (proyecto). Todo efecto se resuelve aquí.
    std::filesystem::path work_dir;
    // Prefijos de escritura prohibidos (p.ej. `<proyecto>/.satellite/`).
    std::vector<std::filesystem::path> deny_write_prefixes;
    // Capacidades autorizadas por el SecurityPolicy del proyecto.
    bool allow_fs_write = false;
    bool allow_fs_read = true;   // lectura por defecto (como el default)
    bool allow_process = false;
    bool allow_network = false;
};

// Resuelve `relative` contra work_dir y comprueba que NO escape del sandbox.
// Devuelve la ruta absoluta canónica cuando es segura; cadena vacía si escapa.
inline std::filesystem::path sandbox_resolve(
    const AgentSandbox& sandbox, const std::string& relative)
{
    if (relative.empty())
        return {};
    std::filesystem::path candidate = sandbox.work_dir / relative;
    std::error_code ec;
    auto absolute = std::filesystem::weakly_canonical(candidate, ec);
    if (ec)
        return {};
    auto root = std::filesystem::weakly_canonical(sandbox.work_dir, ec);
    // El candidate debe estar dentro de root (o ser el propio root).
    if (!absolute.empty() && absolute != root)
    {
        auto root_str = root.string();
        auto abs_str = absolute.string();
        if (abs_str.rfind(root_str, 0) != 0 || (abs_str.size() > root_str.size()
                && abs_str[root_str.size()] != '\\' && abs_str[root_str.size()] != '/'))
            return {};
    }
    return absolute;
}

// Comprueba que `absolute` (ya resuelta y dentro de work_dir) no caiga en un
// prefijo de escritura denegado (p.ej. `.satellite/`). Comparación por prefijo
// de ruta normalizada (cada componente; tolerante a separadores).
inline bool sandbox_is_denied_prefix(
    const AgentSandbox& sandbox, const std::filesystem::path& absolute)
{
    for (const auto& prefix : sandbox.deny_write_prefixes)
    {
        std::error_code ec;
        auto canonical_prefix = std::filesystem::weakly_canonical(prefix, ec);
        if (ec)
            continue;
        auto prefix_str = canonical_prefix.string();
        auto abs_str = absolute.string();
        if (prefix_str.size() >= abs_str.size())
        {
            if (abs_str == prefix_str)
                return true;
            continue;
        }
        if (abs_str.rfind(prefix_str, 0) == 0
            && (abs_str[prefix_str.size()] == '\\' || abs_str[prefix_str.size()] == '/'))
            return true;
    }
    return false;
}

// Escribe `content` en `relative` (dentro del work_dir). Requiere
// `allow_fs_write`. Crea los directorios padre. Devuelve true en éxito.
inline bool sandbox_write_file(const AgentSandbox& sandbox,
                               const std::string& relative,
                               const std::string& content)
{
    if (!sandbox.allow_fs_write)
        return false;
    auto path = sandbox_resolve(sandbox, relative);
    if (path.empty() || sandbox_is_denied_prefix(sandbox, path))
        return false;
    std::error_code ec;
    std::filesystem::create_directories(path.parent_path(), ec);
    std::ofstream ofs(path, std::ios::binary);
    if (!ofs)
        return false;
    ofs.write(content.data(), static_cast<std::streamsize>(content.size()));
    return static_cast<bool>(ofs);
}

// Lee `relative` (dentro del work_dir). Requiere `allow_fs_read`.
// Devuelve el contenido; cadena vacía si no se puede / no permitido.
inline std::string sandbox_read_file(const AgentSandbox& sandbox,
                                     const std::string& relative)
{
    if (!sandbox.allow_fs_read)
        return {};
    auto path = sandbox_resolve(sandbox, relative);
    if (path.empty())
        return {};
    std::ifstream ifs(path, std::ios::binary);
    if (!ifs)
        return {};
    std::ostringstream ss;
    ss << ifs.rdbuf();
    return ss.str();
}

// Resultado de una ejecución de proceso dentro del sandbox.
struct ProcessResult
{
    int exit_code = -1;
    std::string output;   // stdout (con stderr mezclado vía 2>&1)
    bool timed_out = false;
};

// Ejecuta `command` con cwd = work_dir. Requiere `allow_process`. Devuelve el
// resultado (exit code + stdout). Es una implementación simple con popen: el
// comando lo decide el LLM, pero queda acotado al proceso del host.
inline ProcessResult sandbox_run_process(
    const AgentSandbox& sandbox, const std::string& command,
    const std::string& cwd = "", int timeout_ms = 0)
{
    ProcessResult result;
    if (!sandbox.allow_process || command.empty())
        return result;

    std::string cmd = command;
    // Lanzar con el cwd del sandbox (si no se especifica otro).
    if (!cwd.empty())
    {
        std::filesystem::path target = cwd;
        if (!target.is_absolute())
            target = sandbox.work_dir / cwd;
        if (sandbox_is_denied_prefix(sandbox, target))
            return result;
#ifdef _WIN32
        cmd = "cd /d \"" + target.string() + "\" && " + cmd;
#else
        cmd = "cd \"" + target.string() + "\" && " + cmd;
#endif
    }
    cmd += " 2>&1";

#ifdef _WIN32
    FILE* pipe = _popen(cmd.c_str(), "r");
#else
    FILE* pipe = popen(cmd.c_str(), "r");
#endif
    if (!pipe)
        return result;

    std::ostringstream ss;
    char buffer[4096];
    std::size_t read = 0;
    while (std::fgets(buffer, sizeof(buffer), pipe) != nullptr)
    {
        ss << buffer;
        read += std::strlen(buffer);
        // Si hay timeout y ya lo superamos, cortar.
        if (timeout_ms > 0 && (read / 1024) * 1024 >= static_cast<std::size_t>(timeout_ms / 4))
            break;
    }
#ifdef _WIN32
    int code = _pclose(pipe);
#else
    int code = pclose(pipe);
#endif
    result.output = ss.str();
    result.exit_code = (-1 == code) ? -1 : code;
    return result;
}

// GET HTTP simple vía curl (subproceso). Requiere `allow_network`.
// No linkea libcurl a los plugins: usa `curl` del sistema. Devuelve el body;
// cadena vacía si falla / no permitido.
inline std::string sandbox_http_get(const AgentSandbox& sandbox,
                                    const std::string& url,
                                    int timeout_sec = 15)
{
    if (!sandbox.allow_network || url.empty())
        return {};
    if (url.rfind("http://", 0) != 0 && url.rfind("https://", 0) != 0)
        return {};  // solo URLs http(s)

    std::string cmd = "curl";
    // Windows usa curl.exe; en máquinas modernas ambos existen.
#ifdef _WIN32
    cmd = "curl.exe";
#endif
    cmd += " -s -L --max-time " + std::to_string(timeout_sec) + " \"" + url + "\"";
#ifdef _WIN32
    FILE* pipe = _popen(cmd.c_str(), "r");
#else
    FILE* pipe = popen(cmd.c_str(), "r");
#endif
    if (!pipe)
        return {};
    std::ostringstream ss;
    char buffer[4096];
    while (std::fgets(buffer, sizeof(buffer), pipe) != nullptr)
        ss << buffer;
#ifdef _WIN32
    _pclose(pipe);
#else
    pclose(pipe);
#endif
    return ss.str();
}

} // namespace satellite::core::agent
