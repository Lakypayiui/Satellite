# PLAN DE IMPLEMENTACIÓN — SATELLITE 2.0 · ETAPAS 5-8

Autocontenido para delegar a un modelo programador. Las Etapas 0-4 quedan definidas en `SATELLITE2_PLAN_ETAPAS_1_4.md` y deben estar implementadas antes de empezar estas (cada etapa se apoya en las anteriores).

**REGLAS COMUNES** (iguales que en el plan 1-4):
1. NO `git commit/push`. NO modificar/borrar archivos preexistentes; solo crear archivos nuevos y editar únicamente archivos del paquete `satellite/` creados en etapas anteriores (nunca `core/`).
2. NO instalar dependencias sin autorización del brief. `.venv` ya tiene: `openai`, `anthropic`, `jsonschema`, `typer`, `fastapi`, `uvicorn`, `pytest`, `httpx`. Usa SIEMPRE `.venv/Scripts/python.exe -E`.
3. Comentarios/docstrings en ingles, type hints, async de punta a punta.
4. Implementa sobre los contratos de Etapa 0 tal cual están (`satellite/core/*.py`, `satellite/*/base.py`). No cambies firmas de `core/`.
5. Tests en `tests/test_*.py` (no tocar `tests/contracts/` ni los tests viejos de la raíz).
6. Verificación: `.venv/Scripts/python.exe -E -m pytest tests/<tus_tests> -q` verde + suite completa verde.
7. Reporte final en español: (1) archivos creados, (2) clases implementadas, (3) decisiones de diseño, (4) código anterior reutilizado y por qué, (5) descartado y por qué, (6) tests creados y nº, (7) resultado real de los tests.
8. Trabaja en la rama git actual. No toques `Desktop/Satellite_OLD`.

---

# ETAPA 5 — GraphRunner: ejecución del grafo con comunicación directa

**Objetivo**: implementar el `DefaultGraphRunner` que ejecuta un `ExecutionGraph` (de Etapa 0): recorre nodos, evalúa `EdgeCondition`s, entrega resultados/feedback DIRECTAMENTE entre nodos (sin Orchestrator), respeta `GraphLimits` (terminación garantizada), emite eventos y produce `TaskResult`. En esta etapa los agentes son reales (Etapa 4) pero en tests se usan agentes fake deterministas.

## Archivos a crear
- `satellite/orchestration/runner.py` → `DefaultGraphRunner`
- Tests: `tests/test_graph_runner.py`, `tests/test_graph_runner_limits.py`

## Especificación

### `DefaultGraphRunner(GraphRunner)`
```python
class DefaultGraphRunner(GraphRunner):
    def __init__(self, factory: AgentFactory, context_engine: ContextEngine | None = None,
                 sink: EventSink | None = None): ...
    async def run(self, graph: ExecutionGraph, task: Task, services: RuntimeServices) -> TaskResult
```
Necesita un `services.factory` para crear agentes, `services.context` para resolver context_refs de cada nodo, `services.sink` para eventos. `RuntimeServices` ya existe en `orchestration/base.py` (Etapa 0). Si `factory` falta → `ContractError`.

**Algoritmo** (bucle de trabajo, no recursión):
1. `graph.validate()` primero (→ GraphValidationError si es inválido).
2. Emitir `TASK_STARTED` y `GRAPH_BUILT` (payload con resumen de nodos/edges).
3. Estado por nodo: `visits: dict[node_id, int]`, `attempts: dict[node_id, int]` (reintentos consumidos), `results: dict[node_id, AgentResult]`, `total_steps = 0`.
4. **Frontera**: lista de `(node_id, entrada)` donde `entrada = {"task": ..., "feedback": findings | None, "context_refs": ...}`. Inicial: `graph.entry_node` con el task original del usuario (el `Node.task` si existe, si no el goal del Task).
5. Mientras la frontera no esté vacía y no se superen límites:
   - Sacar `(node_id, entrada)`.
   - `visits[node_id] += 1`; `total_steps += 1`.
   - **Límites**: si `total_steps > limits.max_total_steps` o `visits[node_id] > limits.max_node_visits` → emitir `NODE_STUCK`, abortar con `TaskResult(status=FAILED, error="límite ... superado", metrics con retries/total_steps)`. `max_retries_global` = suma de intentos totales.
   - Resolver contexto: `bundle = await services.context.resolve(entrada["context_refs"])` (si el engine existe y hay refs; si no, bundle vacío). `snapshot` queda registrado.
   - Crear agente: `agent = services.factory.create(node.spec_id, model_role=node.model_role)`.
   - Ejecutar: `result = await agent.execute(entrada["task"], AgentContext(context=bundle, feedback=entrada["feedback"] o (), sink=services.sink))`.
   - Guardar `results[node_id] = result`; emitir `NODE_FINISHED` (payload: node_id, spec_id, status, summary).
   - **Sucesores**: `edges = graph.successors(node_id, result)`.
     - Si no hay edges → si es el último nodo esperado (sin más frontera) y result.status==OK → fin normal; si no → `NODE_STUCK` (dead-end) → abortar con FAILED (el Orchestrator en Etapa 6 decidirá replanificar).
     - Para cada edge activo:
       - `payload`:
         - `"result"` → el `result.output` (más `summary` y `findings` si los pide el mapping).
         - `"feedback"` → los `findings` del resultado (para reintentos: edge FAIL → nodo creador).
         - `"context"` → `bundle` (o refs).
         - `"none"` → nada.
       - `mapping`: `{campo_target: campo_origen}` — p. ej. `{"task.feedback": "findings"}`. Implementación: extraer del AgentResult por ruta de puntos (helper `_get_path(obj, "output.path")`), insertar en la entrada del target.
       - **Control de reintentos de ciclos**: si el edge lleva a un nodo que ya tiene `results` y el resultado NO es OK → es un reintento: `attempts[target] += 1`; si `attempts[target] > graph.nodes[target].max_retries` → NO re-encolar: emitir `RETRY` con payload agotado y marcar para abortar con FAILED (o pasar al edge alternativo si lo hay: la condición `fail` solo aplica si quedan reintentos; si no, se busca el siguiente edge que matchee). El resultado final: `TaskResult(status=FAILED, error="reintentos agotados en nodo X")` con findings acumulados.
       - Re-encolar `(edge.target, entrada_construida)`.
6. Al terminar sin errores: `TaskResult(status=OK, task_id, summary=resultado del último nodo (o "Tarea completada"), output, findings acumulados, metrics=ExecutionMetrics(duration_ms=..., retries=sum(attempts)))`. Emitir `TASK_FINISHED`.
7. **Registro de ejecución**: cada evento lleva task_id y node_id; sink es opcional (si no hay, todo funciona igual sin eventos).

**Regla de diseño**: el runner NUNCA decide qué nodo sigue "por lógica de negocio": solo evalúa condiciones de edges. Si un nodo termina OK y no tiene edge con condición que matchee, es dead-end → stuck (decisión explícita, no silenciosa).

### Tests obligatorios (con agentes FAKE deterministas — registrar specs fake + factory fake en el registry, SIN LLM real)
- Grafo lineal A→B→C (condición always/ok): verificar orden de ejecución y que B recibió el output de A (payload result + mapping).
- Rama: A → {B si ok, C si failed}; A fake devuelve OK → se ejecuta B, no C.
- Ciclo de feedback: A(creador) → B(verificador) con edges: B ok → FIN; B fail → A (con feedback=findings, max_retries=2). A fake falla la 1ª vez, acierta la 2ª → verificar 2 visitas de A y que la 2ª recibió findings como feedback; resultado OK.
- Ciclo agotado: B siempre fail → tras max_retries → TaskResult FAILED con error claro y sin loop infinito.
- Límite `max_node_visits` (p. ej. 3) → aborta con NODE_STUCK/FAILED rápido (no cuelga).
- `graph.validate()` inválido → GraphValidationError antes de ejecutar nada.
- Eventos: con un sink de prueba (lista), verificar secuencia TASK_STARTED → NODE_STARTED(s) → ... → TASK_FINISHED y que cada evento lleva node_id correcto.

## Definition of Done
El runner ejecuta grafos lineales, ramificados y con ciclos de feedback controlados; límites garantizan terminación; comunicación directa entre nodos verificada (B recibe output de A sin Orchestrator); eventos emitidos; tests verdes.

---

# ETAPA 6 — Planner + Orchestrator

**Objetivo**: implementar el `LLMPlanner` (traduce el goal del usuario en un `ExecutionGraph` usando el catálogo de agentes) y el `DefaultOrchestrator` (flujo global: plan → runner → supervisión → replanificación acotada → resultado). Es la primera etapa que usa LLM de verdad para decidir (aunque los tests usan FakeLLM).

## Archivos a crear
- `satellite/orchestration/planner.py` → `LLMPlanner`
- `satellite/orchestration/orchestrator.py` → `DefaultOrchestrator`
- Tests: `tests/test_planner.py`, `tests/test_orchestrator.py`

## Especificación

### `LLMPlanner(Planner)`
```python
class LLMPlanner(Planner):
    def __init__(self, provider: LLMProvider, registry: AgentRegistry, *, max_attempts: int = 2): ...
    async def plan(self, task: Task, catalog: AgentRegistry,
                   context_hint: tuple[ContextRef, ...] = ()) -> ExecutionGraph
```
1. Construir el prompt del planner (template propio, en `satellite/orchestration/planner_prompt.py` o constante): sistema = "Eres el planificador de Satellite. Dada una tarea y el catálogo de agentes disponibles, produce un ExecutionGraph JSON válido."; incluye: el goal del usuario, el catálogo (id + description + capabilities + tools de cada spec — **solo eso, no prompts internos**), el `context_hint`, y el JSON Schema del grafo (ver abajo).
2. Llamar `provider.complete(...)` con `temperature=0.0`.
3. Parsear el JSON (parser de `providers/parsing.py` — ÚNICO). Errores de parseo → reintento (max_attempts).
4. Validar estructuralmente: `ExecutionGraph.from_dict` + `graph.validate()`. Además verificar que cada `spec_id` existe en el registry (si no → `SpecValidationError` con detalle) y que `task` de cada nodo es dict no vacío (o el goal si falta).
5. Si inválido → reintento con mensaje de error al LLM (los errores de validación como feedback). Tras max_attempts → `ProviderError`/`ContractError` con los errores.
6. `output_schema` del grafo (JSON Schema estricto para guiar al LLM): object con `entry_node` (string), `nodes` (array de {id, spec_id, task: object, context_refs: array de {kind enum, value}, max_retries: int}), `edges` (array de {id, source, target, condition: {type: enum["status","field"], value, field?, operator?} | null, payload: enum["result","feedback","context","none"]}).

**Nota**: el planner NO conoce implementaciones ni prompts: trabaja solo con el catálogo (specs) y produce un grafo de capacidades. La tarea concreta de cada nodo la escribe el LLM en `node.task` (instancia de la capacidad).

### `DefaultOrchestrator(Orchestrator)`
```python
class DefaultOrchestrator(Orchestrator):
    def __init__(self, planner: Planner, runner: GraphRunner, services: RuntimeServices,
                 store: Store | None = None, sink: EventSink | None = None): ...
    async def execute(self, task: Task) -> TaskResult
```
1. Emitir `TASK_STARTED`. Si hay store → `save_task`.
2. `graph = await planner.plan(task, registry)`; si store → `save_graph(task.task_id, graph)`; emitir `GRAPH_BUILT`.
3. `result = await runner.run(graph, task, services)`.
4. **Supervisión**: si `result.status == FAILED` y el fallo es de dead-end/reintentos agotados → **replanificación acotada**: reintentar el plan UNA vez más con instrucción extra al planner ("el grafo anterior falló: <error>. Produce un plan más simple o alternativo") — máx 1 replan (evitar loops). Si el 2º intento también falla → devolver FAILED con error completo y findings acumulados.
5. Persistir: si store → `save_task` con estado final (o `save_state` con el TaskResult); emitir `TASK_FINISHED`.
6. Devolver `TaskResult`.

**Inyección**: el Orchestrator recibe todo por constructor (planner, runner, services, store, sink) — la composición final se hará en Etapa 7 (CLI/web). No crea dependencias internas hardcodeadas.

### Tests obligatorios (FakeLLM con scripts que devuelven grafos JSON válidos/inválidos)
- planner: goal simple → grafo con nodos de specs del catálogo; parseo de respuesta válida; respuesta con JSON corrupto → reintento (verificar 2 llamadas al provider) y luego error; grafo con spec_id inexistente → error claro.
- planner determinismo: misma entrada + FakeLLM con misma respuesta → mismo grafo.
- orchestrator: integración con runner + registry + FakeLLM: tarea "crear y verificar html" → grafo con html_writer + html_reviewer → resultado OK (con agentes fake reales de Etapa 4 si están, o fakes mínimos en el test); fallo de runner → replanificación (verificar 2 planes); 2º fallo → FAILED.
- eventos y store: si se inyecta store fake, verificar save_task/save_graph llamados.

## Definition of Done
Goal de usuario → ExecutionGraph válido → ejecución con runner → TaskResult. Replanificación acotada funciona. Sin acoplamiento a proveedor (todo inyectado).

---

# ETAPA 7 — Persistencia + EventBus + CLI + Web Console

**Objetivo**: las 4 superficies sobre el mismo core: `FileStore` (`.satellite/`), `EventBus` con buffer consultable, CLI Typer, y Web Console FastAPI que visualiza el ExecutionGraph en vivo. Ninguna contiene lógica de ejecución: consumen `Orchestrator` + eventos.

## Archivos a crear
- `satellite/persistence/store.py` → `FileStore`
- `satellite/events/bus.py` → `EventBus`, `EventBuffer`
- `satellite/cli/app.py` → aplicación Typer (`satellite` CLI)
- `satellite/cli/__main__.py` → `python -m satellite.cli`
- `satellite/web/__init__.py`, `satellite/web/app.py` (FastAPI), `satellite/web/__main__.py` (uvicorn), `satellite/web/runtime_manager.py` (sesiones de ejecución en memoria: task_id → estado/eventos para polling)
- `satellite/web/static/index.html` (+ app.js mínimo, puede reutilizar estilos del código viejo SOLO copiando archivos estáticos, sin tocar el código viejo)
- `satellite/tools/git.py` → `GitStatusTool` (opcional pero recomendado: ejecuta `git status --short` y `git diff --stat` con permisos)
- Tests: `tests/test_file_store.py`, `tests/test_event_bus.py`, `tests/test_cli.py`, `tests/test_web.py`

## Especificación

### `FileStore(Store)` — `.satellite/`
- Raíz: `project_root/.satellite/`. Subdirectorios creados al vuelo: `tasks/`, `plans/`, `executions/`, `state/`, `context/`, `logs/`, `config/`, `agents/`.
- `save_task`/`load_task`: `tasks/<task_id>.json` (Task.to_dict/from_dict).
- `save_graph`/`load_graph`: `plans/<task_id>-graph.json`.
- `append_event`: `executions/<task_id>.jsonl` (un JSON por línea — append con `a`); `load_events`: lee y parsea las líneas (tolerante a líneas corruptas: las omite).
- `save_state`/`load_state`: `state/<task_id>.json`.
- Todo con `Path` y escritura atómica simple (escribir a `.tmp` y `os.replace`). Si `.satellite/` no existe y no se puede crear → error claro.

### `EventBus(EventSink)` + `EventBuffer`
```python
class EventBus(EventSink):
    # listeners: list[EventHandler]; emit() llama a todos (try/except, un listener que falla no rompe el bus)
    # subscribe(handler) -> None; unsubscribe opcional

class EventBuffer:
    # buffer por task_id: dict[str, list[ExecutionEvent]] + listeners
    # append(event)  (lo llama el bus si se suscribe el buffer)
    # events_since(task_id, after_index) -> (list[ExecutionEvent], next_index)   # polling
    # reset(task_id) / prune(older_than) para no crecer infinito
```
- Patrón de uso: `bus.subscribe(buffer.append)`; la web consulta `buffer.events_since(...)`.

### CLI (`cli/app.py`) — fachada Typer
- Comandos: `satellite run "goal" [--project PATH] [--watch]` (ejecuta Orchestrator; imprime eventos en vivo si --watch, o resumen final), `satellite agents` (lista catálogo: id, capabilities, tools), `satellite tasks` (lista de .satellite/tasks), `satellite status <task_id>` (lee events del store y muestra estado), `satellite inspect <task_id>` (muestra el grafo serializado formateado), `satellite config` (muestra/edita `.satellite/config/config.json` — providers por rol; ver Etapa 8/MCP para más).
- Composición (factory real): `build_runtime(project_root) -> (orchestrator, bus, store)` — lee `.satellite/config/config.json` si existe (providers), crea `ProviderFactory`, registra tools por defecto + specs del catálogo, `DefaultAgentFactory`, `DefaultContextEngine`, `FileStore`, `EventBus` → `DefaultOrchestrator`. Este builder vive en `satellite/cli/runtime_builder.py` (reutilizable por web y MCP).
- Sin lógica de ejecución en los handlers: solo llaman al orchestrator y formatean.

### Web (`web/app.py`) — FastAPI
- Endpoints (todos JSON):
  - `GET /api/system` → {root, initialized (existe .satellite), agents: n}
  - `GET /api/agents` → lista specs del catálogo
  - `POST /api/run` {goal} → crea tarea en background (`asyncio.create_task` o thread) → responde {task_id}
  - `GET /api/tasks/<task_id>/events?after=N` → eventos nuevos desde N (polling) + estado actual
  - `GET /api/tasks/<task_id>/graph` → grafo con estados por nodo (visitado, ok/fail, retries) derivado de eventos
  - `GET /api/tasks` → lista de tareas del store
  - `GET /` → index.html (SPA mínima: lista de tareas, botón run, y vista del grafo: nodos con estado coloreado — running/ok/failed — y edges; JS polling cada 1 s).
- **Seguridad**: bind 127.0.0.1 por defecto; SIN endpoints de escritura de archivos arbitrarios ni de ejecución de comandos arbitrarios (solo `run` con goal, que pasa por el runtime con SecurityPolicy). No exponer `api_key` (config: mostrar solo `***` salvo edición explícita). Sanitizar cualquier texto del LLM renderizado en el DOM (escapar HTML — nada de innerHTML con respuestas crudas).
- `RuntimeManager`: dict {task_id: {task, orchestrator, status}}; ejecuta con `asyncio`; guarda eventos en buffer + store.

### Tests obligatorios
- FileStore: roundtrip task/graph/state en tmp_path; append_event + load_events con varias líneas y una corrupta (omitida).
- EventBus: subscribe recibe eventos; listener que lanza excepción no rompe el bus; EventBuffer.events_since con índices.
- CLI: `CliRunner` de typer — run con orchestrator fake inyectado (monkeypatch del builder) → exit 0 y salida contiene resumen; agents lista specs.
- Web: `TestClient` (fastapi) — POST /api/run con orchestrator fake → task_id; GET events → lista; GET / está 200 y contiene el HTML; no hay endpoints de escritura de archivos.

## Definition of Done
`satellite run "goal"` ejecuta un flujo completo real (con LLM configurado o FakeLLM si no hay config) y persiste en `.satellite/`; `satellite status/inspect` leen el estado; la web muestra tareas y su grafo en vivo por polling. Todo sobre el mismo Orchestrator.

---

# ETAPA 8 — MCP Server (fachada sobre el runtime)

**Objetivo**: exponer Satellite como servidor MCP (Model Context Protocol) sobre stdio para que agentes externos (Hermes, OpenCode, Claude Code, OpenClaw) puedan usarlo. **MCP es una fachada**: NO implementa lógica nueva — reutiliza el runtime builder de la Etapa 7. Si la dependencia `mcp` (SDK oficial) no está instalada, usa el protocolo JSON-RPC 2.0 directamente sobre stdio (implementación mínima propia con `json`, `asyncio` y stdlib, sin dependencias nuevas).

## Archivos a crear
- `satellite/mcp/__init__.py`, `satellite/mcp/server.py` → `MCPServer` (servidor stdio JSON-RPC)
- `satellite/mcp/handlers.py` → implementación de las tools MCP
- `satellite/mcp/__main__.py` → `python -m satellite.mcp`
- Tests: `tests/test_mcp_server.py`

## Especificación

### Protocolo (si se implementa propio — recomendado por cero dependencias)
- Lee del stdin líneas JSON-RPC 2.0: `{"jsonrpc":"2.0","id":N,"method":"tools/call","params":{"name":"...","arguments":{...}}}`; responde por stdout con `{"jsonrpc":"2.0","id":N,"result":{...}}` o `{"error":{"code","message"}}`.
- Inicialización: `initialize` (devuelve protocolVersion, capabilities.tools.listChanged=false, serverInfo), `notifications/initialized`, `tools/list` (devuelve las tools declaradas), `tools/call` (ejecuta).
- Formato estándar MCP de `tools/call` result: `{"content":[{"type":"text","text":"..."}],"isError":false}`.

### Tools MCP expuestas (cada una llama al runtime)
- `satellite_run(goal: str, project_root: str | None)` → ejecuta `Orchestrator.execute` con el builder de la Etapa 7 → resultado en texto (summary + estado + error si lo hay). Bloqueante hasta terminar (o con `wait` si se quiere async).
- `satellite_agents(project_root)` → lista del catálogo (id, name, capabilities, tools) en texto/JSON.
- `satellite_graph(task_id, project_root)` → grafo serializado del task (del store) + estados de nodos si hay eventos.
- `satellite_status(task_id, project_root)` → estado actual (eventos recientes resumidos).
- `satellite_cancel(task_id)` → pide cancelación (best-effort; el runner comprueba una bandera entre nodos).

### Handlers (`handlers.py`)
- Cada tool recibe `(arguments: dict, runtime: RuntimeBundle)` donde `RuntimeBundle` = {builder, orchestrators activos por task_id, bus, store}. Construir el runtime UNA vez por `project_root` (cache en dict).
- Errores de runtime → respuesta MCP `isError: true` con mensaje claro (nunca traza cruda).

### `MCPServer`
- Bucle: lee línea a línea del stdin (`sys.stdin` en un thread o `asyncio` con `loop.run_in_executor`), despacha por método, escribe respuesta con flush. Termina limpio en EOF.
- Config: `SATELLITE_MCP_PROJECT_ROOT` env o argumento `--project-root` (default: CWD).

### Tests obligatorios
- Servidor en proceso con stdin/stdout simulados (Pipes): initialize → respuesta con serverInfo; tools/list → contiene las tools; tools/call satellite_agents (o satellite_run con FakeLLM inyectado vía builder monkeypatcheado) → resultado con content. JSON-RPC con id → respuesta con el mismo id. Método desconocido → error JSON-RPC code -32601.

## Definition of Done
El servidor MCP responde a initialize/tools/list/tools/call por stdio; `satellite_run` ejecuta un flujo real (con FakeLLM en tests); sin lógica duplicada (todo pasa por el runtime de Etapa 7). Config para conectarlo desde un cliente MCP (Hermes/OpenCode) documentada en README o comentario del archivo.

---

# RESUMEN DE DEPENDENCIAS ENTRE ETAPAS

```
Etapa 0 (hecha): contratos + ABCs + tests (50 verdes)
  → Etapa 1: tools + security (usa Tool/ToolRegistry/ToolContext de 0)
  → Etapa 2: providers (usa LLMProvider/LLMRequest/LLMResponse de 0; parsing)
  → Etapa 3: context engine (usa ContextRef/Bundle/Limits de 0; la tool request_context se registra en 1)
  → Etapa 4: agentes (usa AgentSpec/AgentResult de 0 + tools 1 + providers 2 + context 3)
  → Etapa 5: graph runner (usa Node/Edge/Graph de 0 + agentes 4)
  → Etapa 6: planner + orchestrator (usa catálogo 4 + runner 5 + providers 2)
  → Etapa 7: persistence + events + CLI + web (envuelven 6)
  → Etapa 8: MCP (fachada sobre 7)
```

Cada etapa se verifica con sus tests antes de pasar a la siguiente; la suite completa debe permanecer verde en todo momento.
