# Satellite

**A declarative microagent framework for software work — Python edition.**

Satellite turns a repository into an LLM-orchestrated workspace: describe a goal
in plain language and it plans and executes it over your project using reusable
agents and skills. It runs as a **CLI**, an interactive **TUI**, an **MCP** stdio
server and an optional **Electron desktop app**.

## What it does

- Turns a goal into an executable **DAG plan**: independent steps run in
  parallel, dependent steps run in order.
- Uses a catalog of reusable **agents and skills** instead of one monolithic
  prompt. New agents/skills are declarative (`spec.json` + prompts, or a script
  + `manifest.json`).
- Lets a specialized agent split large work into **parallel subagents**, with
  hard limits and file/region ownership enforced by the runtime.
- Keeps a small **persistent memory per conversation**: every user/model turn is
  summarized (2 lines for the request, 4 for the reply) into JSON, injected into
  every prompt and reused by compaction, so long sessions forget less.
- Renders **Mermaid diagrams** to SVG and shows them in the desktop chat.
- Searches, reads and downloads from the internet through `web_researcher`
  (`web_search`, `web_fetch`, `web_download`), with bounded responses,
  public-host validation and all fetched content treated as untrusted data.
- Offers optional **desktop control** (macOS AX, Windows UIA, Linux AT-SPI) under
  human approval, a host allowlist and an immutable denylist. It is never exposed
  through MCP.
- Protects provider traffic with an embedded **Safe Proxy** that blocks or masks
  secrets/PII, restores markers in responses and fails closed.

## Base agents

Installed by default:

- `orchestrator` — goal → plan over the existing catalog.
- `agent_creator` — proposes one reusable agent for a capability need, with human approval.
- `skill_curator` — maintains and deduplicates the skill catalog.
- `pele_diagram_agent` — designs Mermaid diagrams and renders them to SVG.
- `desktop_operator` — opens apps and observes/controls desktop windows under the local policy.
- `web_researcher` — searches the web, reads pages and downloads files, citing sources.

## Quick start

Python **3.11+**.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev,desktop]'
.venv/bin/python -m satellite.cli provider
.venv/bin/python -m satellite.cli doctor
.venv/bin/python -m satellite.cli run "add a health endpoint"
.venv/bin/python -m satellite.cli tui
```

On Windows use `py -3.11`, `.venv\Scripts\python` and PowerShell-style paths.
`doctor` checks config, catalog, tools, provider credentials, roles and sandbox.

MCP over stdio: `python -m satellite.mcp`.

## Configuration

Providers and API keys live in `~/.satellite/config/providers.json` and are
shared by projects. Each project only stores the selected provider and its role
preferences in `.satellite/config/config.json`.

Roles can use different providers/models:

- `orchestrator`: planning.
- `context`: context preprocessing, retrieval and turn summaries.
- `agents`: specialized agents.
- `creator`: agent/skill creation.
- `subagents`: children created with `spawn_agent` (unset = inherit the parent).

**Never commit API keys.** Keys come from a global environment variable or the
user-level provider store; the runtime only returns `api_key_set`.

## Desktop app

An Electron/React front end over a local FastAPI backend, using the same runtime
and `.satellite/` storage as the CLI and TUI.

```bash
.venv/bin/python -m pip install -e '.[desktop]'
.venv/bin/python -m satellite.desktop --project <folder>

cd desktop
npm install
npm run dev
```

`npm install` also installs **Pele**, the Mermaid renderer used for diagrams.

**Desktop control** is optional per platform:

- macOS: `pip install -e .[desktop-control-macos]` and grant Accessibility permission.
- Windows: `pip install -e .[desktop-control]`.
- Linux: `pip install -e .[desktop-control-linux]` plus `gir1.2-atspi-2.0` and an active AT-SPI bus.

Enable it in *Settings → Desktop*, add the target apps to the allowed list, and
approve each session. The local denylist always wins.

## Tests

```bash
.venv/bin/python -m pytest tests/ -q
```
