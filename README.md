# Satellite

**A declarative microagent framework for software work — Python edition.**

Satellite turns a repository into an LLM-orchestrated workspace: you describe a
goal in plain language and it plans and executes it over your project, using
reusable agents and skills. It ships as a **CLI**, an interactive **TUI** and an
**MCP** server, plus an optional Electron desktop app for development.

## What it does

- Turns a natural-language goal into a plan and executes it against your project:
  reading and editing files, running commands and running tests.
- Helps with everyday software tasks — add a feature, fix a bug, refactor code,
  write or update tests, explore and document a codebase.
- Ships three base agents at install: `orchestrator` (goal → plan over the
  existing catalog), `agent_creator` (proposes one reusable agent for a need,
  with human approval) and `skill_curator` (maintains the skill catalog).
- Extends declaratively: a new agent is a `spec.json` plus prompts; a new skill
  is a script plus a `manifest.json`.
- Keeps you in control — operations outside the project and commands that are not
  on the allowlist are asked before they run, and a sandbox level selects process
  isolation (default) or Docker confinement.

## Quick start

```bash
# 1. Dependencies (Python 3.11+)
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt

# 2. Configure a provider (guided: pick a provider, paste your API key;
#    models are fetched from the endpoint)
python -m satellite.cli provider

# 3. Check everything
python -m satellite.cli doctor

# 4. Work
python -m satellite.cli run "add a health endpoint"
python -m satellite.cli tui
```

## CLI reference

| Command | Description |
|---|---|
| `run <goal>` | Plan and execute a goal with the orchestrator. |
| `plan <task>` | Plan mode: the model proposes several approaches to choose from. |
| `tui` | Interactive chat interface (conversation is persisted per project). |
| `agents [--create <need>]` | List the catalog or create an agent from a capability need. |
| `tools list` | List runtime tools, marking the kernel primitives. |
| `skills list\|search\|edit\|delete` | Manage global skills. |
| `provider [list\|catalog\|models\|add\|use\|roles\|remove]` | Configure providers, models and per-role overrides. |
| `context` | Show how much context is used (bar + percentage) and the window source. |
| `compact` | Compact the saved conversation (summary + last turns). |
| `stats` | Skill and cache metrics, plus context usage. |
| `doctor` | Environment, config, provider, catalog, schema and sandbox diagnostics. |
| `tasks`, `status`, `inspect` | Persisted tasks, their events and their graph. |
| `setup`, `init`, `reset`, `restore` | Sandbox decision, project init, base state and snapshots. |
| `--safe-mode` | Start with only the three base agents and the primitives. |

## Configuration and secrets

Provider definitions and API keys live in the user-level
`~/.satellite/config/providers.json` store and are shared by projects; project
`.satellite/config/config.json` files keep only the selected provider id and role
preferences. Providers are saved by a unique `id`, so several models can share a
family and base URL, and per-role overrides (`llm.orchestrator`, `llm.context`,
`llm.agents`, `llm.creator`) point at a saved `id`.

**Never commit API keys.** Keys can be supplied by a global environment variable
or stored in the user-level provider store; project-level key fields are ignored
by the runtime, and keys are only ever returned as `api_key_set`.

## Desktop app (development)

An Electron/React front end over a local backend that reuses the same runtime as
the CLI and the TUI, on the same `.satellite/` storage.

```bash
.venv\Scripts\pip install -e .[desktop]              # FastAPI + uvicorn
.venv\Scripts\python -m satellite.desktop --project <carpeta>

cd desktop
npm install          # React, Vite and Electron
npm run dev          # Vite dev server + Electron, with hot reload
```

## Tests

```bash
.venv\Scripts\python -m pytest tests/ -q          # unit + contract + integration
.venv\Scripts\python -m pytest tests/contracts -q # interface contracts
```
