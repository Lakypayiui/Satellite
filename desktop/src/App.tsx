/**
 * Armazón: cabecera de estado en fichas, conversación como área principal, inspector plegable
 * (que en ventanas estrechas va encima, como cajón), paleta de comandos y los dos modales que sí
 * lo son (volcado de datos y confirmación). La configuración —Ajustes, el asistente inicial y el
 * editor de modelo— no se abre como modal: es una página que sustituye el área principal.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { useI18n } from "./i18n";
import {
  DEFAULT_CHAT_WATERMARK,
  DEFAULT_CHAT_WATERMARK_OPACITY,
  DEFAULT_TEXT_INTENSITY,
  DEFAULT_THEME,
  clampWatermarkOpacity,
  isIdioma,
  isTextIntensity,
  isThemeId,
  type IdiomaId,
  type TextIntensityId,
  type ThemeId,
} from "../electron/themes";
import {
  ApiError,
  Approval,
  ChangeSet,
  ChatReply,
  CommandSpecView,
  ConfigView,
  ContextView,
  ETIQUETA_EVENTO,
  GitMutationView,
  GitStatusView,
  JobInfo,
  JobOutput,
  JobWaitView,
  ParityView,
  QueueItem,
  SavedProviderView,
  SessionInfo,
  SessionMutationView,
  Status,
  StreamItem,
  TaskDetailView,
  TaskItem,
  Turn,
  WorktreesView,
  api,
  bridge,
  mensajeDe,
} from "./api";
import { Chat } from "./Chat";
import { CommandPalette } from "./CommandPalette";
import { comandoTraducido } from "./commandsI18n";
import { ModelEditor } from "./ModelEditor";
import { Onboarding } from "./Onboarding";
import { QuickThink } from "./QuickThink";
import { RightPanel } from "./RightPanel";
import { Settings, type AjustesSeccion } from "./Settings";
import { Sidebar } from "./Sidebar";
import { Splash } from "./Splash";
import { TitleBar } from "./TitleBar";

export type Pestana =
  | "actividad"
  | "git"
  | "cambios"
  | "trabajos"
  | "agentes"
  | "skills";

const MAX_EVENTOS = 400;
const CADA_MS = 1500;
const MODOS = ["ask", "auto", "unrestricted"];
const ENCENDIDO = ["on", "true", "1", "yes", "si", "sí"];

/**
 * Comandos que en esta app NO piden datos en la paleta: abren una pantalla de configuración en el
 * área principal. `provider-add` es el caso que importaba: sus campos (modelo, URL base, clave)
 * son justo el editor de modelo, así que se abre la página en vez de una ventana flotante con
 * los campos. Los demás ya no piden nada porque su pantalla es el formulario.
 */
const COMANDOS_DE_PAGINA: Record<string, true> = {
  provider: true,
  roles: true,
  policy: true,
  onboarding: true,
  "provider-add": true,
};

interface Confirmacion {
  titulo: string;
  detalle: string;
  accion: () => void;
}

interface Detalle {
  titulo: string;
  texto: string;
}

function corto(ruta: string): string {
  return ruta.split(/[\\/]/).filter(Boolean).at(-1) ?? ruta;
}

function hora(iso?: string): string {
  if (!iso) return "";
  const fecha = new Date(iso);
  return Number.isNaN(fecha.getTime()) ? "" : fecha.toLocaleTimeString().slice(0, 8);
}

/** Ruta interna de Ajustes: categoría y, si aplica, editor de modelo (`"new"` = agregar). */
interface RutaAjustes {
  seccion: AjustesSeccion | null;
  editor: null | "new" | string;
}

const SECCIONES_AJUSTES: AjustesSeccion[] = [
  "modelos",
  "roles",
  "permisos",
  "escritorio",
  "safe-proxy",
  "mantenimiento",
  "avanzado",
  "apariencia",
];

function hashAjustes(seccion: AjustesSeccion | null, editor: null | "new" | string): string {
  if (editor !== null) {
    const destino = editor === "new" ? "nuevo" : encodeURIComponent(editor);
    return `#/ajustes/modelos/${destino}`;
  }
  return seccion ? `#/ajustes/${seccion}` : "#/ajustes";
}

/** Lee una ruta de Ajustes del hash; `null` si el hash no pertenece a Ajustes. */
function leerHashAjustes(hash: string): RutaAjustes | null {
  const limpio = (hash || "").replace(/^#/, "");
  if (!limpio.startsWith("/ajustes")) return null;
  const partes = limpio.split("/").filter(Boolean); // ["ajustes", ...]
  const seccionRaw = partes[1] ?? "";
  if (!seccionRaw) return { seccion: null, editor: null };
  const seccion = SECCIONES_AJUSTES.find((item) => item === seccionRaw);
  if (!seccion) return { seccion: null, editor: null };
  if (seccion === "modelos" && partes[2]) {
    return {
      seccion,
      editor: partes[2] === "nuevo" ? "new" : decodeURIComponent(partes[2]),
    };
  }
  return { seccion, editor: null };
}

export function App() {
  const { t, error, idioma, setIdioma, sincronizarIdioma } = useI18n();
  const [status, setStatus] = useState<Status | null>(null);
  const [cargando, setCargando] = useState(true);
  const [caido, setCaido] = useState<string | null>(null);
  // Splash de carga: fondo difuminado + icono hasta que el runtime está listo. Se retira al
  // estar listo, si el backend cae o si se agota el tiempo (nunca tapa la interfaz para siempre).
  const [splashFuera, setSplashFuera] = useState(false);
  useEffect(() => {
    const temporizador = setTimeout(() => setSplashFuera(true), 12_000);
    return () => clearTimeout(temporizador);
  }, []);
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
  // Git y worktrees: estado real del repo y contexto de ejecución (worktree activo).
  const [git, setGit] = useState<GitStatusView | null>(null);
  const [arboles, setArboles] = useState<WorktreesView | null>(null);
  // Cola de instrucciones persistida (lo siguiente a ejecutar).
  const [cola, setCola] = useState<QueueItem[]>([]);
  const drenando = useRef(false);
  const [ocupado, setOcupado] = useState(false);
  const [pestana, setPestana] = useState<Pestana>("actividad");
  const [izq, setIzq] = useState(true);
  // El inspector arranca plegado: la conversación es el área principal.
  const [der, setDer] = useState(false);
  // Cambios recién terminados sin revisar: la pestaña Cambios lo señala hasta que se mire.
  const [atencionCambios, setAtencionCambios] = useState(false);
  // Pantalla de configuración que sustituye el contenido del área principal. Es una sola: Ajustes,
  // el asistente inicial y el editor de modelo no se apilan, porque eso era justo lo que dejaba
  // ventanas flotantes encima de la app. `null` = la conversación.
  const [pantalla, setPantalla] = useState<"ajustes" | "asistente" | null>(null);
  // Editor de modelo: qué proveedor se edita (`null` = agregar uno nuevo). Es una página más, así
  // que «Volver»/guardar devuelven a Ajustes, que es de donde se entra.
  const [modeloEditor, setModeloEditor] = useState<{
    proveedor: SavedProviderView | null;
  } | null>(null);
  // Categoría de Ajustes abierta (`null` = página principal con la lista de categorías).
  const [ajustesSeccion, setAjustesSeccion] = useState<AjustesSeccion | null>(null);
  // Tema de apariencia: arranca con el persistido (el proceso principal lo pasa como argumento).
  const [tema, setTema] = useState<ThemeId>(() => {
    const inicial: unknown = window.satellite?.theme?.initial ?? DEFAULT_THEME;
    return isThemeId(inicial) ? inicial : DEFAULT_THEME;
  });
  // Intensidad del blanco (sólo Deep Dark la usa). Por defecto, media.
  const [intensidad, setIntensidad] = useState<TextIntensityId>(() => {
    const inicial: unknown = window.satellite?.theme?.initialIntensity ?? DEFAULT_TEXT_INTENSITY;
    return isTextIntensity(inicial) ? inicial : DEFAULT_TEXT_INTENSITY;
  });
  // Marca de agua del chat: visible/oculta y su opacidad, persistidas junto al tema.
  const [marca, setMarca] = useState<boolean>(() => {
    const inicial = window.satellite?.theme?.initialWatermark;
    return inicial === "0" ? false : inicial === "1" ? true : DEFAULT_CHAT_WATERMARK;
  });
  const [marcaOpacidad, setMarcaOpacidad] = useState<number>(() =>
    clampWatermarkOpacity(
      window.satellite?.theme?.initialWatermarkOpacity ?? DEFAULT_CHAT_WATERMARK_OPACITY,
    ),
  );

  // La opacidad elegida viaja como variable CSS: el chat la aplica en vivo y el mismo valor
  // vale para temas claros y oscuros (no se reescala por tema).
  useEffect(() => {
    document.documentElement.style.setProperty("--chat-marca-opacidad", String(marcaOpacidad));
  }, [marcaOpacidad]);
  // Proveedores guardados al día para resolver el id del editor al restaurar una ruta.
  const guardadosRef = useRef<SavedProviderView[]>([]);
  const rutaInicialAplicada = useRef(false);
  const [paleta, setPaleta] = useState(false);
  // Comando con el que abrir la paleta ya en el paso de argumentos (elegido con «/» inline).
  const [paletaInicial, setPaletaInicial] = useState<string | null>(null);
  const [confirmacion, setConfirmacion] = useState<Confirmacion | null>(null);
  const [detalle, setDetalle] = useState<Detalle | null>(null);
  // Asistente inicial (proyecto + proveedor); se abre solo la primera vez.
  const onboardingAuto = useRef(false);
  // Respuesta en vivo (TEXT_DELTA) del turno en curso: texto del segmento actual.
  const [vivo, setVivo] = useState<{ segment: number; text: string } | null>(null);
  // Turno que corre en el backend sin que esta ventana lo lanzara (p. ej. tras recargar).
  const [turnoRemoto, setTurnoRemoto] = useState(false);
  // Índice SSE en el que empezó el turno: la línea de estado sólo mira eventos posteriores.
  const [desde, setDesde] = useState(0);
  const ultimoIndice = useRef(0);
  const ocupadoRef = useRef(false);
  // Sin carpeta elegida el "proyecto" sería la propia carpeta de Satellite: la ejecución se
  // bloquea (el backend lo hace cumplir igual; aquí es para no ofrecer lo que va a fallar).
  const bloqueado = Boolean(status && !status.project_explicit);

  // El atributo `data-theme` del documento es la fuente del tema para todo el CSS.
  useEffect(() => {
    document.documentElement.dataset.theme = tema;
  }, [tema]);

  // `data-intensidad` sólo tiene reglas para Deep Dark; en el resto es inerte.
  useEffect(() => {
    document.documentElement.dataset.intensidad = intensidad;
  }, [intensidad]);

  // Si los argumentos de arranque no llegaron, se consultan una vez tema e intensidad.
  useEffect(() => {
    let vigente = true;
    void bridge
      .leerTema()
      .then((preferencia) => {
        if (!vigente) return;
        const guardado = preferencia.theme;
        if (isThemeId(guardado)) setTema((actual) => (actual === guardado ? actual : guardado));
        const intensidad = preferencia.text_intensity;
        if (isTextIntensity(intensidad)) {
          setIntensidad((actual) => (actual === intensidad ? actual : intensidad));
        }
        if (typeof preferencia.chat_watermark === "boolean") {
          const visible = preferencia.chat_watermark;
          setMarca((actual) => (actual === visible ? actual : visible));
        }
        if (typeof preferencia.chat_watermark_opacity === "number") {
          const opacidad = clampWatermarkOpacity(preferencia.chat_watermark_opacity);
          setMarcaOpacidad((actual) => (actual === opacidad ? actual : opacidad));
        }
        if (typeof preferencia.language === "string" && isIdioma(preferencia.language)) {
          sincronizarIdioma(preferencia.language);
        }
      })
      .catch(() => {});
    return () => {
      vigente = false;
    };
  }, []);

  // Cambio inmediato + persistencia; si la escritura falla, el tema sigue aplicado en la sesión.
  const cambiarTema = useCallback((siguiente: ThemeId) => {
    setTema(siguiente);
    document.documentElement.dataset.theme = siguiente;
    void bridge.guardarTema(siguiente).catch(() => {});
  }, []);

  const cambiarIntensidad = useCallback((siguiente: TextIntensityId) => {
    setIntensidad(siguiente);
    document.documentElement.dataset.intensidad = siguiente;
    void bridge.guardarIntensidad(siguiente).catch(() => {});
  }, []);

  // Mismo patrón que el tema: aplicación inmediata + persistencia (si falla, sigue en sesión).
  const cambiarMarca = useCallback((siguiente: boolean) => {
    setMarca(siguiente);
    void bridge.guardarMarca(siguiente).catch(() => {});
  }, []);

  const cambiarMarcaOpacidad = useCallback((siguiente: number) => {
    const valor = clampWatermarkOpacity(siguiente);
    setMarcaOpacidad(valor);
    document.documentElement.style.setProperty("--chat-marca-opacidad", String(valor));
    void bridge.guardarMarcaOpacidad(valor).catch(() => {});
  }, []);

  const cambiarIdioma = useCallback(
    (siguiente: IdiomaId) => {
      setIdioma(siguiente);
    },
    [setIdioma],
  );

  const cargarTodo = useCallback(async () => {
    const [
      estado,
      listaProyectos,
      listaSesiones,
      listaTareas,
      listaTrabajos,
      vistaConfig,
      vistaGit,
      vistaArboles,
      vistaCola,
    ] = await Promise.all([
      api<Status>("/status"),
      api<{ current: string; recents: string[] }>("/projects"),
      api<{ current: string; sessions: SessionInfo[] }>("/sessions"),
      api<{ tasks: TaskItem[] }>("/tasks"),
      api<{ jobs: JobInfo[] }>("/jobs"),
      api<ConfigView>("/config"),
      api<GitStatusView>("/git/status"),
      api<WorktreesView>("/worktrees"),
      api<{ items: QueueItem[] }>("/queue"),
    ]);
    setStatus(estado);
    setTurnoRemoto(Boolean(estado.turn?.running) && !ocupadoRef.current);
    setProyectos(listaProyectos.recents);
    setSesiones(listaSesiones.sessions);
    setSesionActiva(listaSesiones.current);
    setTareas(listaTareas.tasks);
    setTrabajos(listaTrabajos.jobs);
    setConfig(vistaConfig);
    setGit(vistaGit);
    setArboles(vistaArboles);
    setCola(vistaCola.items);
    // Primer arranque: si no se eligió proyecto o no hay proveedor, se abre el asistente
    // una vez. Después queda accesible desde la paleta (Ctrl+K · onboarding).
    if (!onboardingAuto.current && (!estado.project_explicit || !estado.provider_configured)) {
      onboardingAuto.current = true;
      setPantalla("asistente");
    }
    if (listaSesiones.current) {
      const actual = listaSesiones.sessions.find((s) => s.id === listaSesiones.current);
      if (actual?.archived) {
        // La activa quedó archivada (p. ej. se archivó la única conversación): estado vacío.
        setSesionActiva("");
        setTurnos([]);
      } else {
        const detalleSesion = await api<{ turns: Turn[] }>(
          `/sessions/${listaSesiones.current}/turns`,
        );
        setTurnos(detalleSesion.turns);
      }
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
        setAviso(error(exc));
      } finally {
        setCargando(false);
      }
    })();
    const cortarCaida = bridge.onBackendDown((mensaje) => setCaido(mensaje));
    const cortarEventos = bridge.subscribe(0, (item) => {
      const tipo = item.event.event_type;
      if (typeof item.index === "number") {
        ultimoIndice.current = Math.max(ultimoIndice.current, item.index);
      }
      // Los deltas de texto (streaming) alimentan la burbuja en vivo, no la actividad.
      if (tipo === "text_delta") {
        const payload = item.event.payload ?? {};
        const segmento = typeof payload.segment === "number" ? payload.segment : 0;
        const trozo = typeof payload.text === "string" ? payload.text : "";
        setVivo((previo) =>
          previo && previo.segment === segmento
            ? { segment: segmento, text: previo.text + trozo }
            : { segment: segmento, text: trozo },
        );
        return;
      }
      // Un permiso pendiente se anuncia por evento: no hace falta sondear.
      if (tipo === "approval_requested" || tipo === "approval_resolved") {
        void recargarPermisos().catch(() => {});
        return;
      }
      if (tipo === "task_finished") {
        setTurnoRemoto(false);
        // Un turno que no lanzó esta ventana: su respuesta llega recargando la sesión.
        if (!ocupadoRef.current) {
          setVivo(null);
          void cargarTodo().catch(() => {});
        }
      }
      setEventos((previos) => [...previos, item].slice(-MAX_EVENTOS));
      if (tipo === "task_finished" || tipo === "task_started") {
        const tareaId = item.event.task_id;
        void api<{ tasks: TaskItem[] }>("/tasks")
          .then((r) => {
            setTareas(r.tasks);
            // Al terminar una tarea que tocó archivos, se abre Cambios y se marca para revisar.
            if (tipo !== "task_finished" || !tareaId) return;
            // El repo pudo cambiar: se refresca el estado de git en el sitio.
            void api<GitStatusView>("/git/status").then(setGit).catch(() => {});
            const tarea = r.tasks.find((elemento) => elemento.task_id === tareaId);
            if (tarea && tarea.changes.length > 0) {
              // Se marca para revisar sin abrir el cajón: la respuesta es lo primero.
              setAtencionCambios(true);
              void api<ChangeSet>(`/tasks/${encodeURIComponent(tareaId)}/changes`)
                .then(setCambios)
                .catch(() => {});
            }
          })
          .catch(() => {});
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

  // Los permisos llegan por evento (approval_requested). Los trabajos en segundo plano no
  // emiten eventos: se miran a intervalos cortos mientras hay un turno.
  useEffect(() => {
    if (!ocupado) return;
    const timer = setInterval(() => {
      void api<{ jobs: JobInfo[] }>("/jobs").then((r) => setTrabajos(r.jobs)).catch(() => {});
    }, CADA_MS);
    return () => clearInterval(timer);
  }, [ocupado]);

  // Router interno de Ajustes: el hash refleja la categoría/página abierta y el historial del
  // navegador (atrás/adelante) vuelve a aplicar la ruta.
  useEffect(() => {
    const aplicar = () => {
      const ruta = leerHashAjustes(window.location.hash);
      if (ruta) {
        aplicarRutaAjustes(ruta);
        return;
      }
      const hash = window.location.hash;
      if (!hash || hash === "#/") {
        setPantalla((actual) => (actual === "ajustes" ? null : actual));
        setAjustesSeccion(null);
        setModeloEditor(null);
      }
    };
    window.addEventListener("hashchange", aplicar);
    return () => window.removeEventListener("hashchange", aplicar);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Restaura una ruta de Ajustes de un enlace o recarga. Si apunta a un editor, espera a tener
  // la config para resolver el proveedor; la sección se aplica igualmente.
  useEffect(() => {
    if (rutaInicialAplicada.current) return;
    const ruta = leerHashAjustes(window.location.hash);
    if (!ruta) {
      rutaInicialAplicada.current = true;
      return;
    }
    if (ruta.editor && ruta.editor !== "new" && !config) return;
    rutaInicialAplicada.current = true;
    aplicarRutaAjustes(ruta);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [config]);

  // Se vuelve a registrar en cada render a propósito: los atajos usan el estado de ahora.
  useEffect(() => {
    const alPulsar = (evento: KeyboardEvent) => {
      if (evento.key === "Escape") {
        // Con la paleta abierta, su propio Esc vuelve atrás o la cierra.
        if (paleta) return;
        if (detalle) setDetalle(null);
        else if (confirmacion) setConfirmacion(null);
        // Las pantallas de configuración se cierran en el orden en que están: el editor devuelve a
        // Ajustes y Ajustes a la conversación.
        else if (modeloEditor) cerrarEditorModelo();
        else if (pantalla === "ajustes" && ajustesSeccion) irAAjustes(null);
        else if (pantalla === "ajustes") cerrarAjustes();
        else if (pantalla) setPantalla(null);
        else if (der) setDer(false);
        return;
      }
      if (!evento.ctrlKey) return;
      const tecla = evento.key.toLowerCase();
      if (tecla === "k") {
        evento.preventDefault();
        if (paleta) setPaleta(false);
        else abrirPaleta();
      } else if (tecla === "n") {
        evento.preventDefault();
        void nuevaSesion();
      } else if (tecla === "b") {
        evento.preventDefault();
        setIzq((valor) => !valor);
      } else if (tecla === "j") {
        evento.preventDefault();
        setDer((valor) => !valor);
      } else if (tecla === ",") {
        evento.preventDefault();
        irAAjustes(null);
      }
    };
    window.addEventListener("keydown", alPulsar);
    return () => window.removeEventListener("keydown", alPulsar);
  });

  async function enviar(texto: string, planMode: boolean) {
    if (bloqueado) {
      setAviso(t("app.blocked.run"));
      setPantalla("asistente");
      return;
    }
    // Un turno a la vez: con uno en curso, lo correcto es encolar (no lanzar otro en paralelo).
    if (ocupado) {
      setAviso(t("app.busy.enqueue"));
      return;
    }
    setAviso(null);
    setOcupado(true);
    ocupadoRef.current = true;
    setDesde(ultimoIndice.current);
    setVivo(null);
    // El mensaje del usuario se muestra de inmediato; la respuesta del backend lo
    // reconciliará con el turno persistido al terminar (cargarTodo reemplaza la lista).
    const optimista: Turn = {
      role: "user",
      content: texto,
      task_id: "",
      status: "pending",
      diagram: null,
    };
    setTurnos((previos) => [...previos, optimista]);
    try {
      const respuesta = await api<ChatReply>("/messages", {
        method: "POST",
        body: { text: texto, plan_mode: planMode },
      });
      // La respuesta se pinta en cuanto llega; la recarga completa va después, de fondo.
      if (!respuesta.cancelled) {
        const respuestaTurno: Turn = {
          role: "assistant",
          content: respuesta.text,
          task_id: respuesta.task_id,
          status: respuesta.status,
          diagram: respuesta.diagram ?? null,
          question: respuesta.question ?? null,
        };
        setTurnos((previos) => [
          ...previos.map((turno) =>
            turno === optimista ? { ...turno, status: "", task_id: respuesta.task_id } : turno,
          ),
          respuestaTurno,
        ]);
      }
      setVivo(null);
      if (respuesta.error) setAviso(respuesta.error);
      else if (
        respuesta.context &&
        (respuesta.context.refs.length > 0 ||
          (respuesta.context.external?.length ?? 0) > 0 ||
          (respuesta.context.ambiguous?.length ?? 0) > 0 ||
          (respuesta.context.ignored?.length ?? 0) > 0 ||
          respuesta.context.missing.length > 0)
      ) {
        // Igual que la TUI: se dice qué @contexto se adjuntó (o quedó fuera y no se
        // autorizó), qué es ambiguo, qué es sensible/excluido y qué no se encontró.
        setAviso(t("app.context.summary", { resumen: respuesta.context.summary }));
      }
      void cargarTodo().catch((exc) => setAviso(error(exc)));
    } catch (exc) {
      if (exc instanceof ApiError && exc.code === "turn_running") {
        // El backend ya tiene un turno (otra ventana, o tras recargar): se encola.
        setTurnos((previos) => previos.filter((turno) => turno !== optimista));
        await encolar(texto, planMode);
        setAviso(t("app.busy.enqueue"));
      } else {
        setAviso(error(exc));
      }
    } finally {
      setVivo(null);
      setOcupado(false);
      ocupadoRef.current = false;
      void recargarPermisos().catch(() => {});
    }
    // Terminado el turno, si hay instrucciones encoladas se ejecuta la siguiente.
    void drenarCola();
  }

  /** Corta el turno en curso. Lo ya hecho queda: no se finge que la tarea no pasó. */
  async function cancelarTurno() {
    try {
      const respuesta = await api<{ how?: string; note?: string }>("/turns/cancel", {
        method: "POST",
      });
      const via =
        respuesta.how === "cooperative" ? t("app.cancel.cooperative") : t("app.cancel.forced");
      setAviso(
        t("app.cancel.done", {
          via,
          nota: respuesta.note ?? t("app.cancel.defaultNote"),
        }),
      );
    } catch (exc) {
      setAviso(error(exc));
    } finally {
      // «Ocupado» lo libera el propio turno cuando su petición termina: liberarlo aquí
      // dejaba enviar otro mensaje mientras el anterior seguía en vuelo.
      await recargarPermisos().catch(() => {});
    }
  }

  async function nuevaSesion() {
    if (bloqueado) {
      setAviso(t("app.blocked.newSession"));
      setPantalla("asistente");
      return;
    }
    try {
      const respuesta = await api<{ session_id: string; sessions: SessionInfo[] }>("/sessions", {
        method: "POST",
        body: { name: "" },
      });
      // La respuesta ya trae la lista: no hace falta recargar nada más.
      setSesiones(respuesta.sessions);
      setSesionActiva(respuesta.session_id);
      setTurnos([]);
      setAtencionCambios(false);
    } catch (exc) {
      setAviso(error(exc));
    }
  }

  async function usarSesion(id: string) {
    try {
      const respuesta = await api<{ turns: Turn[] }>(`/sessions/${id}/activate`, {
        method: "POST",
      });
      setSesionActiva(id);
      setTurnos(respuesta.turns);
      setAtencionCambios(false);
    } catch (exc) {
      setAviso(error(exc));
    }
  }

  /** Adopta la lista y la activa que devuelve una mutación, sin recargar otros datos. */
  function adoptarSesiones(vista: SessionMutationView) {
    setSesiones(vista.sessions);
    setSesionActiva(vista.sessions.find((s) => s.id === vista.current && !s.archived)?.id ?? "");
  }

  async function renombrarSesion(id: string, nombre: string) {
    try {
      const vista = await api<SessionMutationView>(`/sessions/${id}`, {
        method: "PATCH",
        body: { name: nombre },
      });
      adoptarSesiones(vista);
    } catch (exc) {
      setAviso(error(exc));
    }
  }

  /** Fija o deja de fijar una conversación. No cambia cuál está activa. */
  async function fijarSesion(id: string, pinned: boolean) {
    try {
      const vista = await api<SessionMutationView>(`/sessions/${id}`, {
        method: "PATCH",
        body: { pinned },
      });
      adoptarSesiones(vista);
    } catch (exc) {
      setAviso(error(exc));
    }
  }

  /** Archiva o desarchiva. Al archivar la activa, el backend ya eligió otra (o ninguna). */
  async function archivarSesion(id: string, archived: boolean) {
    try {
      const anterior = sesionActiva;
      const vista = await api<SessionMutationView>(`/sessions/${id}`, {
        method: "PATCH",
        body: { archived },
      });
      adoptarSesiones(vista);
      // Sólo si la conversación activa cambió se tocan sus turnos: archivar otra, o fijar,
      // no recarga nada. Desarchivar la única vuelve a dejarla activa y aquí se carga.
      const activa = vista.sessions.find((s) => s.id === vista.current && !s.archived)?.id ?? "";
      if (activa !== anterior) {
        if (activa) {
          const detalle = await api<{ turns: Turn[] }>(`/sessions/${activa}/turns`);
          setTurnos(detalle.turns);
        } else {
          setTurnos([]);
        }
        setAtencionCambios(false);
      }
    } catch (exc) {
      setAviso(error(exc));
    }
  }

  /** Confirmación explícita: borrar una conversación es irreversible. */
  function pedirBorrarSesion(id: string) {
    const sesion = sesiones.find((item) => item.id === id);
    const nombre = sesion?.name || id;
    setConfirmacion({
      titulo: t("sidebar.deleteTitle", { nombre }),
      detalle: t("sidebar.deleteDetail"),
      accion: () => void borrarSesion(id, nombre),
    });
  }

  async function borrarSesion(id: string, nombre: string) {
    try {
      const vista = await api<{ session_id: string; current: string; sessions: SessionInfo[] }>(
        `/sessions/${id}`,
        { method: "DELETE" },
      );
      setSesiones(vista.sessions);
      const activa =
        vista.sessions.find((s) => s.id === vista.current && !s.archived)?.id ?? "";
      setSesionActiva(activa);
      if (activa) {
        const detalle = await api<{ turns: Turn[] }>(`/sessions/${activa}/turns`);
        setTurnos(detalle.turns);
      } else {
        setTurnos([]);
      }
      setAtencionCambios(false);
      setAviso(t("sidebar.deleted", { nombre }));
    } catch (exc) {
      setAviso(error(exc));
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
      setAtencionCambios(false);
      await cargarTodo();
      await recargarPermisos();
    } catch (exc) {
      setAviso(error(exc));
    }
  }

  async function decidirPermiso(
    proposalId: string,
    projectRoot: string,
    decision: string,
    feedback = "",
    reason = "",
  ) {
    try {
      await api(`/approvals/${proposalId}`, {
        method: "POST",
        body: { decision, feedback, reason, project_root: projectRoot },
      });
      await recargarPermisos();
    } catch (exc) {
      setAviso(error(exc));
    }
  }

  async function cargarCambios(taskId: string) {
    try {
      setCambios(await api<ChangeSet>(`/tasks/${taskId}/changes`));
    } catch (exc) {
      setCambios(null);
      setAviso(error(exc));
    }
  }

  /** Salta al primer permiso pendiente: es donde la app está esperando una respuesta. */
  function irAPermiso() {
    document
      .getElementById("permiso-pendiente")
      ?.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  // --- git y worktrees ---

  async function cargarGit() {
    try {
      setGit(await api<GitStatusView>("/git/status"));
    } catch (exc) {
      setAviso(error(exc));
    }
  }

  /** Una mutación git (stage/unstage/commit) devuelve el estado fresco: se adopta sin recargar. */
  async function mutarGit(ruta: string, cuerpo: unknown) {
    try {
      const vista = await api<GitMutationView>(ruta, { method: "POST", body: cuerpo });
      setGit(vista.status);
      const error = typeof vista.result?.error === "string" ? vista.result.error : "";
      if (error) setAviso(error);
    } catch (exc) {
      setAviso(error(exc));
    }
  }

  function marcarGit(paths: string[], stage: boolean) {
    void mutarGit(stage ? "/git/stage" : "/git/unstage", { paths });
  }

  async function commitGit(message: string) {
    await mutarGit("/git/commit", { message });
  }

  async function crearWorktree(name: string) {
    try {
      setArboles(await api<WorktreesView>("/worktrees", { method: "POST", body: { name } }));
      setAviso(t("app.worktree.created", { nombre: name }));
      await cargarGit();
    } catch (exc) {
      setAviso(error(exc));
    }
  }

  async function activarWorktree(path: string) {
    try {
      const vista = await api<WorktreesView>("/worktrees/active", {
        method: "POST",
        body: { path },
      });
      setArboles(vista);
      setAviso(
        vista.runtime_error
          ? t("app.worktree.changedNoRuntime", { error: vista.runtime_error })
          : vista.active
            ? t("app.worktree.runsIn", { destino: vista.active })
            : t("app.worktree.runsProject"),
      );
      await cargarGit();
    } catch (exc) {
      setAviso(error(exc));
    }
  }

  function pedirQuitarWorktree(path: string, force: boolean) {
    setConfirmacion({
      titulo: force ? t("app.worktree.removeForceTitle") : t("app.worktree.removeTitle"),
      detalle: force
        ? t("app.worktree.removeForceDetail", { ruta: path })
        : t("app.worktree.removeDetail", { ruta: path }),
      accion: () => void quitarWorktree(path, force),
    });
  }

  async function quitarWorktree(path: string, force: boolean) {
    try {
      const vista = await api<WorktreesView>("/worktrees/remove", {
        method: "POST",
        body: { path, force },
      });
      setArboles(vista);
      setAviso(t("app.worktree.removed", { ruta: vista.removed || path }));
      await cargarGit();
    } catch (exc) {
      setAviso(error(exc));
    }
  }

  // --- cola de instrucciones ---

  async function encolar(texto: string, planMode: boolean) {
    try {
      const vista = await api<{ items: QueueItem[] }>("/queue", {
        method: "POST",
        body: { text: texto, plan_mode: planMode },
      });
      setCola(vista.items);
      setAviso(t("app.queue.enqueued"));
    } catch (exc) {
      setAviso(error(exc));
    }
  }

  async function editarCola(id: string, texto: string) {
    try {
      const vista = await api<{ items: QueueItem[] }>(`/queue/${id}`, {
        method: "PATCH",
        body: { text: texto },
      });
      setCola(vista.items);
    } catch (exc) {
      setAviso(error(exc));
    }
  }

  async function quitarCola(id: string) {
    try {
      const vista = await api<{ items: QueueItem[] }>(`/queue/${id}`, { method: "DELETE" });
      setCola(vista.items);
    } catch (exc) {
      setAviso(error(exc));
    }
  }

  async function vaciarCola() {
    try {
      const vista = await api<{ items: QueueItem[] }>("/queue/clear", { method: "POST" });
      setCola(vista.items);
    } catch (exc) {
      setAviso(error(exc));
    }
  }

  /** Tras terminar un turno, si hay cola, ejecuta el siguiente (uno tras otro, en orden). */
  async function drenarCola() {
    // Nunca se ejecuta un item mientras hay un turno en curso (sería concurrencia real).
    if (drenando.current || ocupado) return;
    drenando.current = true;
    try {
      let vista = await api<{ items: QueueItem[] }>("/queue");
      setCola(vista.items);
      while (vista.items.length > 0) {
        const siguiente = vista.items[0];
        vista = await api<{ items: QueueItem[] }>(`/queue/${siguiente.id}`, { method: "DELETE" });
        setCola(vista.items);
        await enviar(siguiente.text, siguiente.plan_mode);
      }
    } catch (exc) {
      setAviso(error(exc));
    } finally {
      drenando.current = false;
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
          titulo: t("app.restore.detailTitle", { id: taskId }),
          texto: previa.preview?.diff ?? t("app.noDiff"),
        });
        setConfirmacion({
          titulo: t("app.restore.title"),
          detalle: t("app.restore.confirmDetail", {
            n: previa.preview?.files.length ?? 0,
          }),
          accion: () => void confirmarRestauracion(taskId),
        });
      } catch (exc) {
        setAviso(error(exc));
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
        t("app.restore.done", {
          restaurados: resultado.result?.restored?.length ?? 0,
          eliminados: resultado.result?.removed?.length ?? 0,
        }),
      );
      await cargarTodo();
      await cargarCambios(taskId);
    } catch (exc) {
      setAviso(error(exc));
    }
  }

  async function verSalida(jobId: string) {
    try {
      setSalida(await api<JobOutput>(`/jobs/${jobId}/output`));
    } catch (exc) {
      setAviso(error(exc));
    }
  }

  async function pararTrabajo(jobId: string) {
    try {
      await api(`/jobs/${jobId}/stop`, { method: "POST" });
      const respuesta = await api<{ jobs: JobInfo[] }>("/jobs");
      setTrabajos(respuesta.jobs);
      setAviso(t("app.job.stopped", { id: jobId }));
    } catch (exc) {
      setAviso(error(exc));
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

  /** Comando para abrir la TUI desde una terminal (la app gráfica no puede abrirla sola). */
  function comandoTui(): string {
    return ruta
      ? `python -m satellite.cli tui --project "${ruta}"`
      : "python -m satellite.cli tui";
  }

  /** Copia al portapapeles, con respaldo: no todos los contextos lo permiten. */
  async function copiarTexto(texto: string): Promise<boolean> {
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(texto);
        return true;
      }
    } catch {
      // sin permiso de portapapeles se prueba el respaldo de abajo
    }
    try {
      const area = document.createElement("textarea");
      area.value = texto;
      area.setAttribute("readonly", "");
      area.style.position = "fixed";
      area.style.opacity = "0";
      document.body.appendChild(area);
      area.select();
      const ok = document.execCommand("copy");
      document.body.removeChild(area);
      return ok;
    } catch {
      return false;
    }
  }

  /** Abre la paleta; con `inicial`, directo al paso de argumentos de ese comando. */
  function abrirPaleta(inicial?: string) {
    setPaletaInicial(inicial ?? null);
    setPaleta(true);
  }

  /** Un «/comando» del compositor: los que abren una pantalla se van a ella sin más pasos —
   *  el formulario es la página, no una ventana con campos—; los que piden datos, a la paleta. */
  function lanzarComando(spec: CommandSpecView) {
    if (COMANDOS_DE_PAGINA[spec.id]) {
      void ejecutarComando(spec.id, {});
      return;
    }
    if (spec.args.length > 0) {
      abrirPaleta(spec.id);
      return;
    }
    void ejecutarComando(spec.id, {});
  }

  /** Todo lo que ofrece la paleta. Las claves son las del catálogo compartido (`GET /commands`). */
  async function ejecutarComando(clave: string, args: Record<string, string>) {
    const arg = (nombre: string) => (args[nombre] ?? "").trim();
    try {
      switch (clave) {
        case "project":
          await abrirProyecto();
          break;

        case "sessions":
          await verificar("/sessions", t("app.detail.sessions"));
          break;
        case "new":
          await nuevaSesion();
          break;
        case "session": {
          // /session <new|use|name|pin|unpin|archive|unarchive>: misma operación que la barra.
          const accion = arg("accion").toLowerCase();
          const valor = arg("valor");
          if (accion === "new") {
            await nuevaSesion();
          } else if (accion === "use") {
            if (valor) await usarSesion(valor);
            else setAviso(t("app.usage.sessionUse"));
          } else if (accion === "name") {
            if (!sesionActiva) setAviso(t("app.session.noActiveRename"));
            else if (valor) await renombrarSesion(sesionActiva, valor);
            else setAviso(t("app.usage.sessionName"));
          } else if (accion === "pin" || accion === "unpin") {
            if (valor) await fijarSesion(valor, accion === "pin");
            else setAviso(t("app.usage.sessionPin"));
          } else if (accion === "archive" || accion === "unarchive") {
            if (valor) await archivarSesion(valor, accion === "archive");
            else setAviso(t("app.usage.sessionArchive"));
          } else {
            setAviso(t("app.session.unknownAction"));
          }
          break;
        }
        case "panel-left":
          setIzq((valor) => !valor);
          break;
        case "git":
        case "worktrees":
          // Ambas abren la pestaña Git del inspector (estado y worktrees viven juntos).
          setPestana("git");
          setDer(true);
          void cargarGit();
          break;
        case "comments":
          setPestana("cambios");
          setDer(true);
          break;
        case "queue": {
          // Lectura de la cola; editarla/ejecutarla es del compositor (no se duplica aquí).
          const lineas = cola.length
            ? cola
                .map(
                  (item, indice) =>
                    `${indice + 1}. ${item.text}${item.plan_mode ? " [plan]" : ""}`,
                )
                .join("\n")
            : t("app.queue.empty");
          setDetalle({
            titulo: t("app.queue.title"),
            texto: `${lineas}\n\n${t("app.queue.note")}`,
          });
          break;
        }
        case "panel-right":
          setDer((valor) => !valor);
          break;

        case "context": {
          const datos = await api<ContextView>("/context");
          setDetalle({
            titulo: t("app.context.title"),
            texto: `${datos.text}\n\n${t("app.context.meta", {
              real: datos.real_tokens,
              estimado: datos.estimated_tokens,
              ventana: datos.window_tokens,
              fuente: datos.window_source,
            })}`,
          });
          break;
        }
        case "run":
          // Igual que /run: un turno normal con el runtime.
          await enviar(arg("mensaje"), false);
          break;
        case "plan":
          // Igual que /plan: el LLM propone alternativas antes de tocar nada.
          await enviar(arg("tarea"), true);
          break;
        case "history": {
          if (!sesionActiva) {
            setAviso(t("app.session.noActive"));
            break;
          }
          const datos = await api<{ turns: Turn[] }>(
            `/sessions/${encodeURIComponent(sesionActiva)}/turns`,
          );
          const texto = datos.turns.length
            ? datos.turns
                .map((turno) => `${turno.role === "user" ? "›" : "•"} ${turno.content}`)
                .join("\n\n")
            : t("app.history.empty");
          setDetalle({ titulo: t("app.history.title"), texto });
          break;
        }
        case "compact":
          await verificar("/compact", t("app.detail.compact"), "POST");
          break;
        case "cancel":
          await cancelarTurno();
          break;

        case "agents":
          await verificar("/agents", t("app.detail.agentCatalog"));
          break;
        case "create":
          await verificar("/agents", t("app.detail.createAgent"), "POST", {
            need: arg("necesidad"),
          });
          break;
        case "skills":
          await verificar("/skills", t("app.detail.skillCatalog"));
          break;
        case "skill-search":
          await verificar(
            `/skills/search?q=${encodeURIComponent(arg("texto"))}`,
            t("app.detail.searchSkills"),
          );
          break;
        case "skill-edit": {
          const cuerpo: { description?: string; tags?: string[] } = {};
          if (arg("descripcion")) cuerpo.description = arg("descripcion");
          const tags = arg("tags")
            .split(",")
            .map((tag) => tag.trim())
            .filter(Boolean);
          if (tags.length > 0) cuerpo.tags = tags;
          const manifiesto = await api<Record<string, unknown>>(
            `/skills/${encodeURIComponent(arg("id"))}`,
            { method: "PATCH", body: cuerpo },
          );
          setDetalle({
            titulo: t("app.skill.updated", { id: arg("id") }),
            texto: JSON.stringify(manifiesto, null, 2),
          });
          await cargarTodo();
          break;
        }
        case "skill-delete":
          setConfirmacion({
            titulo: t("app.skill.deleteTitle", { id: arg("id") }),
            detalle: t("app.skill.deleteDetail"),
            accion: () =>
              void verificar(
                `/skills/${encodeURIComponent(arg("id"))}`,
                t("app.detail.deleteSkill"),
                "DELETE",
              ),
          });
          break;
        case "curate":
          await verificar("/curate", t("app.detail.curate"), "POST", {
            request: arg("peticion"),
          });
          break;
        case "tools":
          await verificar("/tools", t("app.detail.runtimeTools"));
          break;

        case "status": {
          const id = arg("task_id");
          const datos = await api<TaskDetailView>(`/tasks/${encodeURIComponent(id)}`);
          const lineas = (datos.events ?? []).map((evento) => {
            const etiqueta = t(ETIQUETA_EVENTO[evento.event_type] ?? "event.unknown");
            const spec = evento.payload?.spec;
            const sujeto = evento.tool_name ?? (spec === undefined ? "" : `spec=${String(spec)}`);
            const marca = hora(evento.timestamp);
            return `${marca ? `${marca}  ` : ""}${etiqueta}${sujeto ? ` · ${sujeto}` : ""}`;
          });
          setDetalle({
            titulo: t("app.events.title", { id }),
            texto: lineas.length > 0 ? lineas.join("\n") : t("app.events.empty"),
          });
          break;
        }
        case "inspect": {
          const id = arg("task_id");
          const datos = await api<TaskDetailView>(`/tasks/${encodeURIComponent(id)}`);
          setDetalle({
            titulo: t("app.trace.title", { id }),
            texto: datos.trace
              ? JSON.stringify(datos.trace, null, 2)
              : t("app.trace.none", { id }),
          });
          break;
        }
        case "changes":
          setPestana("cambios");
          setDer(true);
          break;
        case "jobs":
          setPestana("trabajos");
          setDer(true);
          await verificar("/jobs", t("app.detail.jobs"));
          break;
        case "job-output": {
          const id = arg("id");
          const datos = await api<JobOutput>(`/jobs/${encodeURIComponent(id)}/output`);
          setDetalle({ titulo: t("app.job.outputTitle", { id }), texto: datos.output || t("app.job.noOutput") });
          break;
        }
        case "job-wait": {
          const id = arg("id");
          const segundos = Number(arg("segundos") || "30") || 30;
          const datos = await api<JobWaitView>(`/jobs/${encodeURIComponent(id)}/wait`, {
            method: "POST",
            body: { timeout: segundos },
          });
          setAviso(t("app.job.status", { id, estado: datos.estado, codigo: datos.status }));
          break;
        }
        case "job-stop":
          await pararTrabajo(arg("id"));
          break;
        case "tasks":
          await verificar("/tasks", t("app.detail.tasks"));
          break;
        case "undo-task":
          pedirRestauracion(arg("task_id"));
          break;

        case "provider":
          irAAjustes("modelos");
          break;
        case "providers":
          await verificar("/providers", t("app.detail.savedProviders"));
          break;
        case "provider-catalog":
          await verificar("/providers/catalog", t("app.detail.providerCatalog"));
          break;
        case "provider-models": {
          // Igual que `satellite provider models`: lista los modelos de una URL.
          const base = arg("base_url") || provActivo?.base_url || "";
          if (!base) {
            setAviso(t("app.provider.needBaseUrl"));
            break;
          }
          const familia = arg("family") || "openai";
          await verificar(
            `/models?base_url=${encodeURIComponent(base)}&family=${encodeURIComponent(familia)}&api_key=${encodeURIComponent(arg("api_key"))}`,
            t("app.detail.providerModels"),
          );
          break;
        }
        case "provider-add":
          // Igual que `satellite provider add`, pero el formulario es el editor de modelo: una
          // página, no una ventana con campos. Es el mismo flujo que Ajustes → «Agregar modelo».
          abrirEditorModelo(null);
          break;
        case "provider-use":
          // Igual que `satellite provider use`: deja activo un proveedor guardado.
          await activarProveedor(arg("id"));
          break;
        case "provider-remove":
          setConfirmacion({
            titulo: t("app.provider.deleteTitle", { id: arg("id") }),
            detalle: t("app.provider.deleteDetail"),
            accion: () =>
              void verificar(
                `/providers/${encodeURIComponent(arg("id"))}`,
                t("app.detail.deleteProvider"),
                "DELETE",
              ),
          });
          break;
        case "roles":
          irAAjustes("roles");
          break;

        case "policy":
          irAAjustes("permisos");
          break;
        case "mode": {
          const modo = arg("modo").toLowerCase();
          if (!MODOS.includes(modo)) {
            setAviso(t("app.mode.unknown", { modo: arg("modo") }));
            break;
          }
          await cambiarAjustes({ policy_mode: modo });
          break;
        }
        case "safe-mode":
          await cambiarAjustes({
            safe_mode: ENCENDIDO.includes(arg("valor").toLowerCase()),
          });
          break;
        case "doctor":
          await verificar("/doctor", t("app.detail.doctor"));
          break;
        case "stats":
          await verificar("/stats", t("app.detail.stats"));
          break;
        case "setup":
          await verificar("/setup", t("app.detail.setup"), "POST");
          break;
        case "init":
          await verificar("/init", t("app.detail.init"), "POST");
          break;
        case "reset":
          setConfirmacion({
            titulo: t("settings.maintenance.reset.title"),
            detalle: t("settings.maintenance.reset.detail"),
            accion: () =>
              void verificar("/reset", t("app.detail.reset"), "POST", {
                keep_skills: true,
                confirm: true,
              }),
          });
          break;
        case "snapshots":
          await verificar("/snapshots", t("app.detail.snapshots"));
          break;
        case "restore":
          setConfirmacion({
            titulo: t("app.snapshot.restoreTitle", { nombre: arg("nombre") }),
            detalle: t("app.snapshot.restoreDetail"),
            accion: () =>
              void verificar("/restore", t("app.detail.restoreSnapshot"), "POST", {
                name: arg("nombre"),
                confirm: true,
              }),
          });
          break;
        case "config":
          setDetalle({
            titulo: t("app.config.title"),
            texto: JSON.stringify(config?.config ?? {}, null, 2),
          });
          break;

        case "commands": {
          // El catálogo completo y su paridad: lo mismo que publica `satellite commands`.
          const datos = await api<{ commands: CommandSpecView[]; parity: ParityView }>(
            "/commands",
          );
          // El catálogo se traduce al idioma actual: el id y el `usage` no se tocan.
          const lineas = datos.commands.map(
            (comando) =>
              `/${comando.id}  [${comando.surfaces.join(",")}]  ${comandoTraducido(comando, t).summary}`,
          );
          const huecos = Object.entries(datos.parity.gaps).map(
            ([superficie, ids]) =>
              `${superficie}: ${ids.length > 0 ? ids.join(", ") : t("app.commands.none")}`,
          );
          setDetalle({
            titulo: t("app.commands.title"),
            texto: [...lineas, "", t("app.commands.gaps"), ...huecos].join("\n"),
          });
          break;
        }
        case "tui": {
          // La TUI es la otra interfaz: la app gráfica no puede abrirla por ti. En vez de
          // esconder el comando, se dice y se da el comando exacto para una terminal.
          const comando = comandoTui();
          const copiado = await copiarTexto(comando);
          setDetalle({
            titulo: t("app.tui.title"),
            texto: [
              t("app.tui.line1"),
              t("app.tui.line2"),
              "",
              `  ${comando}`,
              "",
              copiado ? t("app.tui.copied") : t("app.tui.copyManual"),
            ].join("\n"),
          });
          break;
        }
        case "clear":
          // Igual que /clear: limpia la actividad en pantalla (el historial queda).
          setEventos([]);
          setAviso(null);
          break;
        case "help": {
          // Igual que /help: la lista de comandos con su uso, desde el catálogo compartido.
          const datos = await api<{ commands: CommandSpecView[] }>("/commands");
          const propios = datos.commands.filter((comando) =>
            comando.surfaces.includes("desktop"),
          );
          const ancho = Math.max(...propios.map((comando) => (comando.usage || comando.id).length));
          const texto = [
            t("app.help.title"),
            "",
            ...propios.map(
              (comando) =>
                `  ${(comando.usage || `/${comando.id}`).padEnd(ancho)}  ${comandoTraducido(comando, t).summary}`,
            ),
            "",
            t("app.help.freeText"),
          ].join("\n");
          setDetalle({ titulo: t("app.help.detailTitle"), texto });
          break;
        }
        case "onboarding":
          setPantalla("asistente");
          break;
        case "shortcuts":
          setDetalle({
            titulo: t("shortcuts.title"),
            texto: [
              `Ctrl+K   ${t("shortcuts.palette")}`,
              `Ctrl+N   ${t("shortcuts.newChat")}`,
              `Ctrl+B   ${t("shortcuts.leftPanel")}`,
              `Ctrl+J   ${t("shortcuts.inspector")}`,
              `Ctrl+,   ${t("shortcuts.settings")}`,
              `Enter    ${t("shortcuts.send")}`,
              `Esc      ${t("shortcuts.esc")}`,
            ].join("\n"),
          });
          break;
        case "quit":
          window.close();
          break;
        default:
          // El catálogo lo sirve el backend: si trae una acción que la app no sabe
          // ejecutar todavía, se dice en vez de no hacer nada en silencio.
          setAviso(t("app.command.unknown", { clave }));
          break;
      }
    } catch (exc) {
      setAviso(error(exc));
    }
  }

  async function cambiarAjustes(opciones: { policy_mode?: string; safe_mode?: boolean }) {
    try {
      setConfig(await api<ConfigView>("/config", { method: "PUT", body: opciones }));
      await cargarTodo();
    } catch (exc) {
      setAviso(error(exc));
    }
  }

  async function guardarProveedor(datos: {
    name: string;
    model: string;
    base_url: string;
    api_key: string;
    api_key_env: string;
    account_id?: string;
  }) {
    try {
      setConfig(await api<ConfigView>("/providers", { method: "POST", body: datos }));
      setAviso(t("app.provider.savedActive", { nombre: datos.name }));
      await cargarTodo();
    } catch (exc) {
      setAviso(error(exc));
    }
  }

  /** Aplica una ruta de Ajustes al estado (no toca el hash). */
  function aplicarRutaAjustes(ruta: RutaAjustes) {
    setPantalla("ajustes");
    setAjustesSeccion(ruta.seccion);
    if (ruta.editor === null) {
      setModeloEditor(null);
    } else if (ruta.editor === "new") {
      setModeloEditor({ proveedor: null });
    } else {
      const proveedor = guardadosRef.current.find((item) => item.id === ruta.editor) ?? null;
      setModeloEditor(proveedor ? { proveedor } : null);
    }
  }

  /** Navega a una página de Ajustes y la refleja en el hash (historial del navegador). */
  function irAAjustes(
    seccion: AjustesSeccion | null = null,
    editor: null | "new" | string = null,
  ) {
    aplicarRutaAjustes({ seccion, editor });
    const hash = hashAjustes(seccion, editor);
    if (window.location.hash === hash) return;
    try {
      window.location.hash = hash;
    } catch {
      // Sin historial disponible: la navegación interna ya se aplicó.
    }
  }

  /** Vuelve de Ajustes a la conversación. */
  function cerrarAjustes() {
    setPantalla(null);
    setAjustesSeccion(null);
    setModeloEditor(null);
    const hash = "#/";
    if (window.location.hash === hash) return;
    try {
      window.history.replaceState(null, "", hash);
    } catch {
      try {
        window.location.hash = hash;
      } catch {
        // Sin historial disponible: el estado ya se aplicó.
      }
    }
  }

  /** Abre el editor de modelo como página de la categoría Modelos. */
  function abrirEditorModelo(proveedor: SavedProviderView | null) {
    irAAjustes("modelos", proveedor?.id ?? "new");
  }

  /** Cierra el editor y vuelve a la página Modelos, que es su punto de entrada. */
  function cerrarEditorModelo() {
    irAAjustes("modelos", null);
  }

  /** El editor ya guardó: recarga la config, avisa y vuelve a Modelos. */
  function modeloGuardado(mensaje: string) {
    setAviso(mensaje);
    irAAjustes("modelos", null);
    void cargarTodo();
  }

  /** Cambia el proveedor global guardado (deliberado: no se aplica solo). */
  async function activarProveedor(id: string) {
    try {
      setConfig(
        await api<ConfigView>("/providers/active", { method: "POST", body: { provider: id } }),
      );
      await cargarTodo();
    } catch (exc) {
      setAviso(error(exc));
    }
  }

  async function reintentar() {
    setCaido(null);
    const sobre = await bridge.restart();
    if (!sobre.ok) {
      setCaido(sobre.error?.message ?? t("app.backend.restartFailed"));
      return;
    }
    try {
      await cargarTodo();
    } catch (exc) {
      setAviso(error(exc));
    }
  }

  const ruta = status?.project_root ?? "";
  const mostrarSplash = !splashFuera && !caido && !status?.runtime_ready;
  const guardados = Array.isArray(config?.providers)
    ? (config?.providers as unknown as SavedProviderView[])
    : [];
  // El router resuelve el id del editor al restaurar una ruta: necesita la lista al día.
  guardadosRef.current = guardados;
  const provActivo = guardados.find((item) => item.id === config?.active_provider) ?? null;
  const contextoAlto = (status?.context_percent ?? 0) >= 85;
  const runtime = caido
    ? { texto: t("chip.runtime.down"), clase: "chip mal" }
    : status?.runtime_ready
      ? { texto: t("chip.runtime.ready"), clase: "chip acento" }
      : status?.runtime_error
        ? { texto: t("chip.runtime.alert"), clase: "chip alerta" }
        : { texto: t("chip.runtime.unavailable"), clase: "chip" };

  return (
    <div className="aplicacion">
      <TitleBar proyecto={corto(ruta)} />
      <div className="layout">
      <Sidebar
        plegado={!izq}
        ruta={ruta}
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
        onFijar={(id, pinned) => void fijarSesion(id, pinned)}
        onArchivar={(id, archived) => void archivarSesion(id, archived)}
        onBorrar={(id) => pedirBorrarSesion(id)}
        onAjustes={() => irAAjustes(null)}
      />

      <div className="centro">
        <header className="cabecera">
          <span className="proyecto" title={ruta}>
            {corto(ruta) || t("sidebar.noProject")}
          </span>
          <span className={runtime.clase} title={status?.runtime_error ?? undefined}>
            {runtime.texto}
          </span>
          {provActivo ? (
            // Píldora del modelo activo: abre la edición rápida de Think/esfuerzo; el
            // proveedor se puede cambiar dentro, como antes. Reutiliza el endpoint de Ajustes.
            <QuickThink
              provider={provActivo}
              providers={guardados}
              onActivar={(id) => void activarProveedor(id)}
              onCambiado={(vista) => {
                setConfig(vista);
                void cargarTodo();
              }}
              onAviso={setAviso}
            />
          ) : (
            <span className="chip" title={t("chip.model.title")}>
              {status?.provider || t("chip.model.none")}/{status?.model || t("chip.model.noneModel")}
            </span>
          )}
          <span className="chip" title={t("chip.sandbox.title")}>
            {t("chip.sandbox", { n: status?.sandbox ?? "N0" })}
          </span>
          {git?.is_repo && (
            <button
              className={git.clean ? "chip boton" : "chip alerta boton"}
              onClick={() => {
                setPestana("git");
                setDer(true);
              }}
              title={
                arboles?.active
                  ? t("app.git.title.worktree", {
                      rama: git.branch ?? "",
                      ruta: arboles.active,
                    })
                  : t("app.git.title.project", { rama: git.branch ?? "" })
              }
              aria-label={t("app.git.aria")}
            >
              {arboles?.active ? "wt · " : ""}
              {git.branch || "git"}
              {git.clean ? "" : ` · ${git.files?.length ?? 0}`}
            </button>
          )}
          <span
            className={contextoAlto ? "chip alerta" : "chip"}
            title={t("chip.context.title", {
              tokens: status?.context_tokens ?? 0,
              window: status?.context_window ?? 0,
              source: status?.context_source ?? "?",
            })}
          >
            {t("chip.context", { porcentaje: status?.context_percent ?? 0 })}
          </span>
          {permisos.length > 0 && (
            <button
              className="chip alerta boton"
              onClick={irAPermiso}
              title={t("chip.permissions.title")}
              aria-label={`${t("chip.permissions", { n: permisos.length })}: ${t("chip.permissions.title")}`}
            >
              {t("chip.permissions", { n: permisos.length })}
            </button>
          )}
          <span className="sep" />
          <button
            className="chip boton"
            onClick={() => abrirPaleta()}
            title={t("chip.commands.title")}
            aria-label={t("chip.commands.aria")}
          >
            {t("chip.commands")}
          </button>
        </header>

        {bloqueado && (
          <div className="linea-aviso mal">
            <span style={{ flex: 1 }}>{t("banner.blocked")}</span>
            <button className="quiet" onClick={() => setPantalla("asistente")}>
              {t("banner.openWizard")}
            </button>
          </div>
        )}
        {caido && (
          <div className="linea-aviso mal">
            <span>{t("banner.backendDown")}</span>
            <span style={{ flex: 1 }}>{caido}</span>
            <button className="quiet" onClick={() => void reintentar()}>
              {t("common.retry")}
            </button>
          </div>
        )}
        {status && !status.runtime_ready && status.runtime_error && (
          <div className="linea-aviso">
            <span style={{ flex: 1 }}>{status.runtime_error}</span>
            <button className="quiet" onClick={() => irAAjustes("modelos")}>
              {t("banner.configureProvider")}
            </button>
          </div>
        )}
        {aviso && (
          <div className="linea-aviso">
            <span style={{ flex: 1 }}>{aviso}</span>
            <button className="quiet" onClick={() => setAviso(null)}>
              {t("common.close")}
            </button>
          </div>
        )}

        {/* Una sola pantalla ocupa el área principal: el editor de modelo, Ajustes, el asistente
            inicial o, por defecto, la conversación. Todas son páginas, ninguna es un modal. */}
        {modeloEditor ? (
          <ModelEditor
            key={modeloEditor.proveedor?.id ?? "nuevo"}
            proveedor={modeloEditor.proveedor}
            esActivo={
              Boolean(modeloEditor.proveedor?.id) &&
              modeloEditor.proveedor?.id === config?.active_provider
            }
            onGuardado={modeloGuardado}
            onCerrar={cerrarEditorModelo}
            onAviso={setAviso}
          />
        ) : pantalla === "ajustes" && config ? (
          <Settings
            config={config}
            safeMode={Boolean(status?.safe_mode)}
            seccion={ajustesSeccion}
            tema={tema}
            onTema={cambiarTema}
            intensidad={intensidad}
            onIntensidad={cambiarIntensidad}
            marca={marca}
            onMarca={cambiarMarca}
            marcaOpacidad={marcaOpacidad}
            onMarcaOpacidad={cambiarMarcaOpacidad}
            idioma={idioma}
            onIdioma={cambiarIdioma}
            onSeccion={(seccion) => irAAjustes(seccion)}
            onCerrar={cerrarAjustes}
            onPolitica={(modo) => void cambiarAjustes({ policy_mode: modo })}
            onModo={(activo) => void cambiarAjustes({ safe_mode: activo })}
            onEditarModelo={abrirEditorModelo}
            onConfigCambiada={() => void cargarTodo()}
            onAviso={setAviso}
            onConfirmar={(titulo, detalleModal, accion) =>
              setConfirmacion({ titulo, detalle: detalleModal, accion })
            }
          />
        ) : pantalla === "asistente" ? (
          <Onboarding
            ruta={ruta}
            projectExplicit={Boolean(status?.project_explicit)}
            providerConfigured={Boolean(status?.provider_configured)}
            runtimeReady={Boolean(status?.runtime_ready)}
            runtimeError={status?.runtime_error ?? ""}
            onAbrirProyecto={abrirProyecto}
            onGuardarProveedor={guardarProveedor}
            onReintentar={cargarTodo}
            onAviso={setAviso}
            onCerrar={() => setPantalla(null)}
          />
        ) : (
          <Chat
            turnos={turnos}
            permisos={permisos}
            eventos={eventos}
            desde={desde}
            vivo={vivo?.text ?? null}
            ocupado={ocupado || turnoRemoto}
            cargando={cargando}
            bloqueado={bloqueado}
            ruta={ruta}
            runtimeListo={Boolean(status?.runtime_ready)}
            marca={marca}
            policyMode={config?.policy_mode ?? "ask"}
            onPolicyMode={(modo) => void cambiarAjustes({ policy_mode: modo })}
            onEnviar={(texto, planMode) => void enviar(texto, planMode)}
            onPermiso={(id, projectRoot, decision, feedback, reason) =>
              void decidirPermiso(id, projectRoot, decision, feedback, reason)
            }
            onResponder={(texto) => void enviar(texto, false)}
            onComando={(spec) => lanzarComando(spec)}
            onCancelar={() => void cancelarTurno()}
            onBloqueado={() => setPantalla("asistente")}
            cola={cola}
            onEncolar={(texto, planMode) => void encolar(texto, planMode)}
            onEditarCola={(id, texto) => void editarCola(id, texto)}
            onQuitarCola={(id) => void quitarCola(id)}
            onVaciarCola={() => void vaciarCola()}
            onEjecutarCola={() => void drenarCola()}
          />
        )}
      </div>

      {/* En ventanas estrechas el inspector va encima; este fondo cierra el cajón. */}
      {der && <div className="drawer-fondo" onClick={() => setDer(false)} />}

      <RightPanel
        key={ruta}
        plegado={!der}
        pestana={pestana}
        // Elegir pestaña es también abrir el inspector: así el botón del panel plegado
        // (que sólo pide una pestaña) vuelve a sacarlo, sin depender sólo de Ctrl+J.
        onPestana={(nueva) => {
          setPestana(nueva);
          setDer(true);
          if (nueva === "cambios") setAtencionCambios(false);
        }}
        eventos={eventos}
        tareas={tareas}
        trabajos={trabajos}
        cambios={cambios}
        salida={salida}
        atencion={atencionCambios}
        git={git}
        arboles={arboles}
        sesion={sesionActiva}
        onGit={() => void cargarGit()}
        onStage={(paths) => marcarGit(paths, true)}
        onUnstage={(paths) => marcarGit(paths, false)}
        onCommit={(mensaje) => void commitGit(mensaje)}
        onCrearWorktree={(nombre) => void crearWorktree(nombre)}
        onActivarWorktree={(path) => void activarWorktree(path)}
        onQuitarWorktree={(path, force) => pedirQuitarWorktree(path, force)}
        onVerCambios={(id) => void cargarCambios(id)}
        onRestaurar={pedirRestauracion}
        onVerSalida={(id) => void verSalida(id)}
        onParar={(id) => void pararTrabajo(id)}
        onTrabajos={() => void verificar("/jobs", "trabajos")}
        onPaleta={() => abrirPaleta()}
        onCerrar={() => setDer(false)}
      />

      {paleta && (
        <CommandPalette
          inicial={paletaInicial}
          // Estos comandos abren una pantalla de configuración: no se les piden campos aquí.
          paginas={Object.keys(COMANDOS_DE_PAGINA)}
          onEjecutar={(clave, args) => void ejecutarComando(clave, args)}
          onCerrar={() => {
            setPaleta(false);
            setPaletaInicial(null);
          }}
          runtimeListo={Boolean(status?.runtime_ready)}
        />
      )}

      {detalle && (
        <div
          className="ajustes"
          onClick={() => setDetalle(null)}
          role="dialog"
          aria-modal="true"
          aria-label={detalle.titulo}
        >
          <div className="hoja" onClick={(evento) => evento.stopPropagation()}>
            <div className="cabecera-hoja">
              <h2>{detalle.titulo}</h2>
              <button className="quiet" onClick={() => setDetalle(null)} title={t("common.escClose")}>
                {t("common.close")}
              </button>
            </div>
            <pre className="salida" style={{ borderTop: 0, maxHeight: "62vh" }}>
              {detalle.texto}
            </pre>
          </div>
        </div>
      )}

      {confirmacion && (
        <div
          className="ajustes"
          onClick={() => setConfirmacion(null)}
          role="dialog"
          aria-modal="true"
          aria-label={confirmacion.titulo}
        >
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
                  {t("common.yes")}
                </button>
                <button className="quiet" onClick={() => setConfirmacion(null)}>
                  {t("common.cancel")}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
      </div>
      {mostrarSplash && <Splash />}
    </div>
  );
}
