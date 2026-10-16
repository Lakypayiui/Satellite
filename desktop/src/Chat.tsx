/** Chat central: historial, estado de lo que hace el agente, permisos y caja de texto. */

import { useEffect, useRef, useState } from "react";

import { Approval, ETIQUETA_EVENTO, StreamItem, Turn } from "./api";

interface Props {
  turnos: Turn[];
  permisos: Approval[];
  ocupado: boolean;
  cargando: boolean;
  ultimo: StreamItem | null;
  onEnviar: (texto: string, plan: boolean) => void;
  onPermiso: (proposalId: string, decision: string, feedback?: string) => void;
}

const OPCIONES: Record<string, string> = {
  once: "Permitir una vez",
  session: "Permitir esta sesión",
  reject: "Rechazar",
  approve: "Aprobar",
  needs_changes: "Pedir cambios",
};

function queHace(ultimo: StreamItem | null): string {
  if (!ultimo) return "";
  const { event } = ultimo;
  const etiqueta = ETIQUETA_EVENTO[event.event_type] ?? event.event_type;
  if (event.tool_name) return `${etiqueta}: ${event.tool_name}`;
  const spec = event.payload?.spec ?? event.payload?.agent_id;
  return spec ? `${etiqueta}: ${String(spec)}` : etiqueta;
}

export function Chat(props: Props) {
  const [texto, setTexto] = useState("");
  const [plan, setPlan] = useState(false);
  const fondo = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    fondo.current?.scrollTo({ top: fondo.current.scrollHeight, behavior: "smooth" });
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
        {props.cargando && <div className="vacio">Cargando el proyecto…</div>}

        {!props.cargando && props.turnos.length === 0 && (
          <div className="vacio">
            <p>
              <strong>¿Qué quieres que haga?</strong>
            </p>
            <p>
              Escribe un objetivo y el agente planificará y lo ejecutará con las herramientas
              disponibles. Los permisos que necesite te los pedirá aquí, uno a uno.
            </p>
            <p className="meta">
              Ejemplos: «ordena los ficheros de datos y documenta lo que hiciste», «añade pruebas
              para el módulo de rutas», «explícame qué hace este proyecto».
            </p>
          </div>
        )}

        {props.turnos.map((turno, indice) => (
          <div
            key={`${indice}-${turno.task_id}`}
            className={turno.role === "user" ? "burbuja user" : "burbuja assistant"}
          >
            {turno.content}
          </div>
        ))}

        {props.permisos.map((permiso) => (
          <div key={permiso.proposal_id} className="permiso">
            <div>
              <strong>Permiso para {permiso.kind === "command" ? "ejecutar un comando" : permiso.kind}</strong>
            </div>
            <div className="sub">{permiso.summary}</div>
            {permiso.reason && (
              <div className="sub">
                Motivo: {permiso.reason}
                {permiso.details?.command ? ` · ${String(permiso.details.command)}` : ""}
              </div>
            )}
            <div className="opciones">
              {permiso.options.map((opcion) => (
                <button
                  key={opcion}
                  className={opcion === "reject" ? "ghost" : "primary"}
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
      </div>

      {props.ocupado && (
        <div className="franja">
          <span className="chip ok">trabajando</span>
          <span>{queHace(props.ultimo) || "planificando"}</span>
        </div>
      )}

      <div className="composer">
        <textarea
          value={texto}
          placeholder="Escribe el objetivo… (Enter para enviar, Ctrl+Enter también)"
          onChange={(evento) => setTexto(evento.target.value)}
          onKeyDown={(evento) => {
            if (evento.key === "Escape") setTexto("");
            if (evento.key === "Enter" && !evento.shiftKey) {
              evento.preventDefault();
              enviar();
            }
          }}
        />
        <div className="acciones">
          <button className="primary" disabled={props.ocupado || !texto.trim()} onClick={enviar}>
            {props.ocupado ? "En marcha…" : "Enviar"}
          </button>
          <label className="chip" title="Propone alternativas en lugar de ejecutar directamente">
            <input
              type="checkbox"
              checked={plan}
              onChange={(evento) => setPlan(evento.target.checked)}
              style={{ width: "auto", marginRight: 6 }}
            />
            modo plan
          </label>
          {props.ocupado && <span className="chip warn">hay un turno en curso</span>}
        </div>
      </div>
    </>
  );
}
