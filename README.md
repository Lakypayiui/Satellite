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
  `agent_creator`, `skill_curator`, `run_python`, `run_shell`, `read_file`,
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

## Sandbox and skill memoization

- **N0 (default)** — subprocess with a clean environment, no network and a timeout. This is
  process isolation, **not** OS isolation: a script runs with your user's permissions.
- **N1** — Docker container with `--network none` and the workspace mounted **read-only**.
- **N2** — no confinement, for trusted local work only.

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
├── tools/            # primitives (run_python, run_shell, fs…) and agent tools
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
