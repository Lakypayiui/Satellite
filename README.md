# Satellite

**A declarative microagent framework for software work — Python edition.**

Satellite turns a repository into an LLM-orchestrated workspace: you describe a
goal in plain language and it plans and executes it over your project, using
reusable agents and skills. It ships as a **CLI**, an interactive **TUI**, an
**MCP** stdio server and an optional **Electron desktop app** with an embedded
**Safe Proxy**.

## What it does

- Turns a natural-language goal into an executable plan against your project:
  reading and editing files, running commands and running tests.
- Builds a DAG plan. Independent steps with no data dependency run **in
  parallel**; steps that need another step's output run in order.
- Lets a specialized agent split a large task into **parallel subagents**
  (`spawn_agent`), with hard limits and file ownership (see below).
- Helps with everyday software tasks: add a feature, fix a bug, refactor code,
  write or update tests, explore and document a codebase.
- Ships five base agents at install: `orchestrator` (goal → plan over the
  existing catalog), `agent_creator` (proposes one reusable agent for a need,
  with human approval), `skill_curator` (maintains the skill catalog),
  `pele_diagram_agent` (designs Mermaid diagrams and renders them to SVG) and
  `desktop_operator` (observes and controls desktop windows under the local
  policy; excluded from MCP).
- Extends declaratively: a new agent is a `spec.json` plus prompts; a new skill
  is a script plus a `manifest.json`.
- Keeps you in control: paths outside the project and commands not on the
  allowlist are asked before they run, and the sandbox level selects process
  isolation or Docker confinement.
- Renders generated diagrams in the desktop chat, following the active
  appearance theme, while the saved SVG keeps its default white theme.
- Deletes temporary files safely: atomic writes clean their `.tmp` on success or
  failure, and startup removes orphan `.tmp` files older than one hour.

## Parallel subagents

Splitting is the exception, not the rule. A specialized agent may call
`spawn_agent` only when the task is clearly large and separable; otherwise it
solves the task itself. The orchestrator never calls `spawn_agent`, and a
subagent can never create another agent.

- **Budget:** one parent + up to **4 children** per parent. A task-wide ownership
  registry prevents two children from owning the same file or region.
- **Parallel calls:** calls made in the same assistant turn run concurrently.
  The parent also runs its own tools while the children work, then waits for all
  of them before integrating.
- **Isolation:** each child starts with an empty history. It receives only the
  child system prompt plus its `task`, selected `context`, `files_owned` and
  `files_readonly`; it never receives the parent conversation or tool state.
- **Ownership:** children may write only their owned files or regions. A
  region owner must use focused edits and cannot rewrite the whole file.
- **Restrictions:** children cannot spawn agents, delegate, run commands,
  control the desktop or create skills. The runtime enforces these rules, not
  just the prompt.
- **Result:** each child returns `status`, a summary of at most 5 lines and the
  list of modified files. It returns no code, diff or logs.
- **Failures:** a failed or timed-out child does not cancel its siblings. The
  parent decides whether to retry, do that part itself or report the error.

`spawn_agent` parameters: `task`, `context`, `files_owned`, `files_readonly`.
Use `path` for a whole file or `path::region` for a named function/class/region.

The **subagent model** is configured in *Settings → Roles → Modelo para
subagentes* (`llm.subagents`). If it is not set, children inherit their parent's
provider/model; the parent and orchestrator never change.

## Quick start

Python **3.11+**.

```bash
# Linux / macOS
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev,desktop]'
.venv/bin/python -m satellite.cli provider
.venv/bin/python -m satellite.cli doctor
.venv/bin/python -m satellite.cli run "add a health endpoint"
.venv/bin/python -m satellite.cli tui
```

```powershell
# Windows (PowerShell)
py -3.11 -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev,desktop]"
.venv\Scripts\python -m satellite.cli provider
.venv\Scripts\python -m satellite.cli doctor
.venv\Scripts\python -m satellite.cli run "add a health endpoint"
.venv\Scripts\python -m satellite.cli tui
```

`doctor` checks the config, catalog, tools, schema, provider credentials, roles,
quarantines and sandbox. Run it first when something does not start.

MCP over stdio:

```bash
.venv/bin/python -m satellite.mcp          # Linux / macOS
.venv\Scripts\python -m satellite.mcp     # Windows
```

## CLI reference

| Command | Description |
|---|---|
| `run <goal>` | Plan and execute a goal with the orchestrator. |
| `plan <task>` | Plan mode: the model proposes several approaches to choose from. |
| `tui` | Interactive chat interface (conversation is persisted per project). |
| `agents [--create <need>]` | List the catalog or create an agent from a capability need. |
| `tools list` | List runtime tools, marking kernel primitives and the subagent tool. |
| `skills list\|search\|edit\|delete` | Manage global skills. |
| `provider [list\|catalog\|models\|add\|use\|roles\|remove]` | Configure providers, models and per-role overrides. |
| `context` | Show how much context is used (bar + percentage) and the window source. |
| `compact` | Compact the saved conversation (summary + last turns). |
| `stats` | Skill and cache metrics, plus context usage. |
| `doctor` | Environment, config, provider, catalog, schema and sandbox diagnostics. |
| `tasks`, `status`, `inspect` | Persisted tasks, their events and their graph. |
| `commands` | Shared command/action catalog used by CLI, TUI and desktop. |
| `session`, `git`, `comments`, `queue` | Saved conversations, git/worktrees, inline comments and the prompt queue. |
| `setup`, `init`, `reset`, `restore` | Sandbox decision, project init, base state and snapshots. |
| `--safe-mode` | Start with only the base agents and the primitives. |

## Configuration and secrets

Provider definitions and API keys live in the user-level
`~/.satellite/config/providers.json` store and are shared by projects; project
`.satellite/config/config.json` files keep only the selected provider id and role
preferences. Providers are saved by a unique `id`, so several models can share a
family and base URL.

Roles are configured independently:

- `orchestrator`: planner/orchestrator.
- `context`: context preprocessing and retrieval.
- `agents`: specialized agents and their execution.
- `creator`: agent/skill creation.
- `subagents`: children created with `spawn_agent` (unset = inherit the parent).

**Never commit API keys.** Keys can be supplied by a global environment variable
or stored in the user-level provider store; project-level key fields are ignored
by the runtime, and keys are only ever returned as `api_key_set`.

## Platform notes

- **Linux / macOS:** use `.venv/bin/python` and the POSIX commands above.
  Explicit setuptools discovery excludes flat-layout directories such as
  `desktop/` and `Icono/` from the Python package.
- **Windows:** use `.venv\Scripts\python`; `colorama` is installed only there.
- **Desktop control (optional, per platform):**
  - Windows: `pip install -e .[desktop-control]` (`pywinauto`, UIA).
  - macOS: `pip install -e .[desktop-control-macos]` (`pyobjc`); grant
    Accessibility permission to the process. Live smoke:
    `SATELLITE_DESKTOP_CONTROL_MACOS_SMOKE=1`.
  - Linux: `pip install -e .[desktop-control-linux]` plus the system package
    `gir1.2-atspi-2.0` and an active AT-SPI accessibility bus.
  - Enabling the toggle does not grant permissions: add the target apps to the
    **allowed apps** list in *Settings → Desktop* (the local denylist always
    wins), then approve each session. Settings shows whether the native backend
    is installed and, on macOS, whether Accessibility permission is granted.
- Desktop-control backends are optional and import lazily. Missing dependencies
  or permissions produce explicit errors instead of breaking the runtime.
- macOS packaging for the Electron app is a development build; signing,
  hardened runtime and notarization are deployment concerns.

## Desktop app and Safe Proxy

An Electron/React front end over a local FastAPI backend that reuses the same
runtime and the same `.satellite/` storage as the CLI and the TUI.

```bash
# Linux / macOS
.venv/bin/python -m pip install -e '.[desktop]'
.venv/bin/python -m satellite.desktop --project <folder>

# Windows
.venv\Scripts\python -m pip install -e ".[desktop]"
.venv\Scripts\python -m satellite.desktop --project <folder>

cd desktop
npm install
npm run dev
```

`desktop/package.json` includes `pele`, so `npm install` installs the Mermaid
renderer the backend uses for diagrams. The Python resolver also finds a global
or nvm/Homebrew installation when the desktop copy is absent; Node is discovered
outside the shell PATH (important when macOS launches the app from Finder).

The embedded **Safe Proxy** has two modes:

- `internal` (default): no port; policy runs inside the provider request cycle.
- `server` (optional): loopback-only HTTP service with an authenticated control
  API, explicit start/stop and a configurable port.

It blocks or masks secrets/PII before sending, restores markers in the response,
audits without content and fails closed. See
[`satellite/safe_proxy/README.md`](satellite/safe_proxy/README.md) for the full
policy and API.

## Tests

```bash
# Linux / macOS
.venv/bin/python -m pytest tests/ -q
.venv/bin/python -m pytest tests/contracts -q

# Windows
.venv\Scripts\python -m pytest tests/ -q
.venv\Scripts\python -m pytest tests\contracts -q
```

Desktop checks:

```bash
cd desktop
npm run typecheck
npm run test
npm run smoke
```

macOS-specific AX tests use fakes; the live AX smoke requires a macOS session
with pyobjc and Accessibility permission.
