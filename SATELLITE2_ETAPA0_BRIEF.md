# BRIEF DE IMPLEMENTACIÓN — SATELLITE 2.0 · ETAPA 0 (CONTRATOS)

Eres un ingeniero de software implementando la **Etapa 0** de Satellite 2.0. Este documento es autocontenido: no necesitas ningún contexto previo de conversaciones. Implementa SOLO lo que aquí se pide, nada más, nada menos.

---

## 0. REGLAS DEL MODELO EJECUTOR (obligatorias)

1. NO hagas `git commit` ni `git push` bajo ninguna circunstancia.
2. NO modifiques, borres ni renombres NINGÚN archivo existente del repo. Solo CREAS archivos nuevos.
3. NO toques el directorio `Desktop/Satellite_OLD` (es referencia histórica, prohibido).
4. NO cambies de rama git. Trabaja en la rama actual.
5. Implementa los contratos TAL CUAL se especifican aquí. No "mejores" el diseño, no añadas campos, no crees abstracciones extra, no reordenes archivos. Si el código existente contradice este brief, el brief manda.
6. Python 3.12, con type hints completos. Comentarios y docstrings en **ingles**. Usa únicamente stdlib (`dataclasses`, `abc`, `enum`, `json`, `pathlib`, `uuid`, `datetime`, `typing`) — el core NO usa dependencias externas.
7. **Async de punta a punta**: toda operación de IO o de ejecución en las interfaces se declara `async def`.
8. Todo modelo serializable debe tener `to_dict()` (dict JSON-serializable) y `from_dict()` (classmethod) simétricos, salvo que se indique lo contrario.
9. Al terminar, reporta en el formato exacto del apartado 10.

---

## 1. CONTEXTO DE LOS PROYECTOS

### `C:\Users\Ian\Desktop\Satellite` (repo activo)
- Es la implementación ACTUAL de Satellite, en Python, con módulos sueltos en la raíz (sin paquete): `cli.py`, `context.py`, `context_schema.py`, `dispatcher.py`, `execution_log.py`, `expander.py`, `llm.py`, `optimizer.py`, `orchestrator.py`, `planner.py`, `registry.py`, `security.py`, `semantic.py`, `store.py`, `validation.py`, más `runtime/`, `web/`, `tests/` (113 tests pytest verdes hoy), `.venv/` (Python 3.12.7), `requirements.txt`, `README.md`.
- Ese código pertenece al paradigma ANTERIOR (pipeline lineal heredado de un diseño C++, con bridges a un binario C++ en `runtime/`). **NO se reutiliza en esta Etapa 0** y NO se elimina todavía: la limpieza ocurre etapa por etapa cuando el subsistema nuevo reemplace al viejo.
- El objetivo final: rediseñar Satellite como **framework de microagentes de IA especializados por CAPACIDAD, con ExecutionGraph como mecanismo central, contexto just-in-time, tool-layer propia y async**, en un paquete Python nuevo llamado `satellite/`.

### `C:\Users\Ian\Desktop\Satellite_OLD` (prohibido tocar)
- Implementación C++ histórica completa. Solo referencia conceptual. No se porta nada de ella por inercia.

---

## 2. PRINCIPIOS DE ARQUITECTURA CONGELADOS (lo que este paquete debe reflejar)

1. Framework **agnóstico del dominio**: el core no conoce HTML/Python/etc. Los "agentes" son capacidades reutilizables definidas por datos.
2. `AgentSpec` define **qué capacidad** tiene el agente (`html_writer`). `Node.task` define **qué trabajo concreto** se ejecuta en esa ejecución. Un agente NO es una tarea única.
3. Agentes definidos **declarativamente** (specs JSON) + prompts separados (PromptRegistry). El core nunca tiene `if agent == "html_writer"`.
4. **ExecutionGraph** = ciudadano de primera clase: `Node`, `Edge`, `EdgeCondition` estructurado (sin strings arbitrarios), `GraphLimits`. Ejecución A→B directa sin pasar por el Orchestrator.
5. Loops entre agentes vía edges del grafo (p. ej. fail → volver al creador), acotados por límites del runner (`max_total_steps`, `max_node_visits`, `max_retries_global`, `timeout_s`, `token_budget`). `max_turns` (rondas internas LLM↔tool de UN nodo) ≠ `max_retries` (re-ejecución de un nodo por el grafo). Conceptos separados.
6. **Tool layer propia**: `ToolSchema`, `ToolCall`, `ToolResult`, `ToolRegistry`, `ToolContext`. Los providers LLM solo traducen formato (nunca ejecutan tools). Los agentes SÍ ejecutan acciones reales (filesystem/terminal/tests/git) respetando `SecurityPolicy` (deny-by-default).
7. **Contexto just-in-time**: no crear contexto de lo que no se necesita. `ContextEngine.resolve(refs)` devuelve solo lo pedido; `expand()` permite pedir más en caliente. Índice ligero incremental (fases posteriores).
8. **Feedback estructurado** entre agentes vía `Finding` (severity/location/message/suggestion) + `summary` en lenguaje natural para humanos. Nunca pasar historial completo de conversación entre agentes.
9. `Orchestrator` = coordinación global (inicio, planificación, supervisión, stuck/dead-end, límites, replanificación, finalización, persistencia). NO participa en cada transición A→B (eso lo hace el `GraphRunner`).
10. Persistencia en `.satellite/` (config/agents/plans/executions/context/state/logs) = estado de Satellite, ≠ contexto enviado a cada agente (cada nodo recibe solo: su task + contexto mínimo + resultados/feedback relevantes).
11. **MCP** será una fachada sobre el runtime (fases posteriores), no una segunda implementación. CLI y Web Console consumen el mismo core vía **EventSink/EventBuffer** (polling primero; interfaz preparada para SSE/WebSocket).
12. Extensible sin modificar el core: nuevos agentes = spec JSON + prompt txt (+ tool/validator si hace falta).

---

## 3. RECONOCIMIENTO PREVIO (revisar, NO cambiar nada)

Ejecuta estos comandos y verifica el estado antes de escribir código:

```bash
cd /c/Users/Ian/Desktop/Satellite
ls -la                                        # confirmar módulos sueltos en raíz, sin paquete satellite/
git status --short | head -20                 # confirmar rama y estado
git branch --show-current                     # NO cambiar de rama
.venv/Scripts/python.exe -E --version         # Python 3.12.x
.venv/Scripts/python.exe -E -m pytest tests/ -q   # 113 passed (baseline que NO debe romperse)
```

Notas de entorno:
- **Importante**: usa SIEMPRE `.venv/Scripts/python.exe -E` (el `-E` ignora la variable PYTHONPATH del sistema, que apunta a un venv ajeno y rompe imports). No uses `python` a secas.
- El directorio raíz tiene módulos sueltos (cli.py, etc.) que los tests viejos importan. Tu paquete nuevo `satellite/` (directorio con `__init__.py`) convive sin colisión: **NO crees un archivo `satellite.py` en la raíz**.

---

## 4. ESTRUCTURA A CREAR (solo archivos nuevos)

```
C:\Users\Ian\Desktop\Satellite\
├── pyproject.toml                                  (NUEVO — paquete satellite)
├── satellite/                                      (NUEVO — paquete)
│   ├── __init__.py
│   ├── core/
│   │   ├── __init__.py
│   │   ├── errors.py
│   │   ├── spec.py
│   │   ├── task.py
│   │   ├── graph.py
│   │   ├── result.py
│   │   ├── tool_models.py
│   │   ├── context_models.py
│   │   ├── messages.py
│   │   └── events.py
│   ├── providers/
│   │   ├── __init__.py
│   │   └── base.py
│   ├── tools/
│   │   ├── __init__.py
│   │   └── base.py
│   ├── agents/
│   │   ├── __init__.py
│   │   └── base.py
│   ├── context/
│   │   ├── __init__.py
│   │   └── base.py
│   ├── validation/
│   │   ├── __init__.py
│   │   └── base.py
│   ├── orchestration/
│   │   ├── __init__.py
│   │   └── base.py
│   ├── events/
│   │   ├── __init__.py
│   │   └── base.py
│   ├── security/
│   │   ├── __init__.py
│   │   └── base.py
│   └── persistence/
│       ├── __init__.py
│       └── base.py
└── tests/
    └── contracts/                                  (NUEVO — tests de Etapa 0)
        ├── fixtures/
        │   └── html_writer.spec.json
        ├── test_spec.py
        ├── test_graph.py
        ├── test_result_tools.py
        ├── test_context_models.py
        ├── test_events_messages.py
        └── test_interfaces.py
```

Los `__init__.py` de los subpaquetes pueden estar vacíos o re-exportar lo principal (re-exportar es preferible). El `__init__.py` raíz expone la versión (`__version__ = "0.2.0"`) y docstring breve.

---

## 5. ESPECIFICACIÓN EXACTA POR ARCHIVO

### 5.1 `pyproject.toml`

```toml
[project]
name = "satellite"
version = "0.2.0"
description = "Satellite 2.0: framework de microagentes de IA especializados con grafos de ejecucion y contexto bajo demanda"
requires-python = ">=3.11"
dependencies = []

[tool.pytest.ini_options]
testpaths = ["tests"]
```

### 5.2 `satellite/core/errors.py`

Jerarquía de excepciones (todas heredan de Exception):

```python
class SatelliteError(Exception): ...          # base de todo el framework
class ContractError(SatelliteError): ...      # violación de contrato/modelo
class SpecValidationError(ContractError): ... # spec inválida
class GraphValidationError(ContractError): ...# grafo inválido
class ToolError(SatelliteError): ...          # error de tool (ejecución)
class ProviderError(SatelliteError): ...      # error de LLM provider
class ContextError(SatelliteError): ...       # error de contexto
```

### 5.3 `satellite/core/spec.py` — `AgentSpec`

```python
@dataclass(frozen=True)
class AgentSpec:
    id: str                    # no vacío
    name: str                  # no vacío
    description: str = ""      # ayuda al Planner a elegir
    capabilities: tuple[str, ...] = ()   # ≥1, no vacías
    prompt_ref: str = ""       # no vacío (id en PromptRegistry)
    tools: tuple[str, ...] = ()         # puede ser vacío
    validators: tuple[str, ...] = ()    # puede ser vacío
    input_schema: dict | None = None    # JSON Schema (opcional)
    output_schema: dict | None = None   # JSON Schema (opcional)
    model_role: str = "agent"  # rol → modelo en config
    max_turns: int = 1         # ≥0 (0 = razonar sin tools)
    timeout_s: int = 120       # >0
```

- `__post_init__` valida todo y lanza `SpecValidationError` con mensaje claro (qué campo y por qué).
- `to_dict()` → dict JSON-serializable (tuplas → listas; copias de schemas).
- `from_dict(d: dict)` classmethod → acepta listas; valida tipos; lanza `SpecValidationError`.
- Los schemas se guardan tal cual (copiados), sin validar su contenido en esta etapa.
- Ejemplo de spec válida (equivale al fixture JSON del apartado 6): `AgentSpec(id="html_writer", name="HTML Writer", description="Agente especializado en crear y modificar documentos HTML", capabilities=("write_html", "modify_html"), prompt_ref="html_writer", tools=("read_file", "write_file", "edit_file"))`.

### 5.4 `satellite/core/result.py`

```python
class AgentStatus(Enum):
    OK = "ok"
    FAILED = "failed"
    NEEDS_USER_INPUT = "needs_user_input"
    SKIPPED = "skipped"

class FindingSeverity(Enum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"

@dataclass(frozen=True)
class Finding:
    severity: FindingSeverity
    message: str                    # no vacío
    location: str | None = None     # "src/index.html:42"
    suggestion: str | None = None   # orientación para el reintento

@dataclass(frozen=True)
class ToolRecord:                   # trazabilidad: qué tool usó un nodo
    tool_name: str
    arguments: dict = field(default_factory=dict)
    success: bool = True
    error: str | None = None
    duration_ms: int = 0

@dataclass(frozen=True)
class ExecutionMetrics:
    tokens_in: int = 0
    tokens_out: int = 0
    duration_ms: int = 0
    retries: int = 0
    cost_usd: float | None = None

@dataclass(frozen=True)
class AgentResult:                  # lo que un nodo entrega al siguiente
    node_id: str
    spec_id: str
    status: AgentStatus
    summary: str = ""               # lenguaje natural para humano/UI
    output: dict = field(default_factory=dict)   # conforme a output_schema
    tool_calls: tuple[ToolRecord, ...] = ()
    findings: tuple[Finding, ...] = ()           # si es verificador: feedback estructurado
    context_snapshot: ContextSnapshot | None = None  # qué contexto recibió (ver 5.9)
    metrics: ExecutionMetrics = field(default_factory=ExecutionMetrics)
    error: str | None = None
```

- Validación en `__post_init__`: `node_id`/`spec_id` no vacíos.
- `to_dict()`/`from_dict()` para Finding, ToolRecord, ExecutionMetrics, AgentResult (enums ↔ strings `.value`).
- En `from_dict`, aceptar `severity` como string o enum.

### 5.5 `satellite/core/tool_models.py`

```python
@dataclass(frozen=True)
class ToolSchema:                  # descripción de la tool para el LLM
    name: str                      # no vacío
    description: str = ""
    parameters: dict = field(default_factory=dict)  # JSON Schema

@dataclass(frozen=True)
class ToolCall:                    # lo que el LLM solicita
    id: str                        # id del provider
    name: str                      # tool_id (no vacío)
    arguments: dict = field(default_factory=dict)

@dataclass(frozen=True)
class ToolResult:                  # lo que la ejecución devuelve
    call_id: str = ""              # id del ToolCall al que responde
    success: bool = True
    output: Any = None
    error: str | None = None
    duration_ms: int = 0
```

- `to_dict()`/`from_dict()` en los tres.

### 5.6 `satellite/core/context_models.py`

```python
class ContextRefKind(Enum):
    PATH = "path"
    SYMBOL = "symbol"
    DIR = "dir"
    PATTERN = "pattern"
    SUMMARY = "summary"

@dataclass(frozen=True)
class ContextRef:
    kind: ContextRefKind
    value: str                     # no vacío

@dataclass(frozen=True)
class ContextSection:
    ref: ContextRef
    content: str
    tokens: int | None = None
    @property
    def chars(self) -> int: return len(self.content)

@dataclass(frozen=True)
class ContextBundle:               # contexto MÍNIMO de un nodo
    sections: tuple[ContextSection, ...] = ()
    @property
    def total_chars(self) -> int: return sum(s.chars for s in self.sections)
    @property
    def total_tokens(self) -> int: return sum(s.tokens or 0 for s in self.sections)

@dataclass(frozen=True)
class ContextLimits:
    max_total_chars: int | None = None
    max_sections: int | None = None
    max_chars_per_section: int | None = None
    def violations(self, bundle: ContextBundle) -> list[str]:
        # devuelve lista de descripciones de límites excedidos (vacía si ok)
        # p. ej. "max_total_chars=60000 excedido: 70000"

@dataclass(frozen=True)
class ContextSnapshot:             # auditoría: qué recibió el nodo
    node_id: str
    refs: tuple[ContextRef, ...] = ()
    total_chars: int = 0
    total_tokens: int | None = None
    created_at: str = ""           # ISO, default now
```

- `to_dict()`/`from_dict()` en todos (kind ↔ string).
- En `from_dict` aceptar `kind` como string.

### 5.7 `satellite/core/messages.py` — formato interno LLM (normalizado)

```python
class Role(Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"

@dataclass(frozen=True)
class Message:
    role: Role
    content: str = ""
    tool_calls: tuple[ToolCall, ...] = ()   # solo role=assistant
    tool_call_id: str | None = None         # solo role=tool

@dataclass(frozen=True)
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

@dataclass(frozen=True)
class LLMRequest:
    messages: tuple[Message, ...]      # no vacío
    tools: tuple[ToolSchema, ...] | None = None
    temperature: float = 0.0
    max_tokens: int | None = None

@dataclass(frozen=True)
class LLMResponse:
    content: str | None = None
    tool_calls: tuple[ToolCall, ...] | None = None
    stop_reason: str = ""
    usage: Usage | None = None
```

- `to_dict()`/`from_dict()` (role ↔ string).
- Validación: `LLMRequest.messages` no vacío; `Message` con `role==TOOL` requiere `tool_call_id`; `role==ASSISTANT` con `tool_calls` no vacío no requiere `content` (pero no se prohíbe).

### 5.8 `satellite/core/events.py`

```python
class EventType(Enum):
    TASK_STARTED = "task_started"
    GRAPH_BUILT = "graph_built"
    NODE_STARTED = "node_started"
    NODE_FINISHED = "node_finished"
    TOOL_CALLED = "tool_called"
    VALIDATION_FAILED = "validation_failed"
    RETRY = "retry"
    NODE_STUCK = "node_stuck"
    TASK_FINISHED = "task_finished"

@dataclass(frozen=True)
class ExecutionEvent:
    event_type: EventType
    task_id: str
    node_id: str | None = None
    tool_name: str | None = None
    payload: dict = field(default_factory=dict)
    timestamp: str = ""            # ISO UTC, default now
```

- `to_dict()`/`from_dict()`.

### 5.9 `satellite/core/task.py`

```python
@dataclass(frozen=True)
class Task:
    goal: str                      # no vacío (texto libre del usuario)
    project_root: Path             # Path absoluto
    metadata: dict = field(default_factory=dict)
    task_id: str = field(default_factory=lambda: uuid4().hex[:12])

class TaskStatus(Enum):
    OK = "ok"
    FAILED = "failed"
    NEEDS_USER_INPUT = "needs_user_input"
    PARTIAL = "partial"

@dataclass(frozen=True)
class TaskResult:
    task_id: str
    status: TaskStatus
    summary: str = ""
    output: dict | None = None
    findings: tuple[Finding, ...] = ()
    metrics: ExecutionMetrics = field(default_factory=ExecutionMetrics)
    error: str | None = None
    started_at: str = ""           # ISO
    finished_at: str = ""          # ISO
```

- `Task.to_dict()`/`from_dict()`: `project_root` se serializa como string (str(Path)) y se reconstruye con `Path(...)`.
- `TaskResult.to_dict()`/`from_dict()`.

### 5.10 `satellite/core/graph.py` — el corazón

```python
# --- EdgeCondition: condición ESTRUCTURADA y determinista ---
@dataclass(frozen=True)
class EdgeCondition:
    type: str                      # "status" | "field"
    value: Any = None              # p. ej. "ok" | True | 3
    field: str | None = None       # solo type=="field": "status" | "output.<ruta>" | "summary"
    operator: str | None = None    # solo type=="field": "eq"|"ne"|"gt"|"ge"|"lt"|"le"

    # classmethods fábrica:
    @classmethod
    def on_status(cls, status_value: str) -> "EdgeCondition": ...
    @classmethod
    def on_field(cls, field: str, operator: str, value: Any) -> "EdgeCondition": ...

    def matches(self, result: AgentResult) -> bool:
        # type=="status": result.status.value == str(value)  (value puede ser AgentStatus o str)
        # type=="field":
        #   campo = extraer de result: "status" → result.status.value;
        #     "summary" → result.summary; "output.<a>.<b>" → navegar dict result.output
        #   aplicar operator (eq/ne/gt/ge/lt/le) contra value
        #   ante KeyError/AttributeError/TypeError/ValueError → return False (determinista)

# --- Node: instancia CONCRETA de trabajo ---
@dataclass(frozen=True)
class Node:
    id: str                        # no vacío ("n1")
    spec_id: str                   # qué capacidad ejecuta ("html_writer")
    task: dict = field(default_factory=dict)       # la instancia concreta (goal+refs+params)
    context_refs: tuple[ContextRef, ...] = ()      # qué contexto necesita (bajo demanda)
    max_retries: int = 2           # ≥0 (re-ejecuciones por el grafo)
    model_role: str | None = None  # override de modelo para este nodo

# --- Edge: conexión directa entre nodos ---
@dataclass(frozen=True)
class Edge:
    id: str                        # no vacío
    source: str                    # node id
    target: str                    # node id
    condition: EdgeCondition | None = None   # None = siempre
    payload: str = "result"        # "result"|"feedback"|"context"|"none"
    mapping: dict = field(default_factory=dict)  # {campo_target: campo_source_result}

# --- Límites globales del grafo ---
@dataclass(frozen=True)
class GraphLimits:
    max_total_steps: int = 100     # >0
    max_node_visits: int = 10      # >0
    max_retries_global: int = 10   # ≥0
    timeout_s: int = 600           # >0
    token_budget: int | None = None

# --- El grafo ---
@dataclass
class ExecutionGraph:              # NO frozen (construcción incremental)
    entry_node: str
    nodes: dict[str, Node] = field(default_factory=dict)
    edges: list[Edge] = field(default_factory=list)
    limits: GraphLimits = field(default_factory=GraphLimits)

    def add_node(self, node: Node) -> None          # error si id duplicado
    def add_edge(self, edge: Edge) -> None
    def successors(self, node_id: str, result: AgentResult) -> list[Edge]
        # edges con source==node_id y (condition None o condition.matches(result))
    def validate(self) -> None
        # lanza GraphValidationError (mensaje agregando TODOS los problemas) si:
        #  - sin nodos; entry_node inexistente; ids duplicados
        #  - edge.source o edge.target no existen
        #  - algún nodo != entry es INALCANZABLE desde entry siguiendo edges (DFS/BFS)
        #  - payload no está en {"result","feedback","context","none"}
        #  - NÓTESE: los ciclos NO son error (se acotan por GraphLimits en runtime)
    def has_cycles(self) -> bool   # detección DFS (para tests/inspección)
    def to_dict(self) -> dict
    @classmethod
    def from_dict(cls, d: dict) -> "ExecutionGraph"
```

Validaciones de `__post_init__` en Node/Edge/EdgeCondition/GraphLimits (campos no vacíos, rangos, `payload` en conjunto permitido, `operator` válido, coherencia type/field/operator) lanzando `ContractError`/`GraphValidationError` con mensajes claros.

### 5.11 ABCs / Protocols (solo firmas, sin implementación; `NotImplementedError` vía `@abstractmethod`)

**`satellite/providers/base.py`**
```python
class LLMProvider(ABC):
    @abstractmethod
    async def complete(self, request: LLMRequest) -> LLMResponse: ...
```

**`satellite/tools/base.py`**
```python
@dataclass
class ToolContext:
    project_root: Path
    workdir: Path | None = None
    allowed_paths: tuple[Path, ...] | None = None   # raíces permitidas (security)
    env: dict = field(default_factory=dict)         # entorno sanitizado
    sink: EventSink | None = None                   # emitir eventos

class Tool(ABC):
    @property
    @abstractmethod
    def id(self) -> str: ...
    @property
    @abstractmethod
    def description(self) -> str: ...
    @property
    def categories(self) -> tuple[str, ...]: return ()
    @property
    def parameters_schema(self) -> dict: return {}
    @abstractmethod
    async def execute(self, arguments: dict, tool_ctx: ToolContext) -> ToolResult: ...

class ToolRegistry(ABC):
    @abstractmethod
    def register(self, tool: Tool) -> None: ...
    @abstractmethod
    def get(self, tool_id: str) -> Tool: ...           # ToolError si no existe
    @abstractmethod
    def list_for(self, tool_ids: list[str]) -> list[Tool]: ...
    @abstractmethod
    def schemas_for(self, tool_ids: list[str]) -> list[ToolSchema]: ...
    @abstractmethod
    async def execute(self, call: ToolCall, tool_ctx: ToolContext) -> ToolResult: ...
```

**`satellite/agents/base.py`**
```python
@dataclass
class AgentContext:
    context: ContextBundle = field(default_factory=ContextBundle)   # contexto MÍNIMO
    feedback: tuple[Finding, ...] = ()        # findings del verificador (reintento)
    sink: EventSink | None = None

class Agent(ABC):                              # runtime de UN nodo (stateless entre nodos)
    @abstractmethod
    async def execute(self, task: dict, ctx: AgentContext) -> AgentResult: ...

class AgentFactory(ABC):
    @abstractmethod
    def create(self, spec_id: str, *, model_role: str | None = None) -> Agent: ...

class AgentRegistry(ABC):
    @abstractmethod
    def register_spec(self, spec: AgentSpec) -> None: ...
    @abstractmethod
    def get(self, spec_id: str) -> AgentSpec: ...
    @abstractmethod
    def find_by_capability(self, text: str) -> list[AgentSpec]: ...
    @abstractmethod
    def all(self) -> list[AgentSpec]: ...

class PromptRegistry(ABC):
    @abstractmethod
    def get(self, prompt_id: str) -> str: ...
    @abstractmethod
    def register(self, prompt_id: str, template: str) -> None: ...
```

**`satellite/context/base.py`**
```python
class ContextEngine(ABC):
    @abstractmethod
    async def resolve(self, refs: list[ContextRef] | tuple[ContextRef, ...],
                      limits: ContextLimits | None = None) -> ContextBundle: ...
    @abstractmethod
    async def expand(self, node_id: str, new_refs: list[ContextRef] | tuple[ContextRef, ...]) -> ContextBundle: ...
    @abstractmethod
    async def invalidate(self, paths: list[str]) -> None: ...
    @abstractmethod
    async def snapshot(self, node_id: str) -> ContextSnapshot: ...
```

**`satellite/validation/base.py`**
```python
@dataclass(frozen=True)
class CheckResult:
    validator_id: str
    ok: bool
    detail: str = ""

@dataclass
class ValidationContext:
    project_root: Path
    workdir: Path | None = None

class Validator(ABC):
    @abstractmethod
    async def validate(self, result: AgentResult, vctx: ValidationContext) -> CheckResult: ...
```

**`satellite/orchestration/base.py`**
```python
@dataclass
class RuntimeServices:               # inyección de servicios al runner (parcial en Etapa 0)
    registry: AgentRegistry | None = None
    factory: AgentFactory | None = None
    tools: ToolRegistry | None = None
    context: ContextEngine | None = None
    provider: LLMProvider | None = None
    sink: EventSink | None = None

class Planner(ABC):                  # LLM → ExecutionGraph (Etapa 6)
    @abstractmethod
    async def plan(self, task: Task, catalog: AgentRegistry,
                   context_hint: tuple[ContextRef, ...] = ()) -> ExecutionGraph: ...

class GraphRunner(ABC):              # ejecuta A→B→C directo (Etapa 5)
    @abstractmethod
    async def run(self, graph: ExecutionGraph, task: Task,
                  services: RuntimeServices) -> TaskResult: ...

class Orchestrator(ABC):             # global: inicia/supervisa/replanifica (Etapa 6)
    @abstractmethod
    async def execute(self, task: Task) -> TaskResult: ...
```

**`satellite/events/base.py`**
```python
class EventSink(ABC):
    @abstractmethod
    def emit(self, event: ExecutionEvent) -> None: ...
    @abstractmethod
    def subscribe(self, handler: Callable[[ExecutionEvent], None]) -> None: ...
```

**`satellite/security/base.py`**
```python
class SecurityPolicy(ABC):           # deny-by-default
    @abstractmethod
    def check_tool(self, tool_id: str, arguments: dict,
                   allowed_tools: set[str] | None = None) -> bool: ...
```

**`satellite/persistence/base.py`**
```python
class Store(ABC):                    # .satellite/ (Etapa 7)
    @abstractmethod
    async def save_task(self, task: Task) -> None: ...
    @abstractmethod
    async def load_task(self, task_id: str) -> Task: ...
    @abstractmethod
    async def save_graph(self, task_id: str, graph: ExecutionGraph) -> None: ...
    @abstractmethod
    async def load_graph(self, task_id: str) -> ExecutionGraph: ...
    @abstractmethod
    async def append_event(self, task_id: str, event: ExecutionEvent) -> None: ...
    @abstractmethod
    async def load_events(self, task_id: str) -> list[ExecutionEvent]: ...
    @abstractmethod
    async def save_state(self, task_id: str, state: dict) -> None: ...
    @abstractmethod
    async def load_state(self, task_id: str) -> dict | None: ...
```

Los ABCs de `events/base.py` y `security/base.py` NO se importan desde `core/` (evitar ciclos): `core/events.py` define solo el modelo `ExecutionEvent`; la interfaz `EventSink` vive en `events/base.py` y los demás módulos la importan de ahí.

---

## 6. FIXTURE — `tests/contracts/fixtures/html_writer.spec.json`

```json
{
  "id": "html_writer",
  "name": "HTML Writer",
  "description": "Agente especializado en crear y modificar documentos HTML",
  "capabilities": ["write_html", "modify_html"],
  "prompt_ref": "html_writer",
  "tools": ["read_file", "write_file", "edit_file"],
  "validators": [],
  "model_role": "agent",
  "max_turns": 1,
  "timeout_s": 120
}
```

---

## 7. TESTS REQUERIDOS (pytest; casos mínimos obligatorios)

**`tests/contracts/test_spec.py`**
- Cargar el fixture JSON con `AgentSpec.from_dict(json.load(...))` → campos correctos (tuplas).
- `to_dict()` → `from_dict()` → igualdad (round-trip).
- Errores → `SpecValidationError`: id vacío; name vacío; capabilities vacías; prompt_ref vacío; max_turns negativo; timeout_s 0.
- `capabilities` con strings vacíos → error.
- Frozen: asignar atributo lanza `dataclasses.FrozenInstanceError`.

**`tests/contracts/test_graph.py`**
- Grafo lineal A→B→C con `EdgeCondition.on_status("ok")`: `successors("n1", AgentResult ok)` devuelve la arista a B; con resultado `failed` devuelve lista vacía.
- Condición `status failed` selecciona la arista fail.
- Condición `on_field("output.tests_passed", "eq", True)`: matches True/False correctos.
- Condiciones `gt/ge/lt/le` con números; campo inexistente → no matchea.
- `validate()` pasa en grafo válido (lineal y en Y).
- `validate()` lanza `GraphValidationError` en: entry inexistente; edge con target inexistente; nodo inalcanzable desde entry.
- Ciclo A→B→A: `has_cycles()` True pero `validate()` NO lanza (los ciclos se acotan por límites).
- Round-trip `to_dict()`/`from_dict()` del grafo completo (nodos, edges, condiciones, límites).

**`tests/contracts/test_result_tools.py`**
- Round-trip de `AgentResult` completo (con findings, tool_records, metrics).
- Round-trip de `Finding` (severity como string y como enum).
- `ToolCall`/`ToolResult` round-trip; `ToolResult.output` arbitrario (str, dict, None).

**`tests/contracts/test_context_models.py`**
- `ContextRef` kind válidos; kind inválido (string raro) → error.
- `ContextBundle.total_chars` = suma de contenidos; `total_tokens` idem.
- `ContextLimits.violations()`: vacío si ok; reporta al superar `max_total_chars` y `max_sections`.
- Round-trip `ContextSnapshot`.

**`tests/contracts/test_events_messages.py`**
- `EventType` cubre los 9 valores esperados; `ExecutionEvent` round-trip conserva type/payload/node_id.
- `Message` roles; `Message(role=TOOL)` sin `tool_call_id` → error de contrato.
- `LLMRequest` con messages vacío → error; con messages válidos → ok.
- `LLMResponse` round-trip con tool_calls.

**`tests/contracts/test_interfaces.py`**
- Los ABCs NO se pueden instanciar directamente (TypeError).
- Implementar `FakeLLM(LLMProvider)` (devuelve `LLMResponse(content="ok")`) → instanciable; `inspect.iscoroutinefunction(FakeLLM.complete)` es True.
- Implementar `FakeTool(Tool)` con `id`, `description`, `async execute` → instanciable y devuelve `ToolResult`.
- Implementar `FakeValidator(Validator)`, `FakeContextEngine(ContextEngine)`, `FakeStore(Store)` → instanciables.
- Verificar que las firmas de los métodos abstractos de Agent/AgentFactory/AgentRegistry/PromptRegistry/Planner/GraphRunner/Orchestrator/EventSink/SecurityPolicy/ToolRegistry aceptan subclases mínimas (implementaciones triviales que no lancen en construcción).

---

## 8. VERIFICACIÓN (comandos y resultado esperado)

```bash
cd /c/Users/Ian/Desktop/Satellite
.venv/Scripts/python.exe -E -m pytest tests/contracts -q      # TODOS verdes
.venv/Scripts/python.exe -E -m pytest tests/ -q               # 113 tests viejos SIGUEN verdes
```

- Primer comando: 100% passed (los tests de Etapa 0).
- Segundo comando: ningún test viejo roto (el nuevo paquete `satellite/` convive con los módulos sueltos).
- Además: `.venv/Scripts/python.exe -E -c "import satellite.core.graph, satellite.providers.base; print('imports_ok')"` → `imports_ok` (sin errores de import circulares).

---

## 9. DEFINITION OF DONE

- [ ] Existe `satellite/` con todos los archivos del apartado 4.
- [ ] `core/` usa solo stdlib; todos los modelos tienen `to_dict()`/`from_dict()` simétricos.
- [ ] Todos los ABCs declarados existen con las firmas exactas del apartado 5.11.
- [ ] No hay imports circulares entre `core/`, `providers/base.py`, `tools/base.py`, `agents/base.py`, `context/base.py`, `validation/base.py`, `orchestration/base.py`, `events/base.py`, `security/base.py`, `persistence/base.py`.
- [ ] No se modificó/borró ningún archivo preexistente; no hay commits; no se tocó `requirements.txt` ni `.venv`.
- [ ] `pytest tests/contracts -q` → verde.
- [ ] `pytest tests/ -q` → verde (113 viejos + nuevos).

---

## 10. FORMATO DEL REPORTE FINAL (obligatorio, en español)

1. **Contratos creados**: lista de modelos con 1 línea cada uno.
2. **Interfaces creadas**: lista de ABCs con 1 línea cada uno.
3. **Decisiones de diseño tomadas**: las que tomaste dentro del margen del brief (p. ej. detalles de `matches()`, manejo de enums en from_dict) + confirmación de que no te saliste del brief.
4. **Qué partes del código anterior se reutilizaron y por qué**: en Etapa 0 se espera "ninguna o casi ninguna" — si reutilizaste algo, justifícalo; si no, dilo explícito.
5. **Qué partes se descartaron y por qué**: breve (el pipeline viejo contradice el paradigma nuevo; la limpieza es progresiva).
6. **Tests creados**: lista de archivos + nº de tests por archivo.
7. **Resultado de los tests**: salida resumida real de los dos comandos del apartado 8 (números, no inventar).
