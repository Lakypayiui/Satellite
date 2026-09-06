# Satellite

**A general-purpose microagent framework for software development — Python edition.**

Satellite turns any repository into an LLM-orchestrated, microagent-driven
development environment. The LLM decides, the runtime executes:
**the framework determines what can be executed, the dispatcher executes.**

This is the **Python runtime** of Satellite: a Typer CLI, planner, orchestrator,
dispatcher, registry, security policy, validation, persistence, semantic
context layer, agent auto-expansion, and a **web console** (`satellite_py/web`)
with the agent graph, Monaco code editor and live runs.

---

## Features

- **Deterministic runtime** — the LLM never executes code; all execution goes
  through a validated agent registry and dispatcher.
- **Microagent architecture** — tasks are decomposed into tiny operations, each
  one a registered agent with strict input/output schemas.
- **Agent auto-expansion** — if a capability is missing, the Agent Factory
  generates the agent (spec → code → compile → test → register) and only
  registers it if every stage succeeds.
- **Sandboxed effects** — subagents can read/write files in the project, run
  processes and make HTTP requests, confined to the project root (`.satellite/`
  is write-protected); every effect requires the capability to be allowed.
- **Semantic context layer** — project index (files/symbols + mtime
  invalidation), schema-neutral session docs, and a deterministic, provider-
  agnostic ContextCompressor.
- **LLM abstraction** — provider-agnostic clients via `satellite_py.llm`
  (local llama.cpp/Ollama, OpenAI-compatible, DeepSeek, Anthropic).
- **Web console** — local browser UI: agent graph with complement edges,
  Monaco editor + file explorer, per-role model menu, live run events.
- **Security by capabilities** — every agent declares capabilities
  (`filesystem.read`, `filesystem.write`, `process.execute`,
  `network.request`, …); the runtime validates them *before* execution.
- **Persistence** — agents, registry state, context cache and execution logs
  live in the consumer repository under `.satellite/`.
- **Observability** — per-execution records with provider/model, tokens and
  durations.

---

## Quick start

```bash
# 1. Install dependencies (Python 3.10+)
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt

# 2. Initialize any repository (creates .satellite/)
cd /path/to/project
python -m satellite_py.cli init

# 3. Explore
python -m satellite_py.cli agents
python -m satellite_py.cli context build
python -m satellite_py.cli doctor

# 4. Run a goal (auto-expands missing agents when enabled)
python -m satellite_py.cli run "implement authentication"

# 5. Web console
python -m satellite_py.web          # http://127.0.0.1:7900
```

---

## CLI reference

| Command | Description |
|---|---|
| `init` | Converts the current repository into a consumer project (creates `.satellite/`). |
| `agents` | Lists all registered agents (id, name, capabilities, enabled). |
| `agent info <id>` | Shows full details of an agent (schemas, capabilities, version). |
| `agent create <spec.json>` | Compiles and registers an agent from a spec (delegated to the factory). |
| `agent test <id> [input.json]` | Executes an agent once and prints the result. |
| `agent expand <goal> <capability>` | Auto-creates a missing capability via the factory. |
| `agent enable/disable <id>` | Toggles an agent; state persists in `.satellite/registry/`. |
| `context build` | Builds the project index (files/symbols, mtime invalidation). |
| `context inspect` | Shows the cached context in detail. |
| `run <goal>` | LLM-orchestrated goal execution (auto-expand by default). |
| `serve-local` | Serves the bundled local llama.cpp model on `local_llm.port`. |
| `doctor` | Environment and project diagnostics. |

---

## Project structure

```
satellite/
├── satellite_py/
│   ├── cli.py                  # Typer CLI (init, agents, agent, context, run, doctor)
│   ├── planner.py              # Plan generation + topological validation (Kahn, order tie-break)
│   ├── orchestrator.py         # Goal → plan → execute → analyze; complement network
│   ├── dispatcher.py           # Deterministic dispatch + sandbox injection
│   ├── registry.py             # Agent registry (descriptors, complements, enabled)
│   ├── security.py             # Capability policy (deny-by-default, configurable)
│   ├── validation.py           # JSON Schema input/output validation
│   ├── context.py              # ContextPreprocessor (needs analysis, project index)
│   ├── context_schema.py       # Provider-neutral session documents
│   ├── semantic.py             # ContextCompressor + SemanticContextRefiner
│   ├── optimizer.py            # Deterministic context selection
│   ├── expander.py             # Agent auto-expansion (spec templates, effects)
│   ├── llm.py                  # Unified LLM client + config loading
│   ├── store.py                # .satellite/ persistence
│   ├── execution_log.py        # Observability records
│   ├── runtime/                # Bridges: agent_host, cpp_cli, factory
│   └── web/                    # FastAPI web console + static UI (Monaco, cytoscape)
├── tests/                      # pytest suite (mirrors C++ semantics)
└── requirements.txt
```

---

## Testing

```bash
.venv\Scripts\python -m pytest tests/ -q
```

The pytest suite mirrors the C++ semantics: planner topological order,
orchestrator abort-on-failed-dependency, validation error paths, dispatcher,
registry, security, store, expander, CLI, web API and the bridges.

---

## Notes

- The Python runtime can delegate native agent execution and spec compilation
  to external host binaries (`satellite_agent_host`, `satellite_factory_cli`)
  when available (see `satellite_py/runtime/`); without them, the pure-Python
  dispatch still works.
- Provider configuration lives in `.satellite/config/config.json` of each
  consumer project: `llm.provider` (`local`, `openai`, `openai-compatible`,
  `deepseek`, `anthropic`), `llm.model`, `llm.base_url`, `llm.api_key_env`,
  plus per-role sections (`llm.context`, `llm.orchestrator`, `llm.agents`).
