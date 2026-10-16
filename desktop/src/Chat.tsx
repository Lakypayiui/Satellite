/** Chat: historial, qué hace el agente ahora, permisos y la caja de texto. */

import { useEffect, useRef, useState } from "react";

import { Approval, StreamItem, Turn, ETIQUETA_EVENTO } from "./api";

interface Props {
  turnos: Turn[];
  permisos: Approval[];
  ocupado: boolean;
  cargando: boolean;
  ultimo: StreamItem | null;
  onEnviar: (texto: string, plan: boolean) => void;
  onPermiso: (proposalId: string, decision: string, feedback?: string) => void;
  onCancelar: () => void;
  onPaleta: () => void;
}

const OPCIONES: Record<string, string> = {
  once: "permitir una vez",
  session: "permitir esta sesión",
  reject: "rechazar",
  approve: "aprobar",
  needs_changes: "pedir cambios",
};

function enCurso(ultimo: StreamItem | null): string {
  if (!ultimo) return "planificando";
  const { event } = ultimo;
  const etiqueta = ETIQUETA_EVENTO[event.event_type] ?? event.event_type;
  const sujeto = event.tool_name ?? event.payload?.spec ?? event.payload?.agent_id;
  return sujeto ? `${etiqueta} · ${String(sujeto)}` : etiqueta;
}

export function Chat(props: Props) {
  const [texto, setTexto] = useState("");
  const [plan, setPlan] = useState(false);
  const fondo = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    fondo.current?.scrollTo({ top: fondo.current.scrollHeight });
  }, [props.turnos.length, props.permisos.length]);

  function enviar() {
    const limpio = texto.trim();
    if (!limpio || props.ocupado) return;
    setTexto("");
    props.onEnviar(limpio, plan);
    setPlan(false);
  }

  return (
    <>
      <div className="chat" ref={fondo}>
        {props.cargando && <div className="vacio">cargando proyecto…</div>}

        {!props.cargando && props.turnos.length === 0 && (
          <div className="vacio">
            Escribe un objetivo y el agente planifica y lo ejecuta con las herramientas del
            proyecto. Los permisos que necesite te los pedirá aquí.
            <br />
            <br />
            <span style={{ color: "var(--muted)" }}>Ctrl+K</span> abre todos los comandos (proveedor,
            roles, diagnóstico, skills, snapshots…).
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

        {props.permisos.map((permiso) => (
          <div key={permiso.proposal_id} className="permiso">
            <h3>
              {permiso.kind === "command"
                ? "Permiso para ejecutar un comando"
                : permiso.kind === "path"
                  ? "Permiso para acceder a una ruta"
                  : `Permiso: ${permiso.kind}`}
            </h3>
            <div className="porque">{permiso.summary}</div>
            {permiso.reason && <div className="porque">motivo: {permiso.reason}</div>}
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
                  {OPCIONES[opcion] ?? opcion}
                </button>
              ))}
            </div>
          </div>
        ))}

        {props.ocupado && <div className="latido">{enCurso(props.ultimo)}…</div>}
      </div>

      <div className="caja-texto">
        <div className="fila">
          <textarea
            value={texto}
            placeholder="escribe el objetivo…"
            onChange={(evento) => setTexto(evento.target.value)}
            onKeyDown={(evento) => {
              if (evento.key === "Escape") setTexto("");
              if (evento.key === "Enter" && !evento.shiftKey && !evento.ctrlKey) {
                evento.preventDefault();
                enviar();
              }
              if (evento.key === "Enter" && evento.ctrlKey) {
                evento.preventDefault();
                enviar();
              }
            }}
          />
          <div className="controles">
            <button className="primary" disabled={props.ocupado || !texto.trim()} onClick={enviar}>
              enviar
            </button>
            <label>
              <input
                type="checkbox"
                checked={plan}
                onChange={(evento) => setPlan(evento.target.checked)}
              />
              modo plan
            </label>
            <button className="quiet" onClick={props.onPaleta}>
              ctrl+k comandos
            </button>
            {props.ocupado && (
              <button className="quiet" onClick={props.onCancelar} title="Corta el turno en curso">
                cancelar
              </button>
            )}
            <span style={{ marginLeft: "auto" }}>
              {props.ocupado ? "turno en curso" : "enter envía · shift+enter salta línea"}
            </span>
          </div>
        </div>
      </div>
    </>
  );
}
