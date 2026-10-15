# PLAN DE IMPLEMENTACIÓN — SATELLITE 2.0 · ETAPAS 1-4

Autocontenido para delegar a un modelo programador. La **Etapa 0 ya está implementada y verificada** (50 tests verdes): el paquete `satellite/` existe con `core/` (modelos puros con `to_dict()/from_dict()`) y los ABCs en `providers/base.py`, `tools/base.py`, `agents/base.py`, `context/base.py`, `validation/base.py`, `orchestration/base.py`, `events/base.py`, `security/base.py`, `persistence/base.py`.

**REGLAS COMUNES A TODAS LAS ETAPAS**
1. NO hacer `git commit/push`. NO modificar/borrar archivos preexistentes (ni los de Etapa 0 ni el código viejo de la raíz). Solo crear archivos nuevos (y, donde se indique, editar archivos del paquete `satellite/` creados en etapas anteriores).
2. NO instalar dependencias nuevas sin que el brief lo autorice. El `.venv` ya tiene: `openai`, `anthropic`, `jsonschema`, `typer`, `fastapi`, `uvicorn`, `pytest`, `httpx` (transitiva). Usa SIEMPRE `.venv/Scripts/python.exe -E`.
3. Comentarios/docstrings en ingles. Type hints completos. Async de punta a punta.
4. Implementa los contratos de Etapa 0 TAL CUAL están (léelos en `satellite/core/*.py` y `satellite/*/base.py` antes de empezar). No los "mejores" ni cambies firmas. Si necesitas algo nuevo, créalo en tu capa (nunca modifiques `core/`).
5. Tests en `tests/` (p. ej. `tests/test_tools.py`, `tests/test_providers.py`...), pytest. No toques `tests/contracts/` ni `tests/` viejos.
6. Verificación al final de cada etapa: `.venv/Scripts/python.exe -E -m pytest tests/<tus_tests> -q` verde + `.venv/Scripts/python.exe -E -m pytest tests/ -q` (todo el repo sigue verde).
7. Reporte final en español: (1) archivos creados, (2) clases implementadas, (3) decisiones de diseño, (4) código anterior reutilizado y por qué, (5) descartado y por qué, (6) tests creados y nº, (7) resultado real de los tests.
8. Trabaja en la rama git actual. No toques `Desktop/Satellite_OLD`.

---

# ETAPA 1 — ToolSystem + SecurityPolicy + tools básicas

**Objetivo**: implementar la capa de herramientas real: el registro, la política de seguridad deny-by-default y las primeras tools que ejecutan de verdad sobre el filesystem/terminal. Sin LLM todavía.

## Archivos a crear
- `satellite/security/policy.py` → `DefaultSecurityPolicy`
- `satellite/tools/registry.py` → `DefaultToolRegistry`
- `satellite/tools/fs.py` → `ReadFileTool`, `WriteFileTool`, `EditFileTool`, `ListDirTool`
- `satellite/tools/terminal.py` → `RunCommandTool`
- Tests: `tests/test_security_policy.py`, `tests/test_tools_fs.py`, `tests/test_tools_terminal.py`, `tests/test_tool_registry.py`

## Especificación

### `DefaultSecurityPolicy(SecurityPolicy)` — deny-by-default
```python
@dataclass
class DefaultSecurityPolicy(SecurityPolicy):
    allowed_tools: set[str] = field(default_factory=set)       # vacío = deny todo
    allowed_paths: tuple[Path, ...] = ()                       # raíces del fs accesibles
    allowed_commands: tuple[str, ...] = ()                     # binarios/prefixes permitidos
    allow_terminal: bool = False

    def check_tool(self, tool_id, arguments, allowed_tools=None) -> bool
        # tool permitida? → en allowed_tools (si se pasa) Y en self.allowed_tools (o es tool "context")
    def check_path(self, path: Path, project_root: Path) -> bool
        # path resuelto debe estar dentro de project_root y de alguna allowed_paths (si las hay)
        # usa Path.resolve() para neutralizar ".." y symlinks → fuera de rango = False
    def check_command(self, argv: list[str]) -> bool
        # primer elemento (binario) debe matchear allowed_commands o estar bajo allowed_paths
```
- Semántica: **si la política no dice sí, es no**. `allowed_paths=()` con project_root siempre implícito (el runtime pasa project_root; la tool solo trabaja bajo project_root salvo allowed_paths extra).
- Errores de permiso: lanzar `ToolError` con mensaje claro al ejecutar (no silenciar).

### `DefaultToolRegistry(ToolRegistry)`
```python
class DefaultToolRegistry(ToolRegistry):
    def __init__(self, policy: SecurityPolicy | None = None): ...
    # dict interno {tool_id: Tool}; register valida id único (ToolError si duplicado)
    def get(self, tool_id) -> Tool          # ToolError si no existe
    def list_for(self, tool_ids) -> list[Tool]   # ToolError si algún id no existe
    def schemas_for(self, tool_ids) -> list[ToolSchema]  # usa tool.schema()
    async def execute(self, call, tool_ctx) -> ToolResult
        # 1. tool = get(call.name)
        # 2. policy.check_tool(call.name, call.arguments) → False ⇒ ToolResult(success=False, error="permiso denegado...") (NO excepción: el LLM debe ver el error y poder corregir)
        # 3. tool.execute(call.arguments, tool_ctx) con try/except ToolError → ToolResult error
        # 4. emite ExecutionEvent(TOOL_CALLED, tool_name=call.name, payload={args, success}) si tool_ctx.sink
```
- Nótese: el registry NO filtra por policy en `list_for/schemas_for` (eso lo hace quien ensambla las tools del agente en Etapa 4 consultando la política); `execute` SÍ aplica policy como red de seguridad final.

### Tools de filesystem (`fs.py`) — todas con `ToolContext`
Cada tool implementa `Tool` con `id`, `description` (en español), `parameters_schema` (JSON Schema estricto) y `async execute`. **Toda operación valida primero** `policy.check_path(target, tool_ctx.project_root)` (accede a `tool_ctx.policy` si existe — ver ToolContext abajo) → si no, `ToolResult(success=False, error="permiso denegado: <path>")`.

- `read_file(arguments: {path})` → lee texto (utf-8, con fallback errors="replace"); ToolResult.output = contenido (si > ~50 KB, trunca y avisa en error/warning: `output` truncado con nota).
- `write_file(arguments: {path, content})` → crea directorios padre; escribe utf-8; output = {"path": str, "bytes": n}.
- `edit_file(arguments: {path, old_string, new_string})` → reemplaza la PRIMERA ocurrencia exacta; error si `old_string` no aparece (ToolResult success=False con detalle); output = {"path", "reemplazos": 1}.
- `list_dir(arguments: {path, recursive: false})` → lista entradas (nombre, tipo dir/file, tamaño, mtime ISO); output = lista de dicts. Sin recursión por defecto (evitar contextos gigantes).

### `RunCommandTool` (`terminal.py`)
- `parameters_schema`: `{command: string (argv como string simple), timeout_s: int = 30, cwd_relative: string | None}`.
- Ejecuta con `asyncio.create_subprocess_exec(*shlex.split(command), cwd=tool_ctx.workdir or project_root, env=sanitizado)` — **sin shell=True**.
- `policy.check_command(argv)` antes; timeout con `asyncio.wait_for`; captura stdout+stderr (límite 64 KB cada uno, trunca con aviso); exit_code en output: `{"exit_code": n, "stdout": ..., "stderr": ...}`. success = exit_code == 0.

### Ajuste necesario en `satellite/tools/base.py` (única edición permitida de Etapa 0)
`ToolContext` debe llevar la política: añadir campo `policy: SecurityPolicy | None = None` (import desde `satellite.security.base`). Mantén el resto igual (los tests de contracts no dependen de ToolContext).

## Tests obligatorios
- policy: tool no listada → False; path dentro/ fuera de project_root; traversal `../` rechazado; comando no permitido → False.
- registry: register/get/list_for/schemas_for; execute con policy denegando → ToolResult success=False (no excepción); tool inexistente → ToolError.
- fs: write→read roundtrip en tmp_path; edit primera ocurrencia; list_dir; path fuera de rango denegado; write crea subdirectorios.
- terminal: `python -c "print(1+1)"` exit 0 stdout "2"; comando con exit != 0 → success=False; timeout respeta; comando denegado por policy → success=False.
- Usar `tmp_path` de pytest (NO tocar el repo real).

## Definition of Done
Tools ejecutables verificadas por tests; registry funcional; policy deny-by-default probada; tests verdes; nada preexistente modificado salvo el campo añadido en `tools/base.py`.

---

# ETAPA 2 — LLMProvider + ProviderFactory + adapters + tool-calling

**Objetivo**: implementar la capa de proveedores con adapters delgados que traducen el formato interno (`LLMRequest`/`LLMResponse` de `core/messages.py`) al formato nativo de cada proveedor y viceversa. Incluye `FakeLLM` (determinista, para tests) y el mecanismo robusto de parseo de tool calls.

## Archivos a crear
- `satellite/providers/fake.py` → `FakeLLM` (y `FakeToolCallScript` si hace falta para simular secuencias)
- `satellite/providers/openai_compat.py` → `OpenAICompatProvider`
- `satellite/providers/anthropic_provider.py` → `AnthropicProvider`
- `satellite/providers/factory.py` → `ProviderFactory`, `ProviderConfig`
- `satellite/providers/parsing.py` → parser único de tool calls + validación de argumentos
- Tests: `tests/test_providers_fake.py`, `tests/test_providers_parsing.py`, `tests/test_providers_openai.py`, `tests/test_providers_anthropic.py`, `tests/test_provider_factory.py`

## Especificación

### `ProviderConfig` (dataclass, en factory.py)
```python
@dataclass(frozen=True)
class ProviderConfig:
    name: str                 # "openai" | "deepseek" | "ollama" | "llama_cpp" | "gemini" | "anthropic" | "fake"
    model: str
    api_key: str | None = None
    base_url: str | None = None    # para OpenAI-compat: DeepSeek/Ollama/llama.cpp/local
    temperature: float = 0.0
    max_tokens: int | None = None
```
- `ProviderFactory.create(cfg: ProviderConfig) -> LLMProvider`; "fake" → FakeLLM; name en {"openai","deepseek","ollama","llama_cpp","gemini",cualquier base_url OpenAI-compatible} → OpenAICompatProvider; "anthropic" → AnthropicProvider. Desconocido → `ProviderError`.

### `FakeLLM(LLMProvider)` — determinista para tests
```python
class FakeLLM(LLMProvider):
    def __init__(self, script: list[dict] | None = None, default_content: str = "ok"):
        # script: lista de respuestas por llamada; cada dict:
        #   {"content": str} | {"tool_calls": [{"id","name","arguments"}]} | {"error": str}
    async def complete(self, request) -> LLMResponse
        # consume script en orden (si se agota, repite el último o default_content)
        # registra requests recibidos en self.requests (para asserts de tests)
```

### `parsing.py` — EL ÚNICO mecanismo de parseo (no duplicar en otra parte)
- `extract_tool_calls(raw: str | dict | list) -> list[ToolCall]`: acepta la forma ya-parseada del SDK (dict) o texto crudo con posible JSON embebido; si el texto contiene un bloque JSON (delimitado por ```json o primer { al último } balanceado) lo extrae; tolerante a marcado extra alrededor; NUNCA usa regex frágil múltiple; si no hay nada parseable → [].
- `validate_arguments(name, arguments, schemas: list[ToolSchema]) -> None`: localiza schema por name; valida con `jsonschema.validate(arguments, schema.parameters)`; si no hay schema para name → no valida (el registry lo rechazará después); error → `ToolError` con detalle.
- `coerce_arguments(raw) -> dict`: si los argumentos llegan como string JSON, los parsea (json.loads) con fallback seguro.

### `OpenAICompatProvider(LLMProvider)`
- Constructor: `(config: ProviderConfig)`; usa `openai.AsyncOpenAI(api_key=..., base_url=...)` (el SDK `openai` ya está en el .venv). Si `api_key` es None → "not-needed" (para Ollama/llama.cpp local).
- `complete(request)`:
  1. `messages` internos → formato OpenAI (`{"role","content"}`; assistant con `tool_calls`; tool con `tool_call_id`+`content`).
  2. `request.tools` → formato OpenAI `tools=[{"type":"function","function":{"name","description","parameters"}}]` (los schemas ya son JSON Schema).
  3. `await client.chat.completions.create(model, messages, tools?, temperature, max_tokens?)`.
  4. Respuesta → `LLMResponse`: `content` del mensaje; `tool_calls` mapeados a `ToolCall(id, name=function.name, arguments=json.loads o coerce)`; `usage` mapeado; `stop_reason = finish_reason`.
  5. Errores de API → `ProviderError` con mensaje legible (nunca la traza cruda al agente).
- Timeout global de la llamada: 120 s por defecto (configurable).

### `AnthropicProvider(LLMProvider)`
- Constructor: `(config)`; usa `anthropic.AsyncAnthropic(api_key=...)`.
- Traducción: system prompt separado (`system=...` — el primer mensaje system se extrae), mensajes user/assistant; tools → `tools=[{"name","description","input_schema"}]`; tool_result → bloque `{"type":"tool_result","tool_use_id","content"}`.
- Respuesta: `content` = concatenación de bloques text; tool_calls de bloques `tool_use` (id, name, input); `stop_reason` mapeado ("tool_use"/"end_turn"); usage mapeado.

### Tests obligatorios
- fake: script secuencial; registra requests; agota script con default.
- parsing: texto con JSON embebido extraído; dict directo; ruido alrededor; argumentos string-JSON coercidos; validación contra schema (ok y fallo).
- openai/anthropic: **sin red** — monkeypatch del cliente SDK (o inyectar un "transport" falso): construir LLMRequest con messages+tools → assert de la llamada traducida (kwargs del cliente) y de la respuesta normalizada (tool_calls → ToolCall). Cubrir: assistant con tool_calls, tool result, finish_reason tool_use.
- factory: cada name crea el provider correcto; name desconocido → ProviderError.

## Definition of Done
Los 4 providers + factory + parser únicos implementados y probados sin red; las traducciones OpenAI y Anthropic verificadas por tests con cliente falso; no se modifica `core/`.

---

# ETAPA 3 — ContextEngine: resolución just-in-time

**Objetivo**: implementar el ContextEngine real que **solo crea contexto de lo que se necesita**: resuelve `ContextRef`s a `ContextBundle` leyendo únicamente los recursos pedidos, con límites y expansión en caliente. Nada de indexar todo el proyecto al arrancar.

## Archivos a crear
- `satellite/context/engine.py` → `DefaultContextEngine`
- `satellite/context/resolver.py` → `ContextResolver` (refs → secciones)
- `satellite/context/project_index.py` → `ProjectIndex` (índice ligero incremental, mtime)
- `satellite/tools/context_tool.py` → `RequestContextTool` (la tool `request_context`)
- Tests: `tests/test_context_engine.py`, `tests/test_context_resolver.py`, `tests/test_context_tool.py`

## Especificación

### `ContextResolver`
```python
class ContextResolver:
    def __init__(self, project_root: Path, index: ProjectIndex | None = None): ...
    async def resolve_ref(self, ref: ContextRef, limits: ContextLimits | None) -> ContextSection | None
        # kind "path"  → lee el archivo (utf-8, errors="replace"); si no existe → None
        # kind "dir"   → lista los archivos del dir (1 nivel) como contenido texto
        # kind "pattern" → glob relativo a project_root (p. ej. "src/**/*.html") → concatena contenido de los matches (orden alfabético, respetando max_chars_per_section)
        # kind "symbol" → búsqueda textual simple del símbolo en los archivos del índice (grep de líneas con contexto ±3) — SOLO si el índice ya existe; si no, búsqueda directa en los archivos referidos por otros refs
        # kind "summary" → breve ficha del proyecto: lista de archivos top-level + tamaño total (sin leer contenidos)
    async def resolve_many(self, refs, limits) -> ContextBundle
        # resuelve en orden, acumula, aplica ContextLimits.violations() al final:
        # si excede → recorta secciones (de la última a la primera) hasta cumplir; nunca fabrica contenido
```
- Regla de oro: **un ref que no se puede resolver (archivo inexistente) se omite** (no es error — el agente puede pedirlo después con request_context y descubrir que falta).

### `ProjectIndex` — ligero y bajo demanda
- NO indexa el repo entero al construirse. Mantiene: `{relpath: {size, mtime_ns, symbols_opcional}}` solo de lo que ya se ha resuelto o explorado.
- `stat(path) -> dict | None` con mtime; `needs_refresh(path) -> bool` comparando mtime actual; `refresh(path)` actualiza.
- `list_project_files(patterns=None) -> list[Path]`: recorre el árbol SOLO cuando hace falta (glob), ignorando `.git`, `.venv`, `node_modules`, `.satellite`, `__pycache__`, `build/` (gitignore básico). Resultado cacheado con invalidación por mtime de los directorios.

### `DefaultContextEngine(ContextEngine)`
```python
class DefaultContextEngine(ContextEngine):
    def __init__(self, project_root: Path, limits: ContextLimits | None = None): ...
    async def resolve(self, refs, limits=None) -> ContextBundle
        # delega en resolver; registra en self._snapshots por si piden snapshot
    async def expand(self, node_id, new_refs) -> ContextBundle
        # resuelve SOLO new_refs y devuelve bundle nuevo (el runner decide cómo fusionarlo)
    async def invalidate(self, paths) -> None    # marca mtime en el índice
    async def snapshot(self, node_id) -> ContextSnapshot
        # devuelve el último bundle resuelto para node_id (refs + totales + timestamp)
```

### `RequestContextTool(Tool)` — la tool puente
- `id = "request_context"`, `categories = ("context",)`, description: "Pide contexto adicional del proyecto (archivos o patrones) al ContextEngine".
- `parameters_schema`: `{refs: [{kind: enum(path/dir/pattern/symbol/summary), value: string}]}`.
- `execute(arguments, tool_ctx)`: si `tool_ctx` no lleva engine → ToolResult error "context engine no disponible"; si no, `await engine.resolve(refs)` y devuelve el contenido en `output` (secciones con ref + contenido truncado si excede).
- Esta tool SIEMPRE está disponible para cualquier agente (no requiere allowlist — la añade el registry o el assembler de Etapa 4 automáticamente).

### Pruebas obligatorias (usar tmp_path con árbol de prueba)
- resolver path: lee solo ese archivo (verificar con monkeypatch de open que NO se leen los demás).
- pattern: glob `src/**/*.html` devuelve solo matches.
- ref inexistente → se omite sin error.
- límites: bundle que excede max_total_chars → recortado; violations() informa.
- expand: nuevo ref resuelto solo.
- invalidate: tras tocar un archivo (mtime), el índice detecta el cambio.
- context_tool: con engine → devuelve contenido; sin engine → error claro.

## Definition of Done
El engine resuelve bajo demanda con la regla "no crear contexto de lo que no se necesita" verificada por tests (los archivos no pedidos no se abren); límites funcionan; `request_context` operativa.

---

# ETAPA 4 — AgentRegistry + PromptRegistry + AgentFactory + Agent runtime + catálogo inicial

**Objetivo**: hacer ejecutable a un agente: cargar specs (JSON) y prompts (txt), instanciar el `Agent` y ejecutar UN nodo con tools reales: LLM → tool_calls → ejecución → síntesis → `AgentResult` estructurado. Catálogo inicial DEMOSTRATIVO de agentes por capacidad (html_writer, html_reviewer, test_runner, code_analyzer).

## Archivos a crear
- `satellite/agents/registry.py` → `DefaultAgentRegistry`
- `satellite/agents/prompts.py` → `DefaultPromptRegistry`
- `satellite/agents/factory.py` → `DefaultAgentFactory`
- `satellite/agents/runtime.py` → `LLMAgent(Agent)` (y helper `build_system_prompt`)
- `satellite/agents/specs/*.json` → 4 specs demo (data)
- `satellite/agents/prompts/*.txt` → 4 prompts demo (data)
- `satellite/validation/checks.py` → `FileExistsCheck`, `JsonSchemaOutputCheck` (primeros validators deterministas)
- Tests: `tests/test_agent_registry.py`, `tests/test_prompt_registry.py`, `tests/test_agent_runtime.py`, `tests/test_agent_factory.py`, `tests/test_validation_checks.py`

## Especificación

### `DefaultPromptRegistry(PromptRegistry)`
- `register(prompt_id, template)`; `get(prompt_id)` → KeyError con mensaje si falta.
- `load_dir(dir_path)`: carga todos los `*.txt` (id = nombre sin extensión).
- Convención de placeholders (el runtime los rellena): `{task}`, `{context}`, `{feedback}`, `{output_schema}`, `{tools}`.
- Método `render(prompt_id, **kwargs) -> str`: aplica `.format_map` con default seguro (placeholder sin valor → cadena vacía, sin excepción). Implementar con un dict default + `string.Formatter` o `str.format_map` sobre un `dict` con `__missing__` → "".

### `DefaultAgentRegistry(AgentRegistry)`
- `register_spec(spec)` (duplicado → SpecValidationError); `get` → KeyError claro; `all()`.
- `find_by_capability(text)`: matcheo simple case-insensitive: texto del usuario contra `spec.description + capabilities` (substring); devuelve ordenados por nº de coincidencias desc.
- `load_dir(dir_path)`: carga `*.json` → `AgentSpec.from_dict`.
- Rutas por defecto: catálogo base `satellite/agents/specs/`; el proyecto puede pasar un dir extra (`.satellite/agents`) cuyas specs **sobreescriben** por id.

### `DefaultAgentFactory(AgentFactory)`
```python
class DefaultAgentFactory(AgentFactory):
    def __init__(self, registry, prompts, tools: ToolRegistry, provider: LLMProvider,
                 policy: SecurityPolicy | None = None): ...
    def create(self, spec_id, *, model_role=None) -> Agent
        # spec = registry.get(spec_id)
        # prompt = prompts.render(spec.prompt_ref)   # base
        # tool_ids = spec.tools + ["request_context"]
        #  → filtrar por policy si hay: tools permitidas por spec.tools
        # devuelve LLMAgent(spec, prompt, tools_seleccionadas, provider)
```
- (En Etapa 6 el factory podrá elegir provider por `model_role`; por ahora uno solo inyectado.)

### `LLMAgent(Agent)` — runtime de un nodo
```python
class LLMAgent(Agent):
    def __init__(self, spec: AgentSpec, prompt: str, tools: list[Tool], provider: LLMProvider): ...
    async def execute(self, task: dict, ctx: AgentContext) -> AgentResult
```
Flujo (1 ronda de tools + síntesis; `spec.max_turns` controla rondas LLM↔tool, con tope):
1. `system` = prompt renderizada con: task (JSON bonito), context (secciones del bundle), feedback (findings anteriores si hay), output_schema (JSON si existe), tools (lista nombres+descripciones).
2. `user` = serialización del task.
3. `llm.complete(LLMRequest(messages, tools=schemas, temperature=0.0))`.
4. Si `response.tool_calls`: para cada ToolCall → `tools_registry.execute(call, tool_ctx)` (el agent lleva el registry o lo recibe; ToolContext con project_root/workdir/policy/sink). Resultados de tools → mensaje role="tool". Si quedan turnos (`spec.max_turns > 1`) → nueva llamada LLM; si no → llamada final de síntesis pidiendo el output estructurado.
5. **Parseo del resultado**: si `output_schema` existe → extraer JSON del content (usa el parser de providers/parsing.py: `extract_json_object`) y validar con jsonschema; fallo → intentar 1 vez más con mensaje de error al LLM ("tu respuesta no cumple el schema: <errores>"); si sigue fallando → `AgentResult(status=FAILED, error=...)`.
6. Construye `AgentResult`: `status=OK` (o FAILED si error), `summary` (1-2 frases), `output` (dict validado o {"content": texto}), `tool_calls` → ToolRecords (nombre, args, success, error, duración), `metrics` (tokens del usage si el provider los da, duration_ms), `error` si lo hay.
- **Sin loop de auto-corrección**: máximo `spec.max_turns` rondas de tools + 1 síntesis final. La corrección externa vendrá del grafo (Etapa 5).
- Emite eventos `node_started` (payload spec/task) y `node_finished` (payload status/resumen) si `ctx.sink`.

### `satellite/validation/checks.py` — validators deterministas (nivel 1)
```python
class FileExistsCheck(Validator):
    # validate(result, vctx): ok = Path(vctx.project_root, result.output.get("path","")).exists()
class JsonSchemaOutputCheck(Validator):
    # validate(result, vctx): valida result.output contra result.spec.output_schema si existe (o schema pasado en constructor)
```
- `CheckResult(validator_id, ok, detail)`.

### Catálogo demo — `satellite/agents/specs/*.json` + `satellite/agents/prompts/*.txt`
4 specs (nombres de capacidad, NO tareas): `html_writer`, `html_reviewer`, `test_runner`, `code_analyzer`. Cada spec JSON: id/name/description/capabilities/prompt_ref (mismo id)/tools/validators/model_role "agent"/max_turns 1. Tools: html_writer → read_file, write_file, edit_file; html_reviewer → read_file; test_runner → run_command, read_file; code_analyzer → read_file, list_dir. Prompts txt: instrucciones GENERALES de la capacidad (p. ej. html_writer: "Eres un especialista en HTML... Escribe o modifica el archivo indicado en la tarea usando tus herramientas... Devuelve un JSON con {path, summary}"), con placeholders `{task}`, `{context}`, `{feedback}`, `{output_schema}`. **Sin referencias a tareas concretas** (login/landing): la tarea llega en el placeholder.

### Tests obligatorios
- registry: carga specs dir; find_by_capability("crear html") encuentra html_writer; get inexistente → KeyError; override por id.
- prompts: register/get/load_dir; render con placeholders rellenos y con faltantes → "".
- runtime: **con FakeLLM** (Etapa 2) scripteado: (a) tool_call write_file → el agente escribe un archivo real en tmp_path y sintetiza; (b) respuesta final JSON válido → output parseado; (c) respuesta que no cumple output_schema → reintento 1 vez y luego FAILED; (d) max_turns respetado. Verificar `AgentResult.status`, `.output`, `.tool_calls` y que el archivo existe en disco.
- factory: create desde spec demo produce LLMAgent con tools filtradas.
- validation_checks: file_exists ok/ko; json_schema ok/ko.

## Definition of Done
Un agente demo ejecuta una tarea real de punta a punta con FakeLLM (escribe archivos en tmp), devuelve AgentResult estructurado, y el validador determinista lo comprueba. Catálogo + prompts cargables desde disco.
