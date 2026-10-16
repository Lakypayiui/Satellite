/** Panel derecho: actividad, agentes, skills, cambios y trabajos reales del runtime. */

import { useState } from "react";

import { ChangeSet, JobInfo, JobOutput, StreamItem, TaskItem, ETIQUETA_EVENTO } from "./api";
import type { Pestana } from "./App";

interface Props {
  contraido: boolean;
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
}

interface Agente {
  id: string;
  estado: "en espera" | "trabajando" | "completado" | "fallido";
  accion: string;
  detalle: string;
}

interface Skill {
  id: string;
  estado: "ejecutada" | "fallida" | "propuesta";
  detalle: string;
}

const PESTANAS: Pestana[] = ["actividad", "agentes", "skills", "cambios", "trabajos"];

function hora(iso: string): string {
  const fecha = new Date(iso);
  return Number.isNaN(fecha.getTime()) ? "" : fecha.toLocaleTimeString();
}

/** Agentes y skills se deducen **sólo** de los eventos: nada de suposiciones. */
function leerAgentes(eventos: StreamItem[]): Agente[] {
  const mapa = new Map<string, Agente>();
  for (const { event } of eventos) {
    const spec = event.payload?.agent_id ?? event.payload?.spec;
    if (typeof spec !== "string" || !spec) continue;
    const actual = mapa.get(spec) ?? { id: spec, estado: "en espera" as const, accion: "", detalle: "" };
    if (event.event_type === "node_started" || event.event_type === "delegation_started") {
      actual.estado = "trabajando";
      actual.accion = event.tool_name ? `usando ${event.tool_name}` : "razonando";
    } else if (event.event_type === "tool_called") {
      actual.estado = "trabajando";
      actual.accion = `usando ${String(event.tool_name ?? "herramienta")}`;
    } else if (event.event_type === "node_finished" || event.event_type === "delegation_completed") {
      const estado = String(event.payload?.status ?? "");
      actual.estado = estado === "ok" || estado === "partial" ? "completado" : "fallido";
      actual.accion = "";
      actual.detalle = String(event.payload?.summary ?? "");
    } else if (event.event_type === "policy_violation") {
      actual.estado = "fallido";
      actual.accion = "permiso denegado";
    }
    mapa.set(spec, actual);
  }
  return [...mapa.values()];
}

function leerSkills(eventos: StreamItem[]): Skill[] {
  const mapa = new Map<string, Skill>();
  for (const { event } of eventos) {
    const skill = event.payload?.skill_id;
    if (typeof skill !== "string" || !skill) continue;
    if (event.event_type === "skill_executed") {
      const cache = event.payload?.cached === true;
      mapa.set(skill, {
        id: skill,
        estado: "ejecutada",
        detalle: cache ? "resultado de caché (no se volvió a ejecutar)" : "ejecución real",
      });
    } else if (event.event_type === "skill_failed") {
      mapa.set(skill, {
        id: skill,
        estado: "fallida",
        detalle: String(event.payload?.error ?? "fallo sin detalle"),
      });
    } else if (event.event_type === "skill_proposed" && !mapa.has(skill)) {
      mapa.set(skill, { id: skill, estado: "propuesta", detalle: "pendiente de aprobación" });
    }
  }
  return [...mapa.values()];
}

export function RightPanel(props: Props) {
  const [selAgente, setSelAgente] = useState("");
  const [selTarea, setSelTarea] = useState("");
  const agentes = leerAgentes(props.eventos);
  const skills = leerSkills(props.eventos);

  if (props.contraido) {
    return (
      <aside className="right contraido">
        <div className="pestanas">
          <button onClick={() => props.onPestana(props.pestana)} title="Expandir panel">
            ‹
          </button>
        </div>
      </aside>
    );
  }

  return (
    <aside className="right">
      <div className="pestanas">
        {PESTANAS.map((nombre) => (
          <button
            key={nombre}
            className={props.pestana === nombre ? "activa" : ""}
            onClick={() => props.onPestana(nombre)}
          >
            {nombre}
            {nombre === "agentes" && agentes.length > 0 ? ` (${agentes.length})` : ""}
            {nombre === "skills" && skills.length > 0 ? ` (${skills.length})` : ""}
            {nombre === "trabajos" && props.trabajos.length > 0 ? ` (${props.trabajos.length})` : ""}
          </button>
        ))}
      </div>

      <div className="lista">
        {props.pestana === "actividad" && (
          <>
            {props.eventos.length === 0 && (
              <div className="vacio">
                Aquí aparecerá lo que vaya haciendo el agente: herramientas, nodos y permisos.
              </div>
            )}
            {[...props.eventos].reverse().map((item) => (
              <div key={item.index} className="evento">
                <span className="hora">{hora(item.event.timestamp)}</span>
                <span className="tipo">{ETIQUETA_EVENTO[item.event.event_type] ?? item.event.event_type}</span>
                <span>
                  {item.event.tool_name ?? String(item.event.payload?.spec ?? item.event.payload?.agent_id ?? "")}
                </span>
              </div>
            ))}
          </>
        )}

        {props.pestana === "agentes" && (
          <>
            {agentes.length === 0 && (
              <div className="vacio">
                Ningún agente ha participado todavía en esta sesión.
                <br />
                El estado sale de los eventos reales: nodos, delegaciones y herramientas.
              </div>
            )}
            {agentes.map((agente) => (
              <div
                key={agente.id}
                className="tarjeta"
                onClick={() => setSelAgente(selAgente === agente.id ? "" : agente.id)}
                style={{ cursor: "pointer" }}
              >
                <h3>{agente.id}</h3>
                <div className="sub">
                  <span className={agente.estado === "fallido" ? "chip bad" : agente.estado === "trabajando" ? "chip ok" : "chip"}>
                    {agente.estado}
                  </span>{" "}
                  {agente.accion}
                </div>
                {selAgente === agente.id && agente.detalle && (
                  <div className="sub" style={{ marginTop: 6 }}>
                    {agente.detalle}
                  </div>
                )}
              </div>
            ))}
          </>
        )}

        {props.pestana === "skills" && (
          <>
            <div className="franja">
              Sólo lo que se ejecutó de verdad. Estar en el catálogo no cuenta como uso.
            </div>
            {skills.length === 0 && (
              <div className="vacio">
                Ninguna skill se ha ejecutado en esta sesión.
                <br />
                (Las «buscadas y consideradas» aparecerán cuando el runtime emita ese evento.)
              </div>
            )}
            {skills.map((skill) => (
              <div key={skill.id} className="tarjeta">
                <h3>{skill.id}</h3>
                <div className="sub">
                  <span className={skill.estado === "fallida" ? "chip bad" : "chip ok"}>{skill.estado}</span>{" "}
                  {skill.detalle}
                </div>
              </div>
            ))}
          </>
        )}

        {props.pestana === "cambios" && (
          <>
            {props.tareas.length === 0 && <div className="vacio">Todavía no hay tareas guardadas.</div>}
            {props.tareas.length > 0 && (
              <div className="seccion">
                <h2>Tarea</h2>
                <select
                  value={selTarea}
                  onChange={(evento) => {
                    setSelTarea(evento.target.value);
                    if (evento.target.value) props.onVerCambios(evento.target.value);
                  }}
                >
                  <option value="">Elige una tarea…</option>
                  {props.tareas.map((tarea) => (
                    <option key={tarea.task_id} value={tarea.task_id}>
                      {tarea.task_id} {tarea.changes.length ? `· ${tarea.changes.length} archivo(s)` : ""}
                    </option>
                  ))}
                </select>
              </div>
            )}
            {props.cambios && (
              <>
                <div className="franja">
                  {props.cambios.files.length} archivo(s) cambiados · {props.cambios.task_id}
                  <button className="ghost" onClick={() => props.onRestaurar(props.cambios!.task_id)}>
                    Restaurar…
                  </button>
                </div>
                {props.cambios.files.map((archivo) => (
                  <div key={archivo} className="tarjeta">
                    {archivo}
                  </div>
                ))}
                <pre className="diff">{props.cambios.diff}</pre>
              </>
            )}
          </>
        )}

        {props.pestana === "trabajos" && (
          <>
            <div className="franja">
              <span className="crece">Comandos largos en segundo plano</span>
              <button className="ghost" onClick={props.onTrabajos}>
                Actualizar
              </button>
            </div>
            {props.trabajos.length === 0 && (
              <div className="vacio">
                Ningún comando en segundo plano.
                <br />
                El agente puede lanzarlos con <code>run_command background=true</code>.
              </div>
            )}
            {props.trabajos.map((trabajo) => (
              <div key={trabajo.job_id} className="tarjeta">
                <h3>{trabajo.job_id}</h3>
                <div className="sub">
                  <span
                    className={
                      trabajo.status === "failed" ? "chip bad" : trabajo.status === "running" ? "chip ok" : "chip"
                    }
                  >
                    {trabajo.status}
                  </span>{" "}
                  {trabajo.command}
                </div>
                <div className="opciones" style={{ display: "flex", gap: 6, marginTop: 8 }}>
                  <button className="ghost" onClick={() => props.onVerSalida(trabajo.job_id)}>
                    Ver salida
                  </button>
                  {trabajo.status === "running" && (
                    <button className="ghost" onClick={() => props.onParar(trabajo.job_id)}>
                      Detener
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
