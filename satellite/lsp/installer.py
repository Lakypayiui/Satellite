"""Resolve and auto-install language servers under ``~/.satellite/lsp``.

Every recipe is a fixed argv list. There is no shell, no ``sudo`` and no global
package mutation; the model never supplies a command.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath

import httpx

from satellite.kernel.paths import resolve_paths
from satellite.lsp.config import LspSettings, load_lsp_settings
from satellite.lsp.servers import SERVERS, ServerSpec
from satellite.sandbox.process import run_process

JDT_URL = "https://download.eclipse.org/jdtls/snapshots/jdt-language-server-latest.tar.gz"
MAX_JDT_BYTES = 200 * 1024 * 1024
INSTALL_DIR_NAME = "lsp"

_NPM_PACKAGES: dict[str, tuple[str, ...]] = {
    "pyright": ("pyright",),
    "typescript": ("typescript", "typescript-language-server"),
    "sql-language-server": ("sql-language-server",),
    "intelephense": ("intelephense",),
}


class LspInstallError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        command: tuple[str, ...] = (),
        stdout: str = "",
        stderr: str = "",
        hint: str = "",
    ) -> None:
        super().__init__(message)
        self.code = code
        self.details = {
            "code": code,
            "message": message,
            "command": list(command),
            "stdout": stdout[-4000:],
            "stderr": stderr[-4000:],
            "hint": hint,
        }


@dataclass(frozen=True)
class InstallResult:
    ok: bool
    command: tuple[str, ...] = ()
    stdout: str = ""
    stderr: str = ""
    path: str = ""


_INSTALLED: dict[str, tuple[str, ...]] = {}


def lsp_root() -> Path:
    return resolve_paths(None).global_root / INSTALL_DIR_NAME


def _common_bins() -> tuple[Path, ...]:
    home = Path.home()
    bins = [
        home / ".local" / "bin",
        home / ".cargo" / "bin",
        home / "go" / "bin",
        home / ".dotnet" / "tools",
        lsp_root() / "go" / "bin",
        lsp_root() / "dotnet",
    ]
    appdata = os.environ.get("APPDATA")
    if appdata:
        bins.append(Path(appdata) / "npm")
    npm_bin = lsp_root() / "npm" / "node_modules" / ".bin"
    bins.append(npm_bin)
    py_root = lsp_root() / "py"
    bins.extend([py_root / "bin", py_root / "Scripts"])
    return tuple(bins)


def which(name: str) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    suffixes = ("", ".exe", ".cmd", ".bat") if os.name == "nt" else ("",)
    for base in _common_bins():
        for suffix in suffixes:
            candidate = base / f"{name}{suffix}"
            if candidate.is_file():
                return str(candidate)
    return None


def _local_npm_bin(name: str) -> str | None:
    base = lsp_root() / "npm" / "node_modules" / ".bin"
    suffixes = ("", ".cmd", ".bat", ".exe") if os.name == "nt" else ("",)
    for suffix in suffixes:
        candidate = base / f"{name}{suffix}"
        if candidate.is_file():
            return str(candidate)
    return None


def _path_if_file(*candidates: Path) -> str | None:
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return None


def _available(argv: tuple[str, ...]) -> bool:
    if not argv:
        return False
    first = argv[0]
    if Path(first).is_absolute() or os.sep in first or (os.altsep and os.altsep in first):
        if Path(first).is_file():
            return True
        # Python wrapper: python <script> ...
        if len(argv) > 1 and Path(argv[1]).is_file():
            return True
        return False
    return which(first) is not None


def _java_available() -> bool:
    return which("java") is not None


def _jdtls_paths() -> tuple[Path, Path, Path]:
    root = lsp_root() / "jdtls"
    wrapper = root / "bin" / ("jdtls.bat" if os.name == "nt" else "jdtls")
    return root, wrapper, root / "bin"


def _jdtls_command(project_root: Path | None) -> tuple[str, ...] | None:
    if not _java_available():
        return None
    root, wrapper, _ = _jdtls_paths()
    state = lsp_root() / "state"
    root_key = str(Path(project_root or Path.home()).resolve())
    digest = hashlib.sha256(root_key.encode("utf-8")).hexdigest()[:12]
    data = state / "jdtls" / digest
    config = state / "jdtls-config"
    try:
        data.mkdir(parents=True, exist_ok=True)
        config.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    if wrapper.is_file():
        if os.name == "nt" and wrapper.suffix.lower() in (".bat", ".cmd"):
            return (str(wrapper), "-data", str(data), "-configuration", str(config))
        return (
            sys.executable,
            str(wrapper),
            "-data",
            str(data),
            "-configuration",
            str(config),
        )
    system = which("jdtls")
    if system:
        return (system, "-data", str(data), "-configuration", str(config))
    java = which("java")
    jars = sorted((root / "plugins").glob("org.eclipse.equinox.launcher_*.jar"))
    if java and jars:
        platform_config = {
            "nt": "config_win",
            "darwin": "config_mac",
        }.get(os.name, "config_linux")
        return (
            java,
            "-Declipse.application=org.eclipse.jdt.ls.core.id1",
            "-Dosgi.bundles.defaultStartLevel=4",
            "-Declipse.product=org.eclipse.jdt.ls.core.product",
            "-Xmx1G",
            "--add-modules=ALL-SYSTEM",
            "--add-opens",
            "java.base/java.util=ALL-UNNAMED",
            "--add-opens",
            "java.base/java.lang=ALL-UNNAMED",
            "-jar",
            str(jars[-1]),
            "-configuration",
            str(root / platform_config),
            "-data",
            str(data),
        )
    return None


def _override_for(
    server: ServerSpec, settings: LspSettings
) -> tuple[str, ...] | None:
    for key in (server.id, *server.languages):
        override = settings.servers.get(key)
        if override and override.command:
            return tuple(override.command) + tuple(override.args)
    return None


def resolve_command(
    server_id: str,
    *,
    project_root: Path | None = None,
    settings: LspSettings | None = None,
) -> tuple[str, ...] | None:
    """Return a usable argv for the server, or None when it is not installed."""
    server = SERVERS.get(server_id)
    if server is None:
        return None
    settings = settings or load_lsp_settings()
    override = _override_for(server, settings)
    if override is not None:
        return override if _available(override) else None
    cached = _INSTALLED.get(server_id)
    if cached and _available(cached):
        return cached

    if server_id == "pyright":
        candidates = [
            (_local_npm_bin("pyright-langserver"), "--stdio"),
            (which("pyright-langserver"), "--stdio"),
        ]
    elif server_id == "typescript":
        candidates = [
            (_local_npm_bin("typescript-language-server"), "--stdio"),
            (which("typescript-language-server"), "--stdio"),
        ]
    elif server_id == "jdtls":
        command = _jdtls_command(project_root)
        return command if command and _available(command) else None
    elif server_id == "csharp-ls":
        candidates = [
            (
                _path_if_file(
                    lsp_root() / "dotnet" / "csharp-ls",
                    lsp_root() / "dotnet" / "csharp-ls.exe",
                ),
            ),
            (which("csharp-ls"),),
        ]
    elif server_id == "clangd":
        candidates = [
            (
                _path_if_file(
                    lsp_root() / "py" / "bin" / "clangd",
                    lsp_root() / "py" / "Scripts" / "clangd.exe",
                ),
            ),
            (which("clangd"),),
        ]
    elif server_id == "sql-language-server":
        candidates = [
            (_local_npm_bin("sql-language-server"), "up", "--method", "stdio"),
            (which("sql-language-server"), "up", "--method", "stdio"),
        ]
    elif server_id == "gopls":
        candidates = [
            (
                _path_if_file(
                    lsp_root() / "go" / "bin" / "gopls",
                    lsp_root() / "go" / "bin" / "gopls.exe",
                ),
            ),
            (which("gopls"),),
        ]
    elif server_id == "intelephense":
        candidates = [
            (_local_npm_bin("intelephense"), "--stdio"),
            (which("intelephense"), "--stdio"),
        ]
    elif server_id == "rust-analyzer":
        candidate = which("rust-analyzer")
        if not candidate:
            candidate = _path_if_file(
                *sorted(
                    (Path.home() / ".rustup" / "toolchains").glob(
                        "*/bin/rust-analyzer*"
                    )
                )
            )
        candidates = [(candidate,)]
    else:
        return None

    for command in candidates:
        argv = tuple(part for part in command if part)
        if argv and _available(argv):
            return argv
    return None


def status(
    *,
    project_root: Path | None = None,
    settings: LspSettings | None = None,
) -> list[dict]:
    """Availability report; never installs anything."""
    settings = settings or load_lsp_settings()
    result: list[dict] = []
    for server in SERVERS.values():
        command = resolve_command(
            server.id, project_root=project_root, settings=settings
        )
        result.append(
            {
                "id": server.id,
                "name": server.name,
                "languages": list(server.languages),
                "available": command is not None,
                "command": list(command or ()),
                "install_hint": server.install_hint,
            }
        )
    return result


def _install_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = os.environ.copy()
    path_parts = [str(part) for part in _common_bins()]
    path_parts.append(env.get("PATH", ""))
    env["PATH"] = os.pathsep.join(part for part in path_parts if part)
    if extra:
        env.update(extra)
    return env


async def _run(
    argv: list[str], *, timeout: int, env_extra: dict[str, str] | None = None
) -> tuple[int, str, str]:
    try:
        outcome = await run_process(
            argv,
            cwd=lsp_root(),
            env=_install_env(env_extra),
            timeout_s=timeout,
        )
    except (OSError, ValueError) as exc:
        return -1, "", str(exc)
    return (
        outcome.exit_code,
        outcome.stdout.decode(errors="replace"),
        outcome.stderr.decode(errors="replace"),
    )


def _fail(
    server: ServerSpec,
    argv: list[str],
    exit_code: int,
    stdout: str,
    stderr: str,
    *,
    hint: str = "",
) -> LspInstallError:
    detail = (stderr or stdout or "sin salida").strip()[-2000:]
    return LspInstallError(
        "lsp_install_failed",
        f"no se pudo instalar {server.name}: {detail}",
        command=tuple(argv),
        stdout=stdout,
        stderr=stderr,
        hint=hint or server.install_hint,
    )


def _toolchain(server: ServerSpec, message: str) -> LspInstallError:
    return LspInstallError(
        "toolchain_missing",
        message,
        hint=server.install_hint,
    )


def _node_version() -> tuple[int, int] | None:
    node = which("node")
    if not node:
        return None
    try:
        proc = subprocess.run(
            [node, "--version"], capture_output=True, text=True, timeout=5
        )
    except (OSError, subprocess.SubprocessError):
        return None
    text = (proc.stdout or proc.stderr or "").strip().lstrip("v")
    parts = text.split(".")
    try:
        return int(parts[0]), int(parts[1])
    except (IndexError, ValueError):
        return None


def _java_version() -> int | None:
    java = which("java")
    if not java:
        return None
    try:
        proc = subprocess.run(
            [java, "-version"], capture_output=True, text=True, timeout=5
        )
    except (OSError, subprocess.SubprocessError):
        return None
    text = (proc.stderr or "") + (proc.stdout or "")
    import re

    match = re.search(r'version "(\d+)', text)
    return int(match.group(1)) if match else None


def _dotnet_version() -> int | None:
    dotnet = which("dotnet")
    if not dotnet:
        return None
    try:
        proc = subprocess.run(
            [dotnet, "--version"], capture_output=True, text=True, timeout=5
        )
    except (OSError, subprocess.SubprocessError):
        return None
    text = (proc.stdout or "").strip()
    try:
        return int(text.split(".")[0])
    except (IndexError, ValueError):
        return None


async def _install_npm(server: ServerSpec, timeout: int) -> InstallResult:
    npm = which("npm")
    if not npm:
        raise _toolchain(server, "falta npm; instala Node.js 20+")
    packages = _NPM_PACKAGES.get(server.id)
    if not packages:
        raise LspInstallError(
            "lsp_install_failed", f"no hay receta npm para {server.id}"
        )
    if server.id == "sql-language-server":
        version = _node_version()
        if version is not None and version < (22, 13):
            raise _toolchain(
                server,
                "sql-language-server requiere Node.js 22.13+ (SQLite); "
                "actualiza Node o usa diagnósticos SQL sin servidor",
            )
    prefix = lsp_root() / "npm"
    prefix.mkdir(parents=True, exist_ok=True)
    argv = [
        npm,
        "install",
        "--prefix",
        str(prefix),
        "--no-audit",
        "--no-fund",
        "--loglevel=error",
        *packages,
    ]
    code, stdout, stderr = await _run(argv, timeout=timeout)
    if code != 0:
        raise _fail(server, argv, code, stdout, stderr)
    return InstallResult(ok=True, stdout=stdout, stderr=stderr)


async def _install_pip(server: ServerSpec, timeout: int) -> InstallResult:
    target = lsp_root() / "py"
    target.mkdir(parents=True, exist_ok=True)
    argv = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--target",
        str(target),
        "--upgrade",
        "--disable-pip-version-check",
        "clangd",
    ]
    code, stdout, stderr = await _run(argv, timeout=timeout)
    if code != 0:
        raise _fail(
            server,
            argv,
            code,
            stdout,
            stderr,
            hint="fallback: instala clangd con el gestor de paquetes del sistema",
        )
    return InstallResult(ok=True, stdout=stdout, stderr=stderr)


async def _install_go(server: ServerSpec, timeout: int) -> InstallResult:
    go = which("go")
    if not go:
        raise _toolchain(server, "falta Go; instala Go y pon 'go' en el PATH")
    gobin = lsp_root() / "go" / "bin"
    gobin.mkdir(parents=True, exist_ok=True)
    argv = [go, "install", "golang.org/x/tools/gopls@latest"]
    code, stdout, stderr = await _run(
        argv, timeout=timeout, env_extra={"GOBIN": str(gobin), "GO111MODULE": "on"}
    )
    if code != 0:
        raise _fail(server, argv, code, stdout, stderr)
    return InstallResult(ok=True, stdout=stdout, stderr=stderr)


async def _install_dotnet(server: ServerSpec, timeout: int) -> InstallResult:
    dotnet = which("dotnet")
    if not dotnet:
        raise _toolchain(server, "falta dotnet; instala el SDK de .NET 10")
    version = _dotnet_version()
    if version is not None and version < 10:
        raise _toolchain(
            server,
            f"csharp-ls requiere .NET 10 SDK (detectado .NET {version})",
        )
    tool_path = lsp_root() / "dotnet"
    tool_path.mkdir(parents=True, exist_ok=True)
    argv = [
        dotnet,
        "tool",
        "install",
        "--tool-path",
        str(tool_path),
        "csharp-ls",
    ]
    code, stdout, stderr = await _run(argv, timeout=timeout)
    if code != 0:
        raise _fail(server, argv, code, stdout, stderr)
    return InstallResult(ok=True, stdout=stdout, stderr=stderr)


async def _install_rustup(server: ServerSpec, timeout: int) -> InstallResult:
    rustup = which("rustup")
    if not rustup:
        raise _toolchain(server, "falta rustup; instala Rust desde rustup.rs")
    argv = [rustup, "component", "add", "rust-analyzer"]
    code, stdout, stderr = await _run(argv, timeout=timeout)
    if code != 0:
        raise _fail(server, argv, code, stdout, stderr)
    query = [rustup, "which", "rust-analyzer"]
    qcode, qout, qerr = await _run(query, timeout=30)
    if qcode == 0:
        path = qout.strip().splitlines()[-1].strip() if qout.strip() else ""
        if path and Path(path).is_file():
            _INSTALLED["rust-analyzer"] = (path,)
    return InstallResult(ok=True, stdout=stdout, stderr=stderr)


def _unsafe_jdt_member(
    member: tarfile.TarInfo, reason: str = ""
) -> LspInstallError:
    suffix = f" ({reason})" if reason else ""
    return LspInstallError(
        "lsp_install_failed",
        f"archivo JDT inseguro{suffix}: {member.name}",
        hint="descarga manualmente Eclipse JDT LS",
    )


def _jdt_member_target(
    member: tarfile.TarInfo, destination: Path
) -> Path | None:
    """Valida un miembro y devuelve su destino, o ``None`` para la raíz."""
    name = str(member.name)
    if "\x00" in name:
        raise _unsafe_jdt_member(member, "NUL")
    normalized = name.replace("\\", "/")
    windows_path = PureWindowsPath(normalized)
    posix_path = PurePosixPath(normalized)
    if (
        windows_path.drive
        or windows_path.is_absolute()
        or posix_path.is_absolute()
    ):
        raise _unsafe_jdt_member(member, "ruta absoluta")
    parts = [part for part in normalized.split("/") if part not in ("", ".")]
    if ".." in parts:
        raise _unsafe_jdt_member(member, "traversal")
    if member.issym() or member.islnk():
        raise _unsafe_jdt_member(member, "enlace")
    if not (member.isdir() or member.isfile()):
        raise _unsafe_jdt_member(member, "tipo especial")
    if not parts:
        if member.isdir():
            return None
        raise _unsafe_jdt_member(member, "nombre vacío")
    target = destination.joinpath(*parts)
    try:
        target.relative_to(destination)
    except ValueError:  # pragma: no cover — partes ya validadas
        raise _unsafe_jdt_member(member, "fuera del destino") from None
    return target


def _set_member_mode(path: Path, mode: int, *, is_dir: bool) -> None:
    """Aplica permisos sin setuid/setgid ni escritura de grupo/otros."""
    safe_mode = mode & 0o755
    safe_mode |= 0o700 if is_dir else 0o600
    try:
        path.chmod(safe_mode)
    except OSError:
        pass


def _safe_extract(archive: Path, destination: Path) -> None:
    """Extrae un tar.gz sin enlaces, rutas absolutas ni ``..``.

    No usa ``extractall``: en Python 3.11 el filtro ``data`` no está garantizado
    y el fallback anterior era inseguro. La extracción manual solo crea
    directorios y archivos regulares bajo ``destination``.
    """
    destination = destination.resolve()
    try:
        with tarfile.open(archive, "r:gz") as tar:
            members: list[tuple[tarfile.TarInfo, Path]] = []
            for member in tar.getmembers():
                target = _jdt_member_target(member, destination)
                if target is not None:
                    members.append((member, target))
            destination.mkdir(parents=True, exist_ok=True)
            for member, target in members:
                try:
                    if target.is_symlink():
                        raise _unsafe_jdt_member(member, "destino symlink")
                    if member.isdir():
                        target.parent.resolve().relative_to(destination)
                        target.mkdir(parents=True, exist_ok=True)
                        _set_member_mode(target, member.mode, is_dir=True)
                        continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    # Verifica que ningún componente preexistente escape del
                    # destino mediante un symlink.
                    target.parent.resolve().relative_to(destination)
                    source = tar.extractfile(member)
                    if source is None:
                        raise _unsafe_jdt_member(member, "sin contenido")
                    with source, target.open("wb") as handle:
                        shutil.copyfileobj(source, handle)
                    _set_member_mode(target, member.mode, is_dir=False)
                except LspInstallError:
                    raise
                except (OSError, ValueError, tarfile.TarError) as exc:
                    raise LspInstallError(
                        "lsp_install_failed",
                        f"no se pudo extraer JDT LS: {exc}",
                        hint="descarga manualmente Eclipse JDT LS",
                    ) from exc
    except LspInstallError:
        raise
    except (OSError, tarfile.TarError) as exc:
        raise LspInstallError(
            "lsp_install_failed",
            f"no se pudo abrir o extraer JDT LS: {exc}",
            hint="descarga manualmente Eclipse JDT LS",
        ) from exc


async def _install_jdtls(server: ServerSpec, timeout: int) -> InstallResult:
    version = _java_version()
    if version is None:
        raise _toolchain(server, "falta Java 21; instala un JDK 21+")
    if version < 21:
        raise _toolchain(
            server, f"Eclipse JDT LS requiere Java 21+ (detectado Java {version})"
        )
    destination = lsp_root() / "jdtls"
    _, wrapper, _ = _jdtls_paths()
    if wrapper.is_file():
        return InstallResult(ok=True, path=str(destination))
    destination.parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="satellite-jdtls-", dir=str(lsp_root())))
    archive = work / "jdtls.tar.gz"
    extracted = work / "extracted"
    try:
        total = 0
        async with httpx.AsyncClient(
            follow_redirects=True, timeout=httpx.Timeout(timeout)
        ) as client:
            async with client.stream("GET", JDT_URL) as response:
                if response.status_code != 200:
                    raise LspInstallError(
                        "lsp_install_failed",
                        f"JDT LS respondió HTTP {response.status_code}",
                        command=(JDT_URL,),
                        hint=server.install_hint,
                    )
                with archive.open("wb") as handle:
                    async for chunk in response.aiter_bytes():
                        total += len(chunk)
                        if total > MAX_JDT_BYTES:
                            raise LspInstallError(
                                "lsp_install_failed",
                                "el paquete de JDT LS supera 200 MB",
                                command=(JDT_URL,),
                                hint=server.install_hint,
                            )
                        handle.write(chunk)
        extracted.mkdir()
        await asyncio.to_thread(_safe_extract, archive, extracted)
        entries = list(extracted.iterdir())
        source = (
            entries[0]
            if len(entries) == 1 and entries[0].is_dir()
            else extracted
        )
        if destination.exists():
            shutil.rmtree(destination)
        shutil.move(str(source), str(destination))
        return InstallResult(ok=True, path=str(destination))
    finally:
        shutil.rmtree(work, ignore_errors=True)


async def install_server(
    server_id: str,
    *,
    settings: LspSettings | None = None,
    project_root: Path | None = None,
) -> InstallResult:
    """Install one server with a fixed recipe and return its executable."""
    server = SERVERS.get(server_id)
    if server is None:
        raise LspInstallError(
            "lsp_unsupported_language", f"servidor desconocido: {server_id}"
        )
    settings = settings or load_lsp_settings()
    lsp_root().mkdir(parents=True, exist_ok=True)
    existing = resolve_command(
        server_id, project_root=project_root, settings=settings
    )
    if existing is not None:
        return InstallResult(ok=True, command=existing)
    if _override_for(server, settings) is not None:
        raise LspInstallError(
            "lsp_server_unavailable",
            f"el comando configurado para {server.name} no existe",
            hint="corrige ~/.satellite/lsp.json o borra esa entrada",
        )
    timeout = settings.install_timeout_s
    if server_id in _NPM_PACKAGES:
        result = await _install_npm(server, timeout)
    elif server_id == "clangd":
        result = await _install_pip(server, timeout)
    elif server_id == "gopls":
        result = await _install_go(server, timeout)
    elif server_id == "csharp-ls":
        result = await _install_dotnet(server, timeout)
    elif server_id == "rust-analyzer":
        result = await _install_rustup(server, timeout)
    elif server_id == "jdtls":
        result = await _install_jdtls(server, timeout)
    else:
        raise LspInstallError(
            "lsp_install_failed", f"no hay receta para {server_id}"
        )
    command = resolve_command(
        server_id, project_root=project_root, settings=settings
    )
    if command is None:
        raise LspInstallError(
            "lsp_install_failed",
            f"{server.name} se instaló pero no se encontró el ejecutable",
            stdout=result.stdout,
            stderr=result.stderr,
            hint=server.install_hint,
        )
    _INSTALLED[server_id] = command
    return InstallResult(
        ok=True,
        command=command,
        stdout=result.stdout,
        stderr=result.stderr,
        path=result.path,
    )
