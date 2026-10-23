<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="desktop/src/assets/satellite-white.png">
    <img src="desktop/src/assets/satellite-black.png" alt="Satellite" width="128">
  </picture>
</p>

<h1 align="center">Satellite</h1>

<h3 align="center">An LLM-orchestrated workspace for software work — Python edition.</h3>

<p align="center">
  <img alt="Python 3.11+" src="https://img.shields.io/badge/Python-3.11%2B-3776AB?style=for-the-badge&amp;logo=python&amp;logoColor=white">
  <img alt="macOS, Windows and Linux" src="https://img.shields.io/badge/platform-macOS%20%7C%20Windows%20%7C%20Linux-4B5563?style=for-the-badge">
  <img alt="MCP" src="https://img.shields.io/badge/MCP-server-0F766E?style=for-the-badge">
  <img alt="License MIT" src="https://img.shields.io/badge/license-MIT-green?style=for-the-badge">
</p>

<p align="center">
  Describe a goal in plain language and Satellite carries it out over your repository:
  one grounded agent reads the real code, answers, and delegates the work — changes,
  tests, research, diagrams — to a catalog of specialized agents.
</p>

## What it does

| Capability | What you get |
|---|---|
| **Real software work** | Add features, fix bugs, refactor, write tests, explore and document a codebase. |
| **Ten language pairs** | A coder and an auditor/test-author for Python, JavaScript, TypeScript, Java, C#, C++, SQL, Go, PHP and Rust, each backed by its language server (LSP). |
| **Grounded, never blind** | The pilot reads the project before acting; it asks with options when a request is ambiguous instead of guessing. |
| **Runs work in parallel** | Independent parts of a task run at the same time (see below). |
| **Self-extending** | If no agent covers a request it proposes a new one, with your approval, and retries. |
| **Four surfaces, one install** | Desktop app, CLI, interactive terminal (TUI) and MCP server. |
| **Safe by default** | Secrets are protected before anything reaches a provider; permissions are `ask`, `auto` or `unrestricted`. |

## What makes it different

Most "agent + tools" setups run one conversation that calls tools in a line. Satellite's
identity is the **execution graph**.

After the pilot has read the real code, if a task splits into **three or more parts that
can genuinely run at the same time** — or a real dependency structure, such as a review
step that must wait for several coders — it emits a graph of delegations instead of doing
them one by one. The graph runs in **parallel waves**, with **verification gates** (a node
can run only if the ones it depends on succeeded) and **resumption** (if a turn is cut off,
the finished nodes are not redone). For smaller work it simply delegates directly.

The graph is **opt-in by shape**: it is used only when there is enough parallel work to be
worth its overhead, so small tasks stay simple and cheap while large ones fan out. That
grounded, adaptive graph — not a static planner — is what separates Satellite from a plain
tool-calling agent.

## Agents

**Base agents**

| Agent | Role |
|---|---|
| `pilot` | Drives each turn: reads the project, answers, asks and delegates. |
| `agent_creator` | Proposes a reusable agent for a capability you are missing. |
| `skill_curator` | Keeps the skill catalog organized. |
| `pele_diagram_agent` | Designs and renders Mermaid diagrams as SVG. |
| `web_researcher` | Searches the web, reads pages and downloads files. |
| `desktop_operator` | Opens apps and operates desktop windows (when enabled). |

**Language pairs** — `python`, `javascript`, `typescript`, `java`, `csharp`, `cpp`, `sql`,
`go`, `php`, `rust`, each as `<language>_coder` and `<language>_auditor`. Coders can split a
large implementation across up to four child agents; auditors write and run tests through
fixed profiles (pytest, npm test, go test, cargo test, dotnet test, Maven/Gradle, CMake/CTest,
PHPUnit). SQL is diagnostics and lint only — it never runs queries against a database.

## Install the app

Download the package for your system from the
[Releases](https://github.com/Lakypayiui/Satellite/releases) page:

| System | Package |
|---|---|
| macOS (Apple Silicon / Intel) | `Satellite-<version>-mac-arm64.dmg` / `-mac-x64.dmg` |
| Windows | `Satellite-<version>-win-x64.exe` (installer) |
| Linux | `Satellite-<version>-linux-x64.AppImage` or `-linux-x64.tar.gz` |

One install gives you everything — desktop app, CLI, TUI and MCP server — with Python
bundled. To use it from a terminal, open **Settings → Maintenance → Terminal → Install
satellite command**, then:

```bash
satellite tui          # interactive terminal UI
satellite run "..."    # one task from the CLI
satellite mcp          # MCP server over stdio (Claude Desktop, IDEs)
```

Packages are not signed yet: on macOS open the app the first time with right-click → Open;
on Windows choose "More info → Run anyway" in SmartScreen.

## Build the packages yourself

Each OS builds its own installer. On the target system:

```bash
pip install ".[desktop]" pyinstaller
cd desktop
npm install
npm run dist          # → .dmg (macOS), .exe (Windows), .AppImage/.tar.gz (Linux)
```

Pushing a `v*` tag builds all three in GitHub Actions and attaches them to a draft release.

## Run from source

Requirements: **Python 3.11+** and, for the desktop app and diagrams, **Node.js 20+**.
Language agents also need their toolchain installed (Java 21, .NET 10, Go, rustup, a C++
compiler); a missing one is reported with the command to install it.

### Linux / macOS

```bash
git clone git@github.com:Lakypayiui/Satellite.git
cd Satellite

python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev,desktop]'

.venv/bin/python -m satellite.cli provider   # configure provider + API key
.venv/bin/python -m satellite.cli doctor      # check everything

.venv/bin/python -m satellite.cli run "add a health endpoint"
.venv/bin/python -m satellite.cli tui
.venv/bin/python -m satellite.mcp
.venv/bin/python -m satellite.desktop --project <folder>
```

### Windows (PowerShell)

```powershell
git clone git@github.com:Lakypayiui/Satellite.git
cd Satellite

py -3.11 -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev,desktop]"

.venv\Scripts\python -m satellite.cli provider
.venv\Scripts\python -m satellite.cli doctor

.venv\Scripts\python -m satellite.cli run "add a health endpoint"
.venv\Scripts\python -m satellite.cli tui
.venv\Scripts\python -m satellite.mcp
.venv\Scripts\python -m satellite.desktop --project <folder>
```

The desktop backend starts automatically; for live UI development run `npm install && npm
run dev` from `desktop/`.

> **Never commit API keys.** They live in the user-level provider store (`~/.satellite`).

## License

MIT — see [LICENSE](LICENSE).
