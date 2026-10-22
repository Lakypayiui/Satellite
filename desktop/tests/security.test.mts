/**
 * Regresión: la ventana no puede navegar fuera de la app ni abrir ventanas nuevas.
 *
 * El preload se vuelve a ejecutar en cada navegación del `webContents`: si una página
 * remota tomara la ventana, heredaría el puente IPC con acceso al backend.
 */

import assert from "node:assert/strict";
import test from "node:test";

import {
  esNavegacionDeLaApp,
  esUrlExternaPermitida,
  manejadorNavegacion,
  manejadorVentanaNueva,
} from "../electron/security.ts";

const APP_FILE = "file:///opt/Satellite/dist/index.html";
const DEV_URL = "http://localhost:5173/";

test("la app puede quedarse en su propio fichero, incluso con hash", () => {
  assert.equal(esNavegacionDeLaApp(`${APP_FILE}#chat`, APP_FILE), true);
  assert.equal(esNavegacionDeLaApp("file:///opt/Satellite/dist/otra.html", APP_FILE), false);
});

test("en desarrollo sólo se permite el mismo origen", () => {
  assert.equal(esNavegacionDeLaApp("http://localhost:5173/chat", DEV_URL), true);
  assert.equal(esNavegacionDeLaApp("http://localhost:5173.evil.example/", DEV_URL), false);
  assert.equal(esNavegacionDeLaApp("http://localhost:5174/", DEV_URL), false);
  assert.equal(esNavegacionDeLaApp("https://localhost:5173/", DEV_URL), false);
});

test("una URL malformada nunca cuenta como navegación de la app", () => {
  assert.equal(esNavegacionDeLaApp("no es una url", DEV_URL), false);
  assert.equal(esNavegacionDeLaApp("", APP_FILE), false);
});

test("sólo se abre fuera http, https y mailto", () => {
  for (const url of ["https://example.com", " http://example.com ", "mailto:alguien@example.com"]) {
    assert.equal(esUrlExternaPermitida(url), true, url);
  }
  for (const url of [
    "file:///etc/passwd",
    "javascript:alert(1)",
    "data:text/html,<h1>x</h1>",
    "ftp://example.com",
    "",
    "no es una url",
  ]) {
    assert.equal(esUrlExternaPermitida(url), false, url);
  }
});

test("una navegación externa se cancela y se abre en el navegador", () => {
  let cancelada = false;
  const abiertas: string[] = [];
  const manejar = manejadorNavegacion(APP_FILE, (url) => abiertas.push(url));

  manejar(
    {
      preventDefault: () => {
        cancelada = true;
      },
    },
    " https://example.com/x ",
  );

  assert.equal(cancelada, true);
  assert.deepEqual(abiertas, ["https://example.com/x"]);
});

test("una navegación interna no se cancela ni se abre fuera", () => {
  let cancelada = false;
  const abiertas: string[] = [];
  const manejar = manejadorNavegacion(APP_FILE, (url) => abiertas.push(url));

  manejar(
    {
      preventDefault: () => {
        cancelada = true;
      },
    },
    `${APP_FILE}#chat`,
  );

  assert.equal(cancelada, false);
  assert.deepEqual(abiertas, []);
});

test("un protocolo no permitido se bloquea sin abrirlo", () => {
  let cancelada = false;
  const abiertas: string[] = [];
  const manejar = manejadorNavegacion(APP_FILE, (url) => abiertas.push(url));

  manejar(
    {
      preventDefault: () => {
        cancelada = true;
      },
    },
    "javascript:alert(1)",
  );

  assert.equal(cancelada, true);
  assert.deepEqual(abiertas, []);
});

test("las ventanas nuevas siempre se deniegan y los enlaces seguros se abren fuera", () => {
  const abiertas: string[] = [];
  const manejar = manejadorVentanaNueva((url) => abiertas.push(url));

  assert.deepEqual(manejar({ url: "https://example.com" }), { action: "deny" });
  assert.deepEqual(manejar({ url: "javascript:alert(1)" }), { action: "deny" });
  assert.deepEqual(abiertas, ["https://example.com"]);
});
