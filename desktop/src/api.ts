/** Cliente tipado de la API local. El token y las URLs viven en el proceso principal. */

// Sólo el tipo: la etiqueta de cada evento es una clave del diccionario i18n. La importación
// es `type` a propósito, así no se crea un ciclo en tiempo de ejecución con `i18n.tsx`.
import type { Clave } from "./i18n";

export interface Status {
  project_root: string;
  initialized: boolean;
  agents: number;
  skills: number;
  provider: string;
  model: string;
  policy_mode: string;
  sandbox: string;
  safe_mode: boolean;
  context_percent: number;
  context_tokens: number;
  context_window: number;
  context_source: string;
  runtime_ready: boolean;
  runtime_error?: string;
  /** El proyecto lo eligió una persona (si no, el onboarding lo pide). */
  project_explicit: boolean;
  /** Hay proveedor resoluble (lo que hace falta para que el runtime funcione). */
  provider_configured: boolean;
}

export interface SessionInfo {
  id: string;
  name: string;
  turns: number;
  updated_at: string;
  active: boolean;
  /** Fijada: va arriba en la lista principal. Fijar no cambia cuál está activa. */
  pinned?: boolean;
  /** Archivada: se guarda aparte; no aparece en la lista principal ni entre las activas. */
  archived?: boolean;
}

/** `GET /sessions` y la respuesta de `PATCH /sessions/{id}`. */
export interface SessionsView {
  current: string;
  sessions: SessionInfo[];
}

/** Respuesta de `PATCH /sessions/{id}`: la sesión tocada y el estado ya recalculado. */
export interface SessionMutationView extends SessionsView {
  session_id: string;
  name: string;
  pinned: boolean;
  archived: boolean;
}

export interface Turn {
  role: string;
  content: string;
  task_id: string;
  status: string;
}

export interface ChatReply {
  text: string;
  task_id: string;
  status: string;
  error?: string | null;
  compaction?: unknown;
  /** @menciones del mensaje enviado: lo adjuntado y lo que no se encontró. */
  context?: MentionSummary;
  /** Alternativas del modo plan, si la respuesta las trae (`null` = no hay ninguna). */
  plan?: PlanOptionsView | null;
}

/** Una alternativa concreta del modo plan. */
export interface PlanOptionView {
  id: string;
  title: string;
  summary: string;
  approach: string[];
  tradeoffs: string;
  effort: string;
  risk: string;
}

/** Alternativas propuestas para una tarea ambigua (modo plan). */
export interface PlanOptionsView {
  type?: string;
  question: string;
  options: PlanOptionView[];
  recommendation: string;
}

/** Referencia externa de una @mención y en qué quedó su aprobación. */
export type ExternalMentionStatus = "approved" | "denied" | "no_port" | "no_engine" | "cancelled";

/** Resumen de las @menciones de un mensaje (mismo parseo que la TUI). */
export interface MentionSummary {
  refs: { value: string; kind: string }[];
  /** @menciones a rutas fuera del proyecto, con el estado de su aprobación puntual. */
  external?: { value: string; path: string; status: ExternalMentionStatus }[];
  /** @menciones que coinciden con varias rutas: no se lee ninguna, se pide la completa. */
  ambiguous?: { value: string; candidates: string[] }[];
  /** @menciones a archivos sensibles/excluidos: no se adjuntan, pero se dicen. */
  ignored?: string[];
  missing: string[];
  summary: string;
}

/** Un candidato para una @mención del compositor (`GET /context/mentions`). */
export interface MentionSuggestion {
  value: string;
  kind: string;
}

export interface RuntimeEvent {
  event_type: string;
  task_id: string;
  node_id?: string | null;
  tool_name?: string | null;
  payload: Record<string, unknown>;
  timestamp: string;
}

export interface StreamItem {
  index: number;
  event: RuntimeEvent;
}

export interface Approval {
  proposal_id: string;
  kind: string;
  task_id: string;
  summary: string;
  reason: string;
  details: Record<string, unknown>;
  options: string[];
}

export interface TaskItem {
  task_id: string;
  goal?: string;
  status?: string;
  changes: string[];
  error?: string;
}

export interface JobInfo {
  job_id: string;
  command: string;
  pid: number;
  status: string;
  exit_code: number | null;
  started_at: string;
  cwd: string;
}

export interface JobOutput extends JobInfo {
  output: string;
  controllable: boolean;
}

/** Resultado de esperar a que termina un trabajo de fondo. */
export interface JobWaitView {
  job_id: string;
  estado: string;
  status: string;
}

/** Una entrada del registro de cambios de una tarea (`GET /tasks/{id}/changes`). */
export interface ChangeEntry {
  path: string;
  /** `false` = el archivo no existía antes: lo creó la tarea. */
  existed?: boolean;
  /** `false` = hay cambio, pero sin copia previa: no se puede deshacer ni mostrar diff. */
  restorable?: boolean;
  bytes?: number;
  backup?: string;
}

export interface ChangeSet {
  task_id: string;
  files: string[];
  entries: ChangeEntry[];
  diff: string;
}

export interface RestorePreview {
  confirmed: boolean;
  preview?: ChangeSet;
  note?: string;
  result?: { restored?: string[]; removed?: string[]; skipped?: string[] };
}

export interface ConfigView {
  config: Record<string, unknown>;
  policy_mode: string;
  sandbox: string;
  providers: Record<string, unknown>[];
  active_provider: string;
  /** Presente cuando el backend rearmó el runtime tras un cambio (p. ej. PATCH de proveedor). */
  runtime_error?: string;
}

/** Control de escritorio: flag local + proveedor/modelo de visión seleccionado. */
export interface DesktopControlView {
  enabled: boolean;
  vision: {
    provider: string;
    model: string;
    configured: boolean;
    /** Interfaz disponible (el proveedor está seleccionado); no implica visión real. */
    available: boolean;
    /** F7: la visión real no está implementada. */
    implemented: boolean;
    reason: string;
    interface: string;
  };
  ocr: { available: boolean; boxes: boolean; interface: string };
  guards: {
    approvals: boolean;
    grants: boolean;
    limits: boolean;
    audit: boolean;
    mcp: boolean;
  };
  runtime_error?: string;
}

/** Config efectiva del proxy de saneamiento, ya redactada (nunca lleva secretos). */
export interface SafeProxyConfigView {
  server?: Record<string, unknown>;
  upstream?: Record<string, unknown>;
  policy?: Record<string, unknown>;
  code?: Record<string, unknown>;
}

/** Estado real del Safe Proxy (lo que reporta el backend). */
export type SafeProxyState =
  | "internal"
  | "stopped"
  | "running"
  | "config-error"
  | "port-conflict";

/** Modo: dentro del ciclo del proveedor (sin puerto) o servidor en loopback. */
export type SafeProxyMode = "internal" | "server";

/** Safe Proxy: su estado y su configuración (tokens write-only, nunca aquí). */
export interface SafeProxyView {
  state: SafeProxyState;
  mode: SafeProxyMode;
  /** Interruptor real del modo interno (con él apagado, la petición hace bypass). */
  internal_enabled: boolean;
  host: string;
  port: number;
  url: string;
  /** Los tokens están guardados en el host; su valor nunca llega aquí. */
  control_token_set: boolean;
  online: boolean;
  enabled: boolean | null;
  control: { available?: boolean; token_configured?: boolean; token_source?: string } | null;
  state_payload: Record<string, unknown> | null;
  config: SafeProxyConfigView | null;
  error: string;
  error_code: string;
}

export interface AgentSpecView {
  id: string;
  name?: string;
  description?: string;
  capabilities?: string[];
}

export interface SkillView {
  id: string;
  description?: string;
  tags?: string[];
  stats?: Record<string, unknown>;
}

/** Herramienta que expone el runtime, tal como la lista `GET /tools`. */
export interface ToolView {
  id: string;
  categories: string[];
  description: string;
  reserved: boolean;
}

/** Uso de contexto del modelo: lo que se enviaría y lo que se estima de verdad. */
export interface ContextView {
  text: string;
  percent: number;
  used_tokens: number;
  real_tokens: number;
  estimated_tokens: number;
  window_tokens: number;
  window_source: string;
}

/** Detalle de una tarea: lo que se guarda, su grafo, sus eventos y sus cambios. */
export interface TaskDetailView {
  task_id: string;
  task?: Record<string, unknown>;
  graph?: Record<string, unknown>;
  graph_error?: string;
  state?: Record<string, unknown>;
  events?: {
    event_type: string;
    timestamp?: string;
    tool_name?: string | null;
    payload?: Record<string, unknown>;
  }[];
  changes?: { files?: string[]; entries?: ChangeEntry[] };
}

/** Un dato que una acción pide antes de ejecutarse (paleta con campos guiados). */
export interface CommandArg {
  key: string;
  label: string;
  optional: boolean;
  placeholder?: string;
}

/** Un comando/acción del catálogo compartido (`GET /commands`). */
export interface CommandSpecView {
  id: string;
  category: string;
  summary: string;
  usage: string;
  needs_runtime: boolean;
  /** Superficies donde aparece como comando de paleta. */
  surfaces: string[];
  /** No se ejecuta aquí: la app da el comando para una terminal (p. ej. `tui`). */
  terminal_only: boolean;
  args: CommandArg[];
}

/** Informe de paridad del catálogo (`GET /commands`). */
export interface ParityView {
  commands: number;
  per_surface: Record<string, number>;
  gaps: Record<string, string[]>;
}

export interface CatalogEntry {
  key: string;
  label: string;
  family: string;
  base_url: string;
  group: string;
  note: string;
  needs_edit: boolean;
}

export interface SavedProviderView {
  id: string;
  name: string;
  model?: string;
  base_url: string;
  api_key_set?: boolean;
  api_key_env?: string;
  /** Nivel de esfuerzo guardado (""/"auto" = lo decide el proveedor). */
  reasoning_effort?: string;
  /** Interruptor «Think» guardado (null/ausente = sin fijar). */
  think?: boolean | null;
  /** Niveles de esfuerzo que admite su modelo (vacío = el control se desactiva). */
  effort_options?: string[];
  /** Metadata de esfuerzo del modelo (control, default, origen, inferido). */
  effort_meta?: EffortMeta;
  /** Cuenta del proveedor cuando su listado de modelos es por cuenta (p. ej. Fireworks). */
  account_id?: string;
  /** Tope de tokens de salida guardado (0/ausente = sin límite explícito). */
  max_tokens?: number;
}

export interface RolesView {
  roles: string[];
  configured: Record<
    string,
    {
      provider?: string;
      model?: string;
      reasoning_effort?: string;
      think?: boolean | null;
      effort_levels?: string[];
      effort_control?: string;
    }
  >;
  active: string;
  providers: SavedProviderView[];
  runtime_error?: string;
}

/** Metadata de esfuerzo de un modelo, ya normalizada por el backend. */
export interface EffortMeta {
  /** Opciones ofrecibles ("auto", "off" y los niveles). Vacío = el control se desactiva. */
  options: string[];
  levels: string[];
  default: string;
  /** Tipo de control: reasoning_effort | thinking_level | output_config.effort | budget_tokens | provider-specific. */
  control: string;
  /** Parámetro que de verdad se envía (puede ir vacío si no hay canal). */
  param: string;
  can_disable: boolean;
  /** El modelo declara razonamiento (esfuerzo o canal Think). */
  reasoning?: boolean;
  /** Canal Think explícito (p. ej. "thinking"): vacío = el modelo no lo declara. */
  think_param?: string;
  think_on?: string;
  think_off?: string;
  /** De dónde salen los niveles: announced | override | table | none. */
  source: string;
  /** true = deducido del nombre (tabla por prefijo), no declarado por el proveedor. */
  inferred: boolean;
}

export interface ModelsResult {
  ok: boolean;
  url: string;
  models: string[];
  error?: string | null;
  /** El más usado según la tabla curada que ya usa la TUI. */
  suggested?: string;
  /** Capacidades que anunció el proveedor, por modelo (no se reduce el catálogo a ids). */
  capabilities?: Record<
    string,
    {
      reasoning?: boolean;
      effort?: {
        supported_levels?: string[];
        default_level?: string;
        control?: string;
        can_disable?: boolean;
      };
      /** Canal declarado por el proveedor (p. ej. reasoning_effort en OpenRouter). */
      control?: string;
      can_disable?: boolean;
      /** Sólo existe en la API nativa (Gemini thinking_level): no se envía por la compatible. */
      native_only?: boolean;
      supported_parameters?: string[];
      methods?: string[];
      source?: string;
    }
  >;
  /** Niveles de esfuerzo ofrecibles por modelo (catálogo de capacidades + lo anunciado). */
  effort_options?: Record<string, string[]>;
  /** Metadata de esfuerzo por modelo (control, default, origen, inferido). */
  effort_meta?: Record<string, EffortMeta>;
  /** Código HTTP de la última respuesta (0 si ni siquiera hubo respuesta). */
  status?: number;
  /** Rechazo de credencial (401/403): la interfaz lo trata aparte. */
  auth_error?: boolean;
  /** Proveedor reconocido (clave de la estrategia de listado). */
  provider?: string;
}

export interface DoctorCheck {
  ok: boolean;
  mensaje: string;
}

/** Un archivo en el estado de git, con su código de dos letras (X=índice, Y=worktree). */
export interface GitFileEntry {
  path: string;
  code: string;
  staged: boolean;
  unstaged: boolean;
  untracked: boolean;
  conflicted: boolean;
}

/** Estado real del repo (`GET /git/status`). */
export interface GitStatusView {
  is_repo: boolean;
  error?: string;
  root?: string;
  workdir?: string;
  branch?: string;
  detached?: boolean;
  upstream?: string;
  ahead?: number;
  behind?: number;
  clean?: boolean;
  files?: GitFileEntry[];
  staged?: GitFileEntry[];
  unstaged?: GitFileEntry[];
  untracked?: GitFileEntry[];
  conflicts?: GitFileEntry[];
}

/** Un worktree registrado (`GET /worktrees`). */
export interface WorktreeEntry {
  path: string;
  branch: string;
  head: string;
  detached: boolean;
  locked: boolean;
  prunable: boolean;
  managed: boolean;
  main: boolean;
  /** Tiene cambios sin confirmar (sólo se calcula para los gestionados). */
  dirty?: boolean;
}

export interface WorktreesView {
  is_repo: boolean;
  error?: string;
  root: string;
  managed_root?: string;
  worktrees: WorktreeEntry[];
  /** Ruta del worktree activo como contexto de ejecución ("" = el proyecto). */
  active: string;
  runtime_error?: string;
  created?: { path: string; branch: string; base: string };
  removed?: string;
}

/** Resultado de una mutación git: el estado fresco va en `status`. */
export interface GitMutationView {
  result: Record<string, unknown>;
  status: GitStatusView;
}

/** Un comentario inline anclado a líneas del diff (persistido). */
export interface DiffComment {
  id: string;
  file: string;
  side: string;
  line_start: number;
  line_end: number;
  text: string;
  session_id: string;
  task_id: string;
  author: string;
  created_at: string;
  resolved: boolean;
  resolved_at: string;
}

/** Un item de la cola de instrucciones (persistida). */
export interface QueueItem {
  id: string;
  text: string;
  plan_mode: boolean;
  created_at: string;
}

export interface Envelope<T> {
  ok: boolean;
  data?: T;
  error?: { code: string; message: string; status: number };
}

export class ApiError extends Error {
  constructor(
    readonly code: string,
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export interface EstadoVentana {
  maximized: boolean;
  fullscreen: boolean;
}

/** Preferencias de aspecto persistidas por el proceso principal. */
export interface PreferenciaAspecto {
  theme: string;
  text_intensity: string;
  chat_watermark: boolean;
  chat_watermark_opacity: number;
  language: string;
}

interface Bridge {
  request: (ruta: string, options?: { method?: string; body?: unknown }) => Promise<Envelope<unknown>>;
  subscribe: (after: number, onItem: (item: unknown) => void) => () => void;
  onBackendDown: (onDown: (mensaje: string) => void) => () => void;
  restart: () => Promise<Envelope<unknown>>;
  pickFolder: () => Promise<string>;
  info: () => Promise<{ ready: boolean; repoRoot: string; dev: boolean }>;
  chatgpt: {
    status: () => Promise<{ connected: boolean; email: string }>;
    login: () => Promise<{ email: string }>;
    disconnect: () => Promise<{ connected: boolean; email: string }>;
  };
  claude: { login: () => Promise<{ launched: boolean; message?: string }> };
  platform: string;
  windowControls: {
    minimize: () => void;
    toggleMaximize: () => void;
    close: () => void;
    state: () => Promise<EstadoVentana>;
    onState: (onState: (state: EstadoVentana) => void) => () => void;
  };
  theme: {
    initial: string;
    initialIntensity: string;
    initialWatermark: string;
    initialWatermarkOpacity: string;
    initialLanguage: string;
    get: () => Promise<PreferenciaAspecto>;
    set: (theme: string) => Promise<PreferenciaAspecto>;
    setIntensity: (intensity: string) => Promise<PreferenciaAspecto>;
    setWatermark: (visible: boolean) => Promise<PreferenciaAspecto>;
    setWatermarkOpacity: (opacity: number) => Promise<PreferenciaAspecto>;
    setLanguage: (language: string) => Promise<PreferenciaAspecto>;
  };
}

declare global {
  interface Window {
    satellite: Bridge;
  }
}

function puente(): Bridge {
  return window.satellite;
}

/** Petición al backend; convierte el sobre en datos o en un error con mensaje legible. */
export async function api<T>(ruta: string, options: { method?: string; body?: unknown } = {}): Promise<T> {
  const sobre = await puente().request(ruta, options);
  if (!sobre.ok) {
    const error = sobre.error ?? { code: "unknown", message: "error desconocido", status: 0 };
    throw new ApiError(error.code, error.message, error.status);
  }
  return sobre.data as T;
}

export const bridge = {
  subscribe: (after: number, onItem: (item: StreamItem) => void) =>
    puente().subscribe(after, (item) => onItem(item as StreamItem)),
  onBackendDown: (onDown: (mensaje: string) => void) => puente().onBackendDown(onDown),
  restart: () => puente().restart(),
  pickFolder: () => puente().pickFolder(),
  info: () => puente().info(),
  chatgpt: {
    status: () => puente().chatgpt.status(),
    login: () => puente().chatgpt.login(),
    disconnect: () => puente().chatgpt.disconnect(),
  },
  claude: { login: () => puente().claude.login() },
  plataforma: () => puente().platform,
  estadoVentana: () => puente().windowControls.state(),
  alCambiarVentana: (onEstado: (estado: EstadoVentana) => void) =>
    puente().windowControls.onState(onEstado),
  minimizar: () => puente().windowControls.minimize(),
  alternarMaximizar: () => puente().windowControls.toggleMaximize(),
  cerrar: () => puente().windowControls.close(),
  temaInicial: () => puente().theme.initial,
  intensidadInicial: () => puente().theme.initialIntensity,
  marcaInicial: () => puente().theme.initialWatermark,
  marcaOpacidadInicial: () => puente().theme.initialWatermarkOpacity,
  idiomaInicial: () => puente().theme.initialLanguage,
  leerTema: () => puente().theme.get(),
  guardarTema: (tema: string) => puente().theme.set(tema),
  guardarIntensidad: (intensidad: string) => puente().theme.setIntensity(intensidad),
  guardarMarca: (visible: boolean) => puente().theme.setWatermark(visible),
  guardarMarcaOpacidad: (opacidad: number) => puente().theme.setWatermarkOpacity(opacidad),
  guardarIdioma: (idioma: string) => puente().theme.setLanguage(idioma),
};

/** Texto legible de un error, sin envoltorios de Electron. */
export function mensajeDe(exc: unknown): string {
  if (exc instanceof ApiError) return exc.message;
  if (exc instanceof Error) return exc.message.replace(/^Error invoking remote method '[^']+': /, "");
  return String(exc);
}

/** Etiqueta visible de cada tipo de evento: una clave del diccionario i18n (`t(...)`). */
export const ETIQUETA_EVENTO: Record<string, Clave> = {
  task_started: "event.task_started",
  graph_built: "event.graph_built",
  node_started: "event.node_started",
  node_finished: "event.node_finished",
  tool_called: "event.tool_called",
  validation_failed: "event.validation_failed",
  retry: "event.retry",
  node_stuck: "event.node_stuck",
  task_finished: "event.task_finished",
  plan_failed: "event.plan_failed",
  agent_proposed: "event.agent_proposed",
  agent_approved: "event.agent_approved",
  agent_rejected: "event.agent_rejected",
  agent_created: "event.agent_created",
  agent_extended: "event.agent_extended",
  skill_proposed: "event.skill_proposed",
  skill_approved: "event.skill_approved",
  skill_searched: "event.skill_searched",
  skill_executed: "event.skill_executed",
  skill_failed: "event.skill_failed",
  skill_quarantined: "event.skill_quarantined",
  delegation_started: "event.delegation_started",
  delegation_completed: "event.delegation_completed",
  policy_violation: "event.policy_violation",
  safe_mode_entered: "event.safe_mode_entered",
};
