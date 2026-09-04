#  Satellite

**A general-purpose microagent framework for software development.**

Satellite turns any repository into an LLM-orchestrated, microagent-driven development environment. Install it once, use it on any repository: **the LLM decides, the runtime executes.**

```
                    FRAMEWORK
                         │
              ┌──────────┼──────────┐
              │          │          │
           Repo A     Repo B     Repo C
              │          │          │
           satellite init satellite init satellite init
              │          │          │
              └──────────┼──────────┘
                         │
                  Microagent Runtime
```

---

## Table of Contents

- [Why it exists](#why-it-exists)
- [Core philosophy](#core-philosophy)
- [Architecture](#architecture)
- [Key features](#key-features)
- [How it works](#how-it-works)
- [Requirements](#requirements)
- [Build & install](#build--install)
- [Quick start](#quick-start)
- [CLI reference](#cli-reference)
- [Project structure](#project-structure)
- [Components](#components)
- [Benchmark: monolithic vs microagents](#benchmark-monolithic-vs-microagents)
- [Portability](#portability)
- [Local LLM preprocessing](#local-llm-preprocessing)
- [Testing](#testing)
- [Roadmap / limitations](#roadmap--limitations)

---

## Why it exists

Large software tasks fail when delegated to a single LLM agent that receives
everything and is expected to "just implement the feature." Satellite was built
to answer one question:

> What if a task is broken down into **extremely small operations**, each one
> executed by a **specialized, deterministic microagent**, while the LLM only
> decides *what* to do next?

Instead of:

```
LLM → agent → implements the whole authentication system
```

Satellite does:

```
LLM
 → analyze structure        (microagent)
 → locate file              (microagent)
 → create function          (microagent)
 → modify function          (microagent)
 → create test              (microagent)
 → run test                 (microagent)
 → analyze error            (microagent)
 → fix function             (microagent)
 → run test again           (microagent)
```

Each operation is a tiny, testable, registered capability. The framework is a
**product, not a plugin**: it is distributed independently and connected to any
compatible repository through `satellite init`.

---

## Core philosophy

| Principle | Meaning |
|---|---|
| **LLM = orchestration** | The LLM plans, selects agents, and analyzes results. It never executes anything. |
| **Registry = capabilities** | Every capability is a registered agent with a strict schema. |
| **Dispatcher = deterministic execution** | A request goes through Registry → Validator → Agent. No free-form text is ever executed. |
| **Microagents = small functions** | Agents are deliberately tiny: one operation, one input schema, one output schema. |
| **Context Engine = project context** | Analyzes any project root: files, symbols, classes, imports, dependencies. |
| **Context Optimizer = max relevance, min tokens** | Maximizes relevant context while respecting a hard token budget. |
| **Agent Factory = new capabilities** | Missing capability → generated agent → compiled → tested → registered. Never registered unless it works. |

### The determinism rule (non-negotiable)

The LLM must **never** execute commands, functions, code, or arbitrary tools.
It produces a **structured request**:

```json
{
  "agent_id": 42,
  "input": { "...": "..." }
}
```

The runtime then follows a fixed path:

```
AgentID → Registry → AgentDescriptor → Validator → Dispatcher → Agent
```

If the `AgentID` does not exist → `UNKNOWN_AGENT`. There is no arbitrary
execution based on LLM-generated text. This is what makes the runtime safe,
traceable, and reproducible.

---

## Architecture

```
User
    ↓
Orchestrator
    ↓
LLM
    ↓
Agent Registry
    ↓
Planner
    ↓
Context Engine
    ↓
Context Optimizer
    ↓
Dispatcher
    ↓
Microagent
    ↓
Result
    ↓
Orchestrator
```

- **The LLM decides.**
- **The framework determines what can be executed.**
- **The Dispatcher executes.**

---

## Key features

- **Deterministic runtime** — the LLM never executes code; all execution goes
  through a validated agent registry and dispatcher.
- **Microagent architecture** — tasks are decomposed into tiny operations, each
  one a registered agent with strict input/output schemas.
- **Auto-expansion** — if a capability is missing, the Agent Factory generates
  the agent (spec → code → compile → test → plugin → register) and only
  registers it if every stage succeeds.
- **Selectable execution backend** — generated native agents use the isolated
  `native_process` backend by default. `execution.backend: "wasm"` is reserved
  for a configured WASM runtime and fails explicitly until one is installed.
- **Context Engine for any repository** — receives a `ProjectRoot` and analyzes
  files, directories, symbols, functions, classes, includes/imports, and
  dependencies. Works with repos A, B, C… without framework changes.
- **Context Optimizer** — maximizes `relevant_context / token_cost` subject to
  `token_cost <= token_budget`. It never just trims information; it keeps what
  is needed to solve the task. The algorithm is pluggable.
- **Project adapters** — `IProjectAdapter` abstraction with built-in C++ and
  Python adapters; designed to grow to JavaScript, TypeScript, Java, etc.
- **LLM abstraction** — `ILLMProvider` interface with four providers selected
  via `llm.provider` / `SATELLITE_LLM_PROVIDER` and created by
  `ProviderFactory`: DeepSeek, any OpenAI-compatible API, Anthropic (Claude),
  and a local server (llama.cpp/Ollama-style, via `LocalLLMProvider`).
  Provider-agnostic by design.
- **Python runtime** — the `satellite_py/` package ports the runtime core to
  Python (Typer CLI, planner, orchestrator, dispatcher, registry, security,
  validation, persistence), plus bridge agents (`runtime/`) that expose the
  native agent host and the agent factory to the Python side. Validated by a
  30-test pytest suite that mirrors the C++ behavior (including the planner's
  `order`-based topological tie-breaking).
- **Local LLM preprocessing** — an optional local model (Gemma served by
  llama.cpp) sits in front of the big model: it receives the goal first,
  decides what project context is needed, and refines the prompt before the
  main provider (DeepSeek/Claude/…) ever sees it. The local model never
  executes anything — it only emits structured decisions that the runtime
  interprets. See [Local LLM preprocessing](#local-llm-preprocessing).
- **Agent catalog** — compact capability catalog (id, name, description,
  schemas, capabilities) sent to the LLM — no source code, minimal tokens.
- **Security by capabilities** — every agent declares capabilities
  (`filesystem.read`, `process.execute`, `compiler.execute`, `network.request`,
  …). The runtime validates them *before* execution (deny-by-default).
- **Persistence** — agents, registry state, context cache, and execution logs
  live in the consumer repository under `.satellite/` (never hardcoded into the
  framework).
- **Observability** — every execution is logged with `execution_id`, provider,
  model, agent, input, output, duration, status, plus `tokens_before`,
  `tokens_after`, `tokens_saved`, and `relevance_score`.
- **Full CLI** — `satellite init`, `agents`, `agent create/test/enable/disable`,
  `context build/inspect`, `run`, `doctor`, `--version`.
- **Installable product** — reproducible CMake build, libcurl for HTTP and the
  vendored `nlohmann/json` dependency, `cmake --install`, versioned (1.0.0).
- **Portability proven by tests** — the same binary drives a C++ project and a
  Python project without any framework change.

---

## How it works

### 1. Initialize any repository

```bash
satellite init
```

This creates the consumer-project state (only inside the repository):

```
.satellite/
    config/       # project configuration (LLM provider, budgets, security, ...)
    registry/     # persisted agent registry
    agents/       # generated agent specs + plugin workdir
    context/      # context cache
    executions/   # observability logs
```

The framework itself stays installed externally. `satellite init` never
overwrites an already-initialized project.

### 2. Browse the available capabilities

```bash
satellite agents
```

```
ID  NOMBRE    CAPACIDADES    HABILITADO
1   sum       math.sum       si
2   subtract  math.subtract  si
3   multiply  math.multiply  si
4   divide    math.divide    si
5   average   math.average   si
```

The five built-in agents are test agents used to validate the runtime.

### 3. Build the project context

```bash
satellite context build
satellite context inspect
```

The Context Engine analyzes the project (via the detected adapter) and the
Context Optimizer selects the relevant subset within the token budget.

### 4. Run a goal

```bash
export DEEPSEEK_API_KEY=...
satellite run "implement authentication"
```

The Orchestrator asks the LLM for a plan, checks the Agent Catalog, selects
microagents, optimizes context per step, executes deterministically, and
analyzes the results. If a capability is missing, the Agent Factory creates it
— compiled and tested before it is ever registered.

### 5. Diagnose the environment

```bash
satellite doctor
```

Checks the compiler, project initialization, config, registry, and runs a test
agent end-to-end.

---

## Requirements

- C++17 compiler (GCC 9+, Clang, or MSVC)
- CMake ≥ 3.16 (Ninja or Makefiles)
- libcurl (required for HTTP providers)
- `nlohmann/json` is vendored in `third_party/`
- For LLM features: an API key for the configured provider
  (`DEEPSEEK_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`), or a local
  server for `llm.provider = "local"` (no key required by default).
- For the Python runtime: Python 3.10+ with the dependencies in
  `requirements.txt` (`typer`, `jsonschema`, `pytest`, ...).

---

## Build & install

```bash
cmake -S . -B build -G Ninja
cmake --build build
ctest --test-dir build          # run the full test suite

# Install as a standalone tool
cmake --install build --prefix <install-dir>
```

The build is reproducible: C++17, libcurl plus the vendored JSON dependency,
and no network access at build time.

---

## Quick start

```bash
# 1. Build the framework
cmake -S . -B build && cmake --build build

# 2. Go to any repository (see examples/)
cd examples/sample-project

# 3. Turn it into a Satellite consumer
/path/to/satellite/bin/satellite init

# 4. Explore
satellite agents
satellite context build
satellite doctor
```

---

## CLI reference

| Command | Description |
|---|---|
| `satellite init` | Converts the current repository into a consumer project (creates `.satellite/`). |
| `satellite agents` | Lists all registered agents (id, name, capabilities, enabled). |
| `satellite agent info <id>` | Shows full details of an agent (schemas, capabilities, version). |
| `satellite agent create <spec.json>` | Generates an agent from a spec: code → compile → tests → plugin → register. |
| `satellite agent test <id> [input.json]` | Executes an agent with an input and prints the result. |
| `satellite agent enable <id>` / `disable <id>` | Toggles an agent; state persists in `.satellite/registry/`. |
| `satellite context build` | Analyzes the project and caches the context in `.satellite/context/`. |
| `satellite context inspect` | Shows the cached context in detail. |
| `satellite run <goal>` | LLM-orchestrated goal execution (requires `DEEPSEEK_API_KEY`). |
| `satellite doctor` | Environment and project diagnostics. |
| `satellite --version` | Prints the framework version. |

---

## Project structure

```
Satellite/
├── core/                  # Runtime core
│   ├── agent/             #   AgentID, AgentRequest, AgentResult, IAgent, ...
│   ├── registry/          #   AgentRegistry (unordered_map<AgentID, AgentDescriptor>)
│   ├── dispatcher/        #   Deterministic dispatcher (Registry → Validate → Execute)
│   ├── validation/        #   Input validation against schemas
│   ├── agents/            #   Built-in test agents (sum, subtract, multiply, divide, average)
│   ├── protocol/          #   Structured wire protocol (TokenBudget, ExecutionMetadata)
│   └── catalog/           #   Compact agent catalog for the LLM
├── orchestrator/          # Goal → plan → execute → analyze
├── planner/               # Plan validation and topological execution order
├── llm/                   # ILLMProvider + DeepSeek, OpenAI-compatible, Anthropic, Local providers
├── context/
│   ├── engine/            #   Project analysis (files, symbols, deps)
│   ├── optimizer/         #   Token-aware context selection (pluggable)
│   └── adapter/           #   IProjectAdapter: C++ / Python
├── factory/               # Agent Factory + auto-expansion (compile+test+register)
├── security/              # Capability policy (deny-by-default)
├── persistence/           # AgentStore, ProjectInitializer (.satellite/)
├── observability/         # ExecutionLogger (executions + token metrics)
├── config/                # Framework config vs project config (merged)
├── cli/                   # `satellite` executable (C++)
├── satellite_py/          # Python runtime port
│   ├── cli.py             #   Typer-based CLI
│   ├── planner.py         #   Plan + topological order (same semantics as C++)
│   ├── orchestrator.py    #   Plan execution through the dispatcher
│   ├── dispatcher.py      #   Deterministic dispatch + bridge to native agents
│   ├── registry.py        #   Agent registry
│   ├── security.py        #   Capability policy
│   ├── validation.py     #   JSON Schema validation (jsonschema)
│   ├── store.py           #   .satellite/ persistence
│   └── runtime/           #   Bridge agents (agent_host_bridge, factory_bridge)
├── benchmark/             # Monolithic vs microagents benchmark
├── examples/
│   ├── sample-project/    # Consumer example (FASE 22)
│   ├── project_a/         # Portability test: C++ project
│   └── project_b/         # Portability test: Python project
├── tests/                 # CTest suites (C++) + pytest suite (Python runtime)
└── third_party/json/      # nlohmann/json (vendored, header-only)
```

---

## Components

| Component | Responsibility |
|---|---|
| `AgentRegistry` | `std::unordered_map<AgentID, AgentDescriptor>`; register, unregister, find, list, enable, disable. |
| `Dispatcher` | Deterministic pipeline: `UNKNOWN_AGENT → DISABLED → INTERNAL_ERROR → VALIDATION → execute`. |
| `InputValidator` | JSON-schema-style input validation per agent. |
| `AgentCatalog` | LLM-facing compact catalog (no source code). |
| `ContextEngine` | Language-aware project analysis (C++ and Python parsers). |
| `ContextOptimizer` | `optimize(Task, AgentDescriptor, ProjectContext, TokenBudget) → ContextSelection`; tracks `tokens_before/after/saved`, `compression_ratio`, `relevance_score`, `optimization_time_ms`. |
| `ProjectAdapter` | `IProjectAdapter` + factory; detects C++/Python projects. |
| `Planner` | Validates plans (self-dependency, bounds, duplicate deps, order permutation); Kahn topological order with `order`-based tie-breaking; cycle detection. |
| `ProviderFactory` | Creates the configured LLM provider: DeepSeek, OpenAI-compatible, Anthropic, or local. |
| `Orchestrator` | Never executes code directly — delegates to the Dispatcher. |
| `AgentFactory` | spec → code → compile → harness tests → native process proxy → register; any failure = not registered. |
| `AgentExpander` | Auto-expansion: detects missing capabilities, skips existing ones. |
| `SecurityPolicy` | Validates agent capabilities before execution (deny-by-default); agents without capabilities require the explicit `agent.no_capabilities` rule. |
| `AgentStore` | Persistence of specs and registry state in the consumer's `.satellite/`. |
| `ExecutionLogger` | Per-execution records incl. token metrics. |
| `ProjectInitializer` | `satellite init` logic. |

---

## Benchmark: monolithic vs microagents

The benchmark (FASE 23) does **not** assume model B is better — it measures it.
On a synthetic project of 20 files / 600 functions:

| Metric | Model A (monolithic: full context) | Model B (orchestrator + microagents + Context Optimizer) |
|---|---|---|
| Context tokens | 7,310 | 1,835 |
| Tokens saved | — | 5,475 (**75% less**) |
| Compression ratio | — | 0.749 |
| Local latency | 0.039 ms | 0.53 ms |
| Success rate | — | **1.0** (3/3 agents) |

Run it yourself:

```bash
cmake --build build
./build/satellite_benchmark
```

> Honest limitations: token counts are estimates (1 token ≈ 4 chars) and the
> synthetic project is the optimizer's best case. The benchmark proves the
> *mechanism* (3–4× context reduction at 100% success).

---

## Portability

The **same** framework binary runs on both example consumer projects, with no
framework changes:

```bash
# C++ project
cd examples/project_a
satellite init && satellite context build      # → "Lenguaje: C++"

# Python project
cd examples/project_b
satellite init && satellite context build      # → "Lenguaje: Python"
```

Verified by `tests/test_portability.cpp` (13 checks).

---

## Local LLM preprocessing

An optional small, local model sits **in front of** the big model (DeepSeek,
Claude, …): it receives the user's goal first, decides what project context
is actually needed, and only then lets the request through — already refined
and with just the right context. The local model never executes anything
(the determinism rule applies to it too): it only produces *structured
decisions* that the runtime interprets.

### The four pieces

1. **The local model, served by llama.cpp** — `third_party/llama-b10739-bin-win-vulkan-x64/`
   ships the binaries (ggml with CPU fallback + Vulkan, so it runs without a
   dedicated GPU) and the configured default model
   (`config.local_llm_model`, a GGUF under that directory).

2. **`LocalLLMProvider`** ([llm/LocalLLMProvider.h](llm/LocalLLMProvider.h)) —
   a regular `ILLMProvider` that talks HTTP to the llama.cpp server the same
   way `DeepSeekProvider` talks to DeepSeek. Thanks to `ProviderFactory`, the
   rest of the framework cannot tell whether the LLM is local or remote.

3. **`LocalPreprocessor`** ([context/LocalPreprocessor.h](context/LocalPreprocessor.h)) —
   the heart of the feature. Its contract with the runtime is two structs:

   ```cpp
   struct NeedInfo {          // what the local model says it needs
       std::string category;
       std::vector<std::string> files_needed;
       std::vector<std::string> symbols_needed;
       std::string description;
   };
   struct Result {            // the final preprocessing decision
       std::string refined_prompt;              // rewritten/compacted goal
       bool needs_user_input = false;           // is the USER missing info?
       std::string user_prompt;                 // clarifying question
       std::vector<std::string> missing_info;   // missing context categories
   };
   ```

4. **The integration** — [SatelliteCLI.cpp](cli/SatelliteCLI.cpp#L788) hooks
   preprocessing into `satellite run` when `use_local_llm` is enabled, and the
   incremental context layer (`IncrementalContextBuilder`,
   [context/engine/SemanticContext.h](context/engine/SemanticContext.h)) serves
   the requested context on demand (index + mtime-based invalidation).

### Request flow

```
 user ──goal──▶ [1] LocalPreprocessor.preprocess(goal)
                     │  sends the goal to the local model with a system prompt
                     │  that asks it to DECIDE, not to act
                     ▼
           ┌── needs_user_input? ──yes──▶ ask the user (user_prompt) → re-enter
           │ no
           ▼
     missing_info empty? ──no──▶ [2] Satellite fetches the missing context
           │                          (files/symbols from the incremental index)
           ▼ yes
     [3] Final prompt = refined_prompt + freshly fetched context
           │
           ▼
     [4] The BIG model (via ProviderFactory) reasons and orchestrates
           │
           ▼
     [5] The Dispatcher executes the agents (deterministic runtime)
```

Key design point: everything the local model produces is structured data
(categories, files, flags), parsed by the runtime (`llm/JsonExtraction.h`) and
turned into real calls to the context system. It never emits commands to run.

### Configuration

```json
{
  "use_local_llm": true,
  "local_llm": {
    "port": 8080,
    "model": "third_party/llama-b10739-bin-win-vulkan-x64/gemma-4-E2B-it-Q5_K_M.gguf",
    "context_size": 8192
  }
}
```

Defaults live in [config/Config.h](config/Config.h#L27). When
`use_local_llm` is `false` (the default), `satellite run` skips preprocessing
entirely and sends the goal straight to the configured provider.

### Known limitations / open points

- Preprocessing is a **single pass**; an iterative "is this enough? → ask for
  more" loop is not implemented yet.
- The mapping from `missing_info` to the incremental context fetch and the
  final merge into `refined_prompt` is intentionally simple.
- Activation policy (every `run` vs. only large goals vs. token-budget
  threshold) is currently all-or-nothing via `use_local_llm`.
- The bundled default model is a small edge-class model; decision quality vs.
  API cost savings should be validated per use case.

---

## Testing

### C++ (CTest)

- CTest suites covering: agent core (43 checks), registry, validation,
  dispatcher, native agents, protocol, LLM provider, catalog, context engine,
  context optimizer, project adapter, orchestrator, planner, agent factory
  (real runtime compilation with g++), auto-expansion, persistence, project
  initializer, config, security, observability, CLI (end-to-end with the real
  binary), benchmark, and portability.
- Mini test framework with **zero test dependencies** (plain C++17).

```bash
cmake --build build && ctest --test-dir build --output-on-failure
```

### Python runtime (pytest)

The `satellite_py` port has its own test suite (30 tests, 100% passing)
mirroring the C++ semantics: planner topological order (respecting the
`order` field of each step), orchestrator abort-on-failed-dependency,
validation error paths, dispatcher, registry, security, store, expander, CLI,
and the native bridges.

```bash
python -m pytest tests/ -q        # inside the repo, with requirements.txt installed
```

---

## Roadmap / limitations

- [ ] Real tokenizer integration (provider-accurate token counts).
- [ ] Additional project adapters (JavaScript, TypeScript, Java).
- [ ] Process-level isolation for LLM-generated agents (per-agent sandboxing).

---

## License

License to be defined. The framework is intended to be distributed as a
standalone, reusable product — see the [examples](examples/) directory for
consumer-project usage.
