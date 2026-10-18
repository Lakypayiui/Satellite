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
  surface, with policies `ask` / `auto` / `unrestricted`. `ask` and `auto` still ask for what
  the guard does not settle by itself (paths outside the project, commands not in the
  allowlist); `auto` only pre-approves proposals whose validation passes. `unrestricted` asks
  **nothing**: out-of-project paths and commands run without confirmation, so choosing that
  mode *is* the decision — no surface asks for a second confirmation (the
  `activate_unrestricted` helper exists but is not wired to any surface). The mode a surface
  shows is the mode it applies.
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

- **N0 (default)** — subprocess with a clean environment and a timeout. It does not
  enforce network isolation: a script runs with your user's permissions and may reach the
  network. This is process isolation, **not** OS isolation.
- **N1** — Docker container with `--network none` and the workspace mounted **read-only**.
- **N2** — no confinement, for trusted local work only.

Tools can only write inside the workspace (plus the extra roots you declare) and
inside the runtime catalogs. Four zones are always off limits, for reading and for
writing: the package itself (**kernel**), the framework's state (`state/`, where the
conversation, the context window and the skill cache live), the **audit** log and
the provider **config** (global keys and project preferences). A symlink from the workspace
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
| Desktop | Electron/React app over the same local backend (development) — see "Desktop app" below |
| MCP | `python -m satellite.mcp` — exposes the same runtime to MCP clients |

### Cancelling

A running task can be cancelled, and it is a **cooperative** cancellation: the runtime stops
between nodes, and inside the agent loop before each provider call and before each tool. The
provider call in flight is aborted and a running command is terminated, so cancelling a slow
step does not mean waiting for it to finish. What was already done **stays**: the change ledger
keeps the previous version of every file, so a cancelled task can still be undone from the TUI
(`/undo-task`) or from the desktop's Changes panel. Cancelling is not failing — the task ends as
`cancelled`, with what was done counted, and the reason in the summary.

| Surface | How |
|---|---|
| CLI | `Ctrl+C` asks for it; a second `Ctrl+C` forces the exit |
| TUI | `/cancel [motivo]` (also in the `Ctrl+K` palette) |
| Desktop | the `cancelar` button by the composer: it asks first and only cuts the task if the runtime does not give in, and the answer says which of the two happened |
| MCP | `satellite_cancel` |

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

- Provider definitions and API keys live in the user-level
  `~/.satellite/config/providers.json` store and are shared by projects. Project
  `.satellite/config/config.json` files keep only the selected provider id and role
  preferences. Providers are saved by a unique `id`, so
  several models can share a family and base URL without overwriting each other,
  and per-role overrides (`llm.orchestrator`, `llm.context`, `llm.agents`,
  `llm.creator`) point at a saved `id`.
- **Never commit API keys.** Keys can be supplied by a global environment variable or
  stored in the user-level provider store. Project-level key fields are ignored by the
  runtime; keys are only ever returned as `api_key_set` and never printed.
- Existing project keys are left untouched until migrated. Run
  `satellite provider migrate-keys` to review the count, then repeat with
  `satellite provider migrate-keys --confirm` to move them to the global store and remove
  the credential fields from that project's config.
- Other useful keys: `llm.compaction` (`window`, `compact_at`, `keep`),
  `llm.max_tokens.<role>`, `llm.pricing` (real cost reporting).
- `terminal.allowed_commands`: binaries answered **without asking**, as a list of names (for
  example `["git", "python"]`). Empty by default, and there is no way to allow everything:
  anything not listed is asked (once / for the session / reject). A value that cannot be read is
  ignored, and that fails closed — the command simply gets asked.
- **Model discovery is per provider, not one `GET /models`.** Each provider documents its own
  listing, and that lives in one place (`satellite/config/model_sources.py`): OpenAI-compatible
  families use `{base}/models`; xAI publishes `/v1/language-models`; Anthropic uses
  `/v1/models` with `after_id`/`limit`; Gemini is listed through its **native** paginated API
  (`pageToken`) and falls back to its OpenAI-compatible form when the native one does not
  answer; Groq `/openai/v1/models`; OpenRouter `/api/v1/models` (reading each model's
  `supported_parameters`); Together returns a **root array**; Cohere `/v1/models` with
  `models` + `next_page_token`; Fireworks uses the **account** endpoint when `account_id` is
  configured and the inference `/models` otherwise; Mistral its official listing. An unknown or
  self-hosted endpoint keeps the plain `{base}/models` behaviour. A rejection of credentials
  (401/403) is reported as such and the models already loaded are **kept**.
- **Effort/thinking is capability metadata, not a guess from the name.** Levels come from the
  family table, from `llm.models.<id>` or from what the provider announces; when they are only
  deduced from the model name the metadata is flagged `inferred`. `auto` sends nothing; `off`
  only when the family can disable reasoning. The **control** (which parameter carries the
  level) is explicit: `reasoning_effort` (OpenAI-compatible), `thinking_level` (Gemini
  native — not sent through the compatible form), `output_config.effort`, `budget_tokens`
  (Anthropic `thinking`) or `provider-specific`. A model with no metadata offers no options,
  and the generic OpenAI-compatible adapter only ever sends the parameters it knows are valid.
- `llm.models.<id>` overrides the table: `reasoning`, `token_param`, `accepts_temperature`,
  `effort_levels`, `effort_param`/`effort_control`, `effort_default`, `effort_off_value`, and —
  for Anthropic extended thinking — `thinking_type: adaptive` and `thinking_budget`. Per-role
  effort adds `llm.<role>.reasoning_effort`, `llm.<role>.effort_levels` and
  `llm.<role>.effort_control`, so a role can send a level with the right parameter without
  touching the others.
- **Think switch, next to the model picker.** In the model editor and in each role's picker the
  chosen model has a `Think` checkbox and the effort selector beside it; a model that declares
  no reasoning channel shows the control disabled (with the reason). The switch is persisted per
  provider+model (`think` on the provider entry, `llm.<role>.think` for a role) and travels in
  `ProviderConfig.thinking`; `off` sends the model's documented disable channel (e.g. DeepSeek
  `thinking: {type: disabled}`) and **no** fake effort, while `auto` sends nothing. Verified
  against the providers' primary docs: DeepSeek `deepseek-flash`/`deepseek-v4-pro` accept
  `thinking` (`enabled`/`disabled`) and `reasoning_effort` (`none`/`low`/`high`/`max`) —
  https://api-docs.deepseek.com/api/create-chat-completion ; OpenAI `reasoning_effort`
  (`none`…`max`, model-dependent) — https://platform.openai.com/docs/guides/reasoning ;
  Anthropic `thinking: {type: enabled, budget_tokens}` is legacy and rejected on 4.7+ (new
  models use `thinking: {type: adaptive}` + `output_config.effort`) —
  https://docs.anthropic.com/en/docs/build-with-claude/extended-thinking . Custom or
  long-tail models declare their own channel via `llm.models.<id>` (`thinking`,
  `think_on`, `think_off`) instead of a hardcoded allowlist.
- `account_id`: for providers whose model listing is per account (Fireworks), set it in the
  provider entry (`"account_id": "..."`); without it the inference `/models` is used and the
  model id can still be typed by hand.
- A saved provider can carry its own output cap (`"max_tokens"` on the provider entry); it is
  used when a request brings no limit of its own, for both families (OpenAI-compatible and
  Anthropic). The desktop app's full-screen model editor writes it — together with the reasoning
  effort and the effort capabilities the endpoint announced (`providers[].models.<id>`), which
  are honored even outside a role. Things the runtime does **not** read yet (temperature, top_p,
  frequency/presence penalties, timeout) are deliberately not offered there: nothing is saved
  that would be ignored.
- **Safe Proxy is embedded, `internal` mode by default (`Settings → Safe Proxy`).** The sanitiser
  lives inside the repo (`satellite/safe_proxy/`) and works in two modes:

  - **`internal` (default)** — opens **no port**, no listener and no 8787 conflict. The policy is
    applied **inside the provider request cycle** (`SanitizingProvider`, installed once in
    `build_runtime`, so CLI/TUI/desktop/MCP all get it): messages are blocked/obfuscated/masked
    before being sent and placeholders are restored in the response. Same code and same state as the
    server mode (both use `SafeProxyCore`). Its master switch (`internal_enabled`) is **off by
    default**: the shipped policy blocks ordinary content (non-Python code fences,
    “All rights reserved”, secrets) and turning it on silently would break the agent — one click in
    the screen turns it on. With it off the request passes through untouched (clear bypass), and
    disabling it never turns the provider off or loses the request.
  - **`server` (optional)** — the same service, exposed on loopback for external consumers, with its
    own authenticated control API (`GET /control/status`, `GET/PATCH /control/config`,
    `POST /control/enable|disable`), a **separate** control token (constant-time compared, never
    returned), no redirects (`follow_redirects=False`), a short timeout and no outgoing requests
    beyond the configured upstream (no SSRF). It never auto-starts: it starts when `server` is
    chosen explicitly (or with Start) and always reports the real state —
    `internal` / `stopped` / `running` / `config-error` / `port-conflict`.

  No double sanitising: when the selected mode is `server` the provider cycle is **not** wrapped
  (the server is the sanitiser). In `internal` mode the model allow-list and the tools policy are
  **not** applied (the model and tools are Satellite's own) and tool schemas are never rewritten;
  message contents and history `tool_calls` arguments are sanitised. Tokens (access + control) are
  generated by Satellite and stored on the host in `<SATELLITE_HOME>/safe_proxy.json` (mode `0600`,
  atomic write); they are **write-only** — only `control_token_set` is exposed, the same shape as a
  provider `api_key`. Electron `safeStorage` is not used because the renderer never holds the tokens
  (they live in the backend only), so they are never persisted to `localStorage`, shown in the UI, or
  written to logs/audit. Config, allowlisted options (block/mask terms, PII, secrets action, code
  flags, `upstream.timeout_seconds`), state and the audit log live under
  `<SATELLITE_HOME>/safe_proxy/`; persistence is atomic and never stores tokens or secrets, and
  changes apply live. Origin and licensing of the vendored service: `satellite/safe_proxy/NOTICE.md`.

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
`/api/config`, `/api/providers`, `/api/models`, `/api/context/mentions` (the composer's `@`
suggestions), `/api/plan` (plan-mode alternatives). `PATCH /api/sessions/{id}` renames a
session or sets its `pinned`/`archived` flags (only the fields that are present are applied).

Sessions are files under `.satellite/state/conversations/<id>.json`; the active one is
`.satellite/state/current_session.json`. Pinning never changes the active session. Archiving
the active session moves to another non-archived one (pinned first, then most recent), or
leaves no active conversation when none remains; the desktop shows its empty state then.
Archiving only marks the session — its turns are never deleted. Session files written before
these flags existed read as `pinned=false, archived=false`, so nothing has to be migrated.

The same operation is reachable from every surface, over the same store: the desktop sidebar
(`⋯` actions with a collapsible *Archivadas* section), the TUI
(`/session pin|unpin|archive|unarchive <id|nombre>`) and the CLI
(`satellite session pin|unpin|archive|unarchive <id|nombre>`).

### Workspaces, review and queue

- **Git status and explicit operations** (desktop `Git` tab, CLI `satellite git status`, TUI
  `/git`): real branch, upstream/ahead/behind, staged/unstaged/untracked and conflicts.
  `POST /api/git/stage`, `/api/git/unstage` and `/api/git/commit` are the only mutations.
  There is **no push, merge, reset --hard or clean**, and paths are validated against the repo.
- **Worktrees** (`GET/POST /api/worktrees`, active/remove; CLI `satellite git worktree
  list|add|use|remove`; TUI `/worktrees list|add|use|remove`): created under
  `.satellite/worktrees/<name>` with a new branch. Activating one (`use <ruta>`, or
  `use proyecto` to return) moves the **execution workdir** of the runtime
  (`.satellite/state/execution.json`), so the agent's `read_file`/`write_file`/`edit_file` and
  commands run inside the worktree — not the original workspace. `@`/context indexing still
  reads the project root, and the active worktree is **project-wide** (all conversations run
  there; it is not bound per session). The framework data dir should stay gitignored, as
  `.satellite/` already is here. Removing a worktree with uncommitted changes is refused unless
  `force`/`--force` is explicit; listings show `con cambios`/`dirty`.
- **Inline diff comments** (desktop *Cambios* tab; `GET/POST /api/comments`,
  `PATCH/DELETE /api/comments/{id}`; CLI `satellite comments list|add|resolve|reopen|delete`;
  TUI `/comments list|add|resolve|reopen|delete`): in the desktop, click a diff line
  (shift+click extends the range) to anchor a comment. The CLI/TUI have no visual diff, so the
  anchor is explicit: file + side (`new`/`old`) + line or range. Comments are persisted per
  project (and linked to the active session/task when known) in
  `.satellite/state/comments.json`, can be resolved/reopened, and survive reloads. The anchor is
  the **diff line index**, not the final file line.
- **Prompt queue** (`GET/POST /api/queue`, `PATCH/DELETE /api/queue/{id}`, `/api/queue/clear`;
  CLI `satellite queue list|add|edit|remove|clear|run`; TUI
  `/queue list|add|edit|remove|clear|run`): persisted in `.satellite/state/queue.json`. While a
  turn runs, the desktop composer button becomes *encolar* (Enter enqueues too); in the TUI
  `/queue add` works during a turn and `/queue run` drains one prompt per turn. Queued prompts
  can be edited before they run and are executed one at a time in order. This is *next
  instruction after the turn*, not in-flight steering: editing a queued item affects a **future**
  turn, and the runtime has no safe mid-turn edit, so none is simulated. Cancel remains separate
  and does not empty the queue. The queue survives reloads; after a restart it stays
  visible/editable and runs when the next turn finishes, with *ejecutar*, or with `queue run`.

```bash
curl -H "Authorization: Bearer <token>" http://127.0.0.1:<puerto>/api/status
curl -H "Authorization: Bearer <token>" http://127.0.0.1:<puerto>/api/events   # stream
```

Restoring a task's changes never touches the workspace without `{"confirm": true}`: the
first call answers with the files and the full diff that would be discarded.

Command permissions are unchanged and stay deny-by-default: with an empty allowlist every
command is **asked** (once, for the session, or rejected) and nothing is granted silently.
`allow_terminal=false` remains a hard veto, and the sandbox level `N0` is process
isolation (clean env and timeout; network access is still possible) — it is **not**
operating-system isolation.

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

What the interface gives you: open or switch project folder (native dialog), create, rename,
resume, pin and archive conversations (archived ones keep their turns and sit in a collapsible
section), chat with live activity, permission cards (allow once / for the session /
reject), the full diff of the changed files together with a restore that asks for confirmation
**after** showing what would be discarded, background jobs with their output and a stop button,
and settings for provider, model and security policy — with the N0 caveat written where you
choose it. Typing `@` in the composer suggests project paths (files and folders) to attach as
context; the runtime resolves them on send, exactly like the TUI's `@mentions`. Plan mode shows
the model's alternatives in the conversation and lets you pick one (the same `ChatSession.choose`
the TUI uses) or describe your own approach. On first run an onboarding sheet asks for the
project folder and the provider, instead of silently adopting Satellite's own directory; until
a folder is chosen the UI stays usable but execution is refused (the backend blocks sending,
plan choices, agent/skill creation and new conversations, so nothing runs against the implicit
folder).

Keyboard: `Enter` sends (`Shift+Enter` adds a line), `Ctrl+K` opens the command palette,
`Ctrl+N` new conversation, `Ctrl+B` / `Ctrl+J` collapse the side and right panels, `Ctrl+,`
settings, `Esc` close. The right panel has tabs for activity, agents, skills, changes and
jobs; agents and skills are built **only** from real runtime events, so a skill that was
merely available in the catalog is never listed as used. The palette is fed by the shared
command catalog (`GET /api/commands`); an action that only makes sense in a terminal (opening
the TUI) is shown with the exact command to run it, instead of being hidden.

Appearance has its own settings page (**Ajustes → Apariencia**): six finished themes —
Obsidiana (the original dark look and the default), Grafito, Medianoche, Bosque, Claro
cálido and **Deep Dark** — each with a live preview swatch. Choosing one applies it at once
through the `data-theme` attribute and CSS variables (sidebar, chat, panels, settings,
borders, inputs, buttons, badges, states, scrollbar and text selection) and saves it to the
user preferences (`preferences.json` inside Electron's `userData`, e.g.
`%APPDATA%/satellite-desktop`), which is restored on the next launch. A missing or unreadable
file falls back to Obsidiana — no destructive migration. Success/warning/error semantics stay
readable in every theme.

Deep Dark paints the whole app on pure black (`#000`) — windows, panels, sidebar, composer,
menus and overlays — and exposes a **white intensity** control on its own card with four
labelled levels: *Suave* (light grey), *Medio* (the default), *Brillante* (near pure white) and
*Máximo* (pure `#ffffff`). The choice adjusts primary and secondary text and the icons that use
the text color; the accent and the semantic success/warning/error colors are kept. It is stored
as `text_intensity` next to `theme` in the same `preferences.json` and restored on launch
(older files without the field default to *Medio*).

The title strip is drawn by the app, not the OS: the window runs with `frame: false`, so there
is no white native caption, and the strip (project name plus minimize, maximize/restore and
close) follows the selected theme, including its `--titlebar-*` colors. The controls are real
`<button>`s reachable with the keyboard and act through a small, validated IPC surface exposed
by `preload` (`windowControls` and `theme`); the main process only accepts calls coming from the
app's own window. The drag region and each platform's convention are respected (controls on the
left on macOS, on the right elsewhere), and `prefers-reduced-motion` is honored throughout.

Desktop control over the accessibility tree is a separate, **non-kernel** subsystem
(`satellite/desktop_control`). It treats everything read from the UI as **untrusted
data** (never as instructions) and never lets UI text change policy or permissions. The
Windows UIA backend lists windows, returns a compact snapshot **and** performs
native-pattern actions (click/invoke, type/set_value, key, scroll, focus) — no
screenshots and no coordinate fallback. `pywinauto` is an **optional** extra
(`pip install -e .[desktop-control]`, Windows only, stable range `>=0.6.9,<1.0`) and is
imported lazily; without it you get a clear `MissingDependency` naming the extra, and
other platforms answer `UnsupportedPlatform`. macOS and Linux use their own optional
extras (below). A deterministic
`FakeBackend` plus unit tests exercise the contracts with no desktop and no dependency
installed; the real read-only smoke is opt-in via `SATELLITE_DESKTOP_CONTROL_SMOKE=1`.

Safety gates: the three tools (`desktop_list_windows`, `desktop_observe`,
`desktop_act`) are registered **only** for surfaces with a human (CLI/TUI/Desktop) and
**never** in MCP. Listing/observing require a human approval per session+app; acting
requires a revocable in-memory control grant per session+app. `auto` and `unrestricted`
never auto-approve desktop control, and a missing approval port denies. Destructive or
indeterminate actions require a per-action confirmation (strict
AutomationId/control-type sets; unknown ⇒ confirm); password fields are never written;
per-run action/time budgets, a per-minute rate limit and a local emergency stop are
checked before each action. Every call is audited with the append-only `AuditLog` (app,
hashed ref, action type, snapshot id, result — never UI text/name/value).

The host-only configuration lives in `<SATELLITE_HOME>/desktop_control.json` (default
`~/.satellite/desktop_control.json`), is **never written** by tools, the API or MCP,
and is deny-by-default: `{"enabled": false, "allowed_processes": []}` until a human
edits it. Defaults: banned apps include password managers, terminals and system
configuration; `max_actions_per_run=20`, `max_seconds_per_run=120`,
`rate_per_minute=30`, `confirm_destructive=true`. An empty allowlist means nothing is
allowed and the denylist always wins. Docker/N1 cannot control the real desktop: this
runs on the host and requires an explicit policy.

The optional `desktop_operator` agent template is created with
`satellite agents --create desktop_operator` (deterministic: no LLM or provider
needed; it still passes the same validator and human approval, unless `--yes`). Its
static system prompt treats every snapshot as untrusted data and its output is a single
JSON object with exactly `action`, `target`, `text` and `snapshot_id`; it never invents
selectors or coordinates.

Platform backends (all optional, lazily imported, selected by `backend_for_platform`):

- **Windows** — UI Automation via `pywinauto` (extra `desktop-control`): native
  click/invoke, type/set_value, key, scroll and focus.
- **macOS** — Accessibility (AX) via pyobjc (extra `desktop-control-macos`:
  `pyobjc-framework-ApplicationServices` + `pyobjc-framework-Quartz`). The process
  needs Accessibility permission (Ajustes del Sistema → Privacidad y seguridad →
  Accesibilidad). Native actions: click (AXPress/AXConfirm/AXOpen), focus and
  set_value; `type`/`key`/`scroll` answer `unsupported_action` (no CGEvent, no
  coordinates). Windows are listed with CGWindowList.
- **Linux** — AT-SPI via PyGObject (extra `desktop-control-linux`; system package
  `gir1.2-atspi-2.0` and an active accessibility bus). Native actions: click
  (`Action.do_action`), focus (`Component.grab_focus`) and set_value
  (`EditableText.set_text_contents`); `type`/`key`/`scroll` answer
  `unsupported_action`. Without a bus you get `EnvironmentUnavailable`.

Unknown platforms answer `UnsupportedPlatform`. All three share the same security
layer — current snapshot only, deterministic action validation, sensitive redaction,
approval/policy/budgets/audit from F4 — and none can act outside an approved, current
snapshot. Actions a platform cannot perform safely degrade to a clear error; nothing
falls back to coordinates or input injection.

Perception degrades explicitly. When `desktop_observe` returns `quality=poor`, the
result includes `degraded` options: a local OCR interface with boxes
(`OcrFallback`/`OcrResult`; no implementation ships, `available=false`), a
vision-provider escalation interface (marked available only if the host injects a
configured `VisionFallback`; vision is not implemented), and `ask_user`. Nothing is
executed automatically; the agent or the human decides.

The compact snapshot always travels **last** inside a sanitized/truncated
`<data source="snapshot">` block. Observations use a small per-session+window cache
(`.satellite/state/desktop_observations.json`): only the **latest** observation keeps
the full text, while previous ones are compacted to one line
`obs <snapshot_id> · app=<app?> · quality=<...> · elementos=<n> · tokens=<n>`. Each
observation reports its token estimate (the existing retrieval estimator), and the
agent template's user message places that `<data>` block last with a static system
prompt.

**Ajustes → Control de escritorio** exposes the local on/off switch and the vision
provider/model selection. The switch is stored in the host-local
`desktop_control.json` (off by default, written only by the desktop settings screen,
never by tools/agents/MCP). The selection is saved in the existing project
`config.json` under `desktop_control.vision_provider` / `vision_model`, validated
against the saved `providers[]`; if the provider disappears or no model is set, the
screen reports it as unavailable. Vision remains **interface-only** (no images are
sent) and OCR remains an unimplemented interface. Enabling or picking a provider
does **not** bypass approvals, grants, budgets or audit, and MCP has no path to
control these settings.

Honest UIA limits: the tree can be incomplete or stale (a window without useful
elements is reported `quality=poor` instead of faked); higher-integrity/elevated
processes may be unreadable without elevation (**privileges are never raised
automatically**); and Electron/Chromium apps only expose a tree when accessibility is
enabled.

Packaging (electron-builder, installers per platform) is **not finished**: what works today is
development, as above. Shipping it would need a bundled Python interpreter and the package
alongside the app — a separate step, and not claimed here.

### Packaging icons (generated, headless)

`desktop/build/icon.{png,ico,icns}` (plus the `-light` variants) are **generated artifacts** and
are gitignored, so a clean checkout does not have them. They are derived from the original art in
`Icono/` (`Ico_white` for dark themes, `Ico_Black` for light ones): centred square crop, area
resize, and the ICO/ICNS containers with one embedded PNG per size.

- The generator is **Node only** (`desktop/scripts/icons.mjs`, `npm run icons`): it decodes and
  encodes PNG with `node:zlib` and does not use Electron, so it runs **headless** everywhere —
  including the Linux CI runner, with no `node_modules` and no X server. (The previous version
  needed Electron + `app.whenReady()`, which on Linux means `xvfb-run`: a system dependency that
  made a clean checkout unable to reproduce them.)
- Nobody has to remember a manual command: the icon test generates them when they are missing or
  older than the sources, CI runs `npm run icons` before the suite, and `npm run dist` generates
  them before packaging (`npm run icons && npm run build && electron-builder`; electron-builder is
  not a dependency yet — that is the unfinished packaging step above).
- The test then validates the **real** files (PNG size, ICO entries and the size of every embedded
  PNG, ICNS container and types), not just their existence.

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
