# Satellite

**A declarative microagent framework for software work — Python edition.**

Satellite is a Python framework of microagents that turns a repository into an
LLM-orchestrated workspace: describe a goal in plain language and it plans and
executes it over your project using reusable agents and skills.

## What it does

- Works on real software tasks: add features, fix bugs, refactor, write tests,
  explore and document a codebase.
- Ships coder and auditor/test-author agents for Python, JavaScript, TypeScript,
  Java, C#, C++, SQL, Go, PHP and Rust, with LSP intelligence and bounded test
  execution.
- Turns a goal into an executable plan and runs independent steps in parallel.
- Splits large, separable work into parallel subagents when it is worth it.
- Uses reusable agents and skills, and can propose new ones with human approval.
- If no agent covers a request, it tries to create one, asks for approval and
  retries; if that is not possible, it asks how you want to proceed.
- Remembers a compact summary of earlier turns so long sessions stay coherent.
- Searches the internet, reads pages and downloads files.
- Generates Mermaid diagrams and shows them as SVG.
- Can control desktop applications when you enable it, with your approval and an
  app allowlist.
- Protects secrets and personal data before sending content to a provider.
- Lets you choose how permissions are handled: ask, auto or unrestricted.

## Where you can use it

Command line, interactive terminal chat, desktop app and MCP server.

## Requirements

- **Python 3.11+**
- **Node.js 20+** — diagrams, the desktop app and the Python/JS/TS/PHP language
  servers (SQL uses Node 22.13+ for SQLite).
- `pip` and, for the desktop app, `npm`.
- Optional toolchains only for the language agents you use: Java 21 (Java),
  .NET 10 SDK (C#), Go (Go), rustup (Rust), a C++ compiler with clangd (C++).
- Optional per-platform packages for desktop control (see below).

`requirements.txt` contains all Python dependencies for the runtime, the desktop
backend and the tests. `pyproject.toml` is the source of versions and extras.

## Install

### Linux / macOS

```bash
git clone git@github.com:Lakypayiui/Satellite.git
cd Satellite

python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev,desktop]'

.venv/bin/python -m satellite.cli provider   # configure provider + API key
.venv/bin/python -m satellite.cli doctor     # check everything
```

Alternatively, install only the declared dependencies:

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

Roles: `orchestrator`, `context`, `agents`, `creator` and `subagents`. If a role
is not set, it inherits the active provider.

**Never commit API keys.** Use the provider store or a global environment
variable.

## Platform notes

### Diagrams

- The desktop app installs the Mermaid renderer with `npm install`.
- For the CLI/TUI, install Node.js 20+ and `npm install pele` in the project (or
  `npm install -g pele`), or point Satellite at an existing install with
  `SATELLITE_PELE_MODULE` or `SATELLITE_PELE_PATH`.

### Desktop control (optional)

Enabling the option in Settings is not enough: install the platform backend,
grant the OS permission and add the target apps to the allowlist.

- **macOS** — `pip install -e '.[desktop-control-macos]'` and grant Accessibility
  permission in *System Settings → Privacy & Security → Accessibility*.
- **Windows** — `pip install -e ".[desktop-control]"` (UI Automation).
- **Linux** — `pip install -e '.[desktop-control-linux]'`, install the system
  package for AT-SPI (Debian/Ubuntu: `gir1.2-atspi-2.0`) and keep the desktop
  accessibility bus active.

The local denylist (terminals, password managers, system settings) always wins.

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

## Tests

```bash
.venv/bin/python -m pytest tests/ -q          # Linux / macOS
.venv\Scripts\python -m pytest tests/ -q     # Windows
```

## Language agents

Python, JavaScript, TypeScript, Java, C#, C++, SQL, Go, PHP and Rust each have
two base agents: `<language>_coder` implements and refactors code, and
`<language>_auditor` reviews it, writes tests and runs them.

- **LSP** — diagnostics, definition, references, hover and document/workspace
  symbols. It is read-only and understands the real project structure.
- **Parallel work** — the coder agents can split a large implementation into up
  to four child agents; auditors work on their own.
- **Bounded execution** — `code_task` runs code and the project's test suite
  through fixed profiles (pytest, npm test, go test, cargo test, dotnet test,
  Maven/Gradle, CMake/CTest, PHPUnit). It accepts no arbitrary command, shell or
  arguments, and a failing test suite is reported as a result, not as a tool
  error.
- **Automatic servers** — Satellite uses a server already on `PATH`; if it is
  missing, it installs a local copy under `~/.satellite/lsp` on first use. It
  never uses `sudo` or changes global packages. Disable this with
  `{"auto_install": false}` in `~/.satellite/lsp.json`.
- **SQL** is diagnostics and lint only; it never executes queries against a
  database.
- Language runtimes are not installed automatically: a missing Java, .NET, Go or
  Rust toolchain is reported with the command to install it.

## Base agents

- `orchestrator` — turns a goal into a plan using the available catalog.
- `agent_creator` — proposes a reusable agent for a capability you are missing.
- `skill_curator` — keeps the skill catalog organized.
- `pele_diagram_agent` — designs and renders diagrams.
- `desktop_operator` — opens apps and operates desktop windows.
- `web_researcher` — searches the web, reads pages and downloads files.
- `<language>_coder` / `<language>_auditor` — the ten language pairs above.
