/** Armazón de la app: estado del proyecto abierto, chat, panel derecho y atajos. */

import { useCallback, useEffect, useRef, useState } from "react";

import {
  Approval,
  ChangeSet,
  ConfigView,
  JobInfo,
  JobOutput,
  RestorePreview,
  SessionInfo,
  Status,
  StreamItem,
  TaskItem,
  Turn,
  api,
  bridge,
  mensajeDe,
} from "./api";
import { Chat } from "./Chat";
import { RightPanel } from "./RightPanel";
import { Settings } from "./Settings";
import { Sidebar } from "./Sidebar";

export type Pestana = "actividad" | "agentes" | "skills" | "cambios" | "trabajos";

const MAX_EVENTOS = 400;
const CADA_MS = 1500;

export function App() {
  const [status, setStatus] = useState<Status | null>(null);
  const [cargando, setCargando] = useState(true);
  const [caido, setCaido] = useState<string | null>(null);
  const [aviso, setAviso] = useState<string | null>(null);
  const [proyectos, setProyectos] = useState<string[]>([]);
  const [sesiones, setSesiones] = useState<SessionInfo[]>([]);
  const [sesionActiva, setSesionActiva] = useState("");
  const [turnos, setTurnos] = useState<Turn[]>([]);
  const [eventos, setEventos] = useState<StreamItem[]>([]);
  const [permisos, setPermisos] = useState<Approval[]>([]);
  const [tareas, setTareas] = useState<TaskItem[]>([]);
  const [trabajos, setTrabajos] = useState<JobInfo[]>([]);
  const [config, setConfig] = useState<ConfigView | null>(null);
  const [cambios, setCambios] = useState<ChangeSet | null>(null);
  const [salida, setSalida] = useState<JobOutput | null>(null);
  const [restauracion, setRestauracion] = useState<RestorePreview | null>(null);
  const [ocupado, setOcupado] = useState(false);
  const [pestana, setPestana] = useState<Pestana>("actividad");
  const [izq, setIzq] = useState(true);
  const [der, setDer] = useState(true);
  const [ajustes, setAjustes] = useState(false);
  const ocupadoRef = useRef(false);

  const cargarTodo = useCallback(async () => {
    const [estado, listaProyectos, listaSesiones, listaTareas, listaTrabajos, vistaConfig] =
      await Promise.all([
        api<Status>("/status"),
        api<{ current: string; recents: string[] }>("/projects"),
        api<{ current: string; sessions: SessionInfo[] }>("/sessions"),
        api<{ tasks: TaskItem[] }>("/tasks"),
        api<{ jobs: JobInfo[] }>("/jobs"),
        api<ConfigView>("/config"),
      ]);
    setStatus(estado);
    setProyectos(listaProyectos.recents);
    setSesiones(listaSesiones.sessions);
    setSesionActiva(listaSesiones.current);
    setTareas(listaTareas.tasks);
    setTrabajos(listaTrabajos.jobs);
    setConfig(vistaConfig);
    if (listaSesiones.current) {
      const detalle = await api<{ turns: Turn[] }>(`/sessions/${listaSesiones.current}/turns`);
      setTurnos(detalle.turns);
    }
  }, []);

  const recargarPermisos = useCallback(async () => {
    const respuesta = await api<{ pending: Approval[] }>("/approvals");
    setPermisos(respuesta.pending);
    return respuesta.pending;
  }, []);

  useEffect(() => {
    void (async () => {
      try {
        await cargarTodo();
        await recargarPermisos();
      } catch (exc) {
        setAviso(mensajeDe(exc));
      } finally {
        setCargando(false);
      }
    })();
    const cortarCaida = bridge.onBackendDown((mensaje) => setCaido(mensaje));
    const cortarEventos = bridge.subscribe(0, (item) => {
      setEventos((previos) => [...previos, item].slice(-MAX_EVENTOS));
      const tipo = item.event.event_type;
      if (tipo === "task_finished" || tipo === "task_started") {
        void api<{ tasks: TaskItem[] }>("/tasks").then((r) => setTareas(r.tasks)).catch(() => {});
      }
      if (tipo === "policy_violation") {
        setAviso("el agente pidió un permiso que se denegó: mira las aprobaciones pendientes");
      }
    });
    return () => {
      cortarCaida();
      cortarEventos();
    };
  }, [cargarTodo, recargarPermisos]);

  // Mientras hay un turno en marcha, el runtime puede estar esperando un permiso: no emite
  // evento para eso, así que se mira la lista de pendientes a intervalos cortos.
  useEffect(() => {
    if (!ocupado) return;
    const timer = setInterval(() => {
      void recargarPermisos().catch(() => {});
      void api<{ jobs: JobInfo[] }>("/jobs").then((r) => setTrabajos(r.jobs)).catch(() => {});
    }, CADA_MS);
    return () => clearInterval(timer);
  }, [ocupado, recargarPermisos]);

  useEffect(() => {
    ocupadoRef.current = ocupado;
  }, [ocupado]);

  useEffect(() => {
    const alPulsar = (evento: KeyboardEvent) => {
      if (!evento.ctrlKey) return;
      const tecla = evento.key.toLowerCase();
      if (tecla === "b") {
        evento.preventDefault();
        setIzq((valor) => !valor);
      } else if (tecla === "j") {
        evento.preventDefault();
        setDer((valor) => !valor);
      } else if (tecla === ",") {
        evento.preventDefault();
        setAjustes(true);
      } else if (tecla === "n") {
        evento.preventDefault();
        void nuevaSesion();
      } else if (evento.key === "Escape") {
        setAjustes(false);
      }
    };
    window.addEventListener("keydown", alPulsar);
    return () => window.removeEventListener("keydown", alPulsar);
  });

  async function enviar(texto: string, planMode: boolean) {
    setAviso(null);
    setOcupado(true);
    try {
      const respuesta = await api<{ text: string; task_id: string; error?: string | null }>(
        "/messages",
        { method: "POST", body: { text: texto, plan_mode: planMode } },
      );
      if (respuesta.error) setAviso(respuesta.error);
      await cargarTodo();
      setPestana("actividad");
    } catch (exc) {
      setAviso(mensajeDe(exc));
    } finally {
      setOcupado(false);
      void recargarPermisos().catch(() => {});
    }
  }

  async function nuevaSesion() {
    try {
      const respuesta = await api<{ session_id: string }>("/sessions", {
        method: "POST",
        body: { name: "" },
      });
      await cargarTodo();
      setSesionActiva(respuesta.session_id);
      setTurnos([]);
    } catch (exc) {
      setAviso(mensajeDe(exc));
    }
  }

  async function usarSesion(id: string) {
    try {
      const respuesta = await api<{ turns: Turn[] }>(`/sessions/${id}/activate`, {
        method: "POST",
      });
      setSesionActiva(id);
      setTurnos(respuesta.turns);
      await cargarTodo();
    } catch (exc) {
      setAviso(mensajeDe(exc));
    }
  }

  async function renombrarSesion(id: string, nombre: string) {
    try {
      await api(`/sessions/${id}`, { method: "PATCH", body: { name: nombre } });
      await cargarTodo();
    } catch (exc) {
      setAviso(mensajeDe(exc));
    }
  }

  async function abrirProyecto(ruta?: string) {
    try {
      const elegida = ruta ?? (await bridge.pickFolder());
      if (!elegida) return;
      await api<Status>("/projects", { method: "POST", body: { path: elegida } });
      setTurnos([]);
      setEventos([]);
      setCambios(null);
      await cargarTodo();
      await recargarPermisos();
    } catch (exc) {
      setAviso(mensajeDe(exc));
    }
  }

  async function decidirPermiso(proposalId: string, decision: string, feedback = "") {
    try {
      await api(`/approvals/${proposalId}`, { method: "POST", body: { decision, feedback } });
      await recargarPermisos();
    } catch (exc) {
      setAviso(mensajeDe(exc));
    }
  }

  async function cargarCambios(taskId: string) {
    try {
      setCambios(await api<ChangeSet>(`/tasks/${taskId}/changes`));
    } catch (exc) {
      setCambios(null);
      setAviso(mensajeDe(exc));
    }
  }

  async function pedirRestauracion(taskId: string) {
    try {
      setRestauracion(await api<RestorePreview>(`/tasks/${taskId}/restore`, { method: "POST", body: {} }));
    } catch (exc) {
      setAviso(mensajeDe(exc));
    }
  }

  async function confirmarRestauracion(taskId: string) {
    try {
      const resultado = await api<RestorePreview>(`/tasks/${taskId}/restore`, {
        method: "POST",
        body: { confirm: true },
      });
      setRestauracion(null);
      const restaurados = resultado.result?.restored?.length ?? 0;
      const borrados = resultado.result?.removed?.length ?? 0;
      setAviso(`restaurados ${restaurados} archivo(s) y eliminados ${borrados}`);
      await cargarTodo();
      await cargarCambios(taskId);
    } catch (exc) {
      setAviso(mensajeDe(exc));
      setRestauracion(null);
    }
  }

  async function verSalida(jobId: string) {
    try {
      setSalida(await api<JobOutput>(`/jobs/${jobId}/output`));
    } catch (exc) {
      setAviso(mensajeDe(exc));
    }
  }

  async function pararTrabajo(jobId: string) {
    try {
      await api(`/jobs/${jobId}/stop`, { method: "POST" });
      const respuesta = await api<{ jobs: JobInfo[] }>("/jobs");
      setTrabajos(respuesta.jobs);
      setAviso(`trabajo ${jobId} detenido`);
    } catch (exc) {
      setAviso(mensajeDe(exc));
    }
  }

  async function reintentar() {
    setCaido(null);
    const sobre = await bridge.restart();
    if (!sobre.ok) {
      setCaido(sobre.error?.message ?? "no se pudo reiniciar el backend");
      return;
    }
    try {
      await cargarTodo();
    } catch (exc) {
      setAviso(mensajeDe(exc));
    }
  }

  async function cambiarAjustes(opciones: { policy_mode?: string; safe_mode?: boolean }) {
    try {
      setConfig(await api<ConfigView>("/config", { method: "PUT", body: opciones }));
      await cargarTodo();
    } catch (exc) {
      setAviso(mensajeDe(exc));
    }
  }

  async function guardarProveedor(datos: {
    name: string;
    model: string;
    base_url: string;
    api_key: string;
    api_key_env: string;
  }) {
    try {
      setConfig(await api<ConfigView>("/providers", { method: "POST", body: datos }));
      setAviso(`proveedor «${datos.name}» guardado y activo`);
      await cargarTodo();
    } catch (exc) {
      setAviso(mensajeDe(exc));
    }
  }

  return (
    <div className="layout">
      <Sidebar
        contraido={!izq}
        proyectos={proyectos}
        actual={status?.project_root ?? ""}
        sesiones={sesiones}
        activa={sesionActiva}
        onToggle={() => setIzq((valor) => !valor)}
        onAbrir={abrirProyecto}
        onNueva={() => void nuevaSesion()}
        onUsar={(id) => void usarSesion(id)}
        onRenombrar={(id, nombre) => void renombrarSesion(id, nombre)}
      />

      <div className="centro">
        <header className="barra">
          <button className="ghost" onClick={() => setIzq((valor) => !valor)} title="Ctrl+B">
            ☰
          </button>
          <div className="crece">
            <div className="title">Satellite</div>
            <div className="path" title={status?.project_root ?? ""}>
              {status?.project_root ?? "sin proyecto"}
            </div>
          </div>
          <span className={status?.runtime_ready ? "chip ok" : "chip warn"}>
            {status?.runtime_ready ? "runtime listo" : "runtime no disponible"}
          </span>
          <span className="chip">{status?.provider || "sin proveedor"}</span>
          <span className="chip">{status?.model || "sin modelo"}</span>
          <span className={status?.policy_mode === "unrestricted" ? "chip warn" : "chip"}>
            política: {status?.policy_mode ?? "ask"}
          </span>
          <span className="chip" title="aislamiento de proceso, no del sistema operativo">
            sandbox {status?.sandbox ?? "N0"}
          </span>
          <span className="chip">contexto {status?.context_percent ?? 0}%</span>
          <button onClick={() => setAjustes(true)} title="Ctrl+,">
            Ajustes
          </button>
          <button className="ghost" onClick={() => setDer((valor) => !valor)} title="Ctrl+J">
            ▤
          </button>
        </header>

        {caido && (
          <div className="franja">
            <span className="chip bad">backend caído</span>
            <span>{caido}</span>
            <button className="primary" onClick={() => void reintentar()}>
              Reintentar
            </button>
          </div>
        )}
        {aviso && (
          <div className="franja">
            <span>{aviso}</span>
            <button className="ghost" onClick={() => setAviso(null)}>
              Cerrar
            </button>
          </div>
        )}
        {status && !status.runtime_ready && status.runtime_error && (
          <div className="franja">
            <span className="chip warn">runtime</span>
            <span>{status.runtime_error}</span>
          </div>
        )}

        <Chat
          turnos={turnos}
          permisos={permisos}
          ocupado={ocupado}
          cargando={cargando}
          ultimo={eventos.at(-1) ?? null}
          onEnviar={(texto, plan) => void enviar(texto, plan)}
          onPermiso={(id, decision, feedback) => void decidirPermiso(id, decision, feedback)}
        />
      </div>

      <RightPanel
        contraido={!der}
        pestana={pestana}
        onPestana={setPestana}
        eventos={eventos}
        tareas={tareas}
        trabajos={trabajos}
        cambios={cambios}
        salida={salida}
        onVerCambios={(id) => void cargarCambios(id)}
        onRestaurar={(id) => void pedirRestauracion(id)}
        onVerSalida={(id) => void verSalida(id)}
        onParar={(id) => void pararTrabajo(id)}
        onTrabajos={() => {
          void api<{ jobs: JobInfo[] }>("/jobs").then((r) => setTrabajos(r.jobs)).catch(() => {});
        }}
      />

      {ajustes && config && (
        <Settings
          config={config}
          onCerrar={() => setAjustes(false)}
          onPolitica={(modo) => void cambiarAjustes({ policy_mode: modo })}
          onModo={(activo) => void cambiarAjustes({ safe_mode: activo })}
          onProveedor={(datos) => void guardarProveedor(datos)}
        />
      )}

      {restauracion && (
        <div className="modal">
          <div className="caja">
            <h2>Descartar los cambios de la tarea</h2>
            <p className="sub">
              {restauracion.preview?.files.length ?? 0} archivo(s) volverán a su estado anterior y se
              perderá lo que se hizo en esta tarea. Nada se ha tocado todavía.
            </p>
            <pre className="diff">{restauracion.preview?.diff ?? ""}</pre>
            <div className="acciones" style={{ display: "flex", gap: 8, marginTop: 12 }}>
              <button
                className="primary"
                onClick={() => void confirmarRestauracion(restauracion.preview?.task_id ?? "")}
              >
                Sí, descartar
              </button>
              <button className="ghost" onClick={() => setRestauracion(null)}>
                Cancelar
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
