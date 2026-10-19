/**
 * Regresión: la actividad del turno no debe contar los eventos de ciclo de vida.
 *
 * Un chat directo emite sólo `task_started` + `task_finished`, así que el panel
 * mostraba «2 pasos» engañosos. El trabajo real (nodos, herramientas…) sigue visible.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import { pasosDelTurno } from "../src/turnActivity.ts";

interface EventoFalso {
  event: { event_type: string };
  marca: string;
}

function evento(event_type: string, marca = ""): EventoFalso {
  return { event: { event_type }, marca };
}

test("un chat directo sin trabajo real no muestra pasos", () => {
  const eventos = [evento("task_started"), evento("task_finished")];
  assert.deepEqual(pasosDelTurno(eventos), []);
});

test("los pasos de trabajo real se conservan", () => {
  const eventos = [
    evento("task_started"),
    evento("graph_built"),
    evento("node_started"),
    evento("tool_called"),
    evento("node_finished"),
    evento("task_finished"),
  ];
  const tipos = pasosDelTurno(eventos).map((item) => item.event.event_type);
  assert.deepEqual(tipos, ["graph_built", "node_started", "tool_called", "node_finished"]);
});

test("sólo cuenta lo posterior al último «tarea iniciada»", () => {
  const eventos = [
    evento("task_started", "turno-1"),
    evento("node_started", "turno-1"),
    evento("task_started", "turno-2"),
    evento("tool_called", "turno-2"),
    evento("task_finished", "turno-2"),
  ];
  const pasos = pasosDelTurno(eventos);
  assert.deepEqual(
    pasos.map((item) => item.marca),
    ["turno-2"],
  );
  assert.equal(pasos[0].event.event_type, "tool_called");
});

test("sin ningún «tarea iniciada» no hay pasos", () => {
  assert.deepEqual(pasosDelTurno([evento("node_started")]), []);
  assert.deepEqual(pasosDelTurno([]), []);
});
