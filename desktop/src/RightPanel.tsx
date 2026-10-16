/** Panel derecho: actividad, agentes, skills, cambios y trabajos, tal como los cuenta el runtime. */

import { useState } from "react";

import { ChangeSet, JobInfo, JobOutput, StreamItem, TaskItem, ETIQUETA_EVENTO } from "./api";
import type { Pestana } from "./App";

interface Props {
  plegado: boolean;
  pestana: Pestana;
  onPestana: (pestana: Pestana) => void;
  eventos: StreamItem[];
  tareas: TaskItem[];
  trabajos: JobInfo[];
  cambios: ChangeSet | null;
  salida: JobOutput | null;
  onVerCambios: (taskId: string) => void;
  onRestaurar: (taskId: string) => void;
  onVerSalida: (jobId: string) => void;
  onParar: (jobId: string) => void;
  onTrabajos: () => void;
  onPaleta: () => void;
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

const PESTANAS: Pestana[] = ["actividad", "agentes", "skills", "cambios", "trabajos"];

function hora(iso: string): string {
  const fecha = new Date(iso);
  return Number.isNaN(fecha.getTime()) ? "" : fecha.toLocaleTimeString().slice(0, 8);
}

/** Agentes y skills se deducen **sólo** de eventos reales: nada de suposiciones.
 *
 * El agente de cada evento sale del payload (`agent_id`, y de paso los nombres antiguos
 * `spec_id`/`spec`, que el runner ha usado de forma inconsistente) y, cuando el evento no
 * lo trae —caso de `tool_called`—, del nodo al que pertenece, que sí lo trae siempre.
 */
function leerAgentes(eventos: StreamItem[]): Agente[] {
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
      mapa.get(id) ?? { id, estado: "en espera", clase: "", detalle: "", eventos: 0 };
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
      actual.estado = "trabajando";
      actual.clase = "trabajando";
      actual.detalle = event.tool_name ? `usando ${event.tool_name}` : "razonando";
    } else if (event.event_type === "tool_called") {
      actual.estado = "trabajando";
      actual.clase = "trabajando";
      actual.detalle = `usando ${String(event.tool_name ?? "herramienta")}`;
    } else if (event.event_type === "node_finished" || event.event_type === "delegation_completed") {
      const estado = String(event.payload?.status ?? "");
      const bien = estado === "ok" || estado === "partial";
      actual.estado = bien ? "completado" : "fallido";
      actual.clase = bien ? "" : "fallido";
      actual.detalle = String(event.payload?.summary ?? "");
    } else if (event.event_type === "policy_violation") {
      actual.estado = "permiso denegado";
      actual.clase = "fallido";
      actual.detalle = "";
    }
  }
  return [...mapa.values()].reverse();
}

function leerSkills(eventos: StreamItem[]): Skill[] {
  const mapa = new Map<string, Skill>();
  for (const { event } of eventos) {
    const id = event.payload?.skill_id;
    if (typeof id !== "string" || !id) continue;
    if (event.event_type === "skill_executed") {
      mapa.set(id, {
        id,
        estado: "ejecutada",
        clase: "trabajando",
        detalle: event.payload?.cached === true ? "desde caché" : "ejecución real",
      });
    } else if (event.event_type === "skill_failed") {
      mapa.set(id, {
        id,
        estado: "fallida",
        clase: "fallido",
        detalle: String(event.payload?.error ?? "sin detalle"),
      });
    } else if (event.event_type === "skill_proposed" && !mapa.has(id)) {
      mapa.set(id, { id, estado: "propuesta", clase: "", detalle: "pendiente de aprobación" });
    }
  }
  return [...mapa.values()].reverse();
}

export function RightPanel(props: Props) {
  const [selAgente, setSelAgente] = useState("");
  const [selTarea, setSelTarea] = useState("");
  const agentes = leerAgentes(props.eventos);
  const skills = leerSkills(props.eventos);

  if (props.plegado) {
    return (
      <aside className="panel der plegado">
        <button className="plegar" onClick={() => props.onPestana(props.pestana)} title="Ctrl+J">
          ‹
        </button>
      </aside>
    );
  }

  return (
    <aside className="panel der">
      <div className="pestanas">
        {PESTANAS.map((nombre) => (
          <button
            key={nombre}
            className={props.pestana === nombre ? "activa" : ""}
            onClick={() => props.onPestana(nombre)}
          >
            {nombre}
            {nombre === "agentes" && agentes.length ? ` ${agentes.length}` : ""}
            {nombre === "skills" && skills.length ? ` ${skills.length}` : ""}
            {nombre === "trabajos" && props.trabajos.length ? ` ${props.trabajos.length}` : ""}
          </button>
        ))}
      </div>

      <div className="lista">
        {props.pestana === "actividad" && (
          <>
            {props.eventos.length === 0 && (
              <div className="vacio">
                Aquí se verá lo que haga el agente: herramientas, nodos y permisos, según ocurran.
              </div>
            )}
            {[...props.eventos].reverse().map((item) => (
              <div key={item.index} className="evento">
                <span className="hora">{hora(item.event.timestamp)}</span>
                <span className="tipo">
                  {ETIQUETA_EVENTO[item.event.event_type] ?? item.event.event_type}
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

        {props.pestana === "agentes" && (
          <>
            {agentes.length === 0 && (
              <div className="vacio">
                Ningún agente ha participado todavía.
                <br />
                El estado sale de los eventos reales: nodos, delegaciones y herramientas.
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
                  {agente.detalle ? ` · ${agente.detalle}` : ""} · {agente.eventos} eventos
                </div>
                {selAgente === agente.id && (
                  <div className="sub">Detalle completo en la pestaña actividad.</div>
                )}
              </div>
            ))}
          </>
        )}

        {props.pestana === "skills" && (
          <>
            <div className="bloque">
              <p className="etiqueta">Ejecutadas de verdad</p>
              <div className="sub" style={{ color: "var(--muted)", fontSize: 12 }}>
                Estar en el catálogo no cuenta como uso. Busca en él con{" "}
                <button className="quiet" style={{ padding: 0 }} onClick={props.onPaleta}>
                  ctrl+k · skills
                </button>
                .
              </div>
            </div>
            {skills.length === 0 && <div className="vacio">Ninguna skill se ha ejecutado aún.</div>}
            {skills.map((skill) => (
              <div key={skill.id} className="ficha" style={{ cursor: "default" }}>
                <h3>{skill.id}</h3>
                <div className="sub">
                  <span className={`estado ${skill.clase}`}>{skill.estado}</span> · {skill.detalle}
                </div>
              </div>
            ))}
          </>
        )}

        {props.pestana === "cambios" && (
          <>
            {props.tareas.length === 0 && <div className="vacio">Todavía no hay tareas guardadas.</div>}
            {props.tareas.length > 0 && (
              <div className="bloque">
                <p className="etiqueta">Tarea</p>
                <select
                  value={selTarea}
                  onChange={(evento) => {
                    setSelTarea(evento.target.value);
                    if (evento.target.value) props.onVerCambios(evento.target.value);
                  }}
                >
                  <option value="">elegir tarea…</option>
                  {props.tareas.map((tarea) => (
                    <option key={tarea.task_id} value={tarea.task_id}>
                      {tarea.task_id}
                      {tarea.changes.length ? ` · ${tarea.changes.length} archivo(s)` : ""}
                    </option>
                  ))}
                </select>
              </div>
            )}
            {props.cambios && (
              <>
                <div className="bloque" style={{ display: "flex", gap: 8, alignItems: "baseline" }}>
                  <span style={{ flex: 1, fontSize: 12, color: "var(--muted)" }}>
                    {props.cambios.files.length} archivo(s)
                  </span>
                  <button className="quiet" onClick={() => props.onRestaurar(props.cambios!.task_id)}>
                    restaurar…
                  </button>
                </div>
                {props.cambios.files.map((archivo) => (
                  <div key={archivo} className="evento">
                    <span>{archivo}</span>
                  </div>
                ))}
                <pre className="diff">{props.cambios.diff}</pre>
              </>
            )}
          </>
        )}

        {props.pestana === "trabajos" && (
          <>
            <div className="bloque" style={{ display: "flex", gap: 8, alignItems: "baseline" }}>
              <p className="etiqueta" style={{ flex: 1, margin: 0 }}>
                En segundo plano
              </p>
              <button className="quiet" onClick={props.onTrabajos}>
                actualizar
              </button>
            </div>
            {props.trabajos.length === 0 && (
              <div className="vacio">
                Ninguno en marcha. El agente los lanza con <code>run_command background</code>.
              </div>
            )}
            {props.trabajos.map((trabajo) => (
              <div key={trabajo.job_id} className="ficha" style={{ cursor: "default" }}>
                <h3>{trabajo.job_id}</h3>
                <div className="sub">
                  <span className={`estado ${trabajo.status === "failed" ? "fallido" : ""}`}>
                    {trabajo.status}
                  </span>{" "}
                  · {trabajo.command}
                </div>
                <div className="acciones">
                  <button className="quiet" onClick={() => props.onVerSalida(trabajo.job_id)}>
                    ver salida
                  </button>
                  {trabajo.status === "running" && (
                    <button className="quiet" onClick={() => props.onParar(trabajo.job_id)}>
                      detener
                    </button>
                  )}
                </div>
                {props.salida?.job_id === trabajo.job_id && (
                  <pre className="salida">{props.salida.output || "(sin salida todavía)"}</pre>
                )}
              </div>
            ))}
          </>
        )}
      </div>
    </aside>
  );
}
