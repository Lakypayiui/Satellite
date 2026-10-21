<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="desktop/src/assets/satellite-white.png">
    <img src="desktop/src/assets/satellite-black.png" alt="Satellite" width="128">
  </picture>
</p>

<h1 align="center">Satellite</h1>

<h3 align="center">A declarative microagent framework for software work — Python edition.</h3>

<p align="center">
  <img alt="Python 3.11+" src="https://img.shields.io/badge/Python-3.11%2B-3776AB?style=for-the-badge&amp;logo=python&amp;logoColor=white">
  <img alt="macOS, Windows and Linux" src="https://img.shields.io/badge/platform-macOS%20%7C%20Windows%20%7C%20Linux-4B5563?style=for-the-badge">
  <img alt="License MIT" src="https://img.shields.io/badge/license-MIT-green?style=for-the-badge">
  <img alt="LSP" src="https://img.shields.io/badge/LSP-enabled-6D28D9?style=for-the-badge">
  <img alt="MCP" src="https://img.shields.io/badge/MCP-server-0F766E?style=for-the-badge">
</p>

<p align="center">
  Satellite turns a repository into an LLM-orchestrated workspace: describe a goal
  in plain language and it plans and executes it over your project using reusable
  agents and skills.
</p>

## What it does

| Capability | What you get |
|---|---|
| **Real software work** | Add features, fix bugs, refactor, write tests, explore and document a codebase. |
| **Ten language pairs** | Coder and auditor/test-author agents for Python, JavaScript, TypeScript, Java, C#, C++, SQL, Go, PHP and Rust. |
| **Plan and parallelize** | Turns a goal into an executable plan and runs independent steps at the same time. |
| **Subagents** | Splits large, separable work into up to four child agents when it is worth it. |
| **Reusable agents and skills** | Uses the catalog and can propose new agents or skills with human approval. |
| **Never a dead end** | If no agent covers a request, it tries to create one, asks for approval and retries; if that is impossible, it asks how to proceed. |
| **Persistent memory** | Keeps a compact summary of earlier turns so long sessions stay coherent. |
| **Internet access** | Searches the web, reads pages and downloads files into the project. |
| **Diagrams** | Designs Mermaid diagrams and renders them as SVG inside the app, with zoom and pan. |
| **Desktop control** | Opens apps and operates desktop windows when you enable it, with approval and an app allowlist. |
| **Secret protection** | Protects secrets and personal data before sending content to a provider. |
| **Permission modes** | Choose how permissions are handled: `ask`, `auto` or `unrestricted`. |

## Where you can use it

| Surface | Entry point |
|---|---|
| Command line | `python -m satellite.cli run "add a health endpoint"` |
| Interactive terminal | `python -m satellite.cli tui` |
| Desktop app | Satellite desktop app over the local backend |
| MCP server | `python -m satellite.mcp` |

## Quick start

### Linux / macOS

```bash
git clone git@github.com:Lakypayiui/Satellite.git
cd Satellite

python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev,desktop]'

.venv/bin/python -m satellite.cli provider   # configure provider + API key
.venv/bin/python -m satellite.cli doctor     # check everything
```

If you only need the declared dependencies:

```bash
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip install -e .
```

### Windows (PowerShell)

```powershell
git clone git@github.com:Lakypayiui/Satellite.git
cd Satellite

py -3.11 -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev,desktop]"

.venv\Scripts\python -m satellite.cli provider
.venv\Scripts\python -m satellite.cli doctor
```

Or with the requirements file:

```powershell
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m pip install -e .
```

### Run

```bash
# Linux / macOS
.venv/bin/python -m satellite.cli run "add a health endpoint"
.venv/bin/python -m satellite.cli tui
.venv/bin/python -m satellite.mcp

# Windows
.venv\Scripts\python -m satellite.cli run "add a health endpoint"
.venv\Scripts\python -m satellite.cli tui
.venv\Scripts\python -m satellite.mcp
```

## Requirements

| Requirement | Needed for |
|---|---|
| **Python 3.11+** | Runtime, CLI, TUI and tests. |
| **Node.js 20+** | Diagrams, the desktop app and the Python/JS/TS/PHP language servers. SQL uses Node 22.13+ for SQLite. |
| **pip / npm** | Python dependencies and the desktop app. |
| **Java 21** | Java language server and coder/auditor agents. |
| **.NET 10 SDK** | C# language server and coder/auditor agents. |
| **Go / rustup** | Go and Rust language servers and agents. |
| **C++ compiler + clangd** | C++ language server and coder/auditor agents. |
| **Optional desktop packages** | Desktop control on macOS, Windows or Linux (see below). |

`requirements.txt` contains all Python dependencies for the runtime, the desktop
backend and the tests. `pyproject.toml` is the source of versions and extras.

## Configure

Providers and API keys are stored in the user-level
`~/.satellite/config/providers.json` and shared by projects. Each project keeps
only the active provider and its role preferences in
`.satellite/config/config.json`.

```bash
python -m satellite.cli provider            # add/choose a provider
python -m satellite.cli provider roles      # per-role provider/model
python -m satellite.cli doctor              # verify config and credentials
```

| Role | Used by |
|---|---|
| `orchestrator` | Planning and routing. |
| `context` | Turn summaries, compaction and context work. |
| `agents` | Executing agents and language coders/auditors. |
| `creator` | Creating new agents. |
| `subagents` | Spawned child agents. |

If a role is not set, it inherits the active provider.

> **Never commit API keys.** Use the provider store or a global environment variable.

## Language agents

| Language | Coder | Auditor / test author |
|---|---|---|
| Python | `python_coder` | `python_auditor` |
| JavaScript | `javascript_coder` | `javascript_auditor` |
| TypeScript | `typescript_coder` | `typescript_auditor` |
| Java | `java_coder` | `java_auditor` |
| C# | `csharp_coder` | `csharp_auditor` |
| C++ | `cpp_coder` | `cpp_auditor` |
| SQL | `sql_coder` | `sql_auditor` |
| Go | `go_coder` | `go_auditor` |
| PHP | `php_coder` | `php_auditor` |
| Rust | `rust_coder` | `rust_auditor` |

- **LSP** — diagnostics, definition, references, hover and document/workspace
  symbols. It is read-only and understands the real project structure.
- **Parallel work** — coder agents can split a large implementation into up to
  four child agents; auditors work on their own.
- **Bounded execution** — `code_task` runs code and the project's test suite
  through fixed profiles (pytest, npm test, go test, cargo test, dotnet test,
  Maven/Gradle, CMake/CTest, PHPUnit). It accepts no arbitrary command, shell or
  arguments, and a failing test suite is reported as a result, not as a tool error.
- **Automatic servers** — Satellite uses a server already on `PATH`; if it is
  missing, it installs a local copy under `~/.satellite/lsp` on first use. It never
  uses `sudo` or changes global packages. Disable this with
  `{"auto_install": false}` in `~/.satellite/lsp.json`.
- **SQL** is diagnostics and lint only; it never executes queries against a
  database.
- Language runtimes are not installed automatically: a missing Java, .NET, Go or
  Rust toolchain is reported with the command to install it.

## Desktop app

```bash
# Linux / macOS
.venv/bin/python -m pip install -e '.[desktop]'
.venv/bin/python -m satellite.desktop --project <folder>

# Windows
.venv\Scripts\python -m pip install -e ".[desktop]"
.venv\Scripts\python -m satellite.desktop --project <folder>
```

Then, from the `desktop/` directory:

```bash
npm install
npm run dev
```

The app launches its own local backend; you can also start the backend manually
with `python -m satellite.desktop`.

## Platform notes

### Diagrams

- The desktop app installs the Mermaid renderer with `npm install`.
- For the CLI/TUI, install Node.js 20+ and `npm install pele` in the project (or
  `npm install -g pele`), or point Satellite at an existing install with
  `SATELLITE_PELE_MODULE` or `SATELLITE_PELE_PATH`.

### Desktop control (optional)

Enabling the option in Settings is not enough: install the platform backend,
grant the OS permission and add the target apps to the allowlist.

| Platform | Backend | Permission |
|---|---|---|
| **macOS** | `pip install -e '.[desktop-control-macos]'` | Accessibility in *System Settings → Privacy & Security → Accessibility*. |
| **Windows** | `pip install -e ".[desktop-control]"` | UI Automation; no extra permission. |
| **Linux** | `pip install -e '.[desktop-control-linux]'` | Install AT-SPI (`gir1.2-atspi-2.0` on Debian/Ubuntu) and keep the accessibility bus active. |

The local denylist (terminals, password managers, system settings) always wins.

## Base agents

| Agent | Role |
|---|---|
| `orchestrator` | Turns a goal into a plan using the available catalog. |
| `agent_creator` | Proposes a reusable agent for a capability you are missing. |
| `skill_curator` | Keeps the skill catalog organized. |
| `pele_diagram_agent` | Designs and renders diagrams. |
| `desktop_operator` | Opens apps and operates desktop windows. |
| `web_researcher` | Searches the web, reads pages and downloads files. |
| `<language>_coder` / `<language>_auditor` | The ten language pairs above. |

## Tests

```bash
.venv/bin/python -m pytest tests/ -q          # Linux / macOS
.venv\Scripts\python -m pytest tests/ -q     # Windows
```

## License

MIT — see [LICENSE](LICENSE).
