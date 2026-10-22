/**
 * Regresión B-04: el token no se imprime ni se acepta por stdout.
 *
 * El smoke arranca el backend con el mismo protocolo, pero no usa esta clase. Esta
 * comprobación estática evita que el proceso principal vuelva a leer `data.token` o
 * deje de entregar el secreto por stdin.
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const fuente = readFileSync(new URL("../electron/backend.ts", import.meta.url), "utf8");

test("Electron entrega el token por stdin y espera sólo el puerto", () => {
  assert.equal(fuente.includes('"--token-stdin"'), true);
  assert.equal(fuente.includes('stdio: ["pipe", "pipe", "pipe"]'), true);
  assert.equal(fuente.includes("child.stdin.end(`${token}\\n`)"), true);
  assert.equal(fuente.includes("data.token"), false);
});
