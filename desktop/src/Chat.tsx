/**
 * Chat: historial, actividad del turno en curso, permisos y la caja de texto.
 * El primer plano es la conversación; el detalle técnico va plegado.
 */

import { useEffect, useMemo, useRef, useState } from "react";

import {
  Approval,
  CommandSpecView,
  ETIQUETA_EVENTO,
  MentionSuggestion,
  PlanOptionsView,
  QueueItem,
  StreamItem,
  Turn,
  api,
  mensajeDe,
} from "./api";
import { comandoTraducido } from "./commandsI18n";
import { useT, type Clave } from "./i18n";
import { Logo } from "./Logo";

interface Props {
  turnos: Turn[];
  permisos: Approval[];
  eventos: StreamItem[];
  ocupado: boolean;
  cargando: boolean;
  bloqueado: boolean;
  /** Ruta del proyecto abierto: sólo se usa el nombre en la bienvenida. */
  ruta: string;
  /** Si el runtime está listo: los comandos que lo necesitan se atenúan si no lo está. */
  runtimeListo: boolean;
  /** Marca de agua del logo en el área de mensajes (visible/oculta desde Apariencia). */
  marca: boolean;
  plan: PlanOptionsView | null;
  onEnviar: (texto: string, plan: boolean) => void;
  onPermiso: (proposalId: string, decision: string, feedback?: string) => void;
  onElegirPlan: (optionId: string) => void;
  /** Comando elegido con «/» inline; la app lo lanza con el mismo despachador de la paleta. */
  onComando: (spec: CommandSpecView) => void;
  onCancelar: () => void;
  /** Estado bloqueado (sin proyecto): abre el asistente, que es donde se resuelve. */
  onBloqueado: () => void;
  /** Instrucciones encoladas: se ejecutan una tras otra al terminar el turno en curso. */
  cola: QueueItem[];
  onEncolar: (texto: string, plan: boolean) => void;
  onEditarCola: (id: string, texto: string) => void;
  onQuitarCola: (id: string) => void;
  onVaciarCola: () => void;
  /** Ejecuta la cola ahora (cuando la app está inactiva). */
  onEjecutarCola: () => void;
}

/** Sugerencias de la bienvenida: sólo rellenan el compositor, nunca envían solas. */
const SUGERENCIAS: Clave[] = [
  "chat.suggestion.project",
  "chat.suggestion.start",
  "chat.suggestion.changes",
  "chat.suggestion.tests",
];

function corto(ruta: string): string {
  return ruta.split(/[\\/]/).filter(Boolean).at(-1) ?? ruta;
}

const OPCIONES: Record<string, Clave> = {
  once: "chat.permission.once",
  session: "chat.permission.session",
  reject: "chat.permission.reject",
  approve: "chat.permission.approve",
  needs_changes: "chat.permission.needsChanges",
};

/** Ancho de la columna de conversación (el mismo que usan .turno, .progreso y .permiso). */
const ANCHO_COLUMNA = 900;

function hora(iso?: string): string {
  if (!iso) return "";
  const fecha = new Date(iso);
  return Number.isNaN(fecha.getTime()) ? "" : fecha.toLocaleTimeString().slice(0, 8);
}

/** Los pasos del turno en curso: sólo lo posterior al último «tarea iniciada». */
function pasosDelTurno(eventos: StreamItem[]): StreamItem[] {
  for (let indice = eventos.length - 1; indice >= 0; indice -= 1) {
    if (eventos[indice].event.event_type === "task_started") return eventos.slice(indice);
  }
  return [];
}

function detalleDe(evento: StreamItem["event"]): string {
  const sujeto = evento.tool_name ?? evento.payload?.spec ?? evento.payload?.agent_id ?? "";
  return typeof sujeto === "string" ? sujeto : String(sujeto);
}

function enCurso(ultimo: StreamItem | null, t: ReturnType<typeof useT>): string {
  if (!ultimo) return t("chat.status.planning");
  const { event } = ultimo;
  // La etiqueta del evento sale del diccionario i18n; si el backend trae un tipo desconocido,
  // se usa la etiqueta genérica en vez de enseñar el identificador técnico.
  const etiqueta = t(ETIQUETA_EVENTO[event.event_type] ?? "event.unknown");
  const sujeto = detalleDe(event);
  return sujeto ? `${etiqueta} · ${sujeto}` : etiqueta;
}

/** El token que se está escribiendo en el caret: «@contexto» o «/comando». */
interface Token {
  inicio: number;
  fin: number;
  texto: string;
  tipo: "mencion" | "slash";
}

/** Lo que se está escribiendo en el caret, si es un token de contexto o de comando.
 *
 * Las menciones pueden llevar «/» dentro (rutas tipo ``@src/api.py``), así que se miran
 * primero; el «/» sólo abre comandos si NO estamos dentro de una mención y empieza palabra
 * (inicio o tras un espacio). Lo que se inserte con la mención es texto; quien lo convierte
 * en contexto es el runtime, al enviar.
 */
function tokenEnCaret(texto: string, caret: number): Token | null {
  const antes = texto.slice(0, caret);

  const arroba = antes.lastIndexOf("@");
  if (arroba >= 0) {
    const previo = arroba > 0 ? antes[arroba - 1] : "";
    const token = antes.slice(arroba + 1);
    if ((!previo || /\s/.test(previo)) && !/[\s@,;]/.test(token)) {
      const resto = texto.slice(caret).match(/^[^\s@,;]*/);
      return {
        inicio: arroba,
        fin: caret + (resto ? resto[0].length : 0),
        texto: token,
        tipo: "mencion",
      };
    }
  }

  const barra = antes.lastIndexOf("/");
  if (barra >= 0) {
    const previo = barra > 0 ? antes[barra - 1] : "";
    const token = antes.slice(barra + 1);
    if ((!previo || /\s/.test(previo)) && !/[\s@,;/]/.test(token)) {
      const resto = texto.slice(caret).match(/^[^\s@,;/]*/);
      return {
        inicio: barra,
        fin: caret + (resto ? resto[0].length : 0),
        texto: token,
        tipo: "slash",
      };
    }
  }

  return null;
}

export function Chat(props: Props) {
  const t = useT();
  const [texto, setTexto] = useState("");
  const [plan, setPlan] = useState(false);
  const fondo = useRef<HTMLDivElement | null>(null);
  const area = useRef<HTMLTextAreaElement | null>(null);
  const [sugerencias, setSugerencias] = useState<MentionSuggestion[] | null>(null);
  const [errorSug, setErrorSug] = useState("");
  const [selSug, setSelSug] = useState(0);
  const [token, setToken] = useState<Token | null>(null);
  // Catálogo compartido de comandos (el mismo `GET /commands` que usa la paleta) y selección.
  const [comandos, setComandos] = useState<CommandSpecView[] | null>(null);
  const [selCmd, setSelCmd] = useState(0);

  const pasos = pasosDelTurno(props.eventos);
  const ultimo = props.eventos.at(-1) ?? null;

  useEffect(() => {
    fondo.current?.scrollTo({ top: fondo.current.scrollHeight });
  }, [props.turnos.length, props.permisos.length, pasos.length]);

  // El catálogo es el compartido por las tres interfaces: no hay una segunda lista. Se filtra
  // a la superficie de escritorio, igual que la paleta.
  useEffect(() => {
    void (async () => {
      try {
        const datos = await api<{ commands: CommandSpecView[] }>("/commands");
        const todos = Array.isArray(datos?.commands) ? datos.commands : [];
        setComandos(todos.filter((comando) => comando.surfaces.includes("desktop")));
      } catch {
        setComandos([]);
      }
    })();
  }, []);

  // Las sugerencias de @contexto se piden al backend (el mismo listado con el que el runtime
  // resuelve las referencias) y sólo al dejar de teclear, para no castigarlo en cada letra.
  const textoMencion = token?.tipo === "mencion" ? token.texto : null;
  useEffect(() => {
    if (textoMencion === null) {
      setSugerencias(null);
      setErrorSug("");
      return;
    }
    const timer = setTimeout(() => {
      void (async () => {
        try {
          const datos = await api<{ suggestions: MentionSuggestion[] }>(
            `/context/mentions?q=${encodeURIComponent(textoMencion)}`,
          );
          setSugerencias(Array.isArray(datos.suggestions) ? datos.suggestions : []);
          setSelSug(0);
          setErrorSug("");
        } catch (exc) {
          setSugerencias([]);
          setErrorSug(mensajeDe(exc));
        }
      })();
    }, 150);
    return () => clearTimeout(timer);
  }, [textoMencion]);

  // Lista de comandos del «/…»: se filtra por id, resumen, categoría y etiquetas de campos,
  // igual que la paleta. No hay una segunda definición: es el catálogo compartido.
  const textoComando = token?.tipo === "slash" ? token.texto : null;
  // El catálogo llega del backend en español: se traduce antes de filtrar y pintar, para que la
  // búsqueda, la descripción y las etiquetas del autocompletado usen el idioma actual. El id y el
  // `usage` no se traducen.
  const comandosTraducidos = useMemo(
    () => (comandos ?? []).map((comando) => comandoTraducido(comando, t)),
    [comandos, t],
  );
  const comandosVisibles = useMemo(() => {
    const lista = comandosTraducidos;
    const filtro = (textoComando ?? "").trim().toLowerCase();
    if (!filtro) return lista;
    return lista.filter(
      (comando) =>
        comando.id.toLowerCase().includes(filtro) ||
        comando.summary.toLowerCase().includes(filtro) ||
        comando.category.toLowerCase().includes(filtro) ||
        comando.args.some(
          (campo) =>
            campo.label.toLowerCase().includes(filtro) || campo.key.toLowerCase().includes(filtro),
        ),
    );
  }, [comandosTraducidos, textoComando]);

  useEffect(() => {
    setSelCmd(0);
  }, [textoComando]);

  /** Recalcula el token del caret sin recrear el estado si sigue siendo el mismo. */
  function refrescarToken(nodo: HTMLTextAreaElement) {
    const nueva = tokenEnCaret(nodo.value, nodo.selectionStart ?? 0);
    setToken((actual) => {
      if (actual === null && nueva === null) return actual;
      if (
        actual &&
        nueva &&
        actual.inicio === nueva.inicio &&
        actual.fin === nueva.fin &&
        actual.texto === nueva.texto &&
        actual.tipo === nueva.tipo
      ) {
        return actual;
      }
      return nueva;
    });
  }

  /** Inserta el «@valor » donde estaba la mención y deja el caret listo para seguir. */
  function insertarMencion(sugerencia: MentionSuggestion) {
    if (!token) return;
    const nuevo = `${texto.slice(0, token.inicio)}@${sugerencia.value} ${texto.slice(token.fin)}`;
    const pos = token.inicio + sugerencia.value.length + 2;
    setTexto(nuevo);
    setSugerencias(null);
    setToken(null);
    setSelSug(0);
    requestAnimationFrame(() => {
      const nodo = area.current;
      if (!nodo) return;
      nodo.focus();
      nodo.setSelectionRange(pos, pos);
    });
  }

  /** Consume el «/comando» del compositor y lanza su acción (el mismo despachador de la paleta). */
  function ejecutarComandoInline(spec: CommandSpecView) {
    if (!token) return;
    const nuevo = `${texto.slice(0, token.inicio)}${texto.slice(token.fin)}`
      .replace(/[ \t]{2,}/g, " ")
      .replace(/^\s+/, "");
    setTexto(nuevo);
    setToken(null);
    setSelCmd(0);
    props.onComando(spec);
  }

  function enviar() {
    const limpio = texto.trim();
    if (!limpio || props.ocupado || props.bloqueado) return;
    setTexto("");
    setSugerencias(null);
    setToken(null);
    setSelSug(0);
    props.onEnviar(limpio, plan);
    setPlan(false);
  }

  /** Encola el prompt actual para que corra al terminar el turno (no interrumpe el que corre). */
  function encolarAhora() {
    const limpio = texto.trim();
    if (!limpio || props.bloqueado) return;
    setTexto("");
    setSugerencias(null);
    setToken(null);
    setSelSug(0);
    props.onEncolar(limpio, plan);
    setPlan(false);
  }

  /** Añade un fragmento al compositor y deja el caret al final. Nunca envía. */
  function insertarEnCompositor(fragmento: string) {
    setTexto((previo) => (previo ? `${previo.trimEnd()} ${fragmento}` : fragmento));
    requestAnimationFrame(() => {
      const nodo = area.current;
      if (!nodo) return;
      nodo.focus();
      const fin = nodo.value.length;
      nodo.setSelectionRange(fin, fin);
    });
  }

  const nombreProyecto = corto(props.ruta);
  const sinEmpezar =
    !props.cargando &&
    !props.ocupado &&
    props.turnos.length === 0 &&
    pasos.length === 0 &&
    !props.plan;

  return (
    <>
      <div className="chat" ref={fondo}>
        {/* Marca de agua: detrás del contenido, sin capturar clics ni afectar al scroll.
            Su opacidad sale de `--chat-marca-opacidad` (mismo valor en ambos temas). */}
        {props.marca && (
          <div className="chat-marca" aria-hidden="true">
            <Logo className="chat-marca-logo" title="" />
          </div>
        )}
        {props.cargando && <div className="vacio">{t("chat.loadingProject")}</div>}

        {sinEmpezar && (
          <div className="bienvenida">
            <h2>
              {nombreProyecto
                ? t("chat.welcome.project", { proyecto: nombreProyecto })
                : t("chat.welcome")}
            </h2>
            {props.bloqueado ? (
              <>
                <p className="nota">{t("chat.blocked.note")}</p>
                <div className="acciones" style={{ marginTop: 0 }}>
                  <button className="primary" onClick={props.onBloqueado}>
                    {t("chat.chooseProject")}
                  </button>
                </div>
              </>
            ) : (
              <>
                <p className="nota">{t("chat.welcome.note")}</p>
                <div className="sugerencias">
                  {SUGERENCIAS.map((clave) => (
                    <button
                      key={clave}
                      type="button"
                      className="chip boton sugerencia"
                      onClick={() => insertarEnCompositor(t(clave))}
                    >
                      {t(clave)}
                    </button>
                  ))}
                </div>
                <div className="acceso-compositor">
                  <button
                    type="button"
                    className="pista"
                    onClick={() => insertarEnCompositor("@")}
                  >
                    <code>@</code> {t("chat.hint.context")}
                  </button>
                  <button
                    type="button"
                    className="pista"
                    onClick={() => insertarEnCompositor("/")}
                  >
                    <code>/</code> {t("chat.hint.commands")}
                  </button>
                  <button
                    type="button"
                    className="pista"
                    onClick={() => {
                      setPlan(true);
                      area.current?.focus();
                    }}
                  >
                    <code>{t("chat.plan.toggle")}</code> {t("chat.hint.plan")}
                  </button>
                </div>
              </>
            )}
          </div>
        )}

        {props.turnos.map((turno, indice) => (
          <div
            key={`${indice}-${turno.task_id}`}
            className={turno.role === "user" ? "turno usuario" : "turno agente"}
          >
            {turno.content}
          </div>
        ))}

        {props.plan && (
          <div className="plan" role="region" aria-label={t("chat.plan.aria")}>
            <div className="plan-cabecera">{t("chat.plan.header")}</div>
            {props.plan.question && <p className="nota">{props.plan.question}</p>}
            {props.plan.options.map((opcion) => {
              const recomendada = opcion.id === props.plan?.recommendation;
              return (
                <div key={opcion.id} className="opcion-plan">
                  <div className="opcion-plan-cabecera">
                    <h3>{opcion.title}</h3>
                    {recomendada && <span className="chip acento">{t("chat.plan.recommended")}</span>}
                  </div>
                  <p className="resumen">{opcion.summary}</p>
                  {opcion.approach.length > 0 && (
                    <ul className="pasos-plan">
                      {opcion.approach.map((paso, indice) => (
                        <li key={indice}>{paso}</li>
                      ))}
                    </ul>
                  )}
                  {(opcion.effort || opcion.risk || opcion.tradeoffs) && (
                    <div className="meta-plan">
                      {opcion.effort && (
                        <span>{t("chat.plan.effort", { valor: opcion.effort })}</span>
                      )}
                      {opcion.risk && <span>{t("chat.plan.risk", { valor: opcion.risk })}</span>}
                      {opcion.tradeoffs && <span>{opcion.tradeoffs}</span>}
                    </div>
                  )}
                  <div className="acciones">
                    <button
                      className="primary"
                      disabled={props.ocupado || props.bloqueado}
                      onClick={() => props.onElegirPlan(opcion.id)}
                    >
                      {t("chat.plan.choose")}
                    </button>
                  </div>
                </div>
              );
            })}
            <p className="nota">{t("chat.plan.note")}</p>
          </div>
        )}

        {pasos.length > 0 && (
          <details className="progreso" open={props.ocupado}>
            <summary>{t("chat.activity.summary", { n: pasos.length })}</summary>
            {pasos.map((item) => {
              const { event } = item;
              const detalle = detalleDe(event);
              return (
                <div key={item.index} className="paso">
                  <span className="hora">{hora(event.timestamp)}</span>
                  <span className="tipo">
                    {t(ETIQUETA_EVENTO[event.event_type] ?? "event.unknown")}
                  </span>
                  {event.event_type === "tool_called" ? (
                    // El payload de una herramienta puede ser largo: se pliega y se parte.
                    <details className="detalle" style={{ flex: 1, minWidth: 0 }}>
                      <summary>{detalle || t("chat.activity.tool")}</summary>
                      <pre
                        style={{
                          margin: "2px 0 4px",
                          whiteSpace: "pre-wrap",
                          overflowWrap: "anywhere",
                          maxHeight: 260,
                          overflow: "auto",
                        }}
                      >
                        {JSON.stringify(event.payload ?? {}, null, 2)}
                      </pre>
                    </details>
                  ) : (
                    <span className="detalle">{detalle}</span>
                  )}
                </div>
              );
            })}
          </details>
        )}
      </div>

      {/* Progreso compacto: último paso y el cancelar del turno en curso, pegados al compositor. */}
      {props.ocupado && (
        <div className="progreso-compacto" role="status" aria-live="polite">
          <span className="pulso" aria-hidden="true" />
          <span className="ultimo-paso" title={enCurso(ultimo, t)}>
            {enCurso(ultimo, t)}…
          </span>
          <button className="quiet" onClick={props.onCancelar} title={t("chat.cancel.title")}>
            {t("common.cancel")}
          </button>
        </div>
      )}

      {/* Los permisos van pegados a la caja de texto: es ahí donde se responde y donde el
          runtime está esperando. */}
      {props.permisos.map((permiso, indice) => (
        <div
          key={permiso.proposal_id}
          className="permiso"
          id={indice === 0 ? "permiso-pendiente" : undefined}
        >
          <h3>
            {permiso.kind === "command"
              ? t("chat.permission.command")
              : permiso.kind === "path"
                ? t("chat.permission.path")
                : permiso.kind === "context_path"
                  ? t("chat.permission.contextPath")
                  : t("chat.permission.other", { tipo: permiso.kind })}
          </h3>
          <div className="porque">{permiso.summary}</div>
          {permiso.reason && (
            <div className="porque">{t("chat.permission.reason", { motivo: permiso.reason })}</div>
          )}
          {typeof permiso.details?.command === "string" && (
            <div className="detalle">{permiso.details.command}</div>
          )}
          {typeof permiso.details?.path === "string" && (
            <div className="detalle">{permiso.details.path}</div>
          )}
          <div className="acciones">
            {permiso.options.map((opcion) => (
              <button
                key={opcion}
                className={opcion === "reject" ? "quiet" : "primary"}
                onClick={() =>
                  props.onPermiso(
                    permiso.proposal_id,
                    opcion === "once" || opcion === "session" ? "approve" : opcion,
                    opcion === "once" || opcion === "session" ? opcion : "",
                  )
                }
              >
                {opcion in OPCIONES ? t(OPCIONES[opcion]) : opcion}
              </button>
            ))}
          </div>
        </div>
      ))}

      <div className="caja-texto">
        {/* El vocabulario de clases está congelado y no trae fila para el compositor: la
            alineación con la columna del chat (900px, como .turno y .permiso) va aquí. */}
        <div style={{ maxWidth: ANCHO_COLUMNA, margin: "0 auto" }}>
          {props.cola.length > 0 && (
            <div className="cola" aria-label={t("chat.queue.aria")}>
              <div className="cola-cabecera">
                <span className="etiqueta" style={{ margin: 0 }}>
                  {t("chat.queue.header", { n: props.cola.length })}
                </span>
                <button
                  className="quiet"
                  onClick={props.onVaciarCola}
                  title={t("chat.queue.clear.title")}
                >
                  {t("chat.queue.clear")}
                </button>
                <button
                  className="quiet"
                  onClick={props.onEjecutarCola}
                  disabled={props.ocupado || props.bloqueado}
                  title={t("chat.queue.run.title")}
                >
                  {t("chat.queue.run")}
                </button>
              </div>
              {props.cola.map((item, indice) => (
                <div key={item.id} className="cola-item">
                  <span className="cola-num" aria-hidden="true">
                    {indice + 1}
                  </span>
                  <input
                    defaultValue={item.text}
                    aria-label={t("chat.queue.edit.aria", { n: indice + 1 })}
                    title={t("chat.queue.edit.title")}
                    onKeyDown={(evento) => {
                      if (evento.key === "Enter") {
                        evento.preventDefault();
                        evento.currentTarget.blur();
                      }
                    }}
                    onBlur={(evento) => {
                      const valor = evento.target.value.trim();
                      if (valor && valor !== item.text) props.onEditarCola(item.id, valor);
                    }}
                  />
                  <button
                    className="quiet"
                    onClick={() => props.onQuitarCola(item.id)}
                    aria-label={t("chat.queue.remove.aria", { texto: item.text })}
                    title={t("chat.queue.remove.title")}
                  >
                    ✕
                  </button>
                </div>
              ))}
            </div>
          )}

          {sugerencias && token?.tipo === "mencion" && (
            <div className="menciones" aria-label={t("chat.mentions.aria")}>
              {errorSug && (
                <div className="vacio">
                  {t("chat.mentions.error")} {errorSug}
                </div>
              )}
              {!errorSug && sugerencias.length === 0 && (
                <div className="vacio">{t("chat.mentions.empty", { texto: token.texto })}</div>
              )}
              {sugerencias.map((sugerencia, indice) => (
                <button
                  key={`${sugerencia.kind}:${sugerencia.value}`}
                  type="button"
                  className={indice === selSug ? "mencion elegida" : "mencion"}
                  onMouseEnter={() => setSelSug(indice)}
                  // onMouseDown (y no onClick): así el caret no se pierde antes de insertar.
                  onMouseDown={(evento) => {
                    evento.preventDefault();
                    insertarMencion(sugerencia);
                  }}
                >
                  <span className="valor">@{sugerencia.value}</span>
                  <span className="meta">
                    {sugerencia.kind === "dir" ? t("chat.mentions.folder") : t("chat.mentions.file")}
                  </span>
                </button>
              ))}
            </div>
          )}

          {token?.tipo === "slash" && (
            <div className="menciones" aria-label={t("chat.commands.aria")}>
              {comandos === null && <div className="vacio">{t("palette.loading")}</div>}
              {comandos !== null && comandosVisibles.length === 0 && (
                <div className="vacio">{t("chat.commands.empty", { texto: token.texto })}</div>
              )}
              {comandosVisibles.map((comando, indice) => (
                <button
                  key={comando.id}
                  type="button"
                  className={
                    (indice === selCmd ? "mencion elegida" : "mencion") +
                    (comando.needs_runtime && !props.runtimeListo ? " no-disponible" : "")
                  }
                  onMouseEnter={() => setSelCmd(indice)}
                  // onMouseDown (y no onClick): el clic no debe perder el caret antes de actuar.
                  onMouseDown={(evento) => {
                    evento.preventDefault();
                    ejecutarComandoInline(comando);
                  }}
                >
                  <span className="clave">/{comando.id}</span>
                  <span className="desc">{comando.summary}</span>
                  {comando.needs_runtime && !props.runtimeListo && (
                    <span className="meta">{t("palette.needsRuntime")}</span>
                  )}
                  {comando.terminal_only && (
                    <span className="meta">{t("palette.terminalOnly")}</span>
                  )}
                </button>
              ))}
            </div>
          )}

          <textarea
            ref={area}
            value={texto}
            placeholder={t("chat.composer.placeholder")}
            aria-label={t("chat.composer.aria")}
            onChange={(evento) => {
              setTexto(evento.target.value);
              refrescarToken(evento.target);
            }}
            onKeyUp={(evento) => {
              // Esc cierra la lista; recalcular aquí la volvería a abrir.
              if (evento.key === "Escape") return;
              refrescarToken(evento.currentTarget);
            }}
            onClick={(evento) => refrescarToken(evento.currentTarget)}
            onKeyDown={(evento) => {
              // Con una lista abierta, las flechas y Enter/Tab eligen; si no, Enter envía.
              // Shift+Enter nunca se intercepta: sigue saltando de línea.
              if (token?.tipo === "slash") {
                if (comandosVisibles.length > 0) {
                  if (evento.key === "ArrowDown") {
                    evento.preventDefault();
                    setSelCmd((valor) => Math.min(valor + 1, comandosVisibles.length - 1));
                    return;
                  }
                  if (evento.key === "ArrowUp") {
                    evento.preventDefault();
                    setSelCmd((valor) => Math.max(valor - 1, 0));
                    return;
                  }
                  if ((evento.key === "Enter" || evento.key === "Tab") && !evento.shiftKey) {
                    evento.preventDefault();
                    ejecutarComandoInline(
                      comandosVisibles[Math.min(selCmd, comandosVisibles.length - 1)],
                    );
                    return;
                  }
                }
                if (evento.key === "Escape") {
                  evento.preventDefault();
                  setToken(null);
                  return;
                }
              }
              if (sugerencias && token?.tipo === "mencion" && sugerencias.length > 0) {
                if (evento.key === "ArrowDown") {
                  evento.preventDefault();
                  setSelSug((valor) => Math.min(valor + 1, sugerencias.length - 1));
                  return;
                }
                if (evento.key === "ArrowUp") {
                  evento.preventDefault();
                  setSelSug((valor) => Math.max(valor - 1, 0));
                  return;
                }
                if ((evento.key === "Enter" || evento.key === "Tab") && !evento.shiftKey) {
                  evento.preventDefault();
                  insertarMencion(sugerencias[Math.min(selSug, sugerencias.length - 1)]);
                  return;
                }
                if (evento.key === "Escape") {
                  evento.preventDefault();
                  setSugerencias(null);
                  setToken(null);
                  return;
                }
              }
              if (evento.key === "Escape") setTexto("");
              if (evento.key === "Enter" && !evento.shiftKey) {
                evento.preventDefault();
                // Con un turno en curso, Enter encola lo siguiente en vez de descartarlo.
                if (props.ocupado) encolarAhora();
                else enviar();
              }
            }}
          />
          <div className="controles">
            <button
              className="primary"
              disabled={props.bloqueado || !texto.trim()}
              onClick={props.ocupado ? encolarAhora : enviar}
              title={props.ocupado ? t("chat.send.queued.title") : t("chat.send.title")}
            >
              {props.ocupado ? t("chat.enqueue") : t("chat.send")}
            </button>
            <label title={t("chat.plan.toggle.title")}>
              <input
                type="checkbox"
                checked={plan}
                onChange={(evento) => setPlan(evento.target.checked)}
              />
              {t("chat.plan.toggle")}
            </label>
            <span style={{ marginLeft: "auto" }}>
              {props.bloqueado
                ? t("chat.status.blocked")
                : props.ocupado
                  ? t("chat.status.busy")
                  : t("chat.status.hint")}
            </span>
          </div>
        </div>
      </div>
    </>
  );
}
