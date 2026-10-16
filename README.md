# Satellite

**A declarative microagent framework for software work — Python edition.**

Satellite turns a repository into an LLM-orchestrated workspace where the model
**decides** and the runtime **executes**. Agents, skills and prompts are plain
data on disk; every safety decision (permissions, reserved ids, approvals,
sandboxing) lives in deterministic code, never in the prompt.

It ships as a **CLI** plus an interactive **TUI** and an **MCP** server. There is
no web console.

---

## How it works

- **Three base agents at install** — `orchestrator` (turns a goal into a plan
  over the existing catalog, or reports a capability gap), `agent_creator`
  (proposes *one* reusable role per need, with human approval) and
  `skill_curator` (lazy: only maintains the skill catalog when asked).
  They live in the package, are synced to the data dir and **verified by SHA-256**
  on every start: if a base spec or prompt is tampered with, it is restored.
- **Everything else is declarative** — an agent is a `spec.json` plus a
  `system_prompt` / `user_template` pair; a skill is a script plus a
  `manifest.json`. No generated Python is ever imported.
- **Kernel vs data** — the package is immutable; agents, skills, tasks, audit and
  metrics live under the data directory. Reserved ids (`orchestrator`,
  `agent_creator`, `skill_curator`, `run_python`, `run_argv`, `read_file`,
  `write_file`, `list_dir`, `search_files`) can never be created or overwritten,
  in any policy mode.
- **Approval is a port, not a prompt** — one `ApprovalPort` used by every
  surface, with policies `ask` / `auto` / `unrestricted` (the last one requires an
  explicit confirmation).
- **Skills are searched, not guessed** — a stdlib BM25 index (no LLM, no heavy
  dependencies) over the skill catalog, with quarantine after N consecutive
  failures and memoization of deterministic skills.
- **Untrusted data stays data** — everything retrieved (catalog entries, files,
  tool output) is wrapped in `<data>` blocks and sanitized before it reaches a
  model.

## Prompt caching and context

- **Cache-friendly prefixes**: tools → system → stable catalog → dynamic content,
  with explicit breakpoints on Anthropic and automatic prefix caching elsewhere.
  The first call of a new prefix is serialized so the cache is written once.
- **Measured honestly**: every call is logged (hashes and counters only, never
  content) and aggregated into a cache report — hit rate, real cost vs.
  hypothetical cost without cache, and prefix drift detection.
- **Context compaction**: the model's context window is discovered from the
  provider's own `/models` metadata when it is published (and estimated
  otherwise); at 85% of it the conversation is compacted automatically
  (LLM summary, deterministic fallback) and `/compact` does it on demand.
  Anthropic also offers server-side compaction; keeping ours local is
  deliberate (it works with every provider) and can be revisited later.
- **Two documented trade-offs**: `max_tokens` is never read as a context window
  (it is an output cap, so taking it would compact too early), and
  `ProviderConfig.model_overrides` stays out of equality/hashing to keep the
  configuration hashable (it is plumbing, not identity).

## Sandbox and skill memoization

- **N0 (default)** — subprocess with a clean environment, no network and a timeout. This is
  process isolation, **not** OS isolation: a script runs with your user's permissions.
- **N1** — Docker container with `--network none` and the workspace mounted **read-only**.
- **N2** — no confinement, for trusted local work only.

Tools can only write inside the workspace (plus the extra roots you declare) and
inside the runtime catalogs. Four zones are always off limits, for reading and for
writing: the package itself (**kernel**), the framework's state (`state/`, where the
conversation, the context window and the skill cache live), the **audit** log and
the provider **config** (where the API key is stored). A symlink from the workspace
into any of them resolves to the protected zone and is refused.

Note that only **N1** confines `run_command`/`run_python`: in N0 they are plain
subprocesses running with your user's permissions, so the zones above protect the
framework's own files, not the machine.

A skill's result is memoized only when its manifest declares `deterministic: true` **and** a
static audit of the script and its local modules finds nothing non-deterministic (randomness,
clocks, environment, dynamic execution, writes, unauditable dependencies). `bash` skills are
never memoized. The audit raises the bar, it does not prove determinism; `satellite skills list`
says which skills are memoizable and why not.

## Surfaces

| Surface | How |
|---|---|
| CLI | `python -m satellite.cli <command>` |
| TUI | `python -m satellite.cli tui` — chat, `/plan`, `/provider`, `/compact`, `/stats`… |
| MCP | `python -m satellite.mcp` — exposes the same runtime to MCP clients |

### Approvals over MCP

MCP never approves anything by itself. Creating an agent or a skill is asked to the
**client** through `elicitation` (a client capability, per the 2025-06-18 spec), and only for
**low-risk** proposals: no network, no shell, no writes outside the workspace. Anything risky
(those permissions, delegation, edits) is resolved **only in the CLI or the TUI**; over MCP it is
refused with that reason. If the client does not declare elicitation, does not answer, declines or
cancels, the answer is a rejection — the server fails closed and never blocks. Asking the client
does not guarantee a human is behind it: that is the client's responsibility.

## Quick start

```bash
# 1. Dependencies (Python 3.11+)
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt

# 2. Configure a provider (guided: ~57 providers with their base URL, models are
#    fetched from the endpoint; you only paste your API key)
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

## Project structure

```
satellite/
├── agents/           # base specs + prompts (kernel), catalog store, expander
├── approval/         # approval port + policies (CLI, TUI, MCP)
├── cli/              # Typer CLI, provider configurator, runtime builder
├── config/           # provider catalog, saved-provider rules, model listing
├── context/          # context engine + compaction (window, bar, summary)
├── core/             # contracts: messages, usage, specs, plans, proposals
├── kernel/           # paths, policy, manifest verification, audit, migrations
├── mcp/              # MCP server
├── metrics/          # pricing, cache report, skill metrics
├── observability/    # per-call log (hashes/counters), drift detection
├── orchestration/    # planner, runner, delegation, chat, prefix rules
├── persistence/      # .satellite/ store, snapshots, schema migrations
├── providers/        # anthropic, openai-compatible, fake, caching, metered
├── retrieval/        # BM25 index + sanitization of untrusted data
├── sandbox/          # N0 subprocess, N1 Docker, N2 host
├── security/         # deny-by-default capability policy
├── skills/           # manifests, registry, runner, result cache
├── tools/            # primitives (run_python, run_argv, fs…) and agent tools
└── tui/              # Textual interface (session, palette, screens, render)
```

## Configuration and secrets

- Providers live in `.satellite/config/config.json` of each project (kept out of
  version control by `.gitignore`). Providers are saved by a unique `id`, so
  several models can share a family and base URL without overwriting each other,
  and per-role overrides (`llm.orchestrator`, `llm.context`, `llm.agents`,
  `llm.creator`) point at a saved `id`.
- **Never commit API keys.** Prefer `--api-key-env VAR` so the key stays in the
  environment; if it must live in the file, it is only ever read back as
  `api_key_set` and never printed.
- Other useful keys: `llm.compaction` (`window`, `compact_at`, `keep`),
  `llm.max_tokens.<role>`, `llm.pricing` (real cost reporting).

## Desktop app (development)

The desktop app is an Electron/React front end over a **local backend that reuses the same
runtime** as the CLI and the TUI (`Session` / `build_runtime`) on the same `.satellite/`
storage. No agent logic is duplicated outside Python.

### Backend

```bash
.venv\Scripts\pip install -e .[desktop]              # FastAPI + uvicorn (extra "desktop")
.venv\Scripts\python -m satellite.desktop --project <carpeta>
```

It prints **one** JSON line on stdout with the real port and a fresh random token;
everything else (including logs) goes to stderr:

```json
{"port": 51789, "token": "..."}
```

- Listens on `127.0.0.1` only, on an ephemeral port unless `--port` is given.
- Every route under `/api` requires `Authorization: Bearer <token>`. `/api/health` is the
  only open door, and it returns nothing.
- The token lives only in the process that starts the backend and in Electron's main
  process: never written to disk, never logged, never sent to the interface.
- The provider API key is never returned: it is redacted as `api_key_set`.
- `GET /api/status` reports `runtime_ready`, and `runtime_error` with the reason when the
  runtime cannot be built (for example, no provider configured yet). Nothing is faked.

Routes: `/api/status`, `/api/projects`, `/api/sessions`, `/api/messages`, `/api/approvals`,
`/api/events` (SSE, resumable with `?after=<index>`), `/api/tasks`, `/api/tasks/{id}`
(plus `/changes`, `/diff`, `/restore`), `/api/jobs`, `/api/agents`, `/api/skills`,
`/api/config`, `/api/providers`, `/api/models`.

```bash
curl -H "Authorization: Bearer <token>" http://127.0.0.1:<puerto>/api/status
curl -H "Authorization: Bearer <token>" http://127.0.0.1:<puerto>/api/events   # stream
```

Restoring a task's changes never touches the workspace without `{"confirm": true}`: the
first call answers with the files and the full diff that would be discarded.

Command permissions are unchanged and stay deny-by-default: with an empty allowlist every
command is **asked** (once, for the session, or rejected) and nothing is granted silently.
`allow_terminal=false` remains a hard veto, and the sandbox level `N0` is process
isolation (clean env, no network, timeout) — it is **not** operating-system isolation.

### Interface

```bash
cd desktop
npm install          # React, Vite and Electron (the first install downloads Electron)
npm run dev          # Vite dev server + Electron, with hot reload
npm start            # build the renderer and run it without a dev server
npm run smoke        # end-to-end check of the integration, without opening a window
```

How it is wired: the Electron main process starts `python -m satellite.desktop`, reads the
handshake and keeps the token; the interface only talks to the main process over IPC
(`contextBridge`), so neither the token nor any URL is reachable from the renderer. Closing a
window asks the backend to shut down (which drains pending writes) before terminating it, and
if the backend dies the interface shows a banner with a retry that restarts it.

What the interface gives you: open or switch project folder (native dialog), create, rename and
resume conversations, chat with live activity, permission cards (allow once / for the session /
reject), the full diff of the changed files together with a restore that asks for confirmation
**after** showing what would be discarded, background jobs with their output and a stop button,
and settings for provider, model and security policy — with the N0 caveat written where you
choose it.

Keyboard: `Ctrl+Enter` send, `Ctrl+N` new conversation, `Ctrl+B` / `Ctrl+J` collapse the side and
right panels, `Ctrl+,` settings, `Esc` close. The right panel has tabs for activity, agents,
skills, changes and jobs; agents and skills are built **only** from real runtime events, so a
skill that was merely available in the catalog is never listed as used.

Packaging (electron-builder, installers per platform) is **not finished**: what works today is
development, as above. Shipping it would need a bundled Python interpreter and the package
alongside the app — a separate step, and not claimed here.

## Tests

```bash
.venv\Scripts\python -m pytest tests/ -q          # unit + contract + integration
.venv\Scripts\python -m pytest tests/contracts -q # interface contracts
```

The suite covers the kernel guarantees (reserved ids, manifest verification,
audit chain), the agent/skill lifecycle, delegation budgets, sandbox levels,
prompt caching and prefix determinism, context compaction and the CLI/TUI/MCP
surfaces. A live smoke test verifies real cache hits against a provider and is
skipped unless `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` is present.
