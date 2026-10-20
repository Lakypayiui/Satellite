# Satellite

**A declarative microagent framework for software work — Python edition.**

Satellite is a Python framework of microagents that turns a repository into an
LLM-orchestrated workspace: describe a goal in plain language and it plans and
executes it over your project using reusable agents and skills.

## What it does

- Works on real software tasks: add features, fix bugs, refactor, write tests,
  explore and document a codebase.
- Turns a goal into an executable plan and runs independent steps in parallel.
- Splits large, separable work into parallel subagents when it is worth it.
- Uses reusable agents and skills, and can propose new ones with human approval.
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
- **Node.js 20+** — only for diagrams and the desktop app.
- `pip` and, for the desktop app, `npm`.
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

## Base agents

- `orchestrator` — turns a goal into a plan using the available catalog.
- `agent_creator` — proposes a reusable agent for a capability you are missing.
- `skill_curator` — keeps the skill catalog organized.
- `pele_diagram_agent` — designs and renders diagrams.
- `desktop_operator` — opens apps and operates desktop windows.
- `web_researcher` — searches the web, reads pages and downloads files.
