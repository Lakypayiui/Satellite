"""Built-in language server definitions and project-root markers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ServerSpec:
    id: str
    name: str
    languages: tuple[str, ...]
    root_markers: tuple[str, ...]
    install_hint: str


SERVERS: dict[str, ServerSpec] = {
    "pyright": ServerSpec(
        "pyright",
        "Pyright",
        ("python",),
        ("pyproject.toml", "setup.py", "setup.cfg", "requirements.txt"),
        "npm install --prefix ~/.satellite/lsp/npm pyright (requires Node.js 20+)",
    ),
    "typescript": ServerSpec(
        "typescript",
        "TypeScript Language Server",
        ("javascript", "typescript"),
        ("package.json", "tsconfig.json", "jsconfig.json"),
        "npm install --prefix ~/.satellite/lsp/npm typescript typescript-language-server",
    ),
    "jdtls": ServerSpec(
        "jdtls",
        "Eclipse JDT Language Server",
        ("java",),
        ("pom.xml", "build.gradle", "build.gradle.kts", "settings.gradle"),
        "Satellite downloads Eclipse JDT LS into ~/.satellite/lsp/jdtls (requires Java 21)",
    ),
    "csharp-ls": ServerSpec(
        "csharp-ls",
        "csharp-ls",
        ("csharp",),
        ("*.sln", "*.csproj", "global.json"),
        "dotnet tool install --tool-path ~/.satellite/lsp/dotnet csharp-ls "
        "(requires .NET 10 SDK)",
    ),
    "clangd": ServerSpec(
        "clangd",
        "clangd",
        ("cpp",),
        ("compile_commands.json", "CMakeLists.txt", "Makefile"),
        "python -m pip install --target ~/.satellite/lsp/py clangd",
    ),
    "sql-language-server": ServerSpec(
        "sql-language-server",
        "SQL Language Server",
        ("sql",),
        (),
        "npm install --prefix ~/.satellite/lsp/npm sql-language-server "
        "(Node.js 22.13+ for SQLite)",
    ),
    "gopls": ServerSpec(
        "gopls",
        "gopls",
        ("go",),
        ("go.mod", "go.work"),
        "GOBIN=~/.satellite/lsp/go/bin go install golang.org/x/tools/gopls@latest "
        "(requires Go)",
    ),
    "intelephense": ServerSpec(
        "intelephense",
        "Intelephense",
        ("php",),
        ("composer.json",),
        "npm install --prefix ~/.satellite/lsp/npm intelephense",
    ),
    "rust-analyzer": ServerSpec(
        "rust-analyzer",
        "rust-analyzer",
        ("rust",),
        ("Cargo.toml",),
        "rustup component add rust-analyzer",
    ),
}

SERVER_BY_LANGUAGE: dict[str, str] = {
    language: server.id
    for server in SERVERS.values()
    for language in server.languages
}


def server_for_language(language: str) -> ServerSpec | None:
    server_id = SERVER_BY_LANGUAGE.get(str(language or "").strip().lower())
    return SERVERS.get(server_id) if server_id else None


def server_for_id(server_id: str) -> ServerSpec | None:
    return SERVERS.get(str(server_id or "").strip().lower())


def project_root_for(
    server: ServerSpec, file_path: Path | None, project_root: Path
) -> Path:
    """Nearest ancestor with a root marker, never above ``project_root``."""
    root = Path(project_root).resolve()
    if file_path is None or not server.root_markers:
        return root
    current = Path(file_path).resolve().parent
    if current != root and root not in current.parents:
        current = root
    while True:
        for marker in server.root_markers:
            try:
                if any(character in marker for character in "*?["):
                    if any(current.glob(marker)):
                        return current
                elif (current / marker).exists():
                    return current
            except OSError:
                continue
        if current == root or current.parent == current:
            return root
        current = current.parent
