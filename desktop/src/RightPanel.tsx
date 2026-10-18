/**
 * Panel derecho: inspector de actividad, agentes, skills, cambios y trabajos.
 *
 * La regla de siempre: nada se inventa. El estado de los agentes y de las skills sale
 * **sólo** de los eventos reales. Los catálogos (agentes y skills del proyecto) se piden a la
 * API y se muestran en su propia sección, sin insinuar en ningún momento que se hayan usado.
 */

import { Fragment, useEffect, useMemo, useRef, useState } from "react";

import {
  AgentSpecView,
  ChangeEntry,
  ChangeSet,
  DiffComment,
  GitFileEntry,
  GitStatusView,
  JobInfo,
  JobOutput,
  SkillView,
  StreamItem,
  TaskItem,
  WorktreesView,
  ETIQUETA_EVENTO,
  api,
  mensajeDe,
} from "./api";
import type { Pestana } from "./App";
import { useT, type Clave } from "./i18n";

interface Props {
  plegado: boolean;
  pestana: Pestana;
  onPestana: (pestana: Pestana) => void;
  eventos: StreamItem[];
  tareas: TaskItem[];
  trabajos: JobInfo[];
  cambios: ChangeSet | null;
  salida: JobOutput | null;
  /** Hay cambios recién terminados sin revisar: la pestaña Cambios lo señala. */
  atencion?: boolean;
  /** Estado real de git y worktrees (lo mantiene App; aquí sólo se pinta). */
  git: GitStatusView | null;
  arboles: WorktreesView | null;
  /** Conversación activa: los comentarios guardan también su id. */
  sesion?: string;
  onGit: () => void;
  onStage: (paths: string[]) => void;
  onUnstage: (paths: string[]) => void;
  onCommit: (mensaje: string) => void;
  onCrearWorktree: (nombre: string) => void;
  onActivarWorktree: (path: string) => void;
  onQuitarWorktree: (path: string, force: boolean) => void;
  onVerCambios: (taskId: string) => void;
  onRestaurar: (taskId: string) => void;
  onVerSalida: (jobId: string) => void;
  onParar: (jobId: string) => void;
  onTrabajos: () => void;
  onPaleta: () => void;
  onCerrar: () => void;
}

interface Agente {
  id: string;
  estado: string;
  clase: string;
  detalle: string;
  eventos: number;
}

interface Skill {
  id: string;
  estado: string;
  clase: string;
  detalle: string;
}

/** Un resultado de `GET /skills/search`, ya normalizado. */
interface ResultadoSkill {
  id: string;
  score?: number;
  descripcion?: string;
  etiquetas?: string[];
}

const PESTANAS: Pestana[] = ["actividad", "git", "cambios", "trabajos", "agentes", "skills"];

/** Etiqueta de cada pestaña: el id no cambia, sólo lo que se lee. */
const ETIQUETA_PESTANA: Record<Pestana, Clave> = {
  actividad: "tabs.activity",
  git: "tabs.git",
  cambios: "tabs.changes",
  trabajos: "tabs.jobs",
  agentes: "panel.tab.agents",
  skills: "panel.tab.skills",
};

/** Estado de un trabajo en segundo plano (los ids del backend no cambian). */
const ESTADO_TRABAJO: Record<string, Clave> = {
  running: "panel.job.running",
  stopped: "panel.job.stopped",
  done: "panel.job.done",
  failed: "panel.job.failed",
  desconocido: "panel.job.unknown",
};

/** Marca de línea del diff para las clases de color (añadida/quitada/cabecera). */
function claseDiff(linea: string): string {
  if (linea.startsWith("+++") || linea.startsWith("---")) return "cabecera";
  if (linea.startsWith("++") || linea.startsWith("@@")) return "rango";
  if (linea.startsWith("+")) return "suma";
  if (linea.startsWith("-")) return "resta";
  return "";
}

function hora(iso: string): string {
  const fecha = new Date(iso);
  return Number.isNaN(fecha.getTime()) ? "" : fecha.toLocaleTimeString().slice(0, 8);
}

/** Resumen legible de una entrada del registro de cambios (nada que el backend no diga). */
function estadoArchivo(entry: ChangeEntry, t: ReturnType<typeof useT>): string {
  if (entry.existed === false) return t("panel.change.new");
  if (entry.restorable === false) return t("panel.change.noBackup");
  return t("panel.change.modified");
}

/** Líneas añadidas y quitadas de un diff unificado (sin contar las cabeceras). */
function contarDiff(diff: string): { add: number; del: number } {
  let add = 0;
  let del = 0;
  for (const linea of diff.split("\n")) {
    if (linea.startsWith("+++") || linea.startsWith("---")) continue;
    if (linea.startsWith("+")) add += 1;
    else if (linea.startsWith("-")) del += 1;
  }
  return { add, del };
}

/** Agentes y skills se deducen **sólo** de eventos reales: nada de suposiciones.
 *
 * El agente de cada evento sale del payload (`agent_id`, y de paso los nombres antiguos
 * `spec_id`/`spec`, que el runner ha usado de forma inconsistente) y, cuando el evento no
 * lo trae —caso de `tool_called`—, del nodo al que pertenece, que sí lo trae siempre.
 */
function leerAgentes(eventos: StreamItem[], t: ReturnType<typeof useT>): Agente[] {
  const mapa = new Map<string, Agente>();
  const porNodo = new Map<string, string>();

  function agenteDe(event: StreamItem["event"]): Agente | null {
    const directo = event.payload?.agent_id ?? event.payload?.spec_id ?? event.payload?.spec;
    const id =
      typeof directo === "string" && directo
        ? directo
        : (event.node_id ? porNodo.get(event.node_id) : undefined);
    if (!id) return null;
    const actual =
      mapa.get(id) ?? { id, estado: t("panel.agent.state.waiting"), clase: "", detalle: "", eventos: 0 };
    mapa.set(id, actual);
    if (event.node_id) porNodo.set(event.node_id, id);
    return actual;
  }

  for (const item of eventos) {
    const { event } = item;
    const actual = agenteDe(event);
    if (!actual) continue;
    actual.eventos += 1;
    if (event.event_type === "node_started" || event.event_type === "delegation_started") {
      actual.estado = t("panel.agent.state.working");
      actual.clase = "trabajando";
      actual.detalle = event.tool_name
        ? t("panel.agent.state.using", { herramienta: event.tool_name })
        : t("panel.agent.state.thinking");
    } else if (event.event_type === "tool_called") {
      actual.estado = t("panel.agent.state.working");
      actual.clase = "trabajando";
      actual.detalle = t("panel.agent.state.using", {
        herramienta: String(event.tool_name ?? t("panel.event.tool")),
      });
    } else if (event.event_type === "node_finished" || event.event_type === "delegation_completed") {
      const estado = String(event.payload?.status ?? "");
      const bien = estado === "ok" || estado === "partial";
      actual.estado = bien ? t("panel.agent.state.done") : t("panel.agent.state.failed");
      actual.clase = bien ? "" : "fallido";
      actual.detalle = String(event.payload?.summary ?? "");
    } else if (event.event_type === "policy_violation") {
      actual.estado = t("panel.agent.state.denied");
      actual.clase = "fallido";
      actual.detalle = "";
    }
  }
  return [...mapa.values()].reverse();
}

function leerSkills(eventos: StreamItem[], t: ReturnType<typeof useT>): Skill[] {
  const mapa = new Map<string, Skill>();
  for (const { event } of eventos) {
    // Buscadas y consideradas primero: nunca rebajan una skill ya ejecutada o fallida.
    if (event.event_type === "skill_searched") {
      const consideradas = Array.isArray(event.payload?.considered)
        ? (event.payload.considered as Array<Record<string, unknown>>)
        : [];
      for (const item of consideradas) {
        const id = String(item.skill_id ?? "");
        if (!id || mapa.has(id)) continue;
        mapa.set(id, {
          id,
          estado: t("panel.skill.state.considered"),
          clase: "",
          detalle:
            item.score === undefined
              ? t("panel.skill.detail.searched")
              : t("panel.skill.detail.score", { n: String(item.score) }),
        });
      }
      continue;
    }
    const id = event.payload?.skill_id;
    if (typeof id !== "string" || !id) continue;
    if (event.event_type === "skill_executed") {
      mapa.set(id, {
        id,
        estado: t("panel.skill.state.executed"),
        clase: "trabajando",
        detalle:
          event.payload?.cached === true
            ? t("panel.skill.detail.cached")
            : t("panel.skill.detail.real"),
      });
    } else if (event.event_type === "skill_failed") {
      mapa.set(id, {
        id,
        estado: t("panel.skill.state.failed"),
        clase: "fallido",
        detalle: String(event.payload?.error ?? t("panel.noDetail")),
      });
    } else if (event.event_type === "skill_proposed" && !mapa.has(id)) {
      mapa.set(id, {
        id,
        estado: t("panel.skill.state.proposed"),
        clase: "",
        detalle: t("panel.skill.detail.pending"),
      });
    }
  }
  return [...mapa.values()].reverse();
}

/** Normaliza un resultado de la búsqueda de skills sin fiarse de la forma exacta. */
function normalizarResultado(item: Record<string, unknown>): ResultadoSkill {
  const campos = (item.fields ?? {}) as Record<string, unknown>;
  const etiquetas = Array.isArray(campos.tags) ? campos.tags.map((valor) => String(valor)) : [];
  return {
    id: String(item.id ?? item.skill_id ?? ""),
    score: typeof item.score === "number" ? item.score : undefined,
    descripcion: typeof campos.description === "string" ? campos.description : undefined,
    etiquetas: etiquetas.length ? etiquetas : undefined,
  };
}

export function RightPanel(props: Props) {
  const t = useT();
  const [selAgente, setSelAgente] = useState("");
  const [selTarea, setSelTarea] = useState("");
  const [catAgentes, setCatAgentes] = useState<AgentSpecView[]>([]);
  const [catSkills, setCatSkills] = useState<SkillView[]>([]);
  const [filtroAgentes, setFiltroAgentes] = useState("");
  const [consulta, setConsulta] = useState("");
  const [resultados, setResultados] = useState<ResultadoSkill[] | null>(null);
  const [cargando, setCargando] = useState(false);
  const [errorAgentes, setErrorAgentes] = useState("");
  const [errorSkills, setErrorSkills] = useState("");
  const [errorBusqueda, setErrorBusqueda] = useState("");
  const [verDiff, setVerDiff] = useState(true);
  // Git: mensaje de commit y nombre del worktree a crear.
  const [mensajeCommit, setMensajeCommit] = useState("");
  const [nombreWt, setNombreWt] = useState("");
  // Comentarios inline del diff (por tarea) y el ancla seleccionada en el diff.
  const [comentarios, setComentarios] = useState<DiffComment[]>([]);
  const [errorComentarios, setErrorComentarios] = useState("");
  const [comentarioTexto, setComentarioTexto] = useState("");
  const [ancla, setAncla] = useState<{ file: string; start: number; end: number } | null>(null);
  // Ancla manual: camino accesible por teclado (las líneas del diff se eligen con ratón).
  const [archivoManual, setArchivoManual] = useState("");
  const [inicioManual, setInicioManual] = useState("1");
  const [finManual, setFinManual] = useState("1");

  const agentes = leerAgentes(props.eventos, t);
  const skills = leerSkills(props.eventos, t);

  /** El catálogo es informativo: si falla, se dice y el panel sigue con los eventos. */
  async function cargarAgentes() {
    setCargando(true);
    setErrorAgentes("");
    try {
      const datos = await api<{ agents: AgentSpecView[] }>("/agents");
      setCatAgentes(Array.isArray(datos?.agents) ? datos.agents : []);
    } catch (exc) {
      setCatAgentes([]);
      setErrorAgentes(mensajeDe(exc));
    } finally {
      setCargando(false);
    }
  }

  async function cargarSkills() {
    setCargando(true);
    setErrorSkills("");
    try {
      const datos = await api<{ skills: SkillView[] }>("/skills");
      setCatSkills(Array.isArray(datos?.skills) ? datos.skills : []);
    } catch (exc) {
      setCatSkills([]);
      setErrorSkills(mensajeDe(exc));
    } finally {
      setCargando(false);
    }
  }

  async function buscarSkills(texto: string) {
    setErrorBusqueda("");
    try {
      const datos = await api<{ results: Array<Record<string, unknown>> }>(
        `/skills/search?q=${encodeURIComponent(texto)}`,
      );
      const lista = Array.isArray(datos?.results) ? datos.results : [];
      setResultados(lista.map(normalizarResultado));
    } catch (exc) {
      setResultados([]);
      setErrorBusqueda(mensajeDe(exc));
    }
  }

  // --- comentarios inline del diff (persistidos por tarea) ---

  async function cargarComentarios(taskId: string) {
    setErrorComentarios("");
    try {
      const datos = await api<{ comments: DiffComment[] }>(
        `/comments?task_id=${encodeURIComponent(taskId)}`,
      );
      setComentarios(Array.isArray(datos.comments) ? datos.comments : []);
    } catch (exc) {
      setComentarios([]);
      setErrorComentarios(mensajeDe(exc));
    }
  }

  async function agregarComentario() {
    if (!ancla || !comentarioTexto.trim()) return;
    try {
      await api("/comments", {
        method: "POST",
        body: {
          file: ancla.file,
          line_start: ancla.start,
          line_end: ancla.end,
          text: comentarioTexto.trim(),
          task_id: props.cambios?.task_id ?? "",
          session_id: props.sesion ?? "",
        },
      });
      setComentarioTexto("");
      setAncla(null);
      if (props.cambios?.task_id) await cargarComentarios(props.cambios.task_id);
    } catch (exc) {
      setErrorComentarios(mensajeDe(exc));
    }
  }

  async function resolverComentario(id: string, resolved: boolean) {
    try {
      await api(`/comments/${id}`, { method: "PATCH", body: { resolved } });
      if (props.cambios?.task_id) await cargarComentarios(props.cambios.task_id);
    } catch (exc) {
      setErrorComentarios(mensajeDe(exc));
    }
  }

  async function borrarComentario(id: string) {
    try {
      await api(`/comments/${id}`, { method: "DELETE" });
      if (props.cambios?.task_id) await cargarComentarios(props.cambios.task_id);
    } catch (exc) {
      setErrorComentarios(mensajeDe(exc));
    }
  }

  // Al cambiar de tarea se recargan sus comentarios y se olvida el ancla anterior.
  const tareaCambios = props.cambios?.task_id ?? "";
  useEffect(() => {
    setAncla(null);
    setComentarioTexto("");
    if (!tareaCambios) {
      setComentarios([]);
      return;
    }
    void cargarComentarios(tareaCambios);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tareaCambios]);


  const cargados = useRef<Set<string>>(new Set());
  useEffect(() => {
    if (props.plegado) return;
    const superficie = props.pestana;
    if (superficie !== "agentes" && superficie !== "skills") return;
    if (cargados.current.has(superficie)) return;
    cargados.current.add(superficie);
    if (superficie === "agentes") void cargarAgentes();
    else void cargarSkills();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.pestana, props.plegado]);

  // La búsqueda espera a que se deje de teclear: no se castiga al backend por cada letra.
  useEffect(() => {
    const texto = consulta.trim();
    if (!texto) {
      setResultados(null);
      setErrorBusqueda("");
      return;
    }
    const timer = setTimeout(() => {
      void buscarSkills(texto);
    }, 250);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [consulta]);

  const agentesFiltrados = useMemo(() => {
    const texto = filtroAgentes.trim().toLowerCase();
    if (!texto) return catAgentes;
    return catAgentes.filter((agente) =>
      [agente.id, agente.name ?? "", ...(agente.capabilities ?? [])].some((valor) =>
        String(valor).toLowerCase().includes(texto),
      ),
    );
  }, [catAgentes, filtroAgentes]);

  // El diff, línea a línea, con el archivo al que pertenece cada una (para anclar comentarios).
  // La línea del comentario es el índice dentro del diff (1-based), que es lo que se selecciona.
  const filasDiff = useMemo(() => {
    let archivo = "";
    return (props.cambios?.diff ?? "").split("\n").map((linea, indice) => {
      // En un archivo borrado, la ruta está en el `---`; en uno nuevo, en el `+++`.
      if (linea.startsWith("diff --git")) {
        archivo = ""; // separador: aún no pertenece al archivo siguiente
      } else if (linea.startsWith("--- ")) {
        const ruta = linea.slice(4).replace(/^a\//, "").trim();
        if (ruta && ruta !== "/dev/null") archivo = ruta;
      } else if (linea.startsWith("+++ ")) {
        const ruta = linea.slice(4).replace(/^b\//, "").trim();
        if (ruta && ruta !== "/dev/null") archivo = ruta;
      }
      return { n: indice + 1, linea, archivo };
    });
  }, [props.cambios?.diff]);

  if (props.plegado) {
    return (
      <aside className="panel der plegado">
        <button className="plegar" onClick={() => props.onPestana(props.pestana)} title="Ctrl+J">
          ‹
        </button>
      </aside>
    );
  }

  // Resumen del diff y entradas del registro de cambios (lo que el backend ya calculó).
  const cambios = props.cambios;
  const resumenCambios = contarDiff(cambios?.diff ?? "");
  const entradasCambios = cambios?.entries ?? [];

  /** Ancla una línea (clic) o extiende el rango dentro del mismo archivo (shift+clic). */
  function seleccionarLinea(fila: { n: number; archivo: string }, extender: boolean) {
    // Las cabeceras del diff no pertenecen a un archivo concreto: no se anclan.
    if (!fila.archivo) return;
    setAncla((actual) => {
      if (extender && actual && actual.file === fila.archivo) {
        return {
          file: fila.archivo,
          start: Math.min(actual.start, fila.n),
          end: Math.max(actual.end, fila.n),
        };
      }
      return { file: fila.archivo, start: fila.n, end: fila.n };
    });
  }

  return (
    <aside className="panel der">
      <div className="cabecera">
        <span className="etiqueta" style={{ flex: 1, margin: 0 }}>
          {t("panel.title")}
        </span>
        <button className="quiet" title={t("common.close")} onClick={props.onCerrar}>
          ›
        </button>
      </div>

      <div className="pestanas">
        {PESTANAS.map((nombre) => (
          <button
            key={nombre}
            className={props.pestana === nombre ? "activa" : ""}
            onClick={() => props.onPestana(nombre)}
            title={nombre === "cambios" && props.atencion ? t("panel.tab.attention") : undefined}
          >
            {t(ETIQUETA_PESTANA[nombre])}
            {nombre === "agentes" && agentes.length ? ` ${agentes.length}` : ""}
            {nombre === "skills" && skills.length ? ` ${skills.length}` : ""}
            {nombre === "trabajos" && props.trabajos.length ? ` ${props.trabajos.length}` : ""}
            {nombre === "cambios" && props.atencion ? (
              <span
                className="punto-atencion"
                role="img"
                aria-label={t("panel.tab.attention")}
              />
            ) : null}
          </button>
        ))}
      </div>

      <div className="lista">
        {props.pestana === "actividad" && (
          <>
            {props.eventos.length === 0 && (
              <div className="vacio">{t("panel.activity.empty")}</div>
            )}
            {[...props.eventos].reverse().map((item) => (
              <div key={item.index} className="evento">
                <span className="hora">{hora(item.event.timestamp)}</span>
                <span className="tipo">
                  {t(ETIQUETA_EVENTO[item.event.event_type] ?? "event.unknown")}
                </span>
                <span>
                  {String(
                    item.event.tool_name ??
                      item.event.payload?.spec ??
                      item.event.payload?.agent_id ??
                      "",
                  )}
                </span>
              </div>
            ))}
          </>
        )}

        {props.pestana === "git" && (
          <>
            {!props.git ? (
              <div className="vacio">{t("panel.git.loading")}</div>
            ) : !props.git.is_repo ? (
              <div className="vacio">{props.git.error || t("panel.git.notRepo")}</div>
            ) : (
              <>
                <div className="bloque">
                  <div className="resumen-cambios">
                    <span>
                      {t("panel.git.branch", { rama: props.git?.branch || "?" })}
                      {props.git?.detached ? ` ${t("panel.git.detached")}` : ""}
                    </span>
                    {props.git?.upstream ? (
                      <span className="meta">
                        {props.git.upstream} ↑{props.git.ahead ?? 0} ↓{props.git.behind ?? 0}
                      </span>
                    ) : (
                      <span className="meta">{t("panel.git.noUpstream")}</span>
                    )}
                  </div>
                  <div className="meta">
                    {t("panel.git.runningIn", {
                      destino: props.arboles?.active || t("panel.git.theProject"),
                    })}
                  </div>
                  <div className="acciones" style={{ marginTop: 0 }}>
                    <button className="quiet" onClick={props.onGit}>
                      {t("panel.refresh")}
                    </button>
                  </div>
                </div>

                {(props.git?.files?.length ?? 0) === 0 && (
                  <div className="vacio">{t("panel.git.clean")}</div>
                )}

                {(
                  [
                    ["conflicts", "panel.git.conflicts"],
                    ["staged", "panel.git.staged"],
                    ["unstaged", "panel.git.unstaged"],
                    ["untracked", "panel.git.untracked"],
                  ] as const
                ).map(([grupo, titulo]) => {
                  const lista = (props.git?.[grupo] as GitFileEntry[] | undefined) ?? [];
                  if (lista.length === 0) return null;
                  return (
                    <div className="subbloque" key={grupo}>
                      <p className="etiqueta">
                        {t(titulo)} ({lista.length})
                      </p>
                      {lista.map((archivo) => (
                        <div key={archivo.path} className="ficha" style={{ cursor: "default" }}>
                          <h3>{archivo.path}</h3>
                          <div className="sub">
                            {t("panel.git.code")} <code>{archivo.code}</code>
                            {archivo.conflicted ? t("panel.git.conflict") : ""}
                          </div>
                          <div className="acciones">
                            {grupo === "staged" ? (
                              <button
                                className="quiet"
                                onClick={() => props.onUnstage([archivo.path])}
                              >
                                {t("panel.git.unstage")}
                              </button>
                            ) : (
                              <button
                                className="quiet"
                                onClick={() => props.onStage([archivo.path])}
                              >
                                {grupo === "conflicts"
                                  ? t("panel.git.markResolved")
                                  : t("panel.git.stage")}
                              </button>
                            )}
                          </div>
                        </div>
                      ))}
                    </div>
                  );
                })}

                {(props.git?.staged?.length ?? 0) > 0 && (
                  <div className="bloque">
                    <p className="etiqueta">{t("panel.git.commit")}</p>
                    <input
                      value={mensajeCommit}
                      onChange={(evento) => setMensajeCommit(evento.target.value)}
                      placeholder={t("panel.git.commit.placeholder")}
                      aria-label={t("panel.git.commit.aria")}
                    />
                    <div className="acciones">
                      <button
                        className="primary"
                        disabled={!mensajeCommit.trim()}
                        onClick={() => {
                          props.onCommit(mensajeCommit.trim());
                          setMensajeCommit("");
                        }}
                      >
                        {t("panel.git.commit.action")}
                      </button>
                    </div>
                    <p className="nota">{t("panel.git.commit.note")}</p>
                  </div>
                )}

                <div className="bloque">
                  <p className="etiqueta">{t("panel.git.worktrees")}</p>
                  {props.arboles?.is_repo ? (
                    <>
                      <input
                        value={nombreWt}
                        onChange={(evento) => setNombreWt(evento.target.value)}
                        placeholder={t("panel.worktree.name.placeholder")}
                        aria-label={t("panel.worktree.name.aria")}
                      />
                      <div className="acciones">
                        <button
                          className="quiet"
                          disabled={!nombreWt.trim()}
                          onClick={() => {
                            props.onCrearWorktree(nombreWt.trim());
                            setNombreWt("");
                          }}
                        >
                          {t("panel.worktree.create")}
                        </button>
                        <button className="quiet" onClick={() => props.onActivarWorktree("")}>
                          {t("panel.worktree.useProject")}
                        </button>
                      </div>
                      <p className="nota">
                        {t("panel.worktree.note.a")}
                        <code>.satellite/worktrees/</code>
                        {t("panel.worktree.note.b")}
                      </p>
                    </>
                  ) : (
                    <p className="nota">{t("panel.worktree.noGit")}</p>
                  )}
                </div>

                {(props.arboles?.worktrees ?? []).map((arbol) => (
                  <div key={arbol.path} className="ficha" style={{ cursor: "default" }}>
                    <h3>
                      {arbol.branch || t("panel.git.detached")}
                      {arbol.main ? t("panel.worktree.main") : ""}
                    </h3>
                    <div className="sub">
                      {arbol.path}
                      {arbol.locked ? t("panel.worktree.locked") : ""}
                      {arbol.prunable ? t("panel.worktree.prunable") : ""}
                      {arbol.dirty ? t("panel.worktree.dirty") : ""}
                    </div>
                    <div className="acciones">
                      {props.arboles?.active === arbol.path ? (
                        <span className="chip acento">{t("panel.worktree.active")}</span>
                      ) : (
                        <button className="quiet" onClick={() => props.onActivarWorktree(arbol.path)}>
                          {t("panel.worktree.activate")}
                        </button>
                      )}
                      {arbol.managed && !arbol.main && (
                        <>
                          <button
                            className="quiet"
                            onClick={() => props.onQuitarWorktree(arbol.path, false)}
                          >
                            {t("panel.worktree.remove")}
                          </button>
                          <button
                            className="peligro"
                            onClick={() => props.onQuitarWorktree(arbol.path, true)}
                          >
                            {t("panel.worktree.removeForce")}
                          </button>
                        </>
                      )}
                    </div>
                  </div>
                ))}
              </>
            )}
          </>
        )}

        {props.pestana === "agentes" && (
          <>
            <div className="bloque">
              <p className="etiqueta">{t("panel.inUse")}</p>
              <p className="nota">{t("panel.agents.inUse.note")}</p>
            </div>
            {agentes.length === 0 && (
              <div className="vacio">
                {t("panel.agents.empty")}
                <br />
                {t("panel.agents.empty.note")}
              </div>
            )}
            {agentes.map((agente) => (
              <div
                key={agente.id}
                className="ficha"
                onClick={() => setSelAgente(selAgente === agente.id ? "" : agente.id)}
              >
                <h3>{agente.id}</h3>
                <div className="sub">
                  <span className={`estado ${agente.clase}`}>{agente.estado}</span>
                  {agente.detalle ? ` · ${agente.detalle}` : ""}
                  {t("panel.events", { n: agente.eventos })}
                </div>
                {selAgente === agente.id && (
                  <div className="sub">{t("panel.agents.detailNote")}</div>
                )}
              </div>
            ))}

            <div className="bloque">
              <p className="etiqueta">{t("panel.catalog")}</p>
              <input
                className="filtro"
                value={filtroAgentes}
                onChange={(evento) => setFiltroAgentes(evento.target.value)}
                placeholder={t("panel.agents.filter.placeholder")}
                aria-label={t("panel.agents.filter.aria")}
              />
              <div className="acciones">
                <button className="quiet" disabled={cargando} onClick={() => void cargarAgentes()}>
                  {t("panel.refresh")}
                </button>
              </div>
            </div>
            {errorAgentes && (
              <div className="vacio">
                {t("panel.catalog.error")} {errorAgentes}
              </div>
            )}
            {!errorAgentes && catAgentes.length === 0 && (
              <div className="vacio">
                {cargando ? t("panel.catalog.loading") : t("panel.agents.catalog.empty")}
              </div>
            )}
            {!errorAgentes && catAgentes.length > 0 && agentesFiltrados.length === 0 && (
              <div className="vacio">{t("panel.agents.catalog.noMatch")}</div>
            )}
            {agentesFiltrados.map((agente) => (
              <div key={agente.id} className="ficha" style={{ cursor: "default" }}>
                <h3>{agente.id}</h3>
                <div className="sub">
                  {agente.name ? `${agente.name} · ` : ""}
                  {agente.capabilities?.length
                    ? agente.capabilities.join(", ")
                    : t("panel.agents.noCapabilities")}
                </div>
                {agente.description && <div className="nota">{agente.description}</div>}
              </div>
            ))}
          </>
        )}

        {props.pestana === "skills" && (
          <>
            <div className="bloque">
              <p className="etiqueta">{t("panel.inUse")}</p>
              <div className="sub" style={{ color: "var(--muted)", fontSize: 12 }}>
                {t("panel.skills.note.a")}
                <button className="quiet" style={{ padding: 0 }} onClick={props.onPaleta}>
                  {t("panel.skills.palette")}
                </button>
                {t("panel.skills.note.b")}
              </div>
            </div>
            {skills.length === 0 && (
              <div className="vacio">{t("panel.skills.empty")}</div>
            )}
            {skills.map((skill) => (
              <div key={skill.id} className="ficha" style={{ cursor: "default" }}>
                <h3>{skill.id}</h3>
                <div className="sub">
                  <span className={`estado ${skill.clase}`}>{skill.estado}</span> · {skill.detalle}
                </div>
              </div>
            ))}

            <div className="bloque">
              <p className="etiqueta">{t("panel.catalog")}</p>
              <input
                className="filtro"
                value={consulta}
                onChange={(evento) => setConsulta(evento.target.value)}
                placeholder={t("panel.skills.search.placeholder")}
                aria-label={t("panel.skills.search.aria")}
              />
              <div className="acciones">
                <button className="quiet" disabled={cargando} onClick={() => void cargarSkills()}>
                  {t("panel.refresh")}
                </button>
                <button className="quiet" onClick={props.onPaleta} title="Ctrl+K">
                  {t("panel.skills.editPalette")}
                </button>
              </div>
            </div>
            {errorSkills && (
              <div className="vacio">
                {t("panel.catalog.error")} {errorSkills}
              </div>
            )}
            {errorBusqueda && (
              <div className="vacio">
                {t("panel.skills.searchFailed")} {errorBusqueda}
              </div>
            )}
            {resultados && (
              <div className="subbloque">
                <p className="etiqueta">{t("panel.skills.results")}</p>
                {resultados.length === 0 && (
                  <div className="vacio">{t("panel.skills.results.empty")}</div>
                )}
                {resultados.map((item) => (
                  <div key={item.id} className="ficha" style={{ cursor: "default" }}>
                    <h3>{item.id}</h3>
                    <div className="sub">
                      {item.score === undefined
                        ? t("panel.skills.noScore")
                        : t("panel.skills.score", { n: item.score.toFixed(2) })}
                      {item.etiquetas?.length ? ` · ${item.etiquetas.join(", ")}` : ""}
                    </div>
                    {item.descripcion && <div className="nota">{item.descripcion}</div>}
                  </div>
                ))}
              </div>
            )}
            {!resultados && !errorSkills && catSkills.length === 0 && (
              <div className="vacio">
                {cargando ? t("panel.catalog.loading") : t("panel.skills.catalog.empty")}
              </div>
            )}
            {!resultados &&
              catSkills.map((skill) => (
                <div key={skill.id} className="ficha" style={{ cursor: "default" }}>
                  <h3>{skill.id}</h3>
                  <div className="sub">
                    {skill.tags?.length ? skill.tags.join(" · ") : t("panel.skills.noTags")}
                  </div>
                  {skill.description && <div className="nota">{skill.description}</div>}
                </div>
              ))}
          </>
        )}

        {props.pestana === "cambios" && (
          <>
            {props.tareas.length === 0 && (
              <div className="vacio">{t("panel.changes.noTasks")}</div>
            )}
            {props.tareas.length > 0 && (
              <div className="bloque">
                <p className="etiqueta">{t("panel.changes.task")}</p>
                <select
                  value={selTarea}
                  onChange={(evento) => {
                    setSelTarea(evento.target.value);
                    if (evento.target.value) props.onVerCambios(evento.target.value);
                  }}
                >
                  <option value="">{t("panel.changes.chooseTask")}</option>
                  {props.tareas.map((tarea) => (
                    <option key={tarea.task_id} value={tarea.task_id}>
                      {tarea.task_id}
                      {tarea.changes.length
                        ? t("panel.changes.files", { n: tarea.changes.length })
                        : ""}
                    </option>
                  ))}
                </select>
              </div>
            )}
            {cambios && (
              <>
                <div className="bloque">
                  <div className="resumen-cambios">
                    <span>{t("panel.changes.filesCount", { n: cambios.files.length })}</span>
                    <span className="suma">+{resumenCambios.add}</span>
                    <span className="resta">−{resumenCambios.del}</span>
                    <span className="meta">{cambios.task_id}</span>
                  </div>
                  <div className="acciones" style={{ marginTop: 0 }}>
                    <button className="quiet" onClick={() => setVerDiff((valor) => !valor)}>
                      {verDiff ? t("panel.changes.hideDiff") : t("panel.changes.showDiff")}
                    </button>
                    <button className="quiet" onClick={() => props.onRestaurar(cambios.task_id)}>
                      {t("panel.changes.restore")}
                    </button>
                  </div>
                </div>
                {entradasCambios.length > 0
                  ? entradasCambios.map((entrada) => (
                      <div key={entrada.path} className="ficha" style={{ cursor: "default" }}>
                        <h3>{entrada.path}</h3>
                        <div className="sub">{estadoArchivo(entrada, t)}</div>
                      </div>
                    ))
                  : cambios.files.map((archivo) => (
                      <div key={archivo} className="ficha" style={{ cursor: "default" }}>
                        <h3>{archivo}</h3>
                      </div>
                    ))}
                {verDiff && (
                  <>
                    <div className="diff-lineas" role="group" aria-label={t("panel.diff.aria")}>
                      {filasDiff.map((fila) => {
                        const enRango =
                          ancla?.file === fila.archivo &&
                          ancla.start <= fila.n &&
                          fila.n <= ancla.end;
                        const mias = comentarios.filter(
                          (c) => c.file === fila.archivo && c.line_start === fila.n,
                        );
                        return (
                          <Fragment key={fila.n}>
                            <div
                              className={
                                "dif-linea " +
                                claseDiff(fila.linea) +
                                (enRango ? " anclada" : "")
                              }
                              onClick={(evento) => seleccionarLinea(fila, evento.shiftKey)}
                              title={t("panel.diff.line.title")}
                            >
                              <span className="dif-num">{fila.n}</span>
                              <span className="dif-texto">{fila.linea || " "}</span>
                            </div>
                            {mias.map((comentario) => (
                              <div
                                key={comentario.id}
                                className={"comentario" + (comentario.resolved ? " resuelto" : "")}
                              >
                                <div className="sub">
                                  {t("panel.diff.comment.lines")} {comentario.line_start}
                                  {comentario.line_end !== comentario.line_start
                                    ? `–${comentario.line_end}`
                                    : ""}{" "}
                                  · {comentario.file}
                                </div>
                                <div>{comentario.text}</div>
                                <div className="acciones" style={{ marginTop: 4 }}>
                                  <button
                                    className="quiet"
                                    onClick={() =>
                                      void resolverComentario(comentario.id, !comentario.resolved)
                                    }
                                  >
                                    {comentario.resolved
                                      ? t("panel.comment.reopen")
                                      : t("panel.diff.comment.resolve")}
                                  </button>
                                  <button
                                    className="quiet"
                                    onClick={() => void borrarComentario(comentario.id)}
                                  >
                                    {t("panel.diff.comment.delete")}
                                  </button>
                                </div>
                              </div>
                            ))}
                          </Fragment>
                        );
                      })}
                    </div>

                    {!ancla && (
                      <details className="bloque">
                        <summary className="etiqueta">{t("panel.diff.manualAnchor")}</summary>
                        <p className="nota" style={{ marginTop: 0 }}>
                          {t("panel.diff.manualAnchor.note")}
                        </p>
                        <div className="rejilla">
                          <div className="campo">
                            <label>{t("panel.diff.field.file")}</label>
                            <input
                              value={archivoManual}
                              onChange={(evento) => setArchivoManual(evento.target.value)}
                              placeholder="src/app.py"
                              aria-label={t("panel.diff.field.file.aria")}
                            />
                          </div>
                          <div className="campo">
                            <label>{t("panel.diff.field.start")}</label>
                            <input
                              type="number"
                              min={1}
                              value={inicioManual}
                              onChange={(evento) => setInicioManual(evento.target.value)}
                              aria-label={t("panel.diff.field.start.aria")}
                            />
                          </div>
                          <div className="campo">
                            <label>{t("panel.diff.field.end")}</label>
                            <input
                              type="number"
                              min={1}
                              value={finManual}
                              onChange={(evento) => setFinManual(evento.target.value)}
                              aria-label={t("panel.diff.field.end.aria")}
                            />
                          </div>
                        </div>
                        <div className="acciones">
                          <button
                            className="quiet"
                            disabled={!archivoManual.trim()}
                            onClick={() => {
                              const inicio = Math.max(1, Number(inicioManual) || 1);
                              const fin = Math.max(inicio, Number(finManual) || inicio);
                              setAncla({ file: archivoManual.trim(), start: inicio, end: fin });
                            }}
                          >
                            {t("panel.diff.useAnchor")}
                          </button>
                        </div>
                      </details>
                    )}

                    {ancla && (
                      <div className="bloque">
                        <p className="etiqueta">
                          {t("panel.diff.commentIn", { archivo: ancla.file })} ·{" "}
                          {ancla.end !== ancla.start
                            ? t("panel.diff.comment.range", {
                                desde: ancla.start,
                                hasta: ancla.end,
                              })
                            : t("panel.diff.comment.line", { n: ancla.start })}
                        </p>
                        <textarea
                          value={comentarioTexto}
                          onChange={(evento) => setComentarioTexto(evento.target.value)}
                          placeholder={t("panel.diff.comment.placeholder")}
                          aria-label={t("panel.diff.comment.aria")}
                        />
                        <div className="acciones">
                          <button
                            className="primary"
                            disabled={!comentarioTexto.trim()}
                            onClick={() => void agregarComentario()}
                          >
                            {t("panel.diff.comment.add")}
                          </button>
                          <button
                            className="quiet"
                            onClick={() => {
                              setAncla(null);
                              setComentarioTexto("");
                            }}
                          >
                            {t("common.cancel")}
                          </button>
                        </div>
                      </div>
                    )}

                    {errorComentarios && (
                      <div className="vacio">
                        {t("panel.diff.commentsError")} {errorComentarios}
                      </div>
                    )}

                    {comentarios.length > 0 && (
                      <details className="bloque comentarios-todos">
                        <summary className="etiqueta">
                          {t("panel.diff.commentsSummary", {
                            n: comentarios.filter((c) => !c.resolved).length,
                          })}
                        </summary>
                        <p className="nota" style={{ marginTop: 0 }}>
                          {t("panel.diff.comments.note")}
                        </p>
                        {comentarios.map((comentario) => (
                          <div
                            key={comentario.id}
                            className={"comentario" + (comentario.resolved ? " resuelto" : "")}
                          >
                            <div className="sub">
                              {comentario.file} · {t("panel.diff.comment.lines")}{" "}
                              {comentario.line_start}
                              {comentario.line_end !== comentario.line_start
                                ? `–${comentario.line_end}`
                                : ""}
                              {comentario.resolved ? t("panel.diff.comment.resolved") : ""}
                            </div>
                            <div>{comentario.text}</div>
                            <div className="acciones" style={{ marginTop: 4 }}>
                              <button
                                className="quiet"
                                onClick={() =>
                                  void resolverComentario(comentario.id, !comentario.resolved)
                                }
                              >
                                {comentario.resolved
                                  ? t("panel.comment.reopen")
                                  : t("panel.diff.comment.resolve")}
                              </button>
                              <button
                                className="quiet"
                                onClick={() => void borrarComentario(comentario.id)}
                              >
                                {t("panel.diff.comment.delete")}
                              </button>
                            </div>
                          </div>
                        ))}
                      </details>
                    )}
                  </>
                )}
              </>
            )}
          </>
        )}

        {props.pestana === "trabajos" && (
          <>
            <div className="bloque" style={{ display: "flex", gap: 8, alignItems: "baseline" }}>
              <p className="etiqueta" style={{ flex: 1, margin: 0 }}>
                {t("panel.jobs.background")}
              </p>
              <button className="quiet" onClick={props.onTrabajos}>
                {t("panel.refresh")}
              </button>
            </div>
            {props.trabajos.length === 0 && (
              <div className="vacio">
                {t("panel.jobs.empty.a")}
                <code>run_command background</code>
                {t("panel.jobs.empty.b")}
              </div>
            )}
            {props.trabajos.map((trabajo) => (
              <div key={trabajo.job_id} className="ficha" style={{ cursor: "default" }}>
                <h3>{trabajo.job_id}</h3>
                <div className="sub">
                  <span className={`estado ${trabajo.status === "failed" ? "fallido" : ""}`}>
                    {ESTADO_TRABAJO[trabajo.status] ? t(ESTADO_TRABAJO[trabajo.status]) : trabajo.status}
                  </span>{" "}
                  · {trabajo.command}
                </div>
                <div className="acciones">
                  <button className="quiet" onClick={() => props.onVerSalida(trabajo.job_id)}>
                    {t("panel.jobs.viewOutput")}
                  </button>
                  {trabajo.status === "running" && (
                    <button className="quiet" onClick={() => props.onParar(trabajo.job_id)}>
                      {t("panel.jobs.stop")}
                    </button>
                  )}
                </div>
                {props.salida?.job_id === trabajo.job_id && (
                  <pre className="salida">{props.salida.output || t("panel.jobs.noOutput")}</pre>
                )}
              </div>
            ))}
          </>
        )}
      </div>
    </aside>
  );
}
