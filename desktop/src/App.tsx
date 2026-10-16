/** Armazón: cabecera de una línea, chat, panel derecho, paleta de comandos y modales. */

import { useCallback, useEffect, useState } from "react";

import {
  Approval,
  ChangeSet,
  ConfigView,
  JobInfo,
  JobOutput,
  SavedProviderView,
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
import { CommandPalette } from "./CommandPalette";
import { RightPanel } from "./RightPanel";
import { Settings } from "./Settings";
import { Sidebar } from "./Sidebar";

export type Pestana = "actividad" | "agentes" | "skills" | "cambios" | "trabajos";

const MAX_EVENTOS = 400;
const CADA_MS = 1500;

interface Confirmacion {
  titulo: string;
  detalle: string;
  accion: () => void;
}

interface Detalle {
  titulo: string;
  texto: string;
}

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
  const [ocupado, setOcupado] = useState(false);
  const [pestana, setPestana] = useState<Pestana>("actividad");
  const [izq, setIzq] = useState(true);
  const [der, setDer] = useState(true);
  const [ajustes, setAjustes] = useState(false);
  const [paleta, setPaleta] = useState(false);
  const [confirmacion, setConfirmacion] = useState<Confirmacion | null>(null);
  const [detalle, setDetalle] = useState<Detalle | null>(null);

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
      const detalleSesion = await api<{ turns: Turn[] }>(
        `/sessions/${listaSesiones.current}/turns`,
      );
      setTurnos(detalleSesion.turns);
    }
  }, []);

  const recargarPermisos = useCallback(async () => {
    const respuesta = await api<{ pending: Approval[] }>("/approvals");
    setPermisos(respuesta.pending);
  }, []);

  useEffect(() => {
    setEventos([]);
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
    });
    return () => {
      cortarCaida();
      cortarEventos();
    };
    // La raíz del proyecto está en las dependencias a propósito: al cambiar de carpeta se
    // recarga todo y se vuelve a abrir el flujo, porque el puente de eventos es de cada
    // proyecto y seguir escuchando el anterior dejaba el panel desactualizado.
  }, [cargarTodo, recargarPermisos, status?.project_root]);

  // Con un turno en marcha el runtime puede estar esperando un permiso, y eso no emite
  // evento: se mira la lista de pendientes y los trabajos a intervalos cortos.
  useEffect(() => {
    if (!ocupado) return;
    const timer = setInterval(() => {
      void recargarPermisos().catch(() => {});
      void api<{ jobs: JobInfo[] }>("/jobs").then((r) => setTrabajos(r.jobs)).catch(() => {});
    }, CADA_MS);
    return () => clearInterval(timer);
  }, [ocupado, recargarPermisos]);

  useEffect(() => {
    const alPulsar = (evento: KeyboardEvent) => {
      if (!evento.ctrlKey) return;
      const tecla = evento.key.toLowerCase();
      if (tecla === "k") {
        evento.preventDefault();
        setPaleta((valor) => !valor);
      } else if (tecla === "b") {
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
        setPaleta(false);
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
      const respuesta = await api<{ error?: string | null }>("/messages", {
        method: "POST",
        body: { text: texto, plan_mode: planMode },
      });
      if (respuesta.error) setAviso(respuesta.error);
      await cargarTodo();
    } catch (exc) {
      setAviso(mensajeDe(exc));
    } finally {
      setOcupado(false);
      void recargarPermisos().catch(() => {});
    }
  }

  /** Corta el turno en curso. Lo ya hecho queda: no se finge que la tarea no pasó. */
  async function cancelarTurno() {
    try {
      await api("/turns/cancel", { method: "POST" });
      setAviso("turno cancelado; lo que ya se hizo queda en el historial");
    } catch (exc) {
      setAviso(mensajeDe(exc));
    } finally {
      setOcupado(false);
      await recargarPermisos().catch(() => {});
    }
  }

  async function nuevaSesion() {
    try {
      const respuesta = await api<{ session_id: string }>("/sessions", {
        method: "POST",
        body: { name: "" },
      });
      setTurnos([]);
      await cargarTodo();
      setSesionActiva(respuesta.session_id);
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

  function pedirRestauracion(taskId: string) {
    void (async () => {
      try {
        const previa = await api<{ preview?: ChangeSet }>(`/tasks/${taskId}/restore`, {
          method: "POST",
          body: {},
        });
        setDetalle({
          titulo: `Se descartará lo hecho en la tarea ${taskId}`,
          texto: previa.preview?.diff ?? "sin diff",
        });
        setConfirmacion({
          titulo: "Restaurar los cambios de la tarea",
          detalle: `${previa.preview?.files.length ?? 0} archivo(s) volverán a su estado anterior.`,
          accion: () => void confirmarRestauracion(taskId),
        });
      } catch (exc) {
        setAviso(mensajeDe(exc));
      }
    })();
  }

  async function confirmarRestauracion(taskId: string) {
    try {
      const resultado = await api<{
        result?: { restored?: string[]; removed?: string[] };
      }>(`/tasks/${taskId}/restore`, { method: "POST", body: { confirm: true } });
      setDetalle(null);
      setAviso(
        `restaurados ${resultado.result?.restored?.length ?? 0}, eliminados ${resultado.result?.removed?.length ?? 0}`,
      );
      await cargarTodo();
      await cargarCambios(taskId);
    } catch (exc) {
      setAviso(mensajeDe(exc));
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

  async function verificar(ruta: string, titulo: string, metodo = "GET", cuerpo?: unknown) {
    try {
      const datos = await api<Record<string, unknown>>(ruta, {
        method: metodo,
        ...(cuerpo === undefined ? {} : { body: cuerpo }),
      });
      setDetalle({ titulo, texto: JSON.stringify(datos, null, 2) });
      await cargarTodo();
    } catch (exc) {
      setDetalle({ titulo, texto: mensajeDe(exc) });
    }
  }

  async function ejecutarComando(clave: string, argumento: string) {
    switch (clave) {
      case "proyecto":
        await abrirProyecto();
        break;
      case "sesion-nueva":
        await nuevaSesion();
        break;
      case "proveedor":
      case "roles":
      case "politica":
        setAjustes(true);
        break;
      case "sandbox":
        await verificar("/setup", "setup · sandbox", "POST");
        break;
      case "doctor":
        await verificar("/doctor", "doctor");
        break;
      case "stats":
        await verificar("/stats", "stats");
        break;
      case "compactar":
        await verificar("/compact", "compactar contexto", "POST");
        break;
      case "snapshots":
        await verificar("/snapshots", "snapshots");
        break;
      case "config":
        setDetalle({
          titulo: "configuración (sin secretos)",
          texto: JSON.stringify(config?.config ?? {}, null, 2),
        });
        break;
      case "reset":
        setConfirmacion({
          titulo: "Reset del estado base",
          detalle: "Borra los agentes y las skills creados en este proyecto.",
          accion: () => void verificar("/reset", "reset", "POST", { keep_skills: true, confirm: true }),
        });
        break;
      case "agentes":
        setPestana("agentes");
        setDer(true);
        break;
      case "crear-agente":
        await verificar("/agents", "crear agente", "POST", { need: argumento });
        break;
      case "skills-buscar":
        await verificar(`/skills/search?q=${encodeURIComponent(argumento)}`, "buscar skills");
        break;
      case "skills-borrar":
        setConfirmacion({
          titulo: `Borrar la skill ${argumento}`,
          detalle: "Se elimina del catálogo de skills del proyecto. No se puede deshacer.",
          accion: () => void verificar(`/skills/${argumento}`, "borrar skill", "DELETE"),
        });
        break;
      case "curar":
        await verificar("/curate", "curador de skills", "POST", { request: argumento });
        break;
      case "cambios":
        setPestana("cambios");
        setDer(true);
        break;
      case "trabajos":
        setPestana("trabajos");
        setDer(true);
        await verificar("/jobs", "trabajos");
        break;
      case "parar-trabajo":
        await pararTrabajo(argumento);
        break;
      case "paneles":
        setIzq((valor) => !valor);
        setDer((valor) => !valor);
        break;
      case "atajos":
        setDetalle({
          titulo: "atajos",
          texto: [
            "Ctrl+K   paleta de comandos",
            "Ctrl+N   nueva conversación",
            "Ctrl+B   panel izquierdo",
            "Ctrl+J   panel derecho",
            "Ctrl+,   ajustes",
            "Enter    enviar · Shift+Enter salta línea",
            "Esc      cerrar",
          ].join("\n"),
        });
        break;
      case "salir":
        window.close();
        break;
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

  const contextoAlto = (status?.context_percent ?? 0) >= 85;

  return (
    <div className="layout">
      <Sidebar
        plegado={!izq}
        ruta={status?.project_root ?? ""}
        recientes={proyectos}
        sesiones={sesiones}
        activa={sesionActiva}
        proveedor={
          (config?.providers as SavedProviderView[] | undefined)?.find(
            (item) => item.id === config?.active_provider,
          ) ?? null
        }
        onPlegar={() => setIzq((valor) => !valor)}
        onAbrir={abrirProyecto}
        onNueva={() => void nuevaSesion()}
        onUsar={(id) => void usarSesion(id)}
        onRenombrar={(id, nombre) => void renombrarSesion(id, nombre)}
      />

      <div className="centro">
        <header className="cabecera">
          <span className="proyecto" title={status?.project_root ?? ""}>
            {status?.project_root ?? "sin proyecto"}
          </span>
          <span className="sep">·</span>
          <span className={status?.runtime_ready ? "dato" : "dato alerta"}>
            {status?.runtime_ready ? "runtime listo" : "runtime no disponible"}
          </span>
          <span className="sep">·</span>
          <span className="dato">
            {status?.provider || "sin proveedor"}/{status?.model || "sin modelo"}
          </span>
          <span className="sep">·</span>
          <span className={status?.policy_mode === "unrestricted" ? "dato alerta" : "dato"}>
            {status?.policy_mode ?? "ask"}
          </span>
          <span className="sep">·</span>
          <span className="dato">sandbox {status?.sandbox ?? "N0"}</span>
          <span className="sep">·</span>
          <span className={contextoAlto ? "dato alerta" : "dato"}>contexto {status?.context_percent ?? 0}%</span>
          <button className="quiet" onClick={() => setPaleta(true)}>
            ctrl+k
          </button>
        </header>

        {caido && (
          <div className="linea-aviso mal">
            <span>backend caído</span>
            <span style={{ flex: 1 }}>{caido}</span>
            <button className="quiet" onClick={() => void reintentar()}>
              reintentar
            </button>
          </div>
        )}
        {status && !status.runtime_ready && status.runtime_error && (
          <div className="linea-aviso">
            <span style={{ flex: 1 }}>{status.runtime_error}</span>
            <button className="quiet" onClick={() => setAjustes(true)}>
              configurar proveedor
            </button>
          </div>
        )}
        {aviso && (
          <div className="linea-aviso">
            <span style={{ flex: 1 }}>{aviso}</span>
            <button className="quiet" onClick={() => setAviso(null)}>
              cerrar
            </button>
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
          onCancelar={() => void cancelarTurno()}
          onPaleta={() => setPaleta(true)}
        />
      </div>

      <RightPanel
        plegado={!der}
        pestana={pestana}
        onPestana={setPestana}
        eventos={eventos}
        tareas={tareas}
        trabajos={trabajos}
        cambios={cambios}
        salida={salida}
        onVerCambios={(id) => void cargarCambios(id)}
        onRestaurar={pedirRestauracion}
        onVerSalida={(id) => void verSalida(id)}
        onParar={(id) => void pararTrabajo(id)}
        onTrabajos={() => void verificar("/jobs", "trabajos")}
        onPaleta={() => setPaleta(true)}
      />

      {paleta && (
        <CommandPalette
          onEjecutar={(clave, argumento) => void ejecutarComando(clave, argumento)}
          onCerrar={() => setPaleta(false)}
        />
      )}

      {ajustes && config && (
        <Settings
          config={config}
          safeMode={Boolean(status?.safe_mode)}
          onCerrar={() => setAjustes(false)}
          onPolitica={(modo) => void cambiarAjustes({ policy_mode: modo })}
          onModo={(activo) => void cambiarAjustes({ safe_mode: activo })}
          onProveedor={(datos) => void guardarProveedor(datos)}
          onConfigCambiada={() => void cargarTodo()}
          onAviso={setAviso}
          onConfirmar={(titulo, detalle, accion) => setConfirmacion({ titulo, detalle, accion })}
        />
      )}

      {detalle && (
        <div className="ajustes" onClick={() => setDetalle(null)}>
          <div className="hoja" onClick={(evento) => evento.stopPropagation()}>
            <div className="cabecera-hoja">
              <h2>{detalle.titulo}</h2>
              <button className="quiet" onClick={() => setDetalle(null)}>
                cerrar
              </button>
            </div>
            <pre className="salida" style={{ borderTop: 0, maxHeight: "62vh" }}>
              {detalle.texto}
            </pre>
          </div>
        </div>
      )}

      {confirmacion && (
        <div className="ajustes" onClick={() => setConfirmacion(null)}>
          <div className="hoja" onClick={(evento) => evento.stopPropagation()}>
            <div className="cabecera-hoja">
              <h2>{confirmacion.titulo}</h2>
            </div>
            <div className="tarjeta">
              <p className="nota" style={{ marginTop: 0 }}>
                {confirmacion.detalle}
              </p>
              <div className="acciones">
                <button
                  className="primary"
                  onClick={() => {
                    const accion = confirmacion.accion;
                    setConfirmacion(null);
                    accion();
                  }}
                >
                  sí, adelante
                </button>
                <button className="quiet" onClick={() => setConfirmacion(null)}>
                  cancelar
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
