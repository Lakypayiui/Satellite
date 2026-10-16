/** Cliente tipado de la API local. El token y las URLs viven en el proceso principal. */

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
}

export interface SessionInfo {
  id: string;
  name: string;
  turns: number;
  updated_at: string;
  active: boolean;
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

export interface ChangeSet {
  task_id: string;
  files: string[];
  entries: unknown[];
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
}

export interface RolesView {
  roles: string[];
  configured: Record<string, { provider?: string; model?: string }>;
  active: string;
  providers: SavedProviderView[];
  runtime_error?: string;
}

export interface ModelsResult {
  ok: boolean;
  url: string;
  models: string[];
  error?: string | null;
  /** El más usado según la tabla curada que ya usa la TUI. */
  suggested?: string;
}

export interface DoctorCheck {
  ok: boolean;
  mensaje: string;
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

interface Bridge {
  request: (ruta: string, options?: { method?: string; body?: unknown }) => Promise<Envelope<unknown>>;
  subscribe: (after: number, onItem: (item: unknown) => void) => () => void;
  onBackendDown: (onDown: (mensaje: string) => void) => () => void;
  restart: () => Promise<Envelope<unknown>>;
  pickFolder: () => Promise<string>;
  info: () => Promise<{ ready: boolean; repoRoot: string; dev: boolean }>;
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
};

/** Texto legible de un error, sin envoltorios de Electron. */
export function mensajeDe(exc: unknown): string {
  if (exc instanceof ApiError) return exc.message;
  if (exc instanceof Error) return exc.message.replace(/^Error invoking remote method '[^']+': /, "");
  return String(exc);
}

export const ETIQUETA_EVENTO: Record<string, string> = {
  task_started: "tarea iniciada",
  graph_built: "plan construido",
  node_started: "nodo en marcha",
  node_finished: "nodo terminado",
  tool_called: "herramienta",
  validation_failed: "validación fallida",
  retry: "reintento",
  node_stuck: "nodo atascado",
  task_finished: "tarea terminada",
  plan_failed: "plan fallido",
  agent_proposed: "agente propuesto",
  agent_approved: "agente aprobado",
  agent_rejected: "agente rechazado",
  agent_created: "agente creado",
  agent_extended: "agente ampliado",
  skill_proposed: "skill propuesta",
  skill_approved: "skill aprobada",
  skill_searched: "skill buscada (considerada)",
  skill_executed: "skill ejecutada",
  skill_failed: "skill fallida",
  skill_quarantined: "skill en cuarentena",
  delegation_started: "delegación iniciada",
  delegation_completed: "delegación terminada",
  policy_violation: "permiso denegado",
  safe_mode_entered: "modo seguro",
};
