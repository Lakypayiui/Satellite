/**
 * Actividad del turno en curso: qué eventos cuentan como pasos visibles.
 *
 * Los eventos de ciclo de vida (`task_started`/`task_finished`) sólo marcan el
 * arranque y el cierre del turno; no son trabajo realizado. Un chat directo emite
 * únicamente esos dos, así que contarlos mostraba «2 pasos» engañosos cuando en
 * realidad el modelo sólo respondió una frase. El trabajo real (nodos,
 * herramientas, delegaciones…) sí se conserva.
 */

/** Tipos que sólo describen el ciclo de vida del turno, no un paso de trabajo. */
const EVENTOS_DE_CICLO = new Set(["task_started", "task_finished"]);

/** Lo mínimo que necesita un evento para decidir su tipo (compatible con `StreamItem`). */
export interface EventoConTipo {
  event: { event_type: string };
}

/**
 * Los pasos del turno en curso: sólo lo posterior al último «tarea iniciada», sin
 * los eventos de ciclo de vida. Devuelve una lista vacía cuando el turno no dejó
 * ningún paso de trabajo real.
 */
export function pasosDelTurno<T extends EventoConTipo>(eventos: readonly T[]): T[] {
  let inicio = -1;
  for (let indice = eventos.length - 1; indice >= 0; indice -= 1) {
    if (eventos[indice].event.event_type === "task_started") {
      inicio = indice;
      break;
    }
  }
  if (inicio < 0) return [];
  return eventos
    .slice(inicio)
    .filter((item) => !EVENTOS_DE_CICLO.has(item.event.event_type));
}
